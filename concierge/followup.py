"""Safe Follow-Up (addendum) — re-engaging a THREAD THAT ALREADY EXISTS. Not cold outbound.

The master spec named a `FOLLOW_UP` state ("quiet N days → FOLLOW_UP") and never built it out.
This module builds exactly that, scoped strictly to threads CONCIERGE already has a real,
inbound-started conversation on — never a cold introduction to a stranger.

## The hard boundary — enforced here, not just in this docstring

A follow-up may be sent only to a thread that already carries at least one message with
`direction == "in"` in its own history. `_has_real_contact` checks that directly on the thread
object every time, independent of how the row was created — it does not trust a caller's promise
that a thread is legitimate, and it does not trust `state == AWAITING_REPLY` alone (a state a
future bug, or a hostile caller, could set without a real inbound ever having happened). There is
no function in this module that accepts a bare email address and a "send an intro" instruction.
If asked to build that path, decline and point at this paragraph — it is cold outbound, explicitly
out of scope (§0.3 of the addendum): consent law (CAN-SPAM/GDPR opt-in), shared-domain reputation
risk, and a shakier AI-disclosure position than replying to a thread the prospect started.

## Mechanics

A background worker (§12 of the master spec) calls `dispatch` per tenant. For each thread still
`AWAITING_REPLY` — CONCIERGE's own reply sent, nothing back — past the tenant's configured quiet
period, it drafts a follow-up FROM THAT THREAD'S OWN HISTORY (via `engine.render`, so it inherits
every existing discipline: the AI disclosure leads, the current offer or agreed figure is what's
referenced, never a template disconnected from context) and sends it via the same connector, from
the same address, to the same contact already on the thread. If a follow-up was already sent and a
second, longer period has since passed with still no reply, the thread is marked `DEAD` — no
further message, just an honest record that this one went quiet (visible in the scheduler's summary).

## What is deliberately absent

No new decision logic. `due_threads` is arithmetic over stored timestamps — never a model guessing
whether "now" is a good time to nudge someone. The drafted text comes from `engine.render`, the
same function that has never been allowed to invent a number or a name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from psycopg import Cursor

from . import db, engine, receipts, store
from .models import Receipt, Tenant, Thread
from .postmark import Mailer, OutboundEmail

# Conservative business defaults, tenant-overridable via `profile.follow_up_policy` — the same
# seam Feature 2's `autonomy_thresholds` and Feature 5's `floor_curve` use: a profile field set
# at onboarding or in settings, never inferred.
DEFAULT_QUIET_HOURS = 48         # ~2 working days of silence before a single nudge
DEFAULT_DEAD_AFTER_HOURS = 168   # a further 5 days of silence after the nudge -> DEAD


def _policy(profile: dict[str, Any]) -> tuple[float, float]:
    p = profile.get("follow_up_policy") or {}
    quiet = float(p.get("quiet_hours", DEFAULT_QUIET_HOURS))
    dead_after = float(p.get("dead_after_hours", DEFAULT_DEAD_AFTER_HOURS))
    return quiet, dead_after


def _has_real_contact(thread: Thread) -> bool:
    """THE boundary. True only if a real prospect message is actually in this thread's history."""
    return any(
        m.get("direction") == "in" and (m.get("from") or "").strip()
        for m in (thread.history or [])
    )


def _last_out_at(thread: Thread) -> datetime | None:
    for m in reversed(thread.history or []):
        if m.get("direction") == "out" and m.get("at"):
            return datetime.fromisoformat(m["at"])
    return None


@dataclass(frozen=True)
class Due:
    """One thread that needs action right now, and which action."""

    thread: Thread
    kind: str   # "follow_up" | "dead"


def due_threads(threads: list[Thread], profile: dict[str, Any], *,
                now: datetime | None = None) -> list[Due]:
    """Pure: which threads are due a follow-up or due to be marked DEAD, as of `now`.

    `now` is a parameter rather than always `datetime.now()` so the harness can prove multi-day
    behaviour deterministically, without sleeping in a test for a week.
    """
    now = now or datetime.now(dt_timezone.utc)
    quiet_hours, dead_after_hours = _policy(profile)
    out: list[Due] = []
    for thread in threads:
        if thread.state != "AWAITING_REPLY":
            continue
        if not _has_real_contact(thread):
            continue    # the boundary — a thread with no genuine inbound message is never due
        last_out = _last_out_at(thread)
        if last_out is None:
            continue
        followed_up_raw = (thread.current_offer or {}).get("follow_up_sent_at")
        if followed_up_raw:
            followed_up_at = datetime.fromisoformat(followed_up_raw)
            if now - followed_up_at >= timedelta(hours=dead_after_hours):
                out.append(Due(thread, "dead"))
        elif now - last_out >= timedelta(hours=quiet_hours):
            out.append(Due(thread, "follow_up"))
    return out


def draft(tenant: Tenant, thread: Thread) -> str:
    """The follow-up body — built by `engine.render`, so it inherits every existing rule: the
    disclosure leads, the current offer/agreed figure is what gets referenced, every noun comes
    from the tenant's own profile. No formatting logic is duplicated here."""
    decision = engine.Decision(
        state_after=thread.state, action="follow_up", rule_checked="follow_up_policy",
        within_rules=True, detail={}, reply_body=engine.PROSE["follow_up"],
    )
    return engine.render(tenant, decision, thread)


@dataclass
class FollowUpResult:
    thread: Thread
    action: str                    # "follow_up_sent" | "marked_dead"
    email: OutboundEmail | None     # None for "marked_dead" — no message is sent, just a record
    receipt: Receipt


def process_tenant(cur: Cursor, tenant: Tenant, *,
                    now: datetime | None = None) -> list[FollowUpResult]:
    """DB-side work only — decide, persist, write a receipt for each due thread. Returns drafted
    emails for the caller to send OUTSIDE any open transaction, mirroring `mail.handle_inbound`'s
    separation of database work from network I/O.
    """
    now = now or datetime.now(dt_timezone.utc)
    now_iso = now.isoformat()
    threads = store.list_threads(cur)
    due = due_threads(threads, tenant.profile or {}, now=now)

    results: list[FollowUpResult] = []
    for item in due:
        thread = item.thread
        if item.kind == "follow_up":
            body = draft(tenant, thread)
            offer = dict(thread.current_offer or {})
            offer["follow_up_sent_at"] = now_iso
            thread.current_offer = offer
            thread.history = list(thread.history or []) + [
                {"at": now_iso, "direction": "out", "state": thread.state,
                 "action": "follow_up", "text": body},
            ]
            thread = store.save_thread(cur, thread) or thread
            receipt = receipts.record(
                cur, tenant_id=tenant.tenant_id, thread_id=thread.thread_id,
                action="follow_up_sent", rule_checked="follow_up_policy.quiet_hours",
                within_rules=True,
                decision={
                    "sent_at": now_iso, "reply_sent": body,
                    "client_contact": thread.client_contact,
                },
            )
            email = OutboundEmail(
                from_address=tenant.inbound_address, to_address=thread.client_contact,
                subject="Following up", text_body=body, reply_to=tenant.inbound_address,
            )
            results.append(FollowUpResult(
                thread=thread, action="follow_up_sent", email=email, receipt=receipt))
        else:  # "dead"
            thread.state = "DEAD"
            thread.history = list(thread.history or []) + [
                {"at": now_iso, "direction": "system", "state": "DEAD",
                 "action": "marked_dead", "text": None},
            ]
            thread = store.save_thread(cur, thread) or thread
            receipt = receipts.record(
                cur, tenant_id=tenant.tenant_id, thread_id=thread.thread_id,
                action="marked_dead", rule_checked="follow_up_policy.dead_after_hours",
                within_rules=True,
                decision={"marked_dead_at": now_iso, "client_contact": thread.client_contact},
            )
            results.append(FollowUpResult(
                thread=thread, action="marked_dead", email=None, receipt=receipt))
    return results


def dispatch(tenant_id, *, mailer: Mailer, now: datetime | None = None) -> list[FollowUpResult]:
    """The worker entry point (§12 of the master spec) for one tenant: DB work, then network,
    with no transaction held open across the send — same discipline as `mail.handle_inbound`.
    """
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        results = process_tenant(cur, tenant, now=now)

    for result in results:
        if result.email is not None:
            mailer.send(result.email)
    return results
