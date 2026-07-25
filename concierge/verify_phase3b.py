"""GATE 3b-2 — confidence-scored autonomy (Feature 2).

GATE 3 proved the state machine and the guardrails. This gate proves the layer built on top of
both: a decision that is within the tenant's rules may still be too close to call for CONCIERGE
to send unsupervised. `concierge/confidence.py` is the entire answer to "did an LLM decide that?"
— there is nothing in it that could, and the score is attached to the receipt Phase 3 already
writes, not rendered once and discarded.

Checks 1-2 are the gate's own requirement, almost verbatim from the spec: a thin profile queues,
a complete one auto-sends, for the same kind of inquiry. Checks 3-4 go one layer deeper — they
prove the floor-proximity signal is doing real work (a COMPLETE profile still queues a genuinely
marginal negotiation) and that precedent is what moves a marginal decision the rest of the way
(the same negotiation auto-sends once this exact price point has been booked three times).
Check 5 proves the score is persisted and independently retrievable, not just visible on the
Outcome object the caller happened to keep. Check 6 is the regression proof: GATE 3's own
NEW -> BOOKED journey, re-run here, still completes with no human touching it.
"""

from __future__ import annotations

from . import confidence, db, engine, store
from . import verify_phase3 as p3

# Same day-spa template and field keys GATE 3 already exercises, so nothing here is exercising
# an untested onboarding path — only the profiles' completeness differs.

THIN = dict(
    description="We run a day spa offering massage, facials, waxing and nails.",
    business="Threadbare Spa",
    answers={
        "service_menu": [
            {"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
        ],
        # Deliberately nothing else: no floor, no discount cap, no escalation triggers, no ICP,
        # no booking rules, no lexicon. This is what "few fields provided" means concretely.
    },
)

RICH = dict(
    description="We run a day spa offering massage, facials, waxing and nails.",
    business="Halcyon Rooms Rich",
    answers={
        "service_menu": [
            {"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
        ],
        "floor_price": 70,
        "max_discount_pct": 15,
        "booking_lead_time": "Minimum 24 hours notice",
        "timezone": "Europe/London",
        "icp": "Local clients within a few miles",
        "escalation_triggers": ["Anything about pregnancy, allergies or medical conditions"],
        "artifact_sample": "Hi — yes, we do that. I've got Saturday 2pm free.",
        "engagement_noun": "treatment",
        "client_noun": "client",
    },
)


def run(r) -> None:
    db.migrate()

    thin_id = p3._onboard(THIN)
    rich_id = p3._onboard(RICH)

    # ---- 1. a thin profile queues a plain quote instead of sending it
    thin_tenant, thin_thread, thin_outs = p3._converse(
        thin_id, ["How much is a deep tissue massage?"])
    thin_out = thin_outs[0]
    thin_conf = thin_out.confidence
    r.check(
        "A thin profile's quote is drafted but held for owner approval, not sent",
        (thin_conf is not None and not thin_conf["autonomous"]
         and thin_conf["score"] < confidence.DEFAULT_AUTONOMY_THRESHOLD
         and thin_out.action == "quoted"
         and thin_out.reply is None
         and thin_thread.state == "AWAITING_OWNER_APPROVAL"
         and bool(thin_thread.current_offer.get("pending_approval", {}).get("drafted_reply"))),
        "This tenant answered only a price — no floor, no discount cap, no escalation triggers,\n"
        "no ICP, no booking rules, no lexicon. The engine still derives a correct £85 quote (the\n"
        "pricing/guardrail path is untouched), but confidence scores it low on profile\n"
        "completeness alone, so the reply is drafted and queued rather than sent. The drafted\n"
        "text is not lost — it is attached to the thread's own offer for the owner to approve or\n"
        "edit, the same durability every other piece of thread state gets.",
        f"| action: {thin_out.action}   state: {thin_thread.state}\n"
        f"| confidence: {thin_conf}\n"
        f"| reply sent to prospect: {thin_out.reply!r}\n"
        f"| owner_alert: {(thin_out.owner_alert or '').splitlines()[0]}\n"
        f"| drafted reply on thread.current_offer.pending_approval: "
        f"{thin_thread.current_offer.get('pending_approval', {}).get('drafted_reply', '')[:80]}...",
    )

    # ---- 2. a complete profile sends the same kind of quote autonomously
    rich_tenant, rich_thread, rich_outs = p3._converse(
        rich_id, ["How much is a deep tissue massage?"])
    rich_out = rich_outs[0]
    rich_conf = rich_out.confidence
    r.check(
        "A complete profile's quote clears the threshold and is sent immediately",
        (rich_conf is not None and rich_conf["autonomous"]
         and rich_conf["score"] >= confidence.DEFAULT_AUTONOMY_THRESHOLD
         and rich_out.action == "quoted"
         and rich_out.reply is not None
         and rich_thread.state == "AWAITING_REPLY"),
        "Same inquiry, same price, same engine — the only difference is that this tenant filled\n"
        "in the floor, the discount cap, escalation triggers, an ICP, booking rules and their own\n"
        "lexicon. Profile completeness alone is enough to clear the bar for a fresh, unnegotiated\n"
        "quote (nothing has been asked yet, so the floor-proximity signal is at its maximum too),\n"
        "and the reply goes straight out.",
        f"| action: {rich_out.action}   state: {rich_thread.state}\n"
        f"| confidence: {rich_conf}\n"
        f"| first line sent: {p3._first_line(rich_out.reply)}",
    )

    # ---- 3. a COMPLETE profile still queues a genuinely marginal negotiation
    # £75 against an £85 quote and a £72.25 floor (15% cap binds ahead of the £70 floor) sits
    # only 22% of the way back from the floor to the quoted price — within the rules, but the
    # kind of figure this feature exists to have a second pair of eyes on before it goes out,
    # with no track record yet to say this exact price point is safe.
    _, marginal_thread_1, marginal_outs_1 = p3._converse(
        rich_id, ["How much is a deep tissue massage?", "Could you do 75?"])
    marginal_conf_1 = marginal_outs_1[1].confidence
    r.check(
        "A complete profile still queues when the specific figure sits close to the floor",
        (marginal_conf_1 is not None and not marginal_conf_1["autonomous"]
         and marginal_outs_1[1].action == "counter_within_rules"
         and marginal_outs_1[1].reply is None
         and marginal_thread_1.state == "AWAITING_OWNER_APPROVAL"),
        "This is the same tenant as check 2 — profile completeness is identical and maximal. The\n"
        "only thing that changed is the figure on the table: £75 is within the guardrail (it is\n"
        "not a floor breach, GATE 3 check 7 covers that failure mode separately) but it sits close\n"
        "enough to the floor, with zero prior bookings at this price to vouch for it, that the\n"
        "score falls short. This is the proof that confidence is not just a proxy for 'did the\n"
        "tenant fill in their profile' — the floor-proximity signal does real, independent work.",
        f"| asked: 75   quoted: 85   confidence: {marginal_conf_1}",
    )

    # ---- 4. precedent moves the SAME marginal figure over the line
    # Book this exact service at £80 three times for this tenant — a real, successful,
    # comfortably-within-rules price point, not the marginal one — then ask the marginal
    # question again on a fresh thread.
    book_msgs = ["Hi, how much is a deep tissue massage?", "Could you do 80?", "yes please",
                 "London", "1"]
    for _ in range(confidence.PRECEDENT_FULL_CONFIDENCE_COUNT):
        p3._converse(rich_id, book_msgs, p3.FixtureCalendar())

    _, marginal_thread_2, marginal_outs_2 = p3._converse(
        rich_id, ["How much is a deep tissue massage?", "Could you do 75?"])
    marginal_conf_2 = marginal_outs_2[1].confidence
    r.check(
        "The same marginal figure auto-sends once this price point has real precedent",
        (marginal_conf_2 is not None and marginal_conf_2["autonomous"]
         and marginal_conf_2["score"] > marginal_conf_1["score"]
         and marginal_outs_2[1].reply is not None
         and marginal_thread_2.state == "AWAITING_REPLY"),
        f"Three real bookings at £80 (within {confidence.PRECEDENT_BAND_PCT * 100:.0f}% of the "
        f"£75 being asked here) were made for this tenant in between check 3 and this one — "
        "nothing else about the tenant or the figure changed. Precedent is weighted lowest of\n"
        "the three signals on purpose (it only nudges), but it is exactly enough to move this\n"
        "specific, previously-marginal figure into auto-send territory. This is the 'precedent-\n"
        "rich' half of the spec's own scenario, proven as a real change over checks 3 and 4\n"
        "rather than asserted from a single run.",
        f"| check 3 (0 precedent): score {marginal_conf_1['score']:.4f}, "
        f"autonomous {marginal_conf_1['autonomous']}\n"
        f"| check 4 ({confidence.PRECEDENT_FULL_CONFIDENCE_COUNT} precedent): "
        f"score {marginal_conf_2['score']:.4f}, autonomous {marginal_conf_2['autonomous']}\n"
        f"| precedent signal: "
        f"{[s for s in marginal_conf_2['signals'] if s['name'] == 'precedent'][0]}",
    )

    # ---- 5. the score is persisted on the receipt row, not just held on the Outcome object
    with db.tenant_session(thin_id) as cur:
        thin_receipts = store.list_receipts(cur)
    quoted_receipt = next(x for x in thin_receipts if x.action == "quoted")
    r.check(
        "Confidence is written to the receipt and independently retrievable from the database",
        (quoted_receipt.confidence is not None
         and quoted_receipt.confidence["score"] == thin_conf["score"]
         and quoted_receipt.confidence["autonomous"] is False
         # The three WEIGHTED signals are this feature's formula and must all be persisted.
         # `comprehension` (GATE 3c, layer 3) rides along at weight 0.0 — it caps autonomy
         # rather than voting on the score, so asserting the weighted three by name is the
         # honest test here, not a count that changes whenever a cap is added.
         and {s["name"] for s in quoted_receipt.confidence["signals"]}
             >= {"profile_completeness", "floor_proximity", "precedent"}
         and sum(s["weight"] for s in quoted_receipt.confidence["signals"]) == 1.0),
        "A fresh cursor, a fresh query, no reference to the Outcome object from check 1 — this\n"
        "reads the same value back out of PostgreSQL by the receipt's own id. §8 already treats\n"
        "receipts as the audit trail for within_rules; this is the same discipline applied to\n"
        "the confidence score, per the spec's explicit requirement that it be 'auditable in the\n"
        "same way receipts are' rather than computed transiently for display.",
        f"| receipt_id: {quoted_receipt.receipt_id}\n"
        f"| confidence column: {quoted_receipt.confidence}",
    )

    # ---- 6. a second message on the SAME queued thread does not trigger a second autonomous
    # decision. Continues `thin_thread` from check 1 directly (still AWAITING_OWNER_APPROVAL) —
    # `_converse` always opens a fresh thread, so this calls `engine.step` on it directly.
    with db.tenant_session(thin_id) as cur:
        thin_tenant_again = store.get_tenant(cur)
        held_out = engine.step(cur, thin_tenant_again, thin_thread, engine.Inbound(
            body="Still there?", from_address=p3.PROSPECT, from_name="Nadia Okoro"))
        held_thread = held_out.thread
    r.check(
        "A thread already awaiting owner approval is not re-entered by a follow-up message",
        (held_out.action == "hold" and held_thread.state == "AWAITING_OWNER_APPROVAL"
         and held_out.reply is not None and "reviewing" in held_out.reply),
        "Same principle GATE 3 check-pattern already applies to ESCALATED, BOOKED, IGNORED and\n"
        "DEAD threads: once a decision is waiting on the owner, a further message does not get a\n"
        "second autonomous decision stacked on top of it. The prospect gets a holding reply, not\n"
        "silence and not a second drafted-but-unsent quote.",
        f"| second message action: {held_out.action}   state: {held_thread.state}\n"
        f"| reply: {held_out.reply.splitlines()[-1] if held_out.reply else None}",
    )

    # ---- 7. regression: GATE 3's own NEW -> BOOKED journey is unaffected by this feature
    spa_id = p3._onboard(p3.SPA)
    msgs = ["Hi, how much is a deep tissue massage?", "Could you do 80?", "yes please",
            "London", "2"]
    cal = p3.FixtureCalendar()
    _, reg_thread, reg_outs = p3._converse(spa_id, msgs, cal)
    r.check(
        "REGRESSION — GATE 3's baseline NEW -> BOOKED journey still completes with no human",
        (reg_thread.state == "BOOKED" and len(cal.booked) == 1
         and all(o.reply is not None for o in reg_outs)),
        "GATE 3's own fixture tenant (floor, discount cap, escalation triggers, ICP, booking\n"
        "rules and lexicon all set — the profile GATE 3 was always written against) runs the\n"
        "exact same five-message conversation end to end. Every reply sent, nobody queued,\n"
        "state machine and guardrails behaving exactly as GATE 3 already proved. Confidence-\n"
        "scored autonomy is additive: it holds back the decisions it was built to catch (checks\n"
        "1 and 3 above) and leaves this one alone.",
        p3._transcript(msgs, reg_outs) + f"\nfinal state: {reg_thread.state}",
    )

    # ---- honest notes
    r.note(
        "Autonomy thresholds are read from the profile, never inferred",
        f"`profile.autonomy_thresholds` is tenant-settable (at onboarding or later, via the same\n"
        f"`store.update_profile` every other profile edit uses) and defaults to "
        f"{confidence.DEFAULT_AUTONOMY_THRESHOLD} per service when unset — conservative, not\n"
        "permissive, per the spec. No fixture in this gate sets an override: every score above is\n"
        "compared against that documented default.",
        f"DEFAULT_AUTONOMY_THRESHOLD = {confidence.DEFAULT_AUTONOMY_THRESHOLD}\n"
        f"weights: completeness={confidence.WEIGHT_COMPLETENESS}, "
        f"proximity={confidence.WEIGHT_PROXIMITY}, precedent={confidence.WEIGHT_PRECEDENT}",
    )
    r.note(
        "What this gate does not build",
        "Approving or editing a queued draft (the tenant acting on `pending_approval`) is not\n"
        "built here — the owner acts on the drafted text via the same alert channel ESCALATE\n"
        "already uses (email, once Phase 4 is live), exactly as any other escalation today. A\n"
        "dashboard 'approve' button that resumes the thread and sends the edited text is Phase 8\n"
        "territory (a UI over stored state, not a new decision), not a gap in this feature's own\n"
        "claim: drafted, not sent, queued, logged.",
        "",
    )
