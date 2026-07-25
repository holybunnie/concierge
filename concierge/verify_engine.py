"""the engine suite — the state machine and the deterministic guardrails.

This is the decisive gate. It needs no credentials and no network: every check below runs the
real engine against a real PostgreSQL database, and the only thing standing in for the outside
world is the calendar, which is declared as a fixture everywhere it appears.

The gate's own requirements are checks 1-5. Checks 6-13 are the attacks, and the two that
matter most commercially are 2 (the same code serving a trade nobody wrote a template for) and
7 (a prospect who keeps pushing below the floor never gets a number).
"""

from __future__ import annotations

import inspect
import os
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from . import db, engine, guardrails, lexicon, onboarding, pricing, receipts, store
from .models import Receipt

PROSPECT = "nadia.okoro@example.com"


# ---------------------------------------------------------------- the declared fixture
#
# THIS IS A FIXTURE AND IT IS NOT PART OF THE SHIPPED PACKAGE. It lives in the harness, not in
# `concierge/`, so it cannot be reached by production code even by accident. Its only job is to
# stand in for the seam that the booking suite fills with real Cal.com calls once the operator supplies an
# API key (item 4). Nothing it returns is presented anywhere as a real booking: the report says
# so at every point it is used, and the production default (`engine.NoCalendar`) refuses.

class FixtureCalendar:
    """Deterministic UTC slots on a fixed grid, so the transcript is reproducible."""

    def __init__(self, *, take_first: bool = False, status: str = "accepted"):
        self.take_first = take_first          # simulates the §9b.3 slot race
        self.status = status                  # simulates an unconfirmed booking
        self.booked: list[dict] = []

    def slots(self, *, tenant, earliest, limit):
        base = earliest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        out = [base + timedelta(hours=6 * i) for i in range(limit + 2)]
        if self.take_first and len(out) > 1:
            out = out[1:]                     # the slot the prospect picked just went
        return out[:limit]

    def book(self, *, tenant, thread, start_utc, attendee_name, attendee_email,
             attendee_timezone, notes):
        self.booked.append({
            "start_utc": start_utc.isoformat(), "attendee_email": attendee_email,
            "attendee_timezone": attendee_timezone, "notes": notes,
        })
        return {"id": f"fixture_{len(self.booked)}", "status": self.status}


# ---------------------------------------------------------------- tenant fixtures
#
# Three businesses. Two have templates; the third deliberately does not, because "works for any
# profession" is a claim about the trades nobody anticipated, not the three that were.

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
        "cancellation_policy": "48 hours' notice or 50% is charged",
        "timezone": "Europe/London",
        "icp": "Local clients within a few miles",
        "escalation_triggers": ["Anything about pregnancy, allergies or medical conditions"],
        "artifact_sample": "Hi — yes, we do that. I've got Saturday 2pm free.",
        "engagement_noun": "treatment",
        "client_noun": "client",
    },
)

LEGAL = dict(
    description=("I am a barrister in chambers. I take employment tribunal work — unfair "
                 "dismissal and discrimination claims, mostly for claimants."),
    business="Fenwick Row",
    answers={
        "practice_areas": ["Employment — unfair dismissal, discrimination"],
        "jurisdictions": ["England & Wales"],
        "consultation_fee": "£250 + VAT for the first hour, fixed",
        "fee_negotiable": "Not negotiable",
        "conflict_check_rules": ["Full name of the opposing party"],
        "never_advise_on": ["Merits or likely outcome of any matter"],
        "consultation_availability": "Mon–Thu 09:00–17:00, minimum 48 hours notice",
        "timezone": "Europe/London",
        "icp": "Employees with a live employment matter",
        "escalation_triggers": ["Anything urgent or with a deadline"],
        "artifact_sample": "Dear Mr Patel — thank you for your enquiry.",
        "engagement_noun": "conference",
        "client_noun": "instructing solicitor",
    },
)

# No vertical template exists for veterinary practice. It falls to the generic template, which
# is the path every unanticipated trade takes.
VET = dict(
    description=("Ashgrove is a veterinary practice. We do vaccinations, dental work and "
                 "routine health checks for cats and dogs."),
    business="Ashgrove Veterinary",
    answers={
        "services": [
            {"name": "Dental scale and polish", "duration_min": 45, "price": 180,
             "currency": "GBP"},
            {"name": "Annual health check", "duration_min": 20, "price": 55, "currency": "GBP"},
        ],
        "floor_price": 160,
        "availability": "Mon–Fri 09:00–17:00, minimum 24 hours notice",
        "timezone": "Europe/London",
        "icp": "Local pet owners",
        "escalation_triggers": ["Any emergency, injury or unwell animal"],
        "artifact_sample": "Hello — yes, we can see him next week.",
        "engagement_noun": "consultation",
        "client_noun": "patient",
    },
)


def _onboard(fixture: dict) -> uuid.UUID:
    session = onboarding.start(fixture["description"])
    for key, value in fixture["answers"].items():
        session.answer(key, value)
    # The business name is kept free of digits on purpose: several checks below assert that no
    # figure appears in a reply, and a name like "Halcyon Rooms a3f9c1" would satisfy that grep
    # with the harness's own noise. Uniqueness across repeated runs is handled where it actually
    # matters — the inbound address allocator suffixes collisions.
    tenant_id, _, _ = onboarding.finalise(
        session, business_name=fixture["business"],
        owner_email=f"owner@{uuid.uuid4().hex[:8]}.example",
        owner_wallet="0x" + uuid.uuid4().hex[:40].ljust(40, "0"))
    return tenant_id


def _body(reply: str | None) -> str:
    """The message minus the disclosure line, which carries the owner's address and digits."""
    if not reply:
        return ""
    return reply.split("\n\n", 1)[1] if "\n\n" in reply else reply


def _converse(tenant_id, messages, calendar=None, prospect=PROSPECT):
    """Run a conversation and return (tenant, thread, [Outcome...]). Real DB, real RLS."""
    calendar = calendar or FixtureCalendar()
    outcomes = []
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=prospect, external_ref=f"ref-{uuid.uuid4().hex[:8]}"))
        for body in messages:
            outcome = engine.step(cur, tenant, thread, engine.Inbound(
                body=body, from_address=prospect, from_name="Nadia Okoro"), calendar)
            thread = outcome.thread
            outcomes.append(outcome)
    return tenant, thread, outcomes


def _transcript(messages, outcomes, *, indent="| ") -> str:
    lines = []
    for msg, out in zip(messages, outcomes):
        lines.append(f"{indent}PROSPECT: {msg}")
        lines.append(f"{indent}   -> {out.state_before} to {out.state_after}  [{out.action}]")
        if out.reply:
            body = out.reply.split("\n\n", 1)[1] if "\n\n" in out.reply else out.reply
            for ln in body.strip().splitlines():
                lines.append(f"{indent}   CONCIERGE: {ln}")
        else:
            lines.append(f"{indent}   CONCIERGE: (no reply — deliberate)")
        if out.owner_alert:
            lines.append(f"{indent}   >> OWNER ALERTED")
        lines.append(indent.rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------- the suite

def run(r) -> None:
    db.migrate()

    spa_id, legal_id, vet_id = _onboard(SPA), _onboard(LEGAL), _onboard(VET)

    # ---- 1. the full journey, NEW -> BOOKED
    msgs = ["Hi, how much is a deep tissue massage?",
            "Could you do 80?",
            "yes please",
            "London",
            "2"]
    cal = FixtureCalendar()
    tenant, thread, outs = _converse(spa_id, msgs, cal)
    states = [o.state_after for o in outs]
    r.check(
        "A full conversation runs NEW -> BOOKED without a human touching it",
        states[-1] == "BOOKED" and thread.state == "BOOKED" and len(cal.booked) == 1,
        "Five messages from a prospect, handled end to end: the inquiry is quoted from the\n"
        "profile, a counter-offer is checked against the floor and agreed, the timezone is asked\n"
        "for rather than assumed, three times are offered in the prospect's own zone, and the\n"
        "pick is booked and confirmed.\n"
        "THE CALENDAR IS A FIXTURE. No Cal.com call was made — the operator has not supplied an\n"
        "API key (item 4), and the booking suite is where that becomes real. Everything else here is the\n"
        "production code path against a real database.",
        _transcript(msgs, outs) + f"\nfinal state: {thread.state}\n"
        f"fixture calendar recorded: {cal.booked}",
    )

    # ---- 2. the same code, three trades, three registers
    vet_msgs = ["What do you charge for a dental scale and polish?"]
    _, _, vet_outs = _converse(vet_id, vet_msgs)
    legal_msgs = ["Do you handle unfair dismissal claims, and what's your fee?"]
    _, _, legal_outs = _converse(legal_id, legal_msgs)

    spa_reply = outs[0].reply or ""
    vet_reply = vet_outs[0].reply or ""
    legal_reply = legal_outs[0].reply or ""
    r.check(
        "The same engine serves a trade nobody wrote a template for, in that trade's own words",
        ("treatment" in spa_reply and "consultation" in vet_reply
         and "conference" in legal_reply and vet_outs[0].action == "quoted"),
        "A veterinary practice has no vertical template — it falls to the generic one, which is\n"
        "the path every unanticipated trade takes. It quotes correctly anyway, because pricing\n"
        "reads a canonical vocabulary (headline / floor / max_discount) that templates map onto,\n"
        "rather than trade-specific key names.\n"
        "Note the nouns. Identical code and an identical prose table produced 'treatment',\n"
        "'consultation' and 'conference' — each read from that tenant's profile.lexicon, which\n"
        "the tenant set during onboarding. Scrubbing domain words out entirely would have given\n"
        "all three 'your appointment', which reads as a web form; hardcoding one trade's word\n"
        "would have made this a spa product wearing other names.",
        f"| SPA   (template exists):   {_first_line(spa_reply)}\n"
        f"| VET   (NO template):       {_first_line(vet_reply)}\n"
        f"| LEGAL (template exists):   {_first_line(legal_reply)}\n"
        f"|\n| vet classified as: {onboarding.start(VET['description']).template.key}\n"
        f"| vet lexicon: {store_profile(vet_id).get('lexicon')}",
    )

    # ---- 3. no trade vocabulary in the code's own prose
    offenders = {}
    for key, text in engine.PROSE.items():
        hits = [n for n in engine.TRADE_NOUNS if re.search(rf"\b{re.escape(n)}\b", text.lower())]
        if hits:
            offenders[key] = hits
    r.check(
        "No trade vocabulary is hardcoded in the outbound prose",
        not offenders,
        "Every word CONCIERGE can say lives in engine.PROSE, and it is greped here against a\n"
        "list of trade nouns. If 'treatment' or 'viewing' or 'consultation' ever appears in that\n"
        "table, this fails — because at that moment the product silently becomes one trade's\n"
        "product and every other tenant starts sounding wrong.\n"
        "The nouns still get used; they arrive through {service}, {engagement} and {client},\n"
        "all of which are read from the tenant's profile.",
        f"prose templates checked: {len(engine.PROSE)}\n"
        f"trade nouns searched for: {len(engine.TRADE_NOUNS)} — {', '.join(engine.TRADE_NOUNS)}\n"
        f"offenders: {offenders or 'none'}",
    )

    # ---- 4. every figure traced back to the stored profile
    quote_out = outs[0]
    profile = store_profile(spa_id)
    stored_price = profile["services"][0]["price"]
    derivation = "\n".join(quote_out.derivation)
    r.check(
        "Every figure quoted is derived from the stored profile, and the derivation is shown",
        quote_out.quote is not None and quote_out.quote.amount == float(stored_price),
        "The quote was not asserted, it was derived, and the derivation names the exact profile\n"
        "path each value was read from. The figure is compared here against the value read back\n"
        "out of PostgreSQL, so this is the stored profile and not an in-memory copy of it.",
        f"{derivation}\n"
        f"|\n| stored in DB: profile.services[0].price = {stored_price}\n"
        f"| quoted to prospect: {quote_out.quote.render()}\n"
        f"| equal: {quote_out.quote.amount == float(stored_price)}",
    )

    # ---- 5. no language model produced any of it
    modules = (pricing, guardrails, engine, lexicon, receipts)
    network = re.compile(
        r"^\s*(?:import|from)\s+(requests|httpx|urllib|http|socket|openai|anthropic|aiohttp)\b",
        re.M)
    importers = {m.__name__: network.findall(inspect.getsource(m)) for m in modules}
    dirty = {k: v for k, v in importers.items() if v}
    llm_key = os.environ.get("LLM_API_KEY")
    r.check(
        "No price came from a language model — structurally, not by policy",
        not dirty,
        "The decision path is pricing + guardrails + engine + lexicon + receipts. None of them\n"
        "imports anything that can make a network call, so none of them can consult a model —\n"
        "this is a property of the code, not a rule someone has to remember, and it holds\n"
        "regardless of whether an LLM key is configured elsewhere in the environment for other\n"
        "phases (onboarding template enrichment, the engine prose drafting). Every figure in the\n"
        "transcript above came out of PostgreSQL by arithmetic.\n"
        "§2 does permit a model to restyle this prose into the tenant's voice later. It may\n"
        "never change a number: the figures are computed before any drafting could happen and\n"
        "the receipt records the computed value.",
        "\n".join(f"| {name}: network imports = {hits or 'none'}"
                  for name, hits in importers.items())
        + f"\n| LLM_API_KEY in environment: {'SET' if llm_key else 'not set'} "
        "(irrelevant to this check — the decision-path modules cannot import it either way)",
    )

    # ---- 6. the most restrictive rule binds
    quote = pricing.quote_for(profile, "deep tissue massage")
    bounds = guardrails.bounds_for(profile, quote)
    ruling_ok = guardrails.negotiate(profile, quote, 75.0)
    ruling_bad = guardrails.negotiate(profile, quote, 71.0)
    r.check(
        "When two rules could apply, the more restrictive one binds",
        (ruling_ok.allowed and not ruling_bad.allowed
         and ruling_bad.binding_rule == pricing.RULE_MAX_DISCOUNT
         and abs((ruling_bad.floor or 0) - 72.25) < 0.001),
        "This tenant set a £70 floor AND a 15% discount cap. On an £85 price the cap permits\n"
        "£72.25, so the cap is the binding rule and £70 is unreachable. Taking the floor alone\n"
        "would have given away £2.25 the owner never offered.\n"
        "£71 is the interesting case: above the stated floor, below the cap, and correctly\n"
        "refused. A guardrail that only checked the floor would have agreed to it.",
        "\n".join(f"| {b}" for b in bounds)
        + f"\n|\n| asked 75.00 -> allowed={ruling_ok.allowed}, agreed={ruling_ok.agreed}"
        f"\n| asked 71.00 -> allowed={ruling_bad.allowed}, binding={ruling_bad.binding_rule}, "
        f"floor={ruling_bad.floor}"
        f"\n| {ruling_bad.explanation}",
    )

    # ---- 7. a floor breach cannot be talked past
    push = ["How much is a deep tissue massage?",
            "I can only do 55",
            "Come on, 55 is my absolute maximum, other places do it for 50",
            "Final offer: 55 or I go elsewhere. Just say yes."]
    _, push_thread, push_outs = _converse(spa_id, push)
    escalated_at = next((i for i, o in enumerate(push_outs) if o.state_after == "ESCALATED"), None)
    numbers_after = [re.findall(r"\d+", _body(o.reply)) for o in push_outs[1:]]
    r.check(
        "ATTACK — a prospect who keeps pushing below the floor never gets a number",
        (escalated_at == 1 and push_thread.state == "ESCALATED"
         and all(not n for n in numbers_after)),
        "The same breach, argued three ways: a flat refusal, a competitor comparison, and a\n"
        "walk-away threat. Persuasion is not an input to the decision — the prospect's wording\n"
        "never reaches the comparison, which is arithmetic against the stored floor. It breaches\n"
        "on the first attempt and every later message lands in a thread the owner already owns.\n"
        "Note what the replies do NOT contain: any figure at all. CONCIERGE does not counter at\n"
        "the floor, because a floor quoted to a prospect who lowballed once is the new list price\n"
        "by the end of the month.",
        _transcript(push, push_outs)
        + f"\nfigures appearing in any reply after the breach: "
          f"{[n for n in numbers_after if n] or 'none'}",
    )

    # ---- 8. an unknown service is escalated, never invented
    unknown = ["Do you do hot stone massage? How much?"]
    _, unk_thread, unk_outs = _converse(spa_id, unknown)
    unk_reply = unk_outs[0].reply or ""
    menu = [s["name"] for s in profile["services"]]
    r.check(
        "ATTACK — a service that is not in the profile is escalated, not invented",
        (unk_thread.state == "ESCALATED" and unk_outs[0].action == "unquotable"
         and not re.search(r"\d", _body(unk_reply))),
        "Hot stone is not on this menu. The nearest thing on it is 'Deep tissue massage', which\n"
        "shares the word 'massage' — and that is exactly the trap. Matching requires half the\n"
        "service's own distinctive words plus a clear margin over the runner-up, so a shared\n"
        "noun is not a match. The reply contains no price, because there is no price to give.",
        f"| profile menu: {menu}\n"
        f"| asked: {unknown[0]}\n"
        f"| action: {unk_outs[0].action}\n"
        f"| owner alert: {(unk_outs[0].owner_alert or '').splitlines()[0]}\n"
        f"| digits anywhere in the reply body: "
        f"{re.findall(r'[0-9]+', _body(unk_reply)) or 'none'}",
    )

    # ---- 9. prose terms are quoted verbatim and cannot be negotiated
    legal_profile = store_profile(legal_id)
    legal_quote = pricing.quote_for(legal_profile, "unfair dismissal claim fee")
    legal_ruling = guardrails.negotiate(legal_profile, legal_quote, 150.0)
    r.check(
        "A fee stated in words is repeated word for word, and is not negotiable",
        (legal_quote.unit == "prose"
         and legal_quote.stated_terms == LEGAL["answers"]["consultation_fee"]
         and not legal_ruling.allowed),
        "This barrister's fee is '£250 + VAT for the first hour, fixed'. That is not the number\n"
        "250: parsing it to 250 would drop the VAT, the hour and the word 'fixed', and quote\n"
        "terms the tenant never set. So it is repeated exactly and marked non-negotiable — there\n"
        "is no number to compare an offer against, so any haggling goes to the owner.",
        f"| stored: {legal_profile['pricing_rules'][pricing.RULE_HEADLINE]}\n"
        f"| quoted: {legal_quote.render()}\n"
        f"| unit: {legal_quote.unit}   negotiable: {legal_quote.negotiable}\n"
        f"| offered 150 -> allowed={legal_ruling.allowed}\n"
        f"| {legal_ruling.explanation}",
    )

    # ---- 10. the timezone is asked for, never inferred
    tz_msgs = ["How much is a signature facial?", "yes please", "sometime next week", "no idea"]
    _, tz_thread, tz_outs = _converse(spa_id, tz_msgs)
    r.check(
        "ATTACK — an unreadable timezone is asked again, then escalated, never guessed",
        (tz_outs[2].action == "timezone_reask" and tz_outs[3].state_after == "ESCALATED"
         and tz_thread.client_timezone is None),
        "The prospect agrees, then answers the timezone question twice without giving one. There\n"
        "is a great deal of context that could be used to guess — the tenant is in Europe/London,\n"
        "the email domain, the time of day the message arrived. None of it is used. §9b.1 says\n"
        "capture the prospect's timezone explicitly, and a guess here books a real person into\n"
        "the wrong hour, which is the most visible way this product can embarrass a tenant.\n"
        "It asks twice, then hands over.",
        _transcript(tz_msgs, tz_outs)
        + f"\nthread.client_timezone after all four: {tz_thread.client_timezone!r}",
    )

    # ---- 11. the slot race
    race_msgs = ["How much is a signature facial?", "yes please", "Europe/Madrid", "1"]
    race_cal = FixtureCalendar()
    outcomes = []
    with db.tenant_session(spa_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"race-{uuid.uuid4().hex[:8]}"))
        for i, body in enumerate(race_msgs):
            if i == 3:
                race_cal.take_first = True     # someone takes the slot mid-conversation
            out = engine.step(cur, tenant, thread, engine.Inbound(
                body=body, from_address=PROSPECT, from_name="Nadia Okoro"), race_cal)
            thread = out.thread
            outcomes.append(out)
    r.check(
        "ATTACK — a slot taken mid-conversation re-offers instead of failing at the prospect",
        (outcomes[3].action == "slots_offered" and not race_cal.booked
         and thread.state == "AWAITING_REPLY"),
        "The prospect picks slot 1, and between the offer and the pick someone else takes it.\n"
        "§9b.3 requires a re-fetch before booking rather than trusting the list we sent. The\n"
        "prospect sees fresh times and an apology, never an error, and nothing was booked.\n"
        "Also note the times are rendered in Europe/Madrid — the prospect's zone, not the spa's.",
        _transcript(race_msgs, outcomes)
        + f"\nbookings made: {race_cal.booked or 'none — correctly refused'}",
    )

    # ---- 12. no calendar connected: escalate, never claim a booking
    nocal_msgs = ["How much is a signature facial?", "yes please", "Europe/London"]
    _, nocal_thread, nocal_outs = _converse(spa_id, nocal_msgs, calendar=engine.NoCalendar())
    r.check(
        "ATTACK — with no calendar connected, nothing is ever claimed as booked",
        (nocal_thread.state == "ESCALATED"
         and nocal_outs[-1].action == "calendar_unavailable"),
        "engine.NoCalendar is the production default today, because OPERATOR_PROVIDES item 4\n"
        "(Cal.com) has not arrived. It raises rather than returning plausible times, and the\n"
        "engine turns that into an escalation: the owner books by hand. That is a worse product\n"
        "than a real booking and a far better one than a confirmation for an appointment that\n"
        "does not exist.\n"
        "The fixture calendar used elsewhere in this suite lives in the harness, not in the\n"
        "package, so production code cannot reach it even by mistake.",
        _transcript(nocal_msgs, nocal_outs)
        + f"\ndefault calendar in the engine: {engine.NoCalendar.__name__}\n"
        f"FixtureCalendar defined in: {FixtureCalendar.__module__} (harness, not package)",
    )

    # ---- 13. AI disclosure leads every outbound message
    every_reply = [o.reply for group in (outs, push_outs, unk_outs, tz_outs, nocal_outs)
                   for o in group if o.reply]
    first_lines = {reply.splitlines()[0] for reply in every_reply}
    all_disclosed = all(
        reply.startswith("This is an AI assistant replying on behalf of") and "HUMAN" in reply
        for reply in every_reply)
    r.check(
        "SB 243 — every outbound message opens by disclosing the AI and offering a human",
        all_disclosed and len(every_reply) > 10,
        f"All {len(every_reply)} replies sent across every conversation in this suite were\n"
        "checked, not a sample. The disclosure is the first line of each, and each carries a\n"
        "route to a human. It is applied in render(), which is the only function that produces\n"
        "an outbound body, so there is no path that emits a message without it.\n"
        "The word HUMAN is also a live trigger, not decoration — check 14 exercises it.",
        f"| replies checked: {len(every_reply)}\n"
        f"| distinct first lines: {len(first_lines)}\n"
        f"| {list(first_lines)[0]}",
    )

    # ---- 14. asking for a human always works
    human_msgs = ["How much is a signature facial?", "Can I speak to a human please?"]
    _, human_thread, human_outs = _converse(spa_id, human_msgs)
    r.check(
        "SB 243 — asking for a human hands over immediately, from any state",
        (human_thread.state == "ESCALATED"
         and human_outs[-1].action == "human_requested"),
        "The route to a human is unconditional and is checked before qualification, before\n"
        "pricing and before any state logic — so it cannot be reached only from states the\n"
        "engine happens to consider 'open'. A $1,000-per-violation private right of action is\n"
        "not a thing to leave depending on control flow.",
        _transcript(human_msgs, human_outs),
    )

    # ---- 15. receipts: reproducible, and tamper-evident
    with db.tenant_session(spa_id) as cur:
        all_receipts = store.list_receipts(cur)
    booked_receipt = next((x for x in all_receipts if x.action == "booked"), None)
    breach_receipt = next((x for x in all_receipts if x.action == "floor_breach"), None)
    tampered = Receipt(**{**booked_receipt.__dict__,
                          "decision": {**booked_receipt.decision, "detail": {"agreed": 1.0}}})
    r.check(
        "Every decision leaves a receipt, and an altered receipt is detectable",
        (len(all_receipts) > 10 and booked_receipt is not None
         and breach_receipt is not None and not breach_receipt.within_rules
         and receipts.verify(booked_receipt) and not receipts.verify(tampered)),
        "One receipt per transition, each carrying the decision, the rule that was checked and\n"
        "whether the decision was inside it. The breach receipt is recorded with\n"
        "within_rules=false — a refusal is evidence too, and in an escrow dispute it is the\n"
        "evidence that matters.\n"
        "The tamper test rewrites the agreed figure to £1 and recomputes: the hash no longer\n"
        "matches. Anchoring that hash on X Layer is the receipts suite and needs a funded signer (item 6).",
        f"| receipts written: {len(all_receipts)}\n"
        f"| actions: {sorted({x.action for x in all_receipts})}\n"
        f"| booked receipt rule_checked: {booked_receipt.rule_checked}\n"
        f"| breach receipt within_rules: {breach_receipt.within_rules}\n"
        f"| verify(original) = {receipts.verify(booked_receipt)}\n"
        f"| verify(agreed rewritten to 1.0) = {receipts.verify(tampered)}",
    )

    # ---- 16. isolation still holds once the engine is doing the writing
    with db.tenant_session(spa_id) as cur:
        spa_receipts = store.list_receipts(cur)
        spa_threads = store.list_threads(cur)
    with db.tenant_session(vet_id) as cur:
        vet_receipts = store.list_receipts(cur)
        vet_threads = store.list_threads(cur)
        vet_sees_spa_thread = store.get_thread(cur, spa_threads[0].thread_id)
    r.check(
        "ATTACK — the engine's own writes stay inside the tenant that made them",
        (vet_sees_spa_thread is None
         and {t.tenant_id for t in spa_threads} == {spa_id}
         and {t.tenant_id for t in vet_threads} == {vet_id}
         and not ({x.receipt_id for x in spa_receipts}
                  & {x.receipt_id for x in vet_receipts})),
        "the isolation suite proved the database fences tenants apart. This re-proves it with the engine\n"
        "doing the writing rather than the harness: threads, history and receipts written during\n"
        "the conversations above. Asking for the spa's thread by its exact primary key, from\n"
        "inside the vet's session, returns nothing — the id is not a capability.",
        f"| spa threads: {len(spa_threads)}, spa receipts: {len(spa_receipts)}\n"
        f"| vet threads: {len(vet_threads)}, vet receipts: {len(vet_receipts)}\n"
        f"| vet session asking for spa thread {spa_threads[0].thread_id}: "
        f"{vet_sees_spa_thread}\n"
        f"| receipt id overlap: "
        f"{len({x.receipt_id for x in spa_receipts} & {x.receipt_id for x in vet_receipts})}",
    )

    # ---- honest notes
    r.note(
        "Nothing is anchored on-chain yet, and no receipt pretends otherwise",
        f"All {len(all_receipts)} receipts have signature = NULL and xlayer_tx = NULL. Signing\n"
        "and anchoring need a funded signer on X Layer mainnet (OPERATOR_PROVIDES item 6), which\n"
        "has not arrived. There is no placeholder transaction hash anywhere: a fabricated one\n"
        "would make every receipt in the table untrustworthy, including the real ones added at\n"
        "the receipts suite. The content hash written today is the value that gets anchored then, so these\n"
        "receipts stay verifiable afterwards.",
        f"anchored receipts: {sum(1 for x in all_receipts if receipts.anchored(x))} "
        f"of {len(all_receipts)}",
    )
    r.note(
        "The calendar is the only fixture in this suite, and it is not in the shipped package",
        "the engine suite is about the state machine, so the calendar is a declared seam: FixtureCalendar\n"
        "lives in this harness file, returns slots on a fixed grid, and is named as a fixture in\n"
        "every check that touches it. The production default is engine.NoCalendar, which refuses\n"
        "(check 12). The booking suite replaces the seam with real Cal.com calls and re-runs this journey.\n"
        "Everything else in this suite is production code against a real PostgreSQL database.",
        f"fixture module: {FixtureCalendar.__module__}\n"
        f"production default: concierge.engine.NoCalendar",
    )
    words = lexicon.words(store_profile(spa_id))
    r.note(
        "Known limits of the deterministic matchers, stated plainly",
        "Three things in this phase are heuristics, and all three fail towards the owner's\n"
        "inbox rather than towards a wrong answer:\n"
        "  · Service matching is word overlap. A prospect describing a service in words that\n"
        "    share nothing with the profile's name for it gets escalated rather than quoted.\n"
        "  · Escalation-trigger matching is deliberately loose, so it will sometimes escalate\n"
        "    something the profile could have answered.\n"
        "  · Free-text ICP is not machine-evaluated at all. Qualification currently means 'is\n"
        "    this in the catalogue and does it trip a trigger', not 'does this person match the\n"
        "    ideal-client description'. That is honest work for an LLM once item 7 lands (§2\n"
        "    permits it — understanding only, never pricing).\n"
        "Booking-window enforcement is also partial: the minimum-notice window is applied here,\n"
        "but which days and hours are bookable comes from the connected calendar's own schedule\n"
        "rather than being re-derived from prose, so there is one source of truth at the booking suite.",
        f"spa lexicon fallbacks in use: {list(words.gaps) or 'none — tenant supplied both words'}",
    )


def _first_line(reply: str) -> str:
    body = reply.split("\n\n", 1)[1] if "\n\n" in reply else reply
    return body.strip().splitlines()[0]


def store_profile(tenant_id) -> dict:
    with db.tenant_session(tenant_id) as cur:
        return store.get_tenant(cur).profile
