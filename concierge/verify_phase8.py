"""GATE 8 — the tenant summary and the scheduled-worker jobs.

Every number in the summary is read back from real threads and receipts that real conversations
in this gate actually wrote — never seeded or hand-built to look right. Check 5 is deliberately
careful about cost: `anchor_pending`'s mechanism (`receipts.anchor()`) already spends real,
tiny mainnet gas and is proven twice over by GATE 6 and GATE 6b, so this gate does not anchor
anything new — it proves the SCHEDULER's honest-skip path (no signer configured) instead, with the
real X Layer credentials in this environment deliberately, temporarily cleared and restored.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from . import db, engine, followup, scheduler, store, summary
from . import verify_phase3 as p3
from . import verify_phase3b as p3b
from . import verify_phase4 as p4


class _NoXLayerCreds:
    """Temporarily blanks the real X Layer credentials this environment has configured (used by
    GATE 6/6b), so `anchor_pending`'s honest-skip path can be exercised deterministically without
    spending any new real mainnet gas. Restores the original values on exit, always."""

    KEYS = ("XLAYER_PRIVATE_KEY", "XLAYER_CONTRACT")

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ[k] = ""
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def run(r) -> None:
    db.migrate()

    # ---- 1. a real conversation's numbers show up in the summary, exactly
    spa_id = p3._onboard(p3.SPA)
    msgs = ["Hi, how much is a deep tissue massage?", "Could you do 80?", "yes please",
            "London", "2"]
    cal = p3.FixtureCalendar()
    _, thread, outs = p3._converse(spa_id, msgs, cal)

    since = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    with db.tenant_session(spa_id) as cur:
        tenant = store.get_tenant(cur)
        threads = store.list_threads(cur)
        receipts_ = store.list_receipts(cur)
    s = summary.build_summary(threads, receipts_, since=since)
    r.check(
        "The summary's numbers are counted from real receipts this gate just wrote, not asserted",
        (s.inquiries == 1 and s.quotes_sent == 1 and s.negotiations == 1
         and s.bookings == 1 and abs(s.booked_value - 80.0) < 0.01),
        "One real conversation ran end to end: a quote, a negotiated counter (£85 down to £80,\n"
        "within the guardrail), and a booking. `build_summary` is pure arithmetic over the\n"
        "`store.list_threads`/`store.list_receipts` rows this conversation actually produced —\n"
        "the £80 booked value is read back from `receipts.decision.detail.agreed`, the same\n"
        "field GATE 3 already proves is derived from the tenant's own floor, never invented.",
        f"| {summary.render_summary_text(tenant, s)}",
    )

    # ---- 2. an escalation is counted, and its verbatim text is carried into the summary
    push_outs = p3._converse(spa_id, ["How much is a signature facial?", "I can only do 40"])[2]
    with db.tenant_session(spa_id) as cur:
        threads2 = store.list_threads(cur)
        receipts2 = store.list_receipts(cur)
    s2 = summary.build_summary(threads2, receipts2, since=since)
    r.check(
        "A floor breach is counted as an escalation, with the prospect's own words carried verbatim",
        (s2.escalations == 1
         and any("40" in ex for ex in s2.escalation_examples)),
        "£40 against a £70 floor breaches, exactly as GATE 3 check 7 already proves — this gate\n"
        "checks that the breach ALSO surfaces in the owner-facing summary, with the prospect's\n"
        "real message, not a synthesized example. Never fabricated: the text is\n"
        "`decision['inbound_body']`, the exact string the prospect sent.",
        f"| escalations: {s2.escalations}\n| examples: {s2.escalation_examples}",
    )

    # ---- 3. a Feature-2-queued reply is counted separately from an autonomous one
    thin_id = p3._onboard(p3b.THIN)
    p3._converse(thin_id, ["How much is a deep tissue massage?"])
    with db.tenant_session(thin_id) as cur:
        threads3 = store.list_threads(cur)
        receipts3 = store.list_receipts(cur)
    s3 = summary.build_summary(threads3, receipts3, since=since)
    r.check(
        "A reply queued for owner approval (Feature 2) is visible in the summary as its own count",
        s3.queued_for_approval == 1 and s3.quotes_sent == 1,
        "This tenant's profile is deliberately thin (GATE 3b-2's own fixture) — the quote is\n"
        "still counted as a quote, and separately flagged as held for approval, so an owner\n"
        "reading the summary can tell 'CONCIERGE answered this' from 'CONCIERGE answered this,\n"
        "but only after I looked at it', which is precisely the distinction Feature 2 exists to\n"
        "make visible.",
        f"| quotes_sent: {s3.quotes_sent}, queued_for_approval: {s3.queued_for_approval}",
    )

    # ---- 4. the scheduler's own jobs: follow-up dispatch and DEAD-marking feed the same summary
    follow_id = p3._onboard(p3.SPA)
    with db.tenant_session(follow_id) as cur:
        f_tenant = store.get_tenant(cur)
        f_thread = engine.open_thread(cur, f_tenant, engine.Inbound(
            body="", from_address=p3.PROSPECT, external_ref=f"sched-{uuid.uuid4().hex[:8]}"))
        out = engine.step(cur, f_tenant, f_thread, engine.Inbound(
            body="How much is a deep tissue massage?", from_address=p3.PROSPECT,
            from_name="Nadia Okoro"))
        f_thread = out.thread
    quiet_hours, dead_after_hours = followup._policy(f_tenant.profile or {})
    last_out = followup._last_out_at(f_thread)

    mailer = p4.RecordingMailer()
    with _NoXLayerCreds():
        scheduler.dispatch(follow_id, mailer=mailer,
                            now=last_out + timedelta(hours=quiet_hours, minutes=1))
        result_dead = scheduler.dispatch(
            follow_id, mailer=mailer,
            now=last_out + timedelta(hours=quiet_hours + dead_after_hours, minutes=2))

    with db.tenant_session(follow_id) as cur:
        threads4 = store.list_threads(cur)
        receipts4 = store.list_receipts(cur)
    s4 = summary.build_summary(threads4, receipts4, since=since)
    r.check(
        "The scheduler's own follow-up and DEAD-marking jobs feed the same real summary",
        (s4.follow_ups_sent == 1 and s4.threads_gone_dead == 1
         and any(res.action == "marked_dead" for res in result_dead.follow_up_results)),
        "`scheduler.dispatch` is the single per-tenant entry point Phase 8 adds: it calls\n"
        "`followup.process_tenant` (GATE 3b-4's own mechanism, unchanged) on a schedule instead\n"
        "of on demand, and the resulting receipts are the same rows `build_summary` already\n"
        "counts — no separate reporting path that could drift from what actually happened.",
        f"| follow_ups_sent: {s4.follow_ups_sent}, threads_gone_dead: {s4.threads_gone_dead}",
    )

    # ---- 5. the scheduler's anchor job skips honestly with no signer configured
    with db.tenant_session(spa_id) as cur:
        with _NoXLayerCreds():
            anchored_now = scheduler.anchor_pending(cur, tenant)
    r.check(
        "The scheduler's anchoring job skips honestly, never fabricates a signature or a tx hash",
        anchored_now == [],
        "With no X Layer signer/contract configured, `anchor_pending` returns an empty list\n"
        "rather than raising or inventing a placeholder. This gate deliberately does not spend\n"
        "any NEW real mainnet gas proving the anchoring mechanism itself works — GATE 6 and GATE\n"
        "6b already do that, repeatedly, against the real deployed contract. What's provable\n"
        "here, cheaply and repeatably, is that the scheduled job degrades exactly as honestly as\n"
        "every other missing-credential path in this codebase.",
        f"| anchor_pending() with no credentials -> {anchored_now}",
    )

    # ---- 6. a due summary is sent once, and the policy timestamp prevents a re-send in-period
    digest_id = p3._onboard(p3.SPA)
    p3._converse(digest_id, ["Hi, how much is a deep tissue massage?"])
    mailer2 = p4.RecordingMailer()
    with _NoXLayerCreds():
        run1 = scheduler.dispatch(digest_id, mailer=mailer2)
        run2 = scheduler.dispatch(digest_id, mailer=mailer2)
    with db.tenant_session(digest_id) as cur:
        profile_after = store.get_tenant(cur).profile
    r.check(
        "A due summary is generated and sent once; an immediate second run does not re-send it",
        (run1.summary_due is True and run2.summary_due is False
         and len(mailer2.sent) == 1
         and profile_after.get("summary_policy", {}).get("last_sent_at") is not None),
        "First run: no `summary_policy.last_sent_at` on the profile yet, so a summary is due,\n"
        "built from real data, and queued as an email to the owner. The DB write recording that\n"
        "timestamp happens in the same transaction as the rest of the run's decisions — read\n"
        "back here from a fresh query, not the in-memory object — so a second run moments later\n"
        "correctly sees a summary is NOT due yet.",
        f"| run 1 summary_due: {run1.summary_due}, run 2 summary_due: {run2.summary_due}\n"
        f"| emails sent: {len(mailer2.sent)}\n"
        f"| profile.summary_policy: {profile_after.get('summary_policy')}",
    )

    # ---- 7. ISOLATION — two tenants' summaries never mix
    other_id = p3._onboard(p3.LEGAL)
    p3._converse(other_id, ["Do you handle unfair dismissal claims, and what's your fee?"])
    with db.tenant_session(spa_id) as cur:
        spa_receipts_final = store.list_receipts(cur)
    with db.tenant_session(other_id) as cur:
        other_threads = store.list_threads(cur)
        other_receipts = store.list_receipts(cur)
    s_other = summary.build_summary(other_threads, other_receipts, since=since)
    spa_ids = {x.receipt_id for x in spa_receipts_final}
    other_ids = {x.receipt_id for x in other_receipts}
    r.check(
        "ISOLATION — a barrister's summary contains none of the spa's receipts, or vice versa",
        not (spa_ids & other_ids) and s_other.quotes_sent == 1,
        "`build_summary` only ever sees the rows its caller fetched via an RLS-scoped\n"
        "`tenant_session` — the same fence GATE 1 proved holds for every other table. There is\n"
        "no code path here that reads across tenants to build one report.",
        f"| receipt id overlap: {len(spa_ids & other_ids)}\n"
        f"| barrister summary quotes_sent: {s_other.quotes_sent}",
    )
