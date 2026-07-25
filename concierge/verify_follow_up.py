"""the follow-up suite — Safe Follow-Up.

Proves the feature does exactly what the addendum scoped it to do, and NOTHING more: a thread
that already has a real prospect on it gets nudged once, referencing what was actually quoted,
after it goes quiet — and a thread with no genuine prior contact cannot trigger one, however the
clock is pushed. Check 3 is the one that matters most: it is the negative test that proves the
cold-outbound boundary holds in code, not just in the module's docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from . import db, engine, followup, store
from . import verify_engine as p3
from . import verify_email as p4


def run(r) -> None:
    db.migrate()

    tenant_id = p3._onboard(p3.SPA)

    # ---- 1. a real thread, quoted, waiting on the prospect
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=p3.PROSPECT, external_ref=f"fu-{uuid.uuid4().hex[:8]}"))
        out = engine.step(cur, tenant, thread, engine.Inbound(
            body="How much is a deep tissue massage?", from_address=p3.PROSPECT,
            from_name="Nadia Okoro"))
        thread = out.thread
    real_last_out = followup._last_out_at(thread)

    quiet_hours, dead_after_hours = followup._policy(tenant.profile or {})
    just_past_quiet = real_last_out + timedelta(hours=quiet_hours, minutes=1)

    mailer = p4.RecordingMailer()
    results = followup.dispatch(tenant_id, mailer=mailer, now=just_past_quiet)
    followed_up = next((x for x in results if x.thread.thread_id == thread.thread_id), None)

    r.check(
        "A stalled thread gets a follow-up referencing what was ACTUALLY quoted on it",
        (followed_up is not None and followed_up.action == "follow_up_sent"
         and followed_up.email is not None
         and followed_up.email.to_address == thread.client_contact
         and "Deep tissue massage" in followed_up.email.text_body
         and "85" in followed_up.email.text_body
         and followed_up.email.text_body.startswith("This is an AI assistant")
         and len(mailer.sent) == 1 and mailer.sent[0] is followed_up.email),
        "The thread went quiet just past this tenant's own quiet-period setting. The follow-up\n"
        "is drafted by `engine.render` from THIS thread's own current offer — the exact service\n"
        "and price already quoted, £85 for the deep tissue massage — not a generic 'checking in'\n"
        "template disconnected from what was discussed. It goes to the SAME contact, from the\n"
        "SAME connector (`RecordingMailer` here stands in for `postmark.PostmarkMailer` exactly\n"
        "as it does in the email suite), and the AI disclosure still leads.",
        f"| to: {followed_up.email.to_address if followed_up else None}\n"
        f"| body: {followed_up.email.text_body if followed_up else None}\n"
        f"| receipt action: {followed_up.receipt.action if followed_up else None}",
    )

    # ---- 2. still no reply, a second longer period later -> DEAD
    just_past_dead = just_past_quiet + timedelta(hours=dead_after_hours, minutes=1)
    results2 = followup.dispatch(tenant_id, mailer=mailer, now=just_past_dead)
    died = next((x for x in results2 if x.thread.thread_id == thread.thread_id), None)
    r.check(
        "Still no reply after the second, longer period -> the thread is marked DEAD",
        (died is not None and died.action == "marked_dead" and died.email is None
         and died.thread.state == "DEAD" and len(mailer.sent) == 1),
        "No second message is sent — 'DEAD' is an honest internal record that this lead went\n"
        "quiet (useful signal for the tenant's the scheduler summary), not another nudge. Only one\n"
        "email total was ever sent across both checks, confirmed by the recording mailer.",
        f"| action: {died.action if died else None}   state: {died.thread.state if died else None}\n"
        f"| total emails sent so far: {len(mailer.sent)}",
    )

    # ---- 3. ATTACK — no real prior contact, no follow-up, however far the clock is pushed
    # A thread constructed directly (bypassing engine.step entirely, so no real inbound message
    # was EVER received from this contact) — simulating the one way this boundary could be
    # bypassed: something other than a genuine inbound message getting a thread into
    # AWAITING_REPLY. `_has_real_contact` must catch this on the thread's own data, not on trust.
    with db.tenant_session(tenant_id) as cur:
        ghost = store.create_thread(
            cur, tenant_id=tenant_id, client_contact="ghost@example.com",
            client_name="Nobody", external_ref=f"ghost-{uuid.uuid4().hex[:8]}", state="NEW")
        ghost.state = "AWAITING_REPLY"
        ancient = (datetime.now(dt_timezone.utc) - timedelta(days=3650)).isoformat()
        ghost.history = [
            {"at": ancient, "direction": "out", "state": "AWAITING_REPLY",
             "action": "quoted", "text": "a quote nobody actually asked for"},
        ]
        ghost.current_offer = {"quote": {"service": "Deep tissue massage", "amount": 85,
                                          "currency": "GBP", "rendered": "£85"},
                                "awaiting": "reply"}
        ghost = store.save_thread(cur, ghost) or ghost

    far_future = datetime.now(dt_timezone.utc) + timedelta(days=3650)
    mailer2 = p4.RecordingMailer()
    results3 = followup.dispatch(tenant_id, mailer=mailer2, now=far_future)
    ghost_touched = any(x.thread.thread_id == ghost.thread_id for x in results3)
    with db.tenant_session(tenant_id) as cur:
        ghost_after = store.get_thread(cur, ghost.thread_id)
    r.check(
        "ATTACK — a thread with no genuine prior client message can NEVER trigger a follow-up",
        (not ghost_touched and not mailer2.sent
         and ghost_after.state == "AWAITING_REPLY"
         and not followup._has_real_contact(ghost_after)),
        "This thread has an 'out' message 3,650 days old (any real quiet/dead threshold is long\n"
        "since passed) and is sitting in AWAITING_REPLY — by timing alone it would qualify twice\n"
        "over. But its history has no 'direction: in' entry: nobody with this contact ever\n"
        "actually wrote in. `_has_real_contact` catches this on the thread's own stored data,\n"
        "not on a caller's promise that the thread is legitimate — pushed 10 years into the\n"
        "future, it is still never touched, and no email is ever sent to a contact who never\n"
        "reached out. This is Safe Follow-Up's entire boundary against cold outbound, proven as\n"
        "a negative, not just asserted.",
        f"| ghost thread history: {ghost_after.history}\n"
        f"| ghost thread state after 10 years: {ghost_after.state}\n"
        f"| emails sent to the ghost contact: {mailer2.sent}",
    )

    # ---- honest notes
    r.note(
        "What this feature explicitly does not build",
        "Cold outbound — emailing a contact with no existing thread — has no code path here at\n"
        "all, by the addendum's own explicit instruction (§0.3). `dispatch` only ever reads\n"
        "threads that already exist for a tenant (`store.list_threads`); there is no function in\n"
        "this module that accepts a bare address. If a future request asks for one, the correct\n"
        "answer is to decline and point at `concierge/followup.py`'s own module docstring.",
        f"quiet_hours default: {followup.DEFAULT_QUIET_HOURS}, "
        f"dead_after_hours default: {followup.DEFAULT_DEAD_AFTER_HOURS} "
        "(tenant-overridable via profile.follow_up_policy, never inferred)",
    )
