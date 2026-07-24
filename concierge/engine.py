"""The state machine (§9) — the part that actually talks to a prospect.

Every message runs the same loop: resolve the tenant, load only their profile, work out which
*conversational act* just happened, apply the deterministic rules, persist, write a receipt.

## The one rule that keeps this trade-neutral

Read `PROSE` below. It is the complete set of words CONCIERGE puts in an outbound message, and
it contains no trade vocabulary at all — no treatment, no viewing, no consultation, no matter,
no property. That is not because those words are bad. A dentist's replies *should* say
"consultation" and an estate agent's *should* say "viewing"; a product that says "your service
appointment" to everyone reads like a web form and loses the tenant's register, which for a
£250/hour practice is exactly what they are paying to avoid.

The words are trade-specific. They are just not *ours*. Every noun in an outbound message comes
from the tenant's profile:

    {service}      the profile's own name for the thing — "Boiler service", "Signature facial"
    {engagement}   profile.lexicon.engagement_noun — "viewing" / "consultation" / "call-out"
    {client}       profile.lexicon.client_noun — "patient" / "guest" / "buyer"
    {price}        derived by `pricing`, from the profile, by arithmetic

So the split is: **conversational acts live in code, domain vocabulary lives in the profile.**
Accepting an offer, countering on price, asking a question, picking a slot, going quiet — those
are identical in every trade on earth, and encoding them here costs nothing. Naming the thing
being sold is the tenant's business, literally.

The Phase 3 harness proves this rather than asserting it: it greps `PROSE` for a list of trade
nouns and fails if any appears, then runs the same code for a spa, a barrister and a business
no template has ever heard of, and shows the three transcripts side by side reading in three
different registers.

## What is deliberately absent

No LLM call, and no import that could make one. §2 permits a model to draft prose, and one may
later restyle these messages into the tenant's voice using their `artifact_samples`. It may
never change a number, a name or a state: the figures are computed before any drafting could
occur, and the receipt records the computed value.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo, available_timezones

from psycopg import Cursor

from . import config, confidence, guardrails, lexicon, pricing, receipts, store
from .models import Receipt, Tenant, Thread
from .pricing import Quote, Unquotable

# ---------------------------------------------------------------- outbound prose
#
# The entire vocabulary of the product, in one place so it can be audited in one place.
# INVARIANT (checked by GATE 3): no trade noun appears here. Domain words arrive through
# {service}, {engagement} and {client}, all of which come from the tenant's profile.

PROSE: dict[str, str] = {
    # SB 243: first line of every outbound message, with a route to a human. Not configurable.
    "disclosure":
        "This is an AI assistant replying on behalf of {business}. I'm not a human. "
        "If you'd rather deal with a person, reply with the word HUMAN and {owner_email} "
        "will pick this up directly.",

    "quote":
        "Thanks for getting in touch about {service}.\n\n"
        "{service}{duration} is {price}.\n\n"
        "{verify_line}"
        "Would you like to go ahead and get a {engagement} booked in?",

    "counter_agreed":
        "I can do {agreed} for {service}.\n\n"
        "{verify_line}"
        "If that works for you, I'll get a {engagement} booked in.",

    "hold_price":
        "I'm able to hold {service} at {price}.\n\n"
        "{verify_line}"
        "Shall I get a {engagement} booked in?",

    "ask_timezone":
        "Good — let's get that {engagement} booked in.\n\n"
        "What timezone are you in? I'd rather ask than assume and put it in your calendar "
        "an hour out.",

    "ask_timezone_again":
        "Sorry — I didn't recognise that as a timezone, and I'd rather ask twice than book "
        "the wrong hour.\n\n"
        "Could you give me a city or a zone name — for example \"London\", \"New York\", "
        "or \"Europe/Madrid\"?",

    "offer_slots":
        "Here are the next times I can offer for your {engagement}, in {tz}:\n\n"
        "{slots}\n\n"
        "Reply with 1, 2 or 3 and I'll confirm it.",

    "slot_taken":
        "That time went while we were talking — someone else took it. Here's what's still "
        "open, in {tz}:\n\n"
        "{slots}\n\n"
        "Reply with 1, 2 or 3.",

    "booked":
        "Confirmed — your {engagement} is {when} ({tz}).\n\n"
        "{terms_line}"
        "{verify_line}"
        "You'll get a calendar invitation separately.",

    "escalated":
        "Thanks for getting in touch. That one needs {business} to answer personally rather "
        "than me, so I've passed it straight to them and someone will come back to you "
        "directly.",

    "closed":
        "{business} has this one directly now, so I'll leave it with them rather than "
        "reply over the top of a colleague.",

    "awaiting_approval_hold":
        "Thanks for the follow-up — {business} is reviewing the last reply before it goes "
        "out, and you'll hear from them shortly.",

    # Safe Follow-Up (addendum) — reuses {terms_line}, which already renders whatever is
    # currently on the table (an agreed figure, or the original quote) from THIS thread's own
    # offer. Never a generic "just checking in" disconnected from what was actually discussed.
    "follow_up":
        "Just checking in — {terms_line}"
        "Still keen to get a {engagement} booked in, or is there anything else I can answer "
        "first?",

    # Feature 3 (public receipt verification, GATE 6b) — appended to a commitment-bearing
    # reply only when a receipt id and a real public base URL both exist (never a placeholder
    # link). Its own PROSE entry, not an f-string built inline in `render`, so it stays inside
    # the one table GATE 3 check 3 greps for trade vocabulary.
    "verify_line":
        "This offer is cryptographically committed — verify it hasn't changed: "
        "{verify_url}\n\n",
}

# Words that would make this a single-trade product if they appeared in PROSE. The GATE 3
# check greps for these. Extend the list freely — it costs nothing and catches the exact
# regression this design exists to prevent.
TRADE_NOUNS = (
    "treatment", "massage", "facial", "spa", "salon", "therapist",
    "viewing", "property", "listing", "tenancy", "landlord", "commission",
    "consultation", "barrister", "solicitor", "chambers", "matter", "counsel",
    "patient", "clinic", "diagnosis", "call-out", "boiler",
)

ACCEPTED_BOOKING_STATUSES = frozenset({"accepted", "confirmed", "pending"})


# ---------------------------------------------------------------- calendar seam

class CalendarUnavailable(RuntimeError):
    """No calendar is connected, or it refused. Always an escalation — never a claimed booking."""


class Calendar(Protocol):
    """The seam Phase 5 fills with real Cal.com calls.

    Adapters return **UTC-aware datetimes only**. Rendering into the prospect's timezone
    happens here, once, so a timezone bug has exactly one place to live.
    """

    def slots(self, *, tenant: Tenant, earliest: datetime, limit: int) -> list[datetime]: ...

    def book(self, *, tenant: Tenant, thread: Thread, start_utc: datetime,
             attendee_name: str, attendee_email: str, attendee_timezone: str,
             notes: str) -> dict[str, Any]: ...


class NoCalendar:
    """The default. Refuses loudly rather than inventing an appointment (§3).

    This is what runs in production today, because OPERATOR_PROVIDES item 4 (Cal.com) has not
    arrived. A prospect who agrees terms is escalated to the owner to book by hand — which is a
    worse product than a real booking, and an infinitely better one than a confirmation for an
    appointment that does not exist.
    """

    def slots(self, **_: Any) -> list[datetime]:
        raise CalendarUnavailable(
            "No calendar is connected (OPERATOR_PROVIDES item 4, Cal.com API key). I will not "
            "offer times I cannot actually hold."
        )

    def book(self, **_: Any) -> dict[str, Any]:
        raise CalendarUnavailable(
            "No calendar is connected, so this cannot be booked. Escalating to the owner."
        )


# ---------------------------------------------------------------- message types

@dataclass
class Inbound:
    """One message arriving from a prospect. Channel-agnostic — Phase 4 supplies these."""

    body: str
    from_address: str
    from_name: str | None = None
    external_ref: str | None = None
    received_at: datetime = field(
        default_factory=lambda: datetime.now(dt_timezone.utc))


@dataclass
class Outcome:
    """What happened, in enough detail for the harness to show its working."""

    thread: Thread
    state_before: str
    state_after: str
    reply: str | None            # to the prospect; None when replying would be wrong
    owner_alert: str | None      # to the tenant's owner
    receipt: Receipt | None
    action: str
    rule_checked: str
    within_rules: bool
    derivation: tuple[str, ...] = ()
    quote: Quote | None = None
    confidence: dict[str, Any] | None = None


@dataclass
class Decision:
    """The pure result of thinking about a message, before anything is written down."""

    state_after: str
    action: str
    rule_checked: str
    within_rules: bool
    detail: dict[str, Any]
    reply_body: str | None = None
    owner_alert: str | None = None
    derivation: tuple[str, ...] = ()
    quote: Quote | None = None
    offer: dict[str, Any] | None = None
    client_timezone: str | None = None
    offered_slots: list[dict[str, Any]] | None = None
    confidence: dict[str, Any] | None = None
    # Feature 1 (Product-Gap Intelligence) — set ONLY on the Unquotable/"asked about something
    # not in the profile" escalation. Never set for any other escalation reason (a human
    # request, a tripped trigger, a floor breach) — those are not product gaps.
    product_gap: str | None = None


# ---------------------------------------------------------------- conversational acts
#
# Universal across trades: these describe what a person DID, not what they bought.

_ACCEPT = (
    "yes please", "yes", "go ahead", "sounds good", "that works", "works for me",
    "lets do it", "let's do it", "book me", "book it", "happy with that", "perfect",
    "great", "ok great", "okay great", "agreed", "i'll take it", "ill take it", "sign me up",
)

_DISCOUNT_WORDS = (
    "discount", "cheaper", "cheapest", "lower", "reduce", "reduction", "knock", "off the price",
    "best price", "best you can do", "budget", "afford", "too expensive", "beat that", "haggle",
)

_HUMAN_WORDS = ("human", "real person", "speak to someone", "talk to a person")

_SPAM_MARKERS = (
    "unsubscribe", "seo services", "guest post", "backlinks", "increase your ranking",
    "bulk email", "crypto investment", "we can rank your", "link building", "outreach campaign",
)

_MONEY_IN = re.compile(r"[£$€]\s?(\d[\d,]*(?:\.\d{1,2})?)|(\d[\d,]*(?:\.\d{1,2})?)\s?%")
_BARE_NUM = re.compile(r"\b(\d[\d,]*(?:\.\d{1,2})?)\b")
_ORDINALS = {"1": 0, "2": 1, "3": 2, "first": 0, "second": 1, "third": 2, "one": 0, "two": 1, "three": 2}
_NOTICE = re.compile(r"(?:minimum|at least|min)\s*(\d{1,3})\s*(hour|hr|day)", re.I)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").split())


def is_spam(text: str) -> bool:
    """Deliberately small. Real filtering is Postmark's job (Phase 4); this catches the obvious."""
    t = _norm(text)
    return any(m in t for m in _SPAM_MARKERS)


def wants_human(text: str) -> bool:
    return any(w in _norm(text) for w in _HUMAN_WORDS)


def is_acceptance(text: str) -> bool:
    t = _norm(text).strip(".!" )
    return any(t == a or t.startswith(a + " ") or f" {a}" in t for a in _ACCEPT)


def wants_discount(text: str) -> bool:
    return any(w in _norm(text) for w in _DISCOUNT_WORDS)


def named_amount(text: str, *, allow_bare: bool = False) -> float | None:
    """The number a prospect put on the table, if any. Currency-agnostic and unit-agnostic.

    `allow_bare` is set only when a quote is already open on the thread. Real counter-offers
    are usually naked numbers — "could you do 75?", "how about 1.2" — and requiring a currency
    symbol means the most common way a person haggles is the one way this misses. Outside a
    live negotiation a bare number is far more likely to be a house number or a headcount, so
    the looser reading is scoped to where it is safe.
    """
    m = _MONEY_IN.search(text or "")
    raw = (m.group(1) or m.group(2)) if m else None
    if raw is None and allow_bare:
        m = _BARE_NUM.search(text or "")
        raw = m.group(1) if m else None
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def picked_slot(text: str) -> int | None:
    for token in re.findall(r"[a-z0-9]+", _norm(text)):
        if token in _ORDINALS:
            return _ORDINALS[token]
    return None


def parse_timezone(text: str) -> str | None:
    """An IANA zone, or a city that maps to exactly one. Never inferred from anything else (§9b.1).

    Ambiguity returns None and the prospect is asked again. Guessing here books someone into
    the wrong hour, which is the single most visible way this product can embarrass a tenant.
    """
    raw = _norm(text)
    zones = available_timezones()

    for token in re.findall(r"[a-z_]+/[a-z_]+", raw.replace(" ", "_")):
        for z in zones:
            if z.lower() == token:
                return z
    if "utc" in raw.split() or "gmt" in raw.split():
        return "UTC"

    # A bare city name, accepted only when exactly one zone ends with it.
    words = re.findall(r"[a-z]{3,}", raw)
    for word in words:
        hits = [z for z in zones if z.lower().rsplit("/", 1)[-1] == word]
        if len(hits) == 1:
            return hits[0]
    return None


def notice_hours(profile: dict[str, Any]) -> int | None:
    """Minimum lead time from the tenant's own booking rules, when they stated one.

    Only the notice window is enforced here. The *shape* of a week — which days, which hours —
    is the connected calendar's own schedule, and Cal.com already returns availability that
    respects it (§9b.2). Re-deriving it from prose would give two sources of truth that can
    disagree, and the one that loses would be the real calendar.
    """
    rules = ((profile.get("calendar_ref") or {}).get("booking_rules")) or ""
    m = _NOTICE.search(str(rules))
    if not m:
        return None
    n = int(m.group(1))
    return n * 24 if m.group(2).lower().startswith("day") else n


# ---------------------------------------------------------------- the decision

def decide(tenant: Tenant, thread: Thread, inbound: Inbound,
           calendar: Calendar | None = None,
           precedent: list[Receipt] | None = None) -> Decision:
    """Work out what to do about one message. Pure: reads state, writes nothing.

    `precedent` is this tenant's own receipt history, already RLS-scoped — fetched by `step()`
    (which owns the cursor) and passed in as data, the same seam pattern as `calendar`, so this
    function stays a pure read of whatever it is given rather than reaching for a connection
    itself. It feeds Feature 2's precedent signal (`confidence.score`) only.
    """
    calendar = calendar or NoCalendar()
    precedent = precedent or []
    profile = tenant.profile or {}
    words = lexicon.words(profile)
    text = inbound.body or ""
    offer = dict(thread.current_offer or {})

    def escalate(reason: str, *, action: str, rule: str,
                 detail: dict[str, Any] | None = None,
                 product_gap: str | None = None) -> Decision:
        return Decision(
            state_after="ESCALATED", action=action, rule_checked=rule, within_rules=False,
            detail={"reason": reason, **(detail or {})},
            reply_body=PROSE["escalated"],
            owner_alert=f"[{tenant.business_name}] {reason}\n\n"
                        f"From: {inbound.from_address}\nTheir message: {text.strip()}",
            product_gap=product_gap,
        )

    # A thread the owner has taken over is not re-entered. Replying over the top of a human
    # colleague is worse than silence. AWAITING_OWNER_APPROVAL (Feature 2) is the same
    # principle: a drafted reply is already waiting on the owner, so a further message from the
    # prospect must not trigger a second autonomous decision on top of it.
    if thread.state in ("ESCALATED", "BOOKED", "IGNORED", "DEAD", "AWAITING_OWNER_APPROVAL"):
        hold_reply = (
            PROSE["closed"] if thread.state == "ESCALATED"
            else PROSE["awaiting_approval_hold"] if thread.state == "AWAITING_OWNER_APPROVAL"
            else None
        )
        return Decision(
            state_after=thread.state, action="hold", rule_checked="thread.state is terminal",
            within_rules=True,
            detail={"state": thread.state, "note": "no autonomous reply into a closed thread"},
            reply_body=hold_reply,
            owner_alert=f"[{tenant.business_name}] Further message on a {thread.state} thread "
                        f"from {inbound.from_address}: {text.strip()}",
        )

    if wants_human(text):
        return escalate(
            "The prospect asked for a human. SB 243 requires that route always work, so this "
            "hands over immediately and unconditionally.",
            action="human_requested", rule="SB 243 — route to a human on request")

    if is_spam(text):
        return Decision(
            state_after="IGNORED", action="ignored_spam",
            rule_checked="bulk-marketing markers", within_rules=True,
            detail={"markers": [m for m in _SPAM_MARKERS if m in _norm(text)]},
            reply_body=None,   # never reply to spam — it confirms the address is live
            owner_alert=None,
        )

    hits = pricing.matching_escalations(profile, text)
    if hits:
        return escalate(
            "This trips a rule the owner said must always come to them: " + "; ".join(hits),
            action="escalation_trigger",
            rule="profile.escalation_triggers + template hard escalations",
            detail={"triggers": hits})

    # ---- mid-booking sub-states, driven by what we last asked for
    awaiting = offer.get("awaiting")

    if awaiting == "timezone":
        tz = parse_timezone(text)
        if not tz:
            attempts = int(offer.get("timezone_attempts", 1))
            if attempts >= 2:
                return escalate(
                    "Asked twice for a timezone and could not read one. Booking blind would risk "
                    "an appointment in the wrong hour, so this goes to the owner.",
                    action="timezone_unreadable", rule="§9b.1 — capture timezone, never infer",
                    detail={"attempts": attempts})
            offer["timezone_attempts"] = attempts + 1
            return Decision(
                state_after="AWAITING_REPLY", action="timezone_reask",
                rule_checked="§9b.1 — capture timezone, never infer", within_rules=True,
                detail={"attempt": attempts + 1, "unparsed": text.strip()},
                reply_body=PROSE["ask_timezone_again"], offer=offer)
        offer["awaiting"] = "slot_choice"
        return _offer_slots(tenant, thread, offer, tz, words, calendar, escalate)

    if awaiting == "slot_choice":
        idx = picked_slot(text)
        if idx is None:
            tz = thread.client_timezone or offer.get("timezone")
            return _offer_slots(tenant, thread, offer, tz, words, calendar, escalate,
                                prose_key="offer_slots")
        return _book(tenant, thread, inbound, offer, idx, words, calendar, escalate)

    # ---- price movement, in any open state
    quoted = offer.get("quote")
    asked = named_amount(text, allow_bare=bool(quoted))
    if thread.state in ("AWAITING_REPLY", "NEGOTIATING") and (asked is not None or wants_discount(text)):
        if not quoted:
            return escalate(
                "The prospect is negotiating but there is no quote on this thread to negotiate "
                "against.", action="negotiate_without_quote", rule="thread.current_offer.quote")
        quote = _quote_from_offer(quoted)
        if asked is None:
            return escalate(
                "The prospect asked for a discount without naming a figure. There is no number "
                "to check against the floor, and I will not propose one — that would be me "
                "choosing the discount.",
                action="discount_without_figure",
                rule=f"pricing_rules.{pricing.RULE_FLOOR}")

        # Feature 5 (decaying floor): how far into this thread's own negotiation we are, so a
        # tenant's optional `pricing_rules.floor_curve` can be evaluated at the right point.
        # Read BEFORE this round is decided, so a floor breach (which ends the thread) never
        # advances it. A tenant with no curve set never has this read at all — `bounds_for`
        # only looks at it when `pricing.floor_curve(profile)` returns something.
        round_index = int(offer.get("negotiation_round", 0))
        days_elapsed = 0
        if thread.created_at:
            days_elapsed = max(0, (inbound.received_at - thread.created_at).days)

        ruling = guardrails.negotiate(
            profile, quote, asked, round_index=round_index, days_elapsed=days_elapsed)
        detail = {
            "asked": asked, "quoted": quote.amount, "unit": quote.unit,
            "floor": ruling.floor, "binding_rule": ruling.binding_rule,
            "bounds": [str(b) for b in ruling.bounds], "explanation": ruling.explanation,
            "decay_round_index": round_index, "decay_days_elapsed": days_elapsed,
        }
        if not ruling.allowed:
            return Decision(
                state_after="ESCALATED", action="floor_breach",
                rule_checked=f"pricing_rules.{ruling.binding_rule or pricing.RULE_FLOOR}",
                within_rules=False, detail=detail,
                reply_body=PROSE["escalated"],
                owner_alert=f"[{tenant.business_name}] Below your floor — not answered.\n\n"
                            f"{ruling.explanation}\n\nFrom: {inbound.from_address}\n"
                            f"Their message: {text.strip()}",
                derivation=tuple(str(b) for b in ruling.bounds))

        agreed = ruling.agreed
        offer["agreed"] = agreed
        # This round was allowed, so the next one (if the prospect keeps pushing) moves one
        # step further down the curve. Never touched on a breach — that thread is over.
        offer["negotiation_round"] = round_index + 1
        holding = agreed is not None and quote.amount is not None and agreed >= quote.amount
        conf = confidence.score(
            profile=profile, service=quote.service_name, amount=quote.amount,
            agreed=agreed, floor=ruling.floor, receipts=precedent,
        ).as_dict()
        return Decision(
            state_after="AWAITING_REPLY", action="counter_within_rules",
            rule_checked=f"pricing_rules.{ruling.binding_rule}", within_rules=True,
            detail=detail,
            reply_body=(PROSE["hold_price"] if holding else PROSE["counter_agreed"]),
            derivation=tuple(str(b) for b in ruling.bounds),
            quote=quote, offer=offer,
            owner_alert=None, confidence=conf)

    if thread.state in ("AWAITING_REPLY", "NEGOTIATING") and is_acceptance(text):
        if not offer.get("quote"):
            return escalate(
                "The prospect agreed, but there is no quote on this thread for them to have "
                "agreed to.", action="acceptance_without_quote", rule="thread.current_offer")
        offer["awaiting"] = "timezone"
        offer["timezone_attempts"] = 1
        return Decision(
            state_after="AWAITING_REPLY", action="agreed_awaiting_timezone",
            rule_checked="§9b.1 — capture timezone, never infer", within_rules=True,
            detail={"agreed": offer.get("agreed", offer["quote"].get("amount"))},
            reply_body=PROSE["ask_timezone"], offer=offer)

    # ---- otherwise: treat it as a question about what this business sells
    try:
        quote = pricing.quote_for(profile, text)
    except Unquotable as e:
        return escalate(
            e.reason, action="unquotable", rule="profile.services + pricing_rules",
            detail={"considered": e.considered}, product_gap=text.strip())

    offer = {
        "quote": _offer_from_quote(quote),
        "awaiting": "reply",
    }
    conf = None
    if quote.unit != "prose" and quote.amount is not None:
        # A fresh quote, nothing negotiated yet — `agreed` is just the quoted amount, and the
        # binding floor (if any) comes straight from the stored bounds, not from a `Ruling`,
        # because `guardrails.negotiate` is not called until the prospect actually counters.
        floor_bound = guardrails.binding_bound(guardrails.bounds_for(profile, quote))
        conf = confidence.score(
            profile=profile, service=quote.service_name, amount=quote.amount,
            agreed=quote.amount, floor=floor_bound.limit if floor_bound else None,
            receipts=precedent,
        ).as_dict()
    return Decision(
        state_after="AWAITING_REPLY", action="quoted",
        rule_checked="profile.services + pricing_rules", within_rules=True,
        detail={
            "service": quote.service_name, "unit": quote.unit, "amount": quote.amount,
            "rendered": quote.render(),
            "derivation": [str(d) for d in quote.derivation],
        },
        reply_body=PROSE["quote"], derivation=tuple(str(d) for d in quote.derivation),
        quote=quote, offer=offer, confidence=conf)


# ---------------------------------------------------------------- booking helpers

def _offer_slots(tenant, thread, offer, tz, words, calendar, escalate, prose_key="offer_slots"):
    if not tz:
        offer["awaiting"] = "timezone"
        return Decision(
            state_after="AWAITING_REPLY", action="timezone_missing",
            rule_checked="§9b.1 — capture timezone, never infer", within_rules=True,
            detail={}, reply_body=PROSE["ask_timezone"], offer=offer)

    lead = notice_hours(tenant.profile or {}) or 0
    earliest = datetime.now(dt_timezone.utc) + timedelta(hours=lead)
    try:
        starts = calendar.slots(tenant=tenant, earliest=earliest, limit=3)
    except CalendarUnavailable as e:
        return escalate(str(e), action="calendar_unavailable",
                        rule="OPERATOR_PROVIDES item 4 — Cal.com")
    if not starts:
        return escalate(
            "The calendar returned no times inside this tenant's booking rules, so there is "
            "nothing to offer.", action="no_slots", rule="calendar_ref.booking_rules")

    slots = [{"start_utc": s.astimezone(dt_timezone.utc).isoformat(),
              "label": _render_slot(s, tz)} for s in starts[:3]]
    offer.update({"awaiting": "slot_choice", "timezone": tz})
    return Decision(
        state_after="AWAITING_REPLY", action="slots_offered",
        rule_checked=f"calendar_ref.booking_rules (notice {lead}h)", within_rules=True,
        detail={"timezone": tz, "notice_hours": lead, "slots": [s["label"] for s in slots]},
        reply_body=PROSE[prose_key], offer=offer,
        client_timezone=tz, offered_slots=slots)


def _book(tenant, thread, inbound, offer, idx, words, calendar, escalate):
    """§9b.3-5: re-fetch before booking, send UTC, confirm the status the API actually returned."""
    tz = thread.client_timezone or offer.get("timezone")
    previous = list(thread.offered_slots or [])
    if idx >= len(previous):
        return _offer_slots(tenant, thread, offer, tz, words, calendar, escalate)

    chosen = previous[idx]
    lead = notice_hours(tenant.profile or {}) or 0
    earliest = datetime.now(dt_timezone.utc) + timedelta(hours=lead)
    try:
        fresh = calendar.slots(tenant=tenant, earliest=earliest, limit=10)
    except CalendarUnavailable as e:
        return escalate(str(e), action="calendar_unavailable",
                        rule="OPERATOR_PROVIDES item 4 — Cal.com")

    still_open = {s.astimezone(dt_timezone.utc).isoformat() for s in fresh}
    if chosen["start_utc"] not in still_open:
        # The slot race (§9b.3). The prospect never sees an error for this.
        return _offer_slots(tenant, thread, offer, tz, words, calendar, escalate,
                            prose_key="slot_taken")

    start = datetime.fromisoformat(chosen["start_utc"])
    try:
        result = calendar.book(
            tenant=tenant, thread=thread, start_utc=start,
            attendee_name=inbound.from_name or thread.client_name or "Guest",
            attendee_email=inbound.from_address, attendee_timezone=tz,
            notes=_booking_notes(tenant, offer))
    except CalendarUnavailable as e:
        return escalate(str(e), action="calendar_unavailable",
                        rule="OPERATOR_PROVIDES item 4 — Cal.com")

    status = str(result.get("status", "")).lower()
    if status not in ACCEPTED_BOOKING_STATUSES:
        return escalate(
            f"The calendar did not confirm the booking — it returned status {status!r}. I do not "
            f"tell a prospect something is booked on anything less than the API saying so.",
            action="booking_unconfirmed", rule="§9b.5 — confirm status via API",
            detail={"status": status, "response": result})

    offer.update({"awaiting": None, "booked": True,
                  "booking_ref": result.get("id"), "start_utc": chosen["start_utc"]})
    return Decision(
        state_after="BOOKED", action="booked",
        rule_checked="§9b.5 — booking status confirmed via API response", within_rules=True,
        detail={"booking_ref": result.get("id"), "status": status,
                "start_utc": chosen["start_utc"], "timezone": tz,
                "agreed": offer.get("agreed"), "service": (offer.get("quote") or {}).get("service"),
                # The quoted amount, kept even when nothing was negotiated (`agreed` is then
                # None) — Feature 2's precedent signal needs a figure to match against either
                # way (`concierge.confidence._precedent`).
                "amount": (offer.get("quote") or {}).get("amount")},
        reply_body=PROSE["booked"], offer=offer)


def _booking_notes(tenant: Tenant, offer: dict[str, Any]) -> str:
    q = offer.get("quote") or {}
    agreed = offer.get("agreed")
    return (f"Arranged by CONCIERGE for {tenant.business_name}. "
            f"{q.get('service', '')} at {agreed if agreed is not None else q.get('rendered', '')}.")


def _render_slot(start: datetime, tz: str) -> str:
    local = start.astimezone(ZoneInfo(tz))
    return local.strftime("%a %-d %b, %H:%M")


def _offer_from_quote(quote: Quote) -> dict[str, Any]:
    return {
        "service": quote.service_name, "unit": quote.unit, "amount": quote.amount,
        "currency": quote.currency, "stated_terms": quote.stated_terms,
        "basis": quote.basis, "rendered": quote.render(), "negotiable": quote.negotiable,
        "duration_min": quote.duration_min,
        "derivation": [str(d) for d in quote.derivation],
    }


def _quote_from_offer(stored: dict[str, Any]) -> Quote:
    return Quote(
        service_name=stored.get("service", ""), unit=stored.get("unit", "cash"),
        amount=stored.get("amount"), currency=stored.get("currency"),
        stated_terms=stored.get("stated_terms"), basis=stored.get("basis") or "",
        negotiable=bool(stored.get("negotiable")), duration_min=stored.get("duration_min"),
        derivation=(),
    )


# ---------------------------------------------------------------- rendering & persistence

def render(tenant: Tenant, decision: Decision, thread: Thread, *,
           receipt_id: uuid.UUID | None = None) -> str | None:
    """Fill the PROSE template with the tenant's own nouns. Disclosure always leads (SB 243).

    `receipt_id` is optional and only ever affects one thing: whether `{verify_line}` (Feature 3)
    is non-empty. It is pre-generated by `step()` before this row exists in the database, purely
    so the verify link can be embedded in the very email whose receipt it will become — see
    `store.insert_receipt`'s `receipt_id` parameter for the other half of that.
    """
    if decision.reply_body is None:
        return None

    profile = tenant.profile or {}
    words = lexicon.words(profile)
    offer = decision.offer or thread.current_offer or {}
    q = offer.get("quote") or {}
    quote = decision.quote

    duration = q.get("duration_min") or (quote.duration_min if quote else None)
    agreed = offer.get("agreed")
    currency_symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(q.get("currency") or "", "")

    slots = "\n".join(
        f"  {i + 1}. {s['label']}"
        for i, s in enumerate(decision.offered_slots or thread.offered_slots or []))

    when = ""
    if offer.get("start_utc") and (decision.client_timezone or thread.client_timezone):
        when = _render_slot(datetime.fromisoformat(offer["start_utc"]),
                            decision.client_timezone or thread.client_timezone)

    terms_line = ""
    if agreed is not None and q.get("service"):
        terms_line = f"{q['service']} at {currency_symbol}{pricing.fmt_amount(agreed)}.\n\n"
    elif q.get("rendered"):
        terms_line = f"{q.get('service', '')} at {q['rendered']}.\n\n"

    verify_line = ""
    if receipt_id is not None and decision.action in receipts.PUBLIC_ACTIONS:
        base = config.public_base_url()
        if base:
            verify_line = PROSE["verify_line"].format(verify_url=f"{base}/r/{receipt_id}")

    body = decision.reply_body.format(
        business=tenant.business_name,
        owner_email=tenant.owner_email,
        service=q.get("service") or (quote.service_name if quote else ""),
        price=q.get("rendered") or (quote.render() if quote else ""),
        agreed=f"{currency_symbol}{pricing.fmt_amount(agreed)}" if agreed is not None else "",
        engagement=words.engagement,
        client=words.client,
        duration=f" ({duration} minutes)" if duration else "",
        tz=decision.client_timezone or thread.client_timezone or "",
        slots=slots,
        when=when,
        terms_line=terms_line,
        verify_line=verify_line,
    )
    disclosure = PROSE["disclosure"].format(
        business=tenant.business_name, owner_email=tenant.owner_email)
    return f"{disclosure}\n\n{body}"


def step(cur: Cursor, tenant: Tenant, thread: Thread, inbound: Inbound,
         calendar: Calendar | None = None) -> Outcome:
    """Decide, reply, persist, and write the receipt. The cursor is already RLS-scoped."""
    before = thread.state
    precedent = store.list_receipts(cur)   # this tenant's own history, RLS-scoped — Feature 2
    decision = decide(tenant, thread, inbound, calendar, precedent=precedent)
    # Pre-generated so `render` can embed a verify link (Feature 3) pointing at the id this
    # exact decision's receipt will have — the row itself is written further down with this
    # same id, never a different one.
    receipt_id = uuid.uuid4()
    reply = render(tenant, decision, thread, receipt_id=receipt_id)

    # Feature 2 (GATE 3b-2): a pricing/negotiation decision that scored below this service's
    # autonomy threshold is drafted but not sent. The decision itself (state, offer, rule
    # checked) is unchanged — only whether the prospect sees it changes. Queuing happens here,
    # not in `decide()`, because only `step()` knows what the rendered reply actually says.
    queued = decision.confidence is not None and not decision.confidence["autonomous"]
    state_after = "AWAITING_OWNER_APPROVAL" if queued else decision.state_after
    owner_alert = decision.owner_alert
    outbound_reply = reply

    if queued:
        drafted_reply = reply
        outbound_reply = None
        offer = dict(decision.offer if decision.offer is not None else (thread.current_offer or {}))
        offer["pending_approval"] = {
            "decision_action": decision.action,
            "drafted_reply": drafted_reply,
            "confidence": decision.confidence,
        }
        decision.offer = offer
        conf = decision.confidence
        owner_alert = (
            f"[{tenant.business_name}] Confidence {conf['score']:.2f} is below the "
            f"{conf['threshold']:.2f} autonomy threshold set for {conf['service_key']!r}, so "
            f"this reply is drafted but held for you rather than sent automatically.\n\n"
            f"Drafted reply:\n{drafted_reply}\n\n"
            f"From: {inbound.from_address}\nTheir message: {inbound.body.strip()}"
        )

    thread.state = state_after
    if decision.offer is not None:
        thread.current_offer = decision.offer
    if decision.client_timezone:
        thread.client_timezone = decision.client_timezone
    if decision.offered_slots is not None:
        thread.offered_slots = decision.offered_slots
    if inbound.from_name and not thread.client_name:
        thread.client_name = inbound.from_name

    now = datetime.now(dt_timezone.utc).isoformat()
    thread.history = list(thread.history or []) + [
        {"at": now, "direction": "in", "from": inbound.from_address, "text": inbound.body},
        {"at": now, "direction": "out", "state": state_after,
         "action": decision.action, "text": outbound_reply,
         **({"queued_for_approval": True} if queued else {})},
    ]
    thread = store.save_thread(cur, thread) or thread

    words = lexicon.words(tenant.profile or {})
    receipt = receipts.record(
        cur, tenant_id=tenant.tenant_id, thread_id=thread.thread_id,
        action=decision.action, rule_checked=decision.rule_checked,
        within_rules=decision.within_rules,
        decision={
            "state_before": before, "state_after": state_after,
            "action": decision.action, "rule_checked": decision.rule_checked,
            "within_rules": decision.within_rules,
            "inbound_from": inbound.from_address, "inbound_body": inbound.body,
            "reply_sent": outbound_reply,
            "queued_for_approval": queued,
            "detail": decision.detail,
            "lexicon_fallbacks": list(words.gaps),
        },
        confidence=decision.confidence,
        receipt_id=receipt_id,
    )

    # Feature 1 (Product-Gap Intelligence): exactly one side effect on the existing
    # Unquotable/ESCALATE transition — no new decision logic. store.py, not gaps.py: the LLM
    # categorization step runs later, on a schedule (concierge.gaps.classify_pending), never
    # from inside this no-network decision path.
    if decision.product_gap:
        store.insert_gap_event(
            cur, tenant_id=tenant.tenant_id, thread_id=thread.thread_id,
            raw_query_text=decision.product_gap,
        )

    return Outcome(
        thread=thread, state_before=before, state_after=state_after,
        reply=outbound_reply, owner_alert=owner_alert, receipt=receipt,
        action=decision.action, rule_checked=decision.rule_checked,
        within_rules=decision.within_rules, derivation=decision.derivation,
        quote=decision.quote, confidence=decision.confidence)


def open_thread(cur: Cursor, tenant: Tenant, inbound: Inbound) -> Thread:
    """Find this conversation or start it. Threading is per-tenant (§2b)."""
    if inbound.external_ref:
        existing = store.find_thread_by_external_ref(cur, inbound.external_ref)
        if existing:
            return existing
    return store.create_thread(
        cur, tenant_id=tenant.tenant_id, client_contact=inbound.from_address,
        client_name=inbound.from_name, external_ref=inbound.external_ref, state="NEW")
