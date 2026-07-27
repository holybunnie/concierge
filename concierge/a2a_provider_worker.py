"""Restart-safe provider recovery for the exact OKX listing-review task shape.

The marketplace's listing harness creates a private designated task titled
``Try inbound enquiry handling`` with a zero initial offer and a one-USDT maximum. It expects the
provider to counter-apply on chain before its roughly 12-minute acceptance window closes.

The normal daemon event handler remains the primary path. This worker polls authoritative active
task state so a delayed, missed, or misclassified notification cannot leave that review job in
``created``. Its scope is deliberately narrow: ASP #9274, exact title, exact zero-USDT initial
offer, and a fixed 0.05-USDT smoke-test counter. A buyer must still accept and fund that counter
before any work begins.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import config, postmark

PROVIDER_ID = "9274"
REVIEW_TITLE = "Try inbound enquiry handling"
INITIAL_AMOUNT = "0"
COUNTER_AMOUNT = "0.05"
CURRENCY = "USDT"


def _run(*args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["onchainos", "agent", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def eligible(task: dict[str, Any]) -> bool:
    """Match only the measured OKX review shape; near-matches fail closed."""
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "created"
        and task.get("title") == REVIEW_TITLE
        and str(task.get("tokenAmount")) == INITIAL_AMOUNT
        and str(task.get("tokenSymbol")).upper() == CURRENCY
        and bool(str(task.get("counterpartyAgentId") or "").strip())
    )


def _alert(job_id: str, detail: str) -> str:
    """Tell the operator when an apply failed; the journal is not an alerting channel."""
    to_address = config.get("ALERT_EMAIL")
    token = config.postmark_token()
    domain = config.inbound_domain()
    if not (to_address and token and domain):
        return "alert_not_configured"
    try:
        postmark.PostmarkMailer(token).send(postmark.OutboundEmail(
            from_address=f"alerts@{domain}",
            to_address=to_address,
            subject="[CONCIERGE] OKX review apply FAILED",
            text_body=(
                f"The deterministic provider recovery worker could not apply for {job_id}.\n\n"
                f"{detail}\n\nThe review window is short; inspect "
                "journalctl -u concierge-a2a-provider.service immediately."
            ),
        ))
        return "alert_sent"
    except Exception as exc:  # noqa: BLE001 - alert failure must not replace the apply failure
        return f"alert_failed:{type(exc).__name__}"


def run() -> dict[str, Any]:
    active = _run("active-tasks", "--role", "asp")
    if active.returncode != 0:
        raise RuntimeError(f"active-tasks failed: {(active.stderr or active.stdout).strip()[:300]}")
    try:
        payload = json.loads(active.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"active-tasks returned non-JSON: {active.stdout[:200]!r}") from exc
    tasks = (payload.get("data") or {}).get("tasks") or []
    candidates = [task for task in tasks if eligible(task)]
    results: list[dict[str, str]] = []
    for task in candidates:
        job_id = str(task["jobId"])
        applied = _run(
            "apply", job_id,
            "--agent-id", PROVIDER_ID,
            "--token-amount", COUNTER_AMOUNT,
            "--token-symbol", CURRENCY,
        )
        combined = f"{applied.stdout}\n{applied.stderr}".strip()
        if applied.returncode == 0 and "txHash" in combined:
            results.append({"job_id": job_id, "action": "applied", "price": COUNTER_AMOUNT})
            continue
        if "apply record already exists" in combined.lower():
            results.append({"job_id": job_id, "action": "already_applied"})
            continue
        alert = _alert(job_id, combined[:500] or f"apply exited {applied.returncode}")
        raise RuntimeError(
            f"apply failed for {job_id}: {combined[:300] or applied.returncode}; {alert}"
        )
    return {"seen": len(tasks), "eligible": len(candidates), "results": results}


def main() -> int:
    try:
        print(json.dumps(run(), separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"eligible": 0, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
