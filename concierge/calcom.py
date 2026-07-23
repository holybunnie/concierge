"""The Cal.com v2 adapter (Phase 5) — the real thing behind the `engine.Calendar` seam.

GATE 3 built the booking logic against a declared fixture. This fills the same seam with live
Cal.com calls, so the slot-race re-fetch, the prospect-timezone rendering and the "confirm via
the API response, never assume" rule all now run against a real calendar.

Verified live before this was written (see docs/VERIFICATION_LEDGER.md):

  slots    GET  /v2/slots     cal-api-version: 2024-09-04
           → {"status":"success","data":{"2026-07-27":[{"start":"2026-07-27T08:00:00.000Z"}]}}
           a dict keyed by date, each value a list of {start} in UTC (trailing Z).

  bookings POST /v2/bookings  cal-api-version: 2026-02-25
           → start as ISO-8601 UTC, attendee{name,email,timeZone} NESTED. Confirmed at GATE 0
           by the server stating its own contract on an empty body.

Both versions are pinned. GATE 0 proved a stale pin silently downgrades to a different schema
rather than erroring, so the versions live in one place and are asserted, never assumed.

Standard library only, like postmark.py: this signs commitments, so every dependency is weighed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone as dt_timezone
from typing import Any

from . import config
from .models import Tenant, Thread

CAL_BASE = "https://api.cal.com/v2"
SLOTS_VERSION = "2024-09-04"
BOOKINGS_VERSION = "2026-02-25"
USER_AGENT = "CONCIERGE/0.5 (+https://github.com/holybunnie/concierge)"


class CalcomError(RuntimeError):
    """Cal.com returned something we will not read as success. Never swallowed into a fake booking."""


def _calendar_ref(tenant: Tenant) -> dict[str, Any]:
    return (tenant.profile or {}).get("calendar_ref") or {}


def _credentials(tenant: Tenant) -> tuple[str, str]:
    """(api_key, event_type_id) from the tenant's own calendar_ref, or the operator env fallback.

    Per-tenant keys are the data model (§8); the env fallback is what lets the single-operator
    demo run without persisting a live key into a profile. Missing → raise, never guess.
    """
    ref = _calendar_ref(tenant)
    api_key = ref.get("cal_api_key") or config.cal_api_key()
    event_type_id = ref.get("event_type_id") or config.cal_event_type_id()
    if not api_key or not event_type_id:
        raise CalcomError(
            "No Cal.com credentials for this tenant (calendar_ref.cal_api_key / event_type_id, "
            "or CAL_API_KEY / CAL_EVENT_TYPE_ID). CONCIERGE will not invent an availability."
        )
    return str(api_key), str(event_type_id)


def _request(method: str, path: str, *, api_key: str, version: str,
             query: str = "", body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{CAL_BASE}{path}" + (f"?{query}" if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, method=method, data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "cal-api-version": version,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise CalcomError(f"Cal.com {method} {path} → HTTP {e.code}: {detail}") from e


def _parse_utc(value: str) -> datetime:
    """'2026-07-27T08:00:00.000Z' → an aware UTC datetime. Z is normalised for fromisoformat."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt_timezone.utc)


class CalcomCalendar:
    """Implements the `engine.Calendar` protocol against live Cal.com. Reads credentials per call
    from the tenant, so one adapter instance serves every tenant with its own calendar."""

    def slots(self, *, tenant: Tenant, earliest: datetime, limit: int) -> list[datetime]:
        api_key, event_type_id = _credentials(tenant)
        # Cal.com bounds a query by date. Two weeks from `earliest` is plenty to offer three
        # times while staying well inside the rate limit (one call per offer/re-fetch).
        start = earliest.astimezone(dt_timezone.utc)
        end = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end.fromordinal(end.toordinal() + 14)
        query = (f"eventTypeId={event_type_id}"
                 f"&start={start.date().isoformat()}&end={end.date().isoformat()}")
        payload = _request("GET", "/slots", api_key=api_key, version=SLOTS_VERSION, query=query)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise CalcomError(f"Unexpected /slots shape: {json.dumps(payload)[:300]}")

        starts: list[datetime] = []
        for _day, entries in data.items():
            for entry in entries or []:
                raw = entry.get("start") if isinstance(entry, dict) else entry
                if raw:
                    starts.append(_parse_utc(raw))
        # Earliest-first, honouring the notice window the engine already applied via `earliest`.
        starts = sorted(s for s in starts if s >= start)
        return starts[:limit]

    def book(self, *, tenant: Tenant, thread: Thread, start_utc: datetime,
             attendee_name: str, attendee_email: str, attendee_timezone: str,
             notes: str) -> dict[str, Any]:
        api_key, event_type_id = _credentials(tenant)
        body = {
            "start": start_utc.astimezone(dt_timezone.utc)
                     .isoformat().replace("+00:00", "Z"),
            "eventTypeId": int(event_type_id),
            "attendee": {                       # nested, per the verified contract
                "name": attendee_name,
                "email": attendee_email,
                "timeZone": attendee_timezone,
            },
            "metadata": {"concierge": (notes or "")[:480]},
        }
        payload = _request("POST", "/bookings", api_key=api_key,
                           version=BOOKINGS_VERSION, body=body)
        data = payload.get("data") or {}
        # Normalise to what engine._book asserts on: an id and a status. The engine checks the
        # status against ACCEPTED_BOOKING_STATUSES and escalates on anything else — success is
        # confirmed by the API's own word, never assumed from a 2xx.
        return {
            "id": data.get("uid") or data.get("id"),
            "status": (data.get("status") or "").lower(),
            "raw": data,
        }

    def cancel(self, *, tenant: Tenant, booking_uid: str,
               reason: str = "Released by CONCIERGE verification") -> dict[str, Any]:
        """Not part of the engine seam — used by the harness to release a test booking so a real
        calendar is not left with verification clutter."""
        api_key, _ = _credentials(tenant)
        return _request("POST", f"/bookings/{booking_uid}/cancel", api_key=api_key,
                        version=BOOKINGS_VERSION,
                        body={"cancellationReason": reason})
