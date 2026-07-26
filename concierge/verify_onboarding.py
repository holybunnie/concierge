"""the onboarding suite — vertical-aware onboarding, on three real business descriptions.

The gate asks for: correct classification, the right template with a filled example, gaps
flagged, a sample requested, rules read back, and a unique inbound address returned. Those are
checks 1-7. Checks 8-11 are the attacks, and they are the ones worth reading — the expensive
failure in this phase is not a wrong template, it is a template's example price silently
becoming a real business's real price.
"""

from __future__ import annotations

import psycopg

import os

from . import config, db, onboarding, store
from .classify import classify
from .verticals import LEGAL, REAL_ESTATE, SPA_BEAUTY, TEMPLATES


class env_override:
    """Hold one environment variable at a chosen value for the length of a check.

    The honest-degradation checks (a missing domain must produce a visibly dead address) used
    to read whatever the operator happened to have in `.env`, which meant they proved the
    degradation only on a machine where the domain was genuinely absent — and silently asserted
    the opposite of the truth on the VPS, where CONCIERGE_DOMAIN has been set since go-live. A
    check whose result depends on the runner's own configuration is not evidence, so both
    halves are now driven explicitly from here: `value=None` means "prove this with the value
    removed".
    """

    def __init__(self, name: str, value: str | None):
        self.name = name
        self.value = value
        self.previous: str | None = None

    def __enter__(self) -> "env_override":
        self.previous = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value
        # `config.get` calls `load_env`, which would re-inject the .env value on the next read.
        # Marking it loaded is what makes the removal actually hold.
        config._loaded = True
        return self

    def __exit__(self, *_exc) -> None:
        if self.previous is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.previous

# Three descriptions written the way an owner actually writes them — rambling, partial, and
# with the important things left out. None of them mentions the vertical by its template name.
DESCRIPTIONS = {
    "real_estate": (
        "We're a small estate agency in north London, been going 11 years. Mostly residential "
        "sales around N1 and N16, plus a handful of lettings for landlords we've known for ages. "
        "Typical asking price is somewhere between 450k and 1.2m. People email asking whether a "
        "listing is still available and whether they can get a viewing at the weekend, which is "
        "most of what my inbox is. We charge 1.5% commission and I won't go under 1.2%."
    ),
    "legal": (
        "I'm a barrister at a set in Manchester, employment law mainly — unfair dismissal, "
        "discrimination, the occasional contract dispute. England and Wales only. I get a lot of "
        "enquiries from people who've just been made redundant asking whether I can take their "
        "matter and what I charge. Initial consultation is £250 plus VAT for the hour and that is "
        "not negotiable. I obviously can't have anything going out that looks like actual advice "
        "before a conflict check is done."
    ),
    "spa_beauty": (
        "Small spa, six treatment rooms, we do massage, facials, waxing and nails. Deep tissue is "
        "£85 for 60 minutes, the signature facial is £70 for 45 minutes. Saturdays get booked "
        "solid and people are always emailing to ask if we do hot stone and whether they can get "
        "in at the weekend. I need 24 hours notice for a booking."
    ),
}


def _reset(names: list[str]) -> None:
    with psycopg.connect(db.config.owner_database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE business_name = ANY(%s)", (names,))


def run(r) -> None:
    db.migrate()
    _reset(["Northfield & Co", "Marcus Webb (Barrister)", "The Wilding Rooms",
            "The Wilding Rooms Ltd"])

    # ---- 1. classification, on all three, with the evidence that drove it
    results = {v: classify(d) for v, d in DESCRIPTIONS.items()}
    correct = all(results[v].vertical == v and results[v].confident for v in DESCRIPTIONS)
    ev = []
    for v, c in results.items():
        ev.append(f"{v}: -> {c.vertical} (confident={c.confident})")
        ev.append(f"    scores {c.scores}")
        ev.append(f"    matched {', '.join(c.matched_terms[:8])}"
                  + (" …" if len(c.matched_terms) > 8 else ""))
    r.check(
        "All three business descriptions classify to the right vertical",
        correct,
        "Three descriptions written the way owners actually write them — rambling, incomplete,\n"
        "and never naming their own trade in the words the template uses. Each is scored against\n"
        "a weighted lexicon; the decision comes with the exact terms that produced it, so a\n"
        "misclassification is a one-line fix rather than an interrogation of a black box.\n"
        "No LLM is involved, which also means this works today, with no API key.",
        "\n".join(ev),
    )

    # ---- 2. abstention: it can say "I don't know"
    vague = classify("We're a small business and we get a lot of emails. Been going a while.")
    tie = classify("We handle property matters and litigation for landlords and tenants.")
    r.check(
        "An unrecognisable or ambiguous description is refused, not guessed at",
        vague.vertical == "unclear" and not vague.confident and tie.vertical == "unclear",
        "The vertical decides which questions the tenant is asked, so a wrong guess collects the\n"
        "wrong profile entirely — a much more expensive error than admitting ignorance. Two ways\n"
        "of not knowing are handled: nothing recognisable scored, and two verticals scoring too\n"
        "close to call. The second case matters — 'property matters and litigation for landlords'\n"
        "is genuinely both, and a confident classifier would silently pick one.",
        f"vague description  -> {vague.vertical}: {vague.reason}\n"
        f"                      scores {vague.scores}\n"
        f"ambiguous (property + litigation) -> {tie.vertical}: {tie.reason}\n"
        f"                      scores {tie.scores}",
    )

    # ---- 3. each vertical gets its own template, with its own worked example
    sessions = {v: onboarding.start(d) for v, d in DESCRIPTIONS.items()}
    re_brief = sessions["real_estate"].briefing()
    legal_brief = sessions["legal"].briefing()
    spa_brief = sessions["spa_beauty"].briefing()
    ok = ("commission" in re_brief and "Conflict checks" in legal_brief
          and "Treatment menu" in spa_brief and "cancellation" in spa_brief.lower()
          and "Northgate Property" in re_brief and "Fenwick Chambers" in legal_brief)
    r.check(
        "Each vertical is asked its own questions, with a filled example beside each one",
        ok,
        "The estate agent is asked for commission and viewing windows. The barrister is asked for\n"
        "jurisdictions, conflict checks, and what never to advise on. The spa is asked for a\n"
        "treatment menu with durations, and for a cancellation policy. Every question carries a\n"
        "worked example from a clearly fictional business so the tenant can see what a good\n"
        "answer looks like.",
        f"real estate asks for: "
        f"{[f.label for f in REAL_ESTATE.required_fields()]}\n"
        f"legal asks for:       {[f.label for f in LEGAL.required_fields()]}\n"
        f"spa asks for:         {[f.label for f in SPA_BEAUTY.required_fields()]}\n\n"
        f"--- excerpt of the spa briefing as the tenant sees it ---\n"
        + "\n".join(spa_brief.splitlines()[10:22]),
    )

    # ---- 4. the legal template's non-negotiable blocks
    legal_session = sessions["legal"]
    blocks = legal_session.template.hard_escalations
    r.check(
        "The legal template carries hard escalations the tenant cannot switch off",
        len(blocks) >= 3 and any("advice" in b.lower() for b in blocks),
        "Some rules cannot be delegated to a profile. A barrister's inbound agent must never\n"
        "answer 'what should I do about my situation' — that is legal advice, and no combination\n"
        "of profile settings may enable it. These are attached to the template, applied on top of\n"
        "whatever the tenant configures, and shown to them during onboarding so there is no\n"
        "surprise later.",
        "\n".join(f"  · {b}" for b in blocks),
    )

    # ---- 5. gaps are flagged with consequences, before anything goes live
    spa = sessions["spa_beauty"]
    spa.answer("service_menu", [
        {"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
        {"name": "Signature facial", "duration_min": 45, "price": 70, "currency": "GBP"},
    ])
    spa.answer("floor_price", 70)
    gaps_before = spa.gaps()
    gap_keys = {g.field_key for g in gaps_before}
    cancellation_gap = next((g for g in gaps_before if g.field_key == "cancellation_policy"), None)
    r.check(
        "Missing required fields are named, with what each one will cost the tenant",
        "cancellation_policy" in gap_keys and "artifact_sample" in gap_keys
        and cancellation_gap is not None and "escalate" in cancellation_gap.consequence,
        "The spa gave a treatment menu and a floor and stopped, which is exactly what a real\n"
        "owner does. Six required fields are still empty and each is named with its consequence\n"
        "in business terms, not as a validation error. The cancellation policy is the one that\n"
        "matters: clients ask about it constantly, and without it every one of those escalates.",
        f"still missing: {sorted(gap_keys)}\n\n"
        + spa.gap_report(),
    )

    # ---- 6. a sample of the tenant's own writing is requested
    sample_field = SPA_BEAUTY.field("artifact_sample")
    r.check(
        "A real reply the tenant has actually sent is requested, in every vertical",
        all(t.field("artifact_sample") is not None for t in TEMPLATES.values()),
        "Tone is copied from the tenant's own writing rather than approximated. This is asked for\n"
        "in all four templates, and its absence is flagged honestly: replies still go out, in a\n"
        "neutral professional voice, and the tenant is told that is what will happen.",
        f"question: {sample_field.question}\n"
        f"why:      {sample_field.why}\n"
        f"if empty: {sample_field.gap_consequence}",
    )

    # ---- 7. rules read back, then a unique address returned
    for k, v in {
        "cancellation_policy": "48 hours notice or 50% of the treatment price is charged",
        "max_discount_pct": 10,
        "booking_lead_time": "Minimum 24 hours; Saturday bookings by Thursday",
        "timezone": "Europe/London",
        "icp": "Local clients within a few miles, repeat bookings preferred",
        "escalation_triggers": ["Anything about pregnancy, allergies or medical conditions"],
        "artifact_sample": "Hi — yes we do that, it's 60 minutes. I've got Saturday 2pm free.",
    }.items():
        spa.answer(k, v)

    readback = spa.read_back()
    tenant_id, address, _ = onboarding.finalise(
        spa, business_name="The Wilding Rooms",
        owner_email="owner@wilding.example", owner_wallet="0xWILD0000000000000000000000000000000001",
    )
    live = onboarding.address_is_live(address)
    r.check(
        "The rules are read back for confirmation, and a unique inbound address is returned",
        "£70" in readback and "escalate" in readback and "AI agent" in readback
        and address.startswith("the-wilding-rooms@") and spa.gaps() == [],
        "The read-back is rendered from the built profile — the same object the engine will quote\n"
        "from — so what the tenant confirms is literally what the engine reads. A read-back\n"
        "generated from a different source than the engine consumes would be a reassuring lie.\n"
        f"The address is the load-bearing output: it is how anyone reaches this tenant.\n"
        f"{'It is live.' if live else 'The local part is reserved; the domain half is PENDING.'}",
        f"--- read-back as the tenant sees it ---\n{readback}\n"
        f"--- returned ---\ntenant_id: {tenant_id}\ninbound address: {address}",
    )

    # ---- 8. ATTACK: the domain is not faked when the operator has not provided one.
    # Both halves are driven explicitly (see `env_override`) rather than read from whatever the
    # runner's .env happens to hold — the degradation is a property of the code, not of the box.
    with env_override("CONCIERGE_DOMAIN", None):
        pending_addr = onboarding.allocate_inbound_address("The Wilding Rooms")
        pending_live = onboarding.address_is_live(pending_addr)
    with env_override("CONCIERGE_DOMAIN", "quietdesks.com"):
        real_addr = onboarding.allocate_inbound_address("The Wilding Rooms")
        real_live = onboarding.address_is_live(real_addr)
    r.check(
        "A missing domain yields a visibly unusable address, not a plausible-looking one",
        # Case-insensitive: addresses are lowercased on storage, as mail addresses must be.
        (pending_addr.lower().endswith(onboarding.PENDING_DOMAIN.lower()) and not pending_live
         and real_addr.endswith("@inbox.quietdesks.com") and real_live),
        "With no domain provided there is no real address to hand back. The local part is real\n"
        "and permanently reserved; the domain half is PENDING-DOMAIN.invalid — a TLD reserved by\n"
        "RFC 2606 precisely so it can never resolve. Anyone who tries to use it gets an immediate\n"
        "hard failure instead of silence. A placeholder that looks live is worse than no\n"
        "placeholder: it produces a demo that appears to work and an inbox that never receives\n"
        "anything. The second half of the check is the one that stops this rotting: with a domain\n"
        "configured the SAME call returns a real inbox.<domain> address, so the check proves a\n"
        "behaviour rather than passing because the runner happened to be unconfigured.",
        f"with CONCIERGE_DOMAIN removed  -> {pending_addr}  (address_is_live: {pending_live})\n"
        f"with CONCIERGE_DOMAIN set      -> {real_addr}  (address_is_live: {real_live})\n"
        f"address returned by this run's finalise(): {address}  (live: {live})\n"
        f"CONCIERGE_DOMAIN actually in this environment: "
        f"{config.get('CONCIERGE_DOMAIN') or 'NOT PROVIDED'}",
    )

    # ---- 9. ATTACK: could a template's example price become a real tenant's price?
    empty = onboarding.start(DESCRIPTIONS["spa_beauty"])
    empty_profile = empty.build_profile()
    example_prices = [s["price"] for s in SPA_BEAUTY.field("service_menu").example]
    contaminated = _contains_any(empty_profile, example_prices)
    r.check(
        "ATTACK — a tenant who answers nothing gets an empty profile, not the example's prices",
        empty_profile.get("services") is None and not contaminated
        and empty_profile.get("pricing_rules") is None,
        "This is the failure this phase exists to prevent: a business quoting £85 because the\n"
        "template's fictional example said £85. An onboarding session was built and finalised\n"
        "with zero answers. The resulting profile has no services and no pricing rules at all —\n"
        "the example values (85, 70) appear nowhere in it. build_profile() reads self.answers and\n"
        "never touches Field.example, so this is structural rather than a lucky outcome. A\n"
        "profile with no prices cannot quote; the engine escalates instead.",
        f"answers given: {empty.answers}\n"
        f"resulting profile: {empty_profile}\n"
        f"example prices that must NOT appear: {example_prices} -> present: {contaminated}",
    )

    # ---- 10. ATTACK: extracted candidates are not facts
    cands = empty.candidates
    money = [c for c in cands if c.kind == "money"]
    unconfirmed = all(not c.confirmed for c in cands)
    r.check(
        "ATTACK — numbers scraped from the description stay candidates until confirmed",
        len(money) >= 2 and unconfirmed and not _contains_any(empty_profile,
                                                              [c.value for c in money]),
        "The extractor found the real prices in the spa's own prose, which is genuinely useful —\n"
        "it saves the owner retyping them. But a regex cannot tell the tenant's price from a\n"
        "competitor's price mentioned in passing, so every candidate is shown with its\n"
        "surrounding words and must be confirmed. None of them reach the profile on their own:\n"
        "build_profile() cannot see the candidate list at all.",
        "\n".join(f"  {c.kind}: {c.value} from {c.raw!r} — confirmed={c.confirmed}\n"
                  f"      context: …{c.context}…" for c in cands[:4]),
    )

    # ---- 11. ATTACK: two businesses with the same name cannot share an inbox
    second_session = onboarding.start(DESCRIPTIONS["spa_beauty"])
    for k, v in {"service_menu": [{"name": "Facial", "duration_min": 45, "price": 60,
                                   "currency": "GBP"}],
                 "floor_price": 55, "max_discount_pct": 5, "booking_lead_time": "24 hours",
                 "cancellation_policy": "24 hours", "timezone": "Europe/London",
                 "icp": "local", "escalation_triggers": ["medical"],
                 "artifact_sample": "Hi, yes we do."}.items():
        second_session.answer(k, v)
    tid2, address2, _ = onboarding.finalise(
        second_session, business_name="The Wilding Rooms",
        owner_email="other@wilding2.example",
        owner_wallet="0xWILD0000000000000000000000000000000002")

    dup = None
    try:
        with db.tenant_session(tid2) as cur:
            cur.execute("UPDATE tenants SET inbound_address = %s", (address,))
            dup = "UPDATE SUCCEEDED — TWO TENANTS SHARE AN INBOX"
    except psycopg.errors.UniqueViolation as e:
        dup = f"UniqueViolation: {str(e).splitlines()[0]}"
    except Exception as e:
        dup = f"{type(e).__name__}: {str(e).splitlines()[0]}"

    r.check(
        "ATTACK — an identical business name cannot produce a shared inbound address",
        address2 != address and address2.startswith("the-wilding-rooms-2@")
        and "SUCCEEDED" not in dup,
        "Two spas called 'The Wilding Rooms' onboard. The second gets a suffixed address rather\n"
        "than the first one's inbox — a shared address would route one business's client to the\n"
        "other's agent, which is the Tenant-Isolation Law failing at the front door before any\n"
        "query runs. Allocation checks availability through the same resolver that routes real\n"
        "mail, so 'is it free?' and 'who owns it?' cannot disagree. Forcing a collision directly\n"
        "in SQL is then rejected by the UNIQUE constraint, which is what actually wins a race\n"
        "between two simultaneous onboardings.",
        f"tenant 1: {address}\ntenant 2: {address2}\n"
        f"forcing tenant 2's address to equal tenant 1's -> {dup}",
    )

    # ---- 12. the honest note about the missing LLM
    r.note(
        "No LLM was used, and none was needed",
        "OPERATOR_PROVIDES item 7 (LLM key) is still missing. §11 allows an LLM to reason about\n"
        "vertical onboarding, and a later phase may add one to catch descriptions this lexicon\n"
        "misses. Nothing above is stubbed or simulated in its absence — classification is a real\n"
        "weighted lexicon, extraction is real regex, and both run on the tenant's actual words.\n"
        "Item 8 (web search) is also missing, so no vertical template was enriched from live\n"
        "sources; the four built-in templates are what was used, exactly as §11 requires it to\n"
        "say out loud.",
    )


def _contains_any(obj, values) -> bool:
    """Deep search: does any of `values` appear anywhere in this structure?"""
    if isinstance(obj, dict):
        return any(_contains_any(v, values) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_any(v, values) for v in obj)
    return any(obj == v for v in values)
