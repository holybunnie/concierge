"""the floor-curve suite — the decaying floor (Feature 5).

the engine suite already proved a single flat floor is never crossed. This suite proves the richer, OPTIONAL
shape — `pricing_rules.floor_curve` — behaves the same way at every point along the curve, not
just at its resting value: a counter-offer is checked against wherever the curve says CONCIERGE
may currently go, and the absolute floor at the end of it is exactly as hard a line as a flat
floor always was. Checks 1-2 walk a real, multi-round negotiation down the curve and prove each
round matches its own point rather than jumping straight to the eventual floor. Check 3 is the
one to read most carefully: it red-teams the absolute floor specifically, pushing six real
negotiation rounds past the point where the curve has fully decayed and confirming the floor
never drifts a penny below what the tenant actually set. Check 4 is the regression proof: a
tenant with no curve at all is not touched by any of this code path.
"""

from __future__ import annotations

import re

from . import db, engine, guardrails, pricing, store
from . import verify_engine as p3

CURVE = dict(
    description=("Bright Path Consulting helps small businesses set up their books and "
                 "financial processes."),
    business="Bright Path Consulting",
    answers={
        "services": [
            {"name": "Consulting engagement", "duration_min": 90, "price": 1200,
             "currency": "GBP"},
        ],
        "availability": "Mon-Fri 09:00-17:00, minimum 24 hours notice",
        "timezone": "Europe/London",
        "icp": "Small business owners",
        "escalation_triggers": ["Anything about litigation or a regulatory investigation"],
        "artifact_sample": "Thanks for reaching out — happy to help with that.",
        "engagement_noun": "session",
        "client_noun": "client",
    },
)

FLOOR_CURVE = {
    "initial": 1000, "floor": 600, "kind": "cash",
    "decay_trigger": "rounds", "decay_steps": [900, 800, 700],
}
# Sequence read by round_index: 0->1000, 1->900, 2->800, 3->700, 4+->600 (the absolute floor).


def _set_curve(tenant_id) -> None:
    """Attach the decaying floor after onboarding — same seam `autonomy_thresholds` uses
    (Feature 2): a profile field the tenant sets directly, not a template question."""
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        profile = dict(tenant.profile)
        profile["pricing_rules"] = dict(profile.get("pricing_rules") or {})
        profile["pricing_rules"]["floor_curve"] = dict(FLOOR_CURVE)
        store.update_profile(cur, profile)


def _run(tenant_id, msgs):
    outs = []
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=p3.PROSPECT, external_ref=f"curve-{tenant_id.hex[:8]}"))
        for body in msgs:
            out = engine.step(cur, tenant, thread, engine.Inbound(
                body=body, from_address=p3.PROSPECT, from_name="Nadia Okoro"))
            thread = out.thread
            outs.append(out)
    return thread, outs


def _floor_of(out) -> float | None:
    return (out.receipt.decision.get("detail") or {}).get("floor")


def run(r) -> None:
    db.migrate()

    curve_id = p3._onboard(CURVE)
    _set_curve(curve_id)

    # ---- 1-2. a real multi-round negotiation tracks the curve, point by point
    # Each ask sits comfortably (60% of the way) above that round's OWN floor — never exactly on
    # it. Asking exactly at the wire would also trip Feature 2's floor-proximity signal (a
    # separate, correct feature: a figure sitting right on the floor is genuinely lower-
    # confidence) and queue the reply for owner approval instead of sending it, which would be
    # this suite accidentally exercising the autonomy suite instead of the curve itself.
    msgs = [
        "Hi, what's your fee for a consulting engagement?",  # 0: quoted at £1,200
        "Could you do 1120?",        # 1: round 0 -> floor 1,000
        "Could you do 1080 instead?",# 2: round 1 -> floor   900
        "What about 1040?",          # 3: round 2 -> floor   800
        "Can you do 1000?",          # 4: round 3 -> floor   700
        "Could you stretch to 960?", # 5: round 4 -> floor   600 (curve fully decayed)
    ]
    thread, outs = _run(curve_id, msgs)
    floors = [_floor_of(o) for o in outs[1:]]
    expected = [1000.0, 900.0, 800.0, 700.0, 600.0]
    r.check(
        "Each negotiation round is checked against its OWN point on the curve, not the eventual floor",
        (floors == expected
         and all(o.within_rules and o.action == "counter_within_rules" for o in outs[1:])),
        "The tenant set initial=£1,000 decaying through £900/£800/£700 to an absolute floor of\n"
        "£600 over rounds. Five real counter-offers, each accepted, each checked against a\n"
        "DIFFERENT number — £1,000 on the first round through £600 by the fifth, not £600 (the\n"
        "eventual floor) applied from round one, and not £1,000 (the starting point) applied\n"
        "forever. The absolute floor only becomes the binding number once the curve has actually\n"
        "run out of defined points (round 4 here) — round 1 could not have been talked down to\n"
        "£600 by naming a big enough discount.",
        p3._transcript(msgs, outs)
        + "\n| round -> floor actually applied: "
        + ", ".join(f"r{i}={f:,.0f}" for i, f in enumerate(floors)),
    )
    r.check(
        "The curve has moved further down by round 4 than it had by round 1",
        floors[0] > floors[1] > floors[2] > floors[3] > floors[4] == 600.0,
        "Strictly decreasing across every round, resting at exactly the absolute floor once the\n"
        "curve's defined points (four of them: 1,000/900/800/700) are exhausted — never below it,\n"
        "never stalling above it either.",
        f"| floor sequence: {floors}",
    )

    # ---- 3. ATTACK — the absolute floor never breaks, however far past the curve you push
    red_team_msgs = [
        "How about 940?",                                # round 5 -> still floor 600, allowed
        "I could only do 1 pound, take it or leave it",  # round 6 -> floor 600, BELOW IT: breach
    ]
    with db.tenant_session(curve_id) as cur:
        tenant = store.get_tenant(cur)
        for body in red_team_msgs:
            out = engine.step(cur, tenant, thread, engine.Inbound(
                body=body, from_address=p3.PROSPECT, from_name="Nadia Okoro"))
            thread = out.thread
            outs.append(out)
    late_allowed, breach = outs[-2], outs[-1]
    all_replies = [o.reply for o in outs if o.reply]
    # Only currency-prefixed figures — a bare "90 minutes" duration is not a price and must not
    # be mistaken for one when scanning for a floor breach.
    figures_in_any_reply = [
        float(n.replace(",", "")) for reply in all_replies
        for n in re.findall(r"£\s?(\d[\d,]*(?:\.\d+)?)", p3._body(reply))
    ]
    below_floor_anywhere = [f for f in figures_in_any_reply if f < FLOOR_CURVE["floor"]]
    r.check(
        "ATTACK — six real rounds past the curve's own points, the absolute floor still never breaks",
        (_floor_of(late_allowed) == 600.0 and late_allowed.within_rules
         and _floor_of(breach) == 600.0 and not breach.within_rules
         and breach.action == "floor_breach" and thread.state == "ESCALATED"
         and not below_floor_anywhere),
        "Round 5 (£940, still comfortably above the £600 floor) is agreed normally — the curve\n"
        "does not keep decaying forever, it rests at the stated floor exactly as designed. Round\n"
        "6 (£1) is the attack: a wildly low ask, six rounds past where the curve's defined points\n"
        "ran out, on the theory that enough elapsed rounds might erode the floor itself rather\n"
        "than just the willingness to move toward it. It does not — £600 is still the binding\n"
        "number, the breach still escalates rather than countering, and a full scan of every\n"
        "figure that appeared in ANY reply across this entire conversation (seven rounds) finds\n"
        "nothing below £600. That is the feature's entire safety claim, and this is the red-team.",
        f"| round 5 (£940): floor={_floor_of(late_allowed)}, allowed={late_allowed.within_rules}\n"
        f"| round 6 (£1):   floor={_floor_of(breach)}, allowed={breach.within_rules}, "
        f"state={thread.state}\n"
        f"| every figure seen in any reply this conversation: {sorted(set(figures_in_any_reply))}\n"
        f"| any below the £600 floor: {below_floor_anywhere or 'none'}",
    )

    # ---- 4. REGRESSION — a tenant with no curve set is untouched by any of this
    spa_id = p3._onboard(p3.SPA)
    spa_profile = p3.store_profile(spa_id)
    quote = pricing.quote_for(spa_profile, "deep tissue massage")
    bounds_round0 = guardrails.bounds_for(spa_profile, quote, round_index=0)
    bounds_round9 = guardrails.bounds_for(spa_profile, quote, round_index=9)
    ruling_round0 = guardrails.negotiate(spa_profile, quote, 75.0, round_index=0)
    ruling_round9 = guardrails.negotiate(spa_profile, quote, 75.0, round_index=9)
    r.check(
        "REGRESSION — a tenant with no floor_curve set is not affected by round_index at all",
        (pricing.floor_curve(spa_profile) is None
         and [b.limit for b in bounds_round0] == [b.limit for b in bounds_round9]
         and ruling_round0.floor == ruling_round9.floor == 72.25
         and ruling_round0.allowed == ruling_round9.allowed),
        "This is the engine suite's own flat-floor spa tenant (£70 floor, 15% discount cap — the cap binds\n"
        "at £72.25, the engine suite check 6). `pricing.floor_curve()` returns None because this tenant\n"
        "never set one, and `bounds_for`/`negotiate` fall straight to the ORIGINAL flat-floor\n"
        "branch — round_index 0 and round_index 9 produce byte-identical bounds and rulings,\n"
        "because nothing here ever reads the argument for this tenant. Feature 5 is additive: a\n"
        "tenant who never heard of it negotiates exactly as the engine suite already proved.",
        f"| round 0 bounds: {[str(b) for b in bounds_round0]}\n"
        f"| round 9 bounds: {[str(b) for b in bounds_round9]}\n"
        f"| round 0 ruling.floor={ruling_round0.floor}, allowed={ruling_round0.allowed}\n"
        f"| round 9 ruling.floor={ruling_round9.floor}, allowed={ruling_round9.allowed}",
    )

    # ---- honest notes
    r.note(
        "The curve is set on the profile, never inferred and never touched mid-negotiation",
        "`pricing_rules.floor_curve` is written once, by the tenant (at onboarding or in "
        "settings, via `store.update_profile` — the same seam Feature 2's `autonomy_thresholds` "
        "uses). No code path in `engine.py` or `guardrails.py` ever writes to it: 'the agent "
        "decides to be more flexible because the conversation feels promising' is exactly the "
        "failure mode this build refuses to implement, and there is no function here that could.",
        f"floor_curve set this suite: {FLOOR_CURVE}",
    )
