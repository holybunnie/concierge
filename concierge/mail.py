"""LOOP 2 over email (§10): an inbound message becomes a state-machine step and a reply.

This is the glue between Postmark and the engine, and it is deliberately thin. It resolves the
tenant from the recipient address, runs exactly the Phase 3 engine inside a tenant-scoped
session, and then — outside that session — sends whatever the engine decided to say. Network I/O
never happens with a database transaction held open.

Nothing here decides anything a prospect sees: the reply body, the disclosure, the escalation
wording and every figure are the engine's, computed before this module is reached. `mail` only
carries them to and from Postmark.
"""

from __future__ import annotations

import base64
import hmac
from dataclasses import dataclass
from typing import Any

from . import db, engine, postmark, store
from .engine import Calendar, Outcome
from .postmark import Mailer, OutboundEmail, ParsedInbound


# ---------------------------------------------------------------- webhook authenticity

def check_webhook_auth(authorization: str | None, secret: str | None) -> bool:
    """True iff the request carries the shared secret. Fails closed.

    Postmark authenticates inbound webhooks with HTTP Basic Auth in the webhook URL, so the
    common case is `Authorization: Basic base64(user:pass)` and we compare the password half.
    A `Bearer <secret>` form is also accepted for operators who front the webhook differently.
    Comparison is constant-time; a missing secret or header returns False rather than raising,
    because an unauthenticated inbound must be refused, never processed.
    """
    if not secret or not authorization:
        return False
    scheme, _, credentials = authorization.partition(" ")
    scheme = scheme.strip().lower()
    credentials = credentials.strip()
    if scheme == "basic":
        try:
            decoded = base64.b64decode(credentials).decode("utf-8", "replace")
        except Exception:
            return False
        _, _, password = decoded.partition(":")
        candidate = password or decoded
    elif scheme == "bearer":
        candidate = credentials
    else:
        return False
    return hmac.compare_digest(candidate, secret)


# ---------------------------------------------------------------- routing

class Unroutable(db.TenantUnresolved):
    """No tenant owns any address this message was sent to. A hard stop, never a default."""


def resolve_recipient(parsed: ParsedInbound) -> tuple[Any, str]:
    """Return (tenant_id, matched_address). Tries every candidate; refuses to guess a recipient."""
    for candidate in parsed.candidate_recipients:
        try:
            return db.resolve_tenant_by_inbound_address(candidate), candidate
        except db.TenantUnresolved:
            continue
    raise Unroutable(
        "No tenant owns any of the addresses this message was sent to "
        f"({parsed.candidate_recipients or 'none found'}). Refusing to guess a recipient."
    )


# ---------------------------------------------------------------- handling

@dataclass
class Handled:
    tenant_id: Any
    recipient: str
    outcome: Outcome
    reply_email: OutboundEmail | None
    owner_alert_email: OutboundEmail | None


def _reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re: your enquiry"
    return s if s.lower().startswith("re:") else f"Re: {s}"


def _threading_headers(parsed: ParsedInbound) -> list[dict[str, str]]:
    """In-Reply-To this message, References the thread root — so it threads in the client."""
    out: list[dict[str, str]] = []
    if parsed.message_id:
        out.append({"Name": "In-Reply-To", "Value": parsed.message_id})
    if parsed.thread_root:
        out.append({"Name": "References", "Value": parsed.thread_root})
    return out


def handle_inbound(payload: dict[str, Any], *, mailer: Mailer,
                   calendar: Calendar | None = None) -> Handled:
    """Resolve → run the engine (tenant-scoped) → send the reply and any owner alert.

    The mailer is injected: production passes a `PostmarkMailer`, the harness passes a recorder.
    Raises `Unroutable` before touching the database if no tenant owns the recipient.
    """
    parsed = postmark.parse_inbound(payload)
    tenant_id, recipient = resolve_recipient(parsed)

    inbound = parsed.to_inbound()
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, inbound)
        outcome = engine.step(cur, tenant, thread, inbound, calendar)
        owner_email = tenant.owner_email
        business_name = tenant.business_name

    # --- everything below is network, and runs with no transaction held open ---

    reply_email: OutboundEmail | None = None
    if outcome.reply is not None:
        reply_email = OutboundEmail(
            from_address=recipient,                 # replies come *from* the tenant's own inbox
            to_address=parsed.from_address,         # …to whoever wrote in; never a guessed address
            subject=_reply_subject(parsed.subject),
            text_body=outcome.reply,
            reply_to=recipient,
            headers=_threading_headers(parsed),
        )
        mailer.send(reply_email)

    owner_alert_email: OutboundEmail | None = None
    if outcome.owner_alert:
        owner_alert_email = OutboundEmail(
            from_address=recipient,
            to_address=owner_email,
            subject=f"[{business_name}] CONCIERGE escalation",
            text_body=outcome.owner_alert,
            reply_to=parsed.from_address,           # the owner can reply straight to the prospect
        )
        mailer.send(owner_alert_email)

    return Handled(
        tenant_id=tenant_id, recipient=recipient, outcome=outcome,
        reply_email=reply_email, owner_alert_email=owner_alert_email,
    )
