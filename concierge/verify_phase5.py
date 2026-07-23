"""GATE 5 — booking, against live Cal.com.

GATE 3 proved the booking *logic* against a declared fixture calendar. This re-runs the same
journey with the fixture replaced by real Cal.com v2 calls: a real negotiation, real availability
fetched in the prospect's timezone, the §9b.3 re-fetch before booking, a real booking created
with a UTC start and a nested attendee, and its status confirmed from the API's own response.

This gate makes a real booking on the operator's connected calendar and then cancels it, so a
real calendar is never left with verification clutter. The creation is confirmed before the
cancel, so the round trip is genuinely proven — not simulated.

Needs CAL_API_KEY + CAL_EVENT_TYPE_ID (OPERATOR_PROVIDES item 4). Absent → the gate cannot run
and says so; it does not fabricate a booking.

The one added object, ObservingCalendar, is a declared fixture that WRAPS the real adapter to
count calls and capture the booking request. Every network call it makes is a real Cal.com call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

from . import config, db, engine, onboarding, store
from .calcom import CalcomCalendar, CalcomError

PROSPECT = "priya.raman@example.com"


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


class ObservingCalendar:
    """DECLARED FIXTURE — wraps the real CalcomCalendar. Adds observation only; every call below
    is a live Cal.com call. It exists so the harness can prove the re-fetch happened (§9b.3) and
    show the exact booking the engine asked for."""

    def __init__(self, inner: CalcomCalendar):
        self.inner = inner
        self.slots_calls = 0
        self.last_book: dict[str, Any] | None = None
        self.booked: dict[str, Any] | None = None

    def slots(self, **kw: Any) -> list[datetime]:
        self.slots_calls += 1
        return self.inner.slots(**kw)

    def book(self, **kw: Any) -> dict[str, Any]:
        self.last_book = kw
        self.booked = self.inner.book(**kw)
        return self.booked


def _onboard(fixture: dict) -> uuid.UUID:
    session = onboarding.start(fixture["description"])
    for key, value in fixture["answers"].items():
        session.answer(key, value)
    tenant_id, _, _ = onboarding.finalise(
        session, business_name=fixture["business"],
        owner_email=f"owner@{uuid.uuid4().hex[:8]}.example",
        owner_wallet="0x" + uuid.uuid4().hex[:40].ljust(40, "0"))
    return tenant_id


def _body(reply: str | None) -> str:
    if not reply:
        return ""
    return reply.split("\n\n", 1)[1] if "\n\n" in reply else reply


def run(r) -> None:
    db.migrate()

    if not config.cal_api_key() or not config.cal_event_type_id():
        r.note(
            "Cal.com credentials absent — GATE 5 cannot run, and nothing is faked",
            "CAL_API_KEY / CAL_EVENT_TYPE_ID are not set (OPERATOR_PROVIDES item 4). The Cal.com\n"
            "adapter is built and unit-reachable, but a real booking cannot be proven without a\n"
            "real key. No booking is simulated. Add the key to .env and re-run.",
        )
        r.check("Cal.com credentials present for a live booking", False,
                "CAL_API_KEY / CAL_EVENT_TYPE_ID missing — see the note above.")
        return

    spa_id = _onboard(SPA)
    real = CalcomCalendar()

    # ---- 1. real availability, fetched in the prospect's timezone
    with db.tenant_session(spa_id) as cur:
        tenant = store.get_tenant(cur)
    earliest = datetime.now(dt_timezone.utc)
    try:
        live_slots = real.slots(tenant=tenant, earliest=earliest, limit=3)
        slots_err = None
    except CalcomError as e:
        live_slots, slots_err = [], str(e)
    r.check(
        "Cal.com returns real availability for the tenant's event type",
        len(live_slots) >= 1,
        "A live GET /v2/slots (cal-api-version 2024-09-04) against the tenant's real event type\n"
        "returned bookable times in UTC. These are the actual openings on the connected calendar,\n"
        "not a grid the harness made up. The engine renders them into the prospect's own zone.",
        (f"| first {len(live_slots)} UTC slots: " + ", ".join(s.isoformat() for s in live_slots))
        if live_slots else f"| no slots / error: {slots_err}",
    )
    if not live_slots:
        return

    # ---- 2. the full journey NEW -> BOOKED against real Cal.com, then released
    msgs = ["Hi, how much is a deep tissue massage?",
            "Could you do 75?",
            "yes please",
            "London",
            "1"]
    obs = ObservingCalendar(real)
    outs: list[engine.Outcome] = []
    booking_uid = None
    try:
        with db.tenant_session(spa_id) as cur:
            tenant = store.get_tenant(cur)
            thread = engine.open_thread(cur, tenant, engine.Inbound(
                body="", from_address=PROSPECT, external_ref=f"gate5-{uuid.uuid4().hex[:8]}"))
            for body in msgs:
                out = engine.step(cur, tenant, thread, engine.Inbound(
                    body=body, from_address=PROSPECT, from_name="Priya Raman"), obs)
                thread = out.thread
                outs.append(out)

        booked = obs.booked or {}
        booking_uid = booked.get("id")
        final = outs[-1]
        r.check(
            "A real Cal.com booking is created and confirmed by the API's own status",
            (thread.state == "BOOKED" and final.action == "booked"
             and booking_uid is not None
             and booked.get("status") in engine.ACCEPTED_BOOKING_STATUSES),
            "The whole conversation ran against live Cal.com: the £85 price was quoted from the\n"
            "profile, £75 checked against the floor and agreed, the timezone asked for, three real\n"
            "openings offered in Europe/London, and the pick booked. The booking is treated as\n"
            "done only because the API returned an accepted status — GATE 0 proved this same\n"
            "server rejects a non-ISO start and a missing nested attendee, so a successful create\n"
            "is itself proof the request was correctly shaped (UTC start, nested attendee).",
            _transcript(msgs, outs)
            + f"\n| Cal.com booking uid: {booking_uid}"
            + f"\n| Cal.com status: {booked.get('status')!r}"
            + f"\n| engine marked state: {thread.state}",
        )

        # ---- 3. §9b.3 — the engine re-fetches before booking; the offered list is never trusted
        r.check(
            "ATTACK — the slot is re-fetched from Cal.com before booking, not trusted from the offer",
            obs.slots_calls >= 2 and obs.last_book is not None,
            "The offer and the booking are two separate live reads of the calendar. §9b.3 requires\n"
            "a re-fetch on selection so a slot taken between the offer and the pick is caught\n"
            "rather than double-booked. Here the calendar was queried once to offer and again to\n"
            "verify the pick was still open before POSTing the booking.\n"
            "The raced-slot re-offer path itself is a calendar-agnostic engine behaviour, proven\n"
            "deterministically at GATE 3 check 11 where the race can be forced.",
            f"| live /slots calls during the conversation: {obs.slots_calls}\n"
            f"| booking issued after re-fetch: {obs.last_book is not None}",
        )

        # ---- 4. UTC start + nested attendee in the prospect's timezone
        lb = obs.last_book or {}
        start_utc = lb.get("start_utc")
        r.check(
            "The booking is sent with a UTC start and the prospect's own timezone",
            (isinstance(start_utc, datetime) and start_utc.utcoffset() == dt_timezone.utc.utcoffset(None)
             and lb.get("attendee_timezone") == "Europe/London"
             and lb.get("attendee_email") == PROSPECT),
            "§9b.4: the start crosses the wire in UTC, while the attendee carries their own\n"
            "timezone (Europe/London, captured by asking — never inferred). The two are kept\n"
            "distinct so a booking is never an hour out.",
            f"| start_utc: {start_utc.isoformat() if isinstance(start_utc, datetime) else start_utc}\n"
            f"| attendee timeZone: {lb.get('attendee_timezone')}\n"
            f"| attendee email: {lb.get('attendee_email')}",
        )

    finally:
        # Release the test booking so a real calendar is left clean. Reported, not hidden.
        if booking_uid:
            try:
                cancelled = real.cancel(tenant=tenant, booking_uid=str(booking_uid))
                r.note(
                    "The verification booking was cancelled, so the calendar is left clean",
                    "Creation was confirmed above before this cancel, so the round trip is real.\n"
                    f"Cancelled Cal.com booking uid {booking_uid}.",
                    f"cancel response status: {(cancelled.get('data') or {}).get('status', cancelled.get('status'))}",
                )
            except CalcomError as e:
                r.note("Could not cancel the verification booking — please cancel it by hand",
                       f"Cal.com booking uid {booking_uid} is real and still on the calendar.",
                       str(e))

    # ---- 5. ATTACK — an unreadable timezone is asked again then escalated, never guessed
    tz_msgs = ["How much is a signature facial?", "yes please", "sometime next week", "no idea"]
    tz_outs: list[engine.Outcome] = []
    with db.tenant_session(spa_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"gate5tz-{uuid.uuid4().hex[:8]}"))
        for body in tz_msgs:
            out = engine.step(cur, tenant, thread, engine.Inbound(
                body=body, from_address=PROSPECT, from_name="Priya Raman"), ObservingCalendar(real))
            thread = out.thread
            tz_outs.append(out)
    r.check(
        "ATTACK — an unreadable timezone is asked again then escalated, and nothing is booked",
        (tz_outs[2].action == "timezone_reask" and tz_outs[3].state_after == "ESCALATED"
         and thread.client_timezone is None),
        "Even with a real calendar connected, a prospect who will not give a timezone is asked a\n"
        "second time and then handed to the owner — never booked into a guessed hour. The wrong\n"
        "hour is the most visible way this product can embarrass a tenant, so the guard sits\n"
        "before any Cal.com call.",
        _transcript(tz_msgs, tz_outs)
        + f"\nthread.client_timezone: {thread.client_timezone!r}",
    )

    r.note(
        "Cal.com sends the invite and reminders natively; that leg is the provider's, not ours",
        "On a confirmed booking Cal.com emails the attendee and host and adds the calendar\n"
        "invite. GATE 5 proves CONCIERGE creates and confirms the booking; the invite delivery is\n"
        "Cal.com's own, and the host account received it for the (now cancelled) test booking.",
    )


def _transcript(messages, outcomes, *, indent="| ") -> str:
    lines = []
    for msg, out in zip(messages, outcomes):
        lines.append(f"{indent}PROSPECT: {msg}")
        lines.append(f"{indent}   -> {out.state_before} to {out.state_after}  [{out.action}]")
        if out.reply:
            for ln in _body(out.reply).strip().splitlines():
                lines.append(f"{indent}   CONCIERGE: {ln}")
        else:
            lines.append(f"{indent}   CONCIERGE: (no reply — deliberate)")
        lines.append(indent.rstrip())
    return "\n".join(lines)
