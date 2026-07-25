"""The Postmark seam (the email connector). Two directions, one file.

Inbound:  Postmark receives mail on inbox.<domain>, parses it, and POSTs a JSON document to our
          webhook. `parse_inbound` turns that document into the fields the rest of the system
          needs — never trusting a field to exist, because a real MTA omits plenty.

Outbound: `PostmarkMailer.send` POSTs to the Postmark send API. It requires a real token and
          raises loudly without one (§3): there is no offline "pretend to send" path in the
          production object. The harness supplies its own recording mailer, declared as a fixture
          exactly like the engine suite's calendar.

Kept to the standard library on purpose — `verify.py` already speaks HTTP this way, and every
dependency is a supply-chain surface in a system that signs commitments.

## What reality turned out to be (recorded in the Verification Ledger)

Postmark's *inbound* webhook has no HMAC signature — the build spec's "verify signature" does
not apply to it. Authenticity is established by HTTP Basic Auth carried in the webhook URL. So
`concierge.mail.check_webhook_auth` verifies that, and this module does not pretend to check a
signature that the provider never sends.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any, Protocol

from . import db  # for normalise_address; no circular import — db does not import postmark
from .engine import Inbound

POSTMARK_SEND_URL = "https://api.postmarkapp.com/email"
USER_AGENT = "CONCIERGE/0.4 (+https://github.com/holybunnie/concierge)"


class PostmarkError(RuntimeError):
    """The send API returned something other than success. Never swallowed into a fake send."""


# ---------------------------------------------------------------- inbound

@dataclass
class ParsedInbound:
    """One inbound email, reduced to what the engine and the router need.

    `candidate_recipients` is a list rather than one address because a message can legitimately
    reach a tenant on To, Cc, or the SMTP envelope (OriginalRecipient). The router tries each
    against the tenant resolver and takes the first that belongs to a real tenant — a message
    addressed to the prospect with the tenant on Cc still finds its home, and one addressed to
    nobody we know is refused rather than guessed.
    """

    candidate_recipients: list[str]
    from_address: str
    from_name: str | None
    subject: str
    text: str
    message_id: str | None
    thread_root: str | None
    received_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))

    def to_inbound(self) -> Inbound:
        """The channel-agnostic message the state machine consumes.

        `external_ref` is the thread root so that every message in one email conversation opens
        the same thread — see `_thread_root`.
        """
        return Inbound(
            body=self.text,
            from_address=self.from_address,
            from_name=self.from_name,
            external_ref=self.thread_root,
            received_at=self.received_at,
        )


def _headers_map(payload: dict[str, Any]) -> dict[str, str]:
    """Postmark delivers headers as a list of {Name, Value}. Fold to a lowercase-keyed dict."""
    out: dict[str, str] = {}
    for h in payload.get("Headers") or []:
        name = (h.get("Name") or "").strip().lower()
        if name:
            out[name] = h.get("Value") or ""
    return out


def _first_message_id(value: str | None) -> str | None:
    """The first `<...>` token in a References / In-Reply-To / Message-ID header."""
    if not value:
        return None
    start = value.find("<")
    end = value.find(">", start + 1)
    if start != -1 and end != -1:
        return value[start: end + 1]
    return value.strip() or None


def _thread_root(headers: dict[str, str], message_id: str | None) -> str | None:
    """A stable key for the whole email conversation.

    Standard email threading: the root is the first Message-ID in the References chain, which
    every conforming client copies forward on reply. Falling back through In-Reply-To to this
    message's own Message-ID means a brand-new inquiry becomes its own root, and the reply we
    send (carrying References: <root>) keeps the prospect's client pointed back at it.
    """
    refs = _first_message_id(headers.get("references"))
    if refs:
        return refs
    in_reply = _first_message_id(headers.get("in-reply-to"))
    if in_reply:
        return in_reply
    return message_id


def _recipient_candidates(payload: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    if payload.get("OriginalRecipient"):
        raw.append(payload["OriginalRecipient"])
    for key in ("ToFull", "CcFull", "BccFull"):
        for entry in payload.get(key) or []:
            if entry.get("Email"):
                raw.append(entry["Email"])
    # Fall back to the flat header strings if the *Full arrays were absent.
    for key in ("To", "Cc"):
        if payload.get(key):
            raw.extend(part.strip() for part in str(payload[key]).split(","))

    seen: list[str] = []
    for addr in raw:
        try:
            norm = db.normalise_address(addr)
        except db.TenantUnresolved:
            continue
        if norm not in seen:
            seen.append(norm)
    return seen


def parse_inbound(payload: dict[str, Any]) -> ParsedInbound:
    """Reduce a Postmark inbound JSON document to a `ParsedInbound`. Tolerant of missing keys."""
    from_full = payload.get("FromFull") or {}
    from_address = (from_full.get("Email") or payload.get("From") or "").strip()
    from_name = from_full.get("Name") or payload.get("FromName") or None

    headers = _headers_map(payload)
    message_id = _first_message_id(payload.get("MessageID")) or _first_message_id(
        headers.get("message-id"))

    # StrippedTextReply is the prospect's new words with the quoted history removed — exactly
    # what the engine should read. It only exists when Postmark could detect a reply, so fall
    # back to the full text body, then to the subject for a bare "quote for X" one-liner.
    text = (payload.get("StrippedTextReply") or payload.get("TextBody") or "").strip()
    subject = (payload.get("Subject") or "").strip()
    if not text:
        text = subject

    return ParsedInbound(
        candidate_recipients=_recipient_candidates(payload),
        from_address=from_address,
        from_name=(from_name or None),
        subject=subject,
        text=text,
        message_id=message_id,
        thread_root=_thread_root(headers, message_id),
    )


# ---------------------------------------------------------------- outbound

@dataclass
class OutboundEmail:
    """A message to send. Built by `concierge.mail`, sent by a `Mailer`."""

    from_address: str
    to_address: str
    subject: str
    text_body: str
    reply_to: str | None = None
    headers: list[dict[str, str]] = field(default_factory=list)
    message_stream: str = "outbound"

    def to_postmark(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "From": self.from_address,
            "To": self.to_address,
            "Subject": self.subject,
            "TextBody": self.text_body,
            "MessageStream": self.message_stream,
        }
        if self.reply_to:
            body["ReplyTo"] = self.reply_to
        if self.headers:
            body["Headers"] = self.headers
        return body


class Mailer(Protocol):
    def send(self, email: OutboundEmail) -> dict[str, Any]: ...


class PostmarkMailer:
    """The production sender. No token → it refuses; it never simulates a send (§3)."""

    def __init__(self, token: str):
        if not token:
            raise PostmarkError(
                "POSTMARK_SERVER_TOKEN is not set (OPERATOR_PROVIDES item 3). CONCIERGE will not "
                "pretend to send an email — it reports the message as undeliverable instead."
            )
        self._token = token

    def send(self, email: OutboundEmail) -> dict[str, Any]:
        data = json.dumps(email.to_postmark()).encode("utf-8")
        req = urllib.request.Request(
            POSTMARK_SEND_URL, method="POST", data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "X-Postmark-Server-Token": self._token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise PostmarkError(f"Postmark send failed: HTTP {e.code} {detail}") from e
        # Postmark returns ErrorCode 0 on success. Anything else is a failure, reported not hidden.
        if out.get("ErrorCode", 0) != 0:
            raise PostmarkError(f"Postmark send rejected: {out.get('ErrorCode')} {out.get('Message')}")
        return out
