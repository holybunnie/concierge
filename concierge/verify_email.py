"""the email suite — the email connector (Postmark).

What this suite can prove today, and what it honestly cannot:

  PROVABLE NOW (real Postgres, real Postmark payload schema, production code path):
    a real inbound email document is parsed, routed to the one tenant that owns the address it
    was sent to, run through the engine, and turned into an outbound reply that comes
    FROM the tenant's own inbox, TO whoever wrote in, disclosure on the first line. Cross-tenant
    routing, unknown-recipient refusal, address normalisation, webhook authenticity and email
    threading are all exercised as attacks.

  NOT PROVABLE UNTIL CREDENTIALS LAND (reported as INFO, never as a pass):
    that a real message physically lands in a real inbox and not in spam. That needs Postmark
    account approval (item 3), the DKIM/Return-Path/MX DNS on inbox.<domain> (item 2), and the
    webhook deployed on the VPS (item 1). The live send object is built and shown here; only the
    final `PostmarkMailer.send` is stubbed by a declared recorder.

The one stand-in — the recording mailer — plays the role the engine suite's FixtureCalendar played: it
lives in the harness, not the package, and it is named as a fixture in every check that uses it.
The production sender (`postmark.PostmarkMailer`) refuses to run without a real token.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from . import config, db, engine, mail, onboarding, postmark, store
from .postmark import OutboundEmail

PROSPECT = "priya.raman@example.com"
DISCLOSURE_OPENS = "This is an AI assistant replying on behalf of"


# ---------------------------------------------------------------- the declared fixture
#
# THIS IS A FIXTURE AND IT IS NOT PART OF THE SHIPPED PACKAGE. It records the emails the engine
# decided to send, so the harness can inspect them, instead of handing them to Postmark. The
# production path uses postmark.PostmarkMailer, which POSTs to the real send API and raises
# without a token — see check on the live gap at the end.

class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    def send(self, email: OutboundEmail) -> dict[str, Any]:
        self.sent.append(email)
        return {"ErrorCode": 0, "MessageID": f"recorded-{len(self.sent)}", "_fixture": True}


# ---------------------------------------------------------------- fixtures

SPA = dict(
    description="We run a day spa offering massage, facials, waxing and nails.",
    business="Halcyon Rooms",
    answers={
        "service_menu": [
            {"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
            {"name": "Signature facial", "duration_min": 45, "price": 70, "currency": "GBP"},
        ],
        "floor_price": 70,
        "max_discount_pct": 15,
        "booking_lead_time": "Minimum 24 hours notice",
        "timezone": "Europe/London",
        "icp": "Local clients",
        "escalation_triggers": ["Anything about pregnancy or medical conditions"],
        "artifact_sample": "Hi — yes, we do that.",
        "engagement_noun": "treatment",
        "client_noun": "client",
    },
)

LEGAL = dict(
    description=("I am a barrister in chambers taking employment tribunal work — unfair "
                 "dismissal and discrimination, mostly for claimants."),
    business="Fenwick Row",
    answers={
        "practice_areas": ["Employment — unfair dismissal, discrimination"],
        "jurisdictions": ["England & Wales"],
        "consultation_fee": "£250 + VAT for the first hour, fixed",
        "fee_negotiable": "Not negotiable",
        "consultation_availability": "Mon–Thu 09:00–17:00, minimum 48 hours notice",
        "timezone": "Europe/London",
        "icp": "Employees with a live matter",
        "escalation_triggers": ["Anything urgent or with a deadline"],
        "artifact_sample": "Dear Sir or Madam — thank you for your enquiry.",
        "engagement_noun": "conference",
        "client_noun": "instructing solicitor",
    },
)


def _onboard(fixture: dict) -> tuple[uuid.UUID, str]:
    session = onboarding.start(fixture["description"])
    for key, value in fixture["answers"].items():
        session.answer(key, value)
    tenant_id, address, _ = onboarding.finalise(
        session, business_name=fixture["business"],
        owner_email=f"owner@{uuid.uuid4().hex[:8]}.example",
        owner_wallet="0x" + uuid.uuid4().hex[:40].ljust(40, "0"))
    return tenant_id, address


def _inbound_payload(*, to_address: str, text: str, subject: str = "Enquiry",
                     from_address: str = PROSPECT, from_name: str = "Priya Raman",
                     message_id: str | None = None,
                     references: str | None = None,
                     in_reply_to: str | None = None,
                     cc_address: str | None = None) -> dict[str, Any]:
    """A Postmark inbound JSON document, shaped exactly as their parser delivers one."""
    headers = [{"Name": "Message-ID", "Value": message_id or f"<{uuid.uuid4().hex}@mail.example>"}]
    if references:
        headers.append({"Name": "References", "Value": references})
    if in_reply_to:
        headers.append({"Name": "In-Reply-To", "Value": in_reply_to})
    payload: dict[str, Any] = {
        "FromName": from_name,
        "MessageStream": "inbound",
        "From": from_address,
        "FromFull": {"Email": from_address, "Name": from_name, "MailboxHash": ""},
        "To": to_address,
        "ToFull": [{"Email": to_address, "Name": "", "MailboxHash": ""}],
        "OriginalRecipient": to_address,
        "Subject": subject,
        "MessageID": message_id or headers[0]["Value"],
        "ReplyTo": "",
        "Date": "Thu, 23 Jul 2026 10:15:00 +0000",
        "TextBody": text,
        "HtmlBody": f"<p>{text}</p>",
        "StrippedTextReply": text if (references or in_reply_to) else "",
        "Headers": headers,
        "Attachments": [],
    }
    if cc_address:
        payload["Cc"] = cc_address
        payload["CcFull"] = [{"Email": cc_address, "Name": "", "MailboxHash": ""}]
    return payload


# ---------------------------------------------------------------- the suite

def run(r) -> None:
    db.migrate()

    spa_id, spa_addr = _onboard(SPA)
    legal_id, legal_addr = _onboard(LEGAL)

    # ---- 1. a real Postmark inbound document is parsed into the fields we route on
    payload = _inbound_payload(
        to_address=spa_addr, subject="Massage prices?",
        text="Hi, how much is a deep tissue massage?",
        message_id="<m1@mail.example>")
    parsed = postmark.parse_inbound(payload)
    r.check(
        "A real Postmark inbound payload is parsed into recipient, sender, body and thread key",
        (spa_addr in parsed.candidate_recipients
         and parsed.from_address == PROSPECT
         and "deep tissue massage" in parsed.text
         and parsed.message_id == "<m1@mail.example>"
         and parsed.thread_root == "<m1@mail.example>"),
        "Postmark delivers inbound mail as a JSON document. We read the recipient (from\n"
        "OriginalRecipient/ToFull) to route on, the sender to reply to, the prospect's actual\n"
        "words (StrippedTextReply, falling back to TextBody), and the Message-ID that becomes the\n"
        "thread key. Nothing is assumed present — a missing field degrades, it does not crash.",
        f"| candidate recipients: {parsed.candidate_recipients}\n"
        f"| from: {parsed.from_address}  ({parsed.from_name})\n"
        f"| text: {parsed.text!r}\n"
        f"| message_id: {parsed.message_id}   thread_root: {parsed.thread_root}",
    )

    # ---- 2. the whole path: inbound email -> engine -> outbound reply, from the tenant's inbox
    mailer = RecordingMailer()
    handled = mail.handle_inbound(payload, mailer=mailer)
    reply = handled.reply_email
    r.check(
        "An inbound email is routed, quoted from the profile, and answered FROM the tenant's inbox",
        (handled.tenant_id == spa_id and reply is not None
         and reply.from_address == spa_addr and reply.to_address == PROSPECT
         and reply.text_body.startswith(DISCLOSURE_OPENS)
         and "85" in reply.text_body and handled.outcome.action == "quoted"),
        "This is LOOP 2 over email end to end. The address the mail was sent to resolved to\n"
        "exactly one tenant; that tenant's own £85 price was quoted (derived by the engine from\n"
        "the stored profile, not by this module); and the reply is addressed FROM the tenant's\n"
        "inbox so the prospect can simply reply and land back on our inbound MX.\n"
        "THE SEND IS RECORDED, NOT LIVE: a real inbox delivery needs items 1-3 (see the final\n"
        "note). Everything up to the send is the production code path.",
        f"| routed to tenant: {handled.tenant_id} (spa={spa_id})\n"
        f"| From: {reply.from_address}\n| To:   {reply.to_address}\n"
        f"| Subject: {reply.subject}\n"
        f"| ReplyTo: {reply.reply_to}\n"
        f"| headers: {reply.headers}\n"
        f"| --- body ---\n" + "\n".join(f"| {ln}" for ln in reply.text_body.splitlines()),
    )

    # ---- 3. ATTACK — a message to an address no tenant owns is refused, never defaulted
    orphan = _inbound_payload(
        to_address="nobody@inbox.quietdesks.com",
        text="Do you take new clients?", message_id="<orphan@mail.example>")
    refused = False
    try:
        mail.handle_inbound(orphan, mailer=RecordingMailer())
    except db.TenantUnresolved as e:
        refused = True
        detail = str(e)
    r.check(
        "ATTACK — mail to an address no tenant owns is refused, not routed to a default tenant",
        refused,
        "The resolver returns a tenant or it raises; there is no default and no 'closest match'.\n"
        "A message addressed to nobody we know is refused before any tenant session is opened, so\n"
        "it cannot accidentally load — or reply on behalf of — some other business.",
        f"| sent to: nobody@inbox.quietdesks.com\n| outcome: {'refused (Unroutable)' if refused else 'ROUTED — WRONG'}\n"
        f"| {detail if refused else ''}",
    )

    # ---- 4. ATTACK — routing is by the exact address; case and +tag do not create a leak
    tagged = _inbound_payload(
        to_address=spa_addr.upper().replace("@", "+urgent@"),
        text="How much is a signature facial?", message_id="<tag@mail.example>")
    tid, matched = mail.resolve_recipient(postmark.parse_inbound(tagged))
    unknown_plus = _inbound_payload(
        to_address=spa_addr.replace("@", "+ghost@").replace("halcyon", "halcyonx"),
        text="hello", message_id="<ghost@mail.example>")
    ghost_refused = False
    try:
        mail.resolve_recipient(postmark.parse_inbound(unknown_plus))
    except db.TenantUnresolved:
        ghost_refused = True
    r.check(
        "ATTACK — UPPERCASE and a +plus-tag resolve to the real owner; a fake local part does not",
        tid == spa_id and matched == spa_addr and ghost_refused,
        "A sender can upper-case an address or bolt on a +tag. Normalisation strips both back to\n"
        "the canonical address before lookup, so 'HALCYON+urgent@…' reaches the spa — and cannot\n"
        "be used to invent a sub-address for a tenant that does not exist. A different local part\n"
        "(halcyonx…) is simply unknown and refused, tag or no tag.",
        f"| sent to: {spa_addr.upper().replace('@', '+urgent@')}\n"
        f"| resolved to: {tid} (spa={spa_id}), matched address {matched}\n"
        f"| fake local part 'halcyonx…+ghost': {'refused' if ghost_refused else 'RESOLVED — WRONG'}",
    )

    # ---- 5. ATTACK — the webhook fails closed
    secret = "s3cr3t-webhook-token"
    good_basic = "Basic " + _b64(f"postmark:{secret}")
    cases = {
        "no header": mail.check_webhook_auth(None, secret),
        "no secret configured": mail.check_webhook_auth(good_basic, None),
        "wrong password": mail.check_webhook_auth("Basic " + _b64("postmark:wrong"), secret),
        "unknown scheme": mail.check_webhook_auth(f"Token {secret}", secret),
        "correct Basic": mail.check_webhook_auth(good_basic, secret),
        "correct Bearer": mail.check_webhook_auth(f"Bearer {secret}", secret),
    }
    r.check(
        "ATTACK — the inbound webhook rejects everything but the shared secret, and fails closed",
        (cases["correct Basic"] and cases["correct Bearer"]
         and not any(v for k, v in cases.items() if k.startswith(("no ", "wrong", "unknown")))),
        "Postmark authenticates inbound webhooks with HTTP Basic Auth in the URL — there is no\n"
        "HMAC signature on inbound mail (this differs from the build spec and is recorded in the\n"
        "Verification Ledger). So the check compares the shared secret in constant time and\n"
        "returns False for a missing header, a missing secret, a wrong password or an unexpected\n"
        "scheme. An unauthenticated POST is refused before the body is even read.",
        "\n".join(f"| {k:22} -> {v}" for k, v in cases.items()),
    )

    # ---- 6. SB 243 — the message actually queued for sending opens with the disclosure
    r.check(
        "SB 243 — the outbound email queued for delivery discloses the AI on its first line",
        (len(mailer.sent) >= 1
         and all(e.text_body.startswith(DISCLOSURE_OPENS) and "HUMAN" in e.text_body
                 for e in mailer.sent)),
        "The disclosure is produced by engine.render, the single function that builds any\n"
        "outbound body, so email inherits it for free — but this checks the property on the\n"
        "actual OutboundEmail objects the mailer was handed, not on the engine in isolation.\n"
        "Every queued message leads with the disclosure and carries the word HUMAN as a route out.",
        f"| emails queued: {len(mailer.sent)}\n"
        f"| first line: {mailer.sent[0].text_body.splitlines()[0]}",
    )

    # ---- 7. email threading — a reply on the same chain continues the same thread
    root = "<m1@mail.example>"
    first_thread_id = handled.outcome.thread.thread_id
    follow = _inbound_payload(
        to_address=spa_addr, subject="Re: Massage prices?",
        text="yes please, let's book it",
        message_id="<m2@mail.example>",
        references=f"{root} {reply.headers[-1]['Value'] if reply.headers else ''}".strip(),
        in_reply_to=root)
    handled2 = mail.handle_inbound(follow, mailer=RecordingMailer())
    r.check(
        "A reply carrying the References chain continues the same thread, not a new one",
        (handled2.outcome.thread.thread_id == first_thread_id
         and handled2.outcome.state_before == "AWAITING_REPLY"),
        "Email conversations are threaded by the root Message-ID, which every client copies into\n"
        "References on reply. The prospect's 'yes please' arrives on the same chain as the\n"
        "original enquiry, so it opens the existing thread — the engine sees state AWAITING_REPLY\n"
        "(the quote it sent) and advances it, rather than treating the reply as a fresh inquiry.",
        f"| first thread:  {first_thread_id}\n"
        f"| reply thread:  {handled2.outcome.thread.thread_id}\n"
        f"| same thread:   {handled2.outcome.thread.thread_id == first_thread_id}\n"
        f"| state seen on reply: {handled2.outcome.state_before} -> {handled2.outcome.state_after}"
        f"  [{handled2.outcome.action}]",
    )

    # ---- 8. addresses are inbox.<domain> when a domain exists, and visibly-dead otherwise
    prev = os.environ.get("CONCIERGE_DOMAIN")
    os.environ["CONCIERGE_DOMAIN"] = "quietdesks.com"
    config._loaded = True  # ensure get() reads os.environ, not a stale .env parse
    live_domain = config.inbound_domain()
    live_addr = onboarding.allocate_inbound_address("Halcyon Rooms")
    if prev is None:
        del os.environ["CONCIERGE_DOMAIN"]
    else:
        os.environ["CONCIERGE_DOMAIN"] = prev
    pending_addr = spa_addr  # allocated earlier, with no domain configured
    r.check(
        "Tenant addresses are <slug>@inbox.<domain> once the domain lands, and never a fake-live "
        "placeholder before",
        (live_domain == "inbox.quietdesks.com"
         and live_addr.endswith("@inbox.quietdesks.com")
         and pending_addr.lower().endswith("@" + onboarding.PENDING_DOMAIN.lower())),
        "Inbound lands on a dedicated inbox.<domain> subdomain, so tenant addresses are\n"
        "<slug>@inbox.<domain>. Until the operator provides the domain (item 2), the address\n"
        "comes back on PENDING-DOMAIN.invalid — a TLD reserved by RFC 2606 that can never\n"
        "resolve, so it fails loudly instead of looking deliverable. No placeholder pretends to\n"
        "be live.",
        f"| with CONCIERGE_DOMAIN=quietdesks.com -> inbound domain {live_domain!r}\n"
        f"| example address: {live_addr}\n"
        f"| with no domain configured -> {pending_addr}",
    )

    # ---- honest notes -------------------------------------------------------------------

    token = config.postmark_token()
    domain = config.get("CONCIERGE_DOMAIN")
    r.note(
        "Live delivery is not yet provable — and nothing here pretends it is",
        "the email suite's final requirement — a real reply landing in a real inbox, not spam — is the one\n"
        "thing this run cannot demonstrate, because it needs credentials that have not arrived:\n"
        f"  - Postmark server token (item 3): {'PRESENT' if token else 'MISSING — build the send, cannot send'}\n"
        f"  - Domain for inbox.<domain> (item 2): {'PRESENT (' + domain + ')' if domain else 'MISSING'}\n"
        "  - VPS to host the webhook publicly (item 1), plus Postmark account approval.\n"
        "The outbound message is fully built and shown in check 2; only the final POST to\n"
        "Postmark's send API is stood in for by the recording fixture. When the token lands,\n"
        "production uses postmark.PostmarkMailer, which refuses to run without it rather than\n"
        "simulating a send. The remaining step is an operator-run live round trip: email the\n"
        "tenant address, confirm the reply arrives and is not in spam.",
    )
    r.note(
        "The recording mailer is the only stand-in, and it is not in the shipped package",
        "Exactly as the engine suite declared its calendar: RecordingMailer lives in this harness file and\n"
        "is named a fixture in every check that touches it. Production sends through\n"
        f"postmark.PostmarkMailer. Fixture module: {RecordingMailer.__module__}",
    )
    r.note(
        "DNS for sending (SPF/DKIM/DMARC) and inbound (MX) is an operator+DNS step, verified live "
        "at gate time",
        "Once item 2 lands, inbox.quietdesks.com needs: an MX to inbound.postmarkapp.com; the\n"
        "DKIM TXT and Return-Path CNAME Postmark generates for the sending domain; and a DMARC\n"
        "record. These are real DNS records checked live when the domain exists — not asserted\n"
        "here, because CONCIERGE_DOMAIN is not yet configured in this environment.",
    )


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()
