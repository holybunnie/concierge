"""Restart-safe autonomous policy for the dedicated OKX test buyer (#9630).

System notifications are best-effort wakeups. This worker polls authoritative task state and
funds only the one exact commercial test shape the operator approved. It cannot touch arbitrary
tasks, the older 0.02-USDT review, or any provider other than CONCIERGE #9274.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import a2a, db, store
from .marketplace_pricing import claim

BUYER_ID = "9630"
PROVIDER_ID = "9274"
TITLE = "CONCIERGE 30-day test"
AMOUNT = "2.5"
CURRENCY = "USDT"
DESCRIPTION = (
    "Brightside Dental is a dental clinic serving local patients. We offer dental examinations "
    "and hygiene appointments and need enquiries qualified, quoted, and booked."
)
ANSWERS = {
    # Generic-template spellings.
    "services": "Dental examination | 60 | 30 | USDT\nHygiene appointment | 90 | 45 | USDT",
    "floor_price": "55 USDT",
    "max_discount_pct": "8%",
    "availability": "Monday to Friday, 09:00-17:00",
    # The classifier may conservatively choose the spa/clinic template for the same description.
    # Keep the business facts identical under that template's field names; these are aliases, not
    # invented defaults.
    "service_menu": (
        "Dental examination | 60 | 30 | USDT\nHygiene appointment | 90 | 45 | USDT"
    ),
    "packages": "skip",
    "booking_lead_time": "Monday to Friday, 09:00-17:00",
    "cancellation_policy": (
        "Cancellations, no-shows, and refunds must be escalated to the clinic; "
        "CONCIERGE may not promise a charge or refund."
    ),
    "timezone": "Europe/London",
    "icp": "Local dental patients",
    "escalation_triggers": (
        "Clinical advice, emergencies, complaints, refunds, or anything outside the stored services"
    ),
    "artifact_sample": "Thanks for contacting Brightside Dental. We would be happy to help.",
    "engagement_noun": "appointment",
    "client_noun": "patient",
    "group_policy": "One patient per appointment.",
    "travel_policy": "We do not travel; appointments take place at the clinic.",
    "hours_policy": "Weekday hours only.",
    "length_policy": "Examinations are 30 minutes and hygiene appointments are 45 minutes.",
}


def _run(*args: str, json_output: bool = True) -> dict[str, Any] | str:
    completed = subprocess.run(
        ["onchainos", "agent", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return json.loads(completed.stdout) if json_output else completed.stdout.strip()


def _a2a(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["okx-a2a", *args, "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def _buyer_session(job_id: str) -> str:
    queried = _a2a("session", "query", "--job-id", job_id)
    sessions = queried.get("sessions") or []
    wanted = f"job:{job_id}:my:{BUYER_ID}:to:{PROVIDER_ID}"
    if any(row.get("sessionKey") == wanted for row in sessions):
        return wanted
    provider = next(
        (
            row for row in sessions
            if str(row.get("myAgentId")) == PROVIDER_ID
            and str(row.get("toAgentId")) == BUYER_ID
            and row.get("groupId")
        ),
        None,
    )
    if not provider:
        raise RuntimeError("fail closed: provider peer session has no marketplace group id")
    created = _a2a(
        "session", "create",
        "--job-id", job_id,
        "--my-agent-id", BUYER_ID,
        "--to-agent-id", PROVIDER_ID,
        "--group-id", str(provider["groupId"]),
    )
    session = created.get("session") or created.get("data") or created
    key = session.get("sessionKey") if isinstance(session, dict) else None
    if key != wanted:
        raise RuntimeError(f"fail closed: buyer session creation returned {key!r}, wanted {wanted!r}")
    return wanted


def eligible(task: dict[str, Any]) -> bool:
    return (
        str(task.get("myAgentId")) == BUYER_ID
        and task.get("myRole") == "user"
        and str(task.get("counterpartyAgentId")) == PROVIDER_ID
        and task.get("title") == TITLE
        and str(task.get("tokenAmount")) == AMOUNT
        and str(task.get("tokenSymbol")).upper() == CURRENCY
    )


def _answer_onboarding(job_id: str) -> dict[str, Any]:
    try:
        tenant_id = db.resolve_tenant_by_a2a_job(job_id)
    except db.TenantUnresolved:
        return {"action": "waiting_for_tenant"}
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        state = dict((tenant.engagement or {}).get("provisioning") or {}) if tenant else {}
    stage = state.get("stage")
    if stage == "awaiting_owner_email":
        answer = "owner@brightside.example"
    elif stage == "awaiting_business_name":
        answer = "Brightside Dental"
    elif stage == "awaiting_description":
        answer = DESCRIPTION
    elif stage == "interviewing":
        key = state.get("awaiting")
        if not key:
            return {"action": "waiting_for_question"}
        if key not in ANSWERS:
            raise RuntimeError(f"fail closed: no approved test answer for onboarding field {key!r}")
        answer = ANSWERS[key]
    elif stage == "live":
        return {"action": "tenant_live"}
    else:
        return {"action": "waiting", "stage": stage}
    a2a.send(
        job_id,
        answer,
        session_agent_id=BUYER_ID,
        session_key=_buyer_session(job_id),
    )
    return {"action": "answered", "stage": stage, "field": state.get("awaiting")}


def _delivered_live_tenant(job_id: str) -> str:
    """Return the issued address only when the provider's durable delivery preconditions hold."""
    try:
        tenant_id = db.resolve_tenant_by_a2a_job(job_id)
    except db.TenantUnresolved as exc:
        raise RuntimeError("fail closed: submitted job has no provisioned tenant") from exc
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        state = dict((tenant.engagement or {}).get("provisioning") or {}) if tenant else {}
    address = str(tenant.inbound_address if tenant else "")
    if state.get("stage") != "live" or state.get("marketplace_delivered") is not True:
        raise RuntimeError("fail closed: submitted job is not durably live and delivered")
    if not address.endswith("@inbox.quietdesks.com"):
        raise RuntimeError(f"fail closed: issued address is not on the live inbox domain: {address!r}")
    return address


def run() -> dict[str, Any]:
    payload = _run("active-tasks")
    assert isinstance(payload, dict)
    tasks = (payload.get("data") or {}).get("tasks") or []
    candidates = [task for task in tasks if eligible(task)]
    if not candidates:
        return {"seen": len(tasks), "eligible": 0, "accepted": 0}
    if len(candidates) != 1:
        raise RuntimeError(f"fail closed: expected one eligible test job, found {len(candidates)}")

    task = candidates[0]
    job_id = str(task["jobId"])
    status = str(task.get("status"))
    if status == "accepted":
        return {
            "seen": len(tasks), "eligible": 1, "accepted": 0, "job_id": job_id,
            **_answer_onboarding(job_id),
        }
    if status == "submitted":
        address = _delivered_live_tenant(job_id)
        result = _run("complete", job_id, json_output=False)
        return {
            "seen": len(tasks), "eligible": 1, "accepted": 0, "completed": 1,
            "job_id": job_id, "inbound_address": address, "result": result,
        }
    if status != "created":
        return {"seen": len(tasks), "eligible": 1, "accepted": 0, "status": status}

    pricing = claim(job_id, BUYER_ID, AMOUNT, CURRENCY)
    if not pricing["accepted"] or pricing["required_price"] != AMOUNT:
        raise RuntimeError(f"fail closed: pricing rejected authorized test: {pricing}")

    result = _run("confirm-accept", job_id, json_output=False)
    return {
        "seen": len(tasks),
        "eligible": 1,
        "accepted": 1,
        "job_id": job_id,
        "promo_number": pricing["promo_number"],
        "result": result,
    }


def main() -> int:
    try:
        print(json.dumps(run(), separators=(",", ":"), default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"accepted": 0, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
