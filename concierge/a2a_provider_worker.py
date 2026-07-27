"""Restart-safe provider recovery for the exact OKX listing-review task shape.

The marketplace's reviewer agent #6058 creates a private task designating #9274's sole service.
Titles AND budgets vary between review attempts — three measured attempts were published at 0,
0.05 and 1 USDT. The harness expects the provider to apply on chain within a roughly three-minute
polling window, and reports "never accepted designated task" when it does not.

The normal daemon event handler remains the primary path. This worker polls authoritative active
task state so a delayed, missed, or misclassified notification cannot leave that review job in
``created``. Its scope is deliberately narrow: reviewer #6058 to ASP #9274, USDT, any positive
budget.

Two rules learned from failed attempts bound it, and both are about `reject-apply` being
irreversible on-chain:

* **Apply at the task's own posted amount, never above it.** Attempt one was published at 0 and
  applied to at 0.05; the buyer's ``next-action`` classified that as over budget and permanently
  executed ``reject-apply``, which raising the budget later could not undo.
* **Never apply at zero.** There is no amount at or below a zero budget that is worth winning.

It deliberately does NOT price this route through ``marketplace_pricing``. That command prices the
30-day commercial engagement at 2.5 USDT and declined review attempt three (posted at 1 USDT) on
amount mismatch — a correct commercial decision that reads to the marketplace as an ASP that never
answers. A buyer must still accept and fund the application before any work begins.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import config, postmark

PROVIDER_ID = "9274"
# Both measured listing attempts came from OKX's reviewer agent #6058, while the human-readable
# title changed between attempts. #9274 exposes exactly one service, so the authoritative stable
# route is reviewer -> designated provider, not title prose.
REVIEW_BUYER_ID = "6058"
# The price the service is REGISTERED at. It is what we advertise, not what we demand of a review
# task: the reviewer publishes whatever budget it likes and an application above that budget is
# irreversibly rejected, so `apply_amount` follows the task and this stays documentation.
SERVICE_AMOUNT = "0.05"
CURRENCY = "USDT"
# This task was published at zero, applied to at 0.05, and irreversibly rejected before the
# reviewer raised its displayed amount to 0.05. Active-task state now looks eligible but another
# application can never succeed (code 1001). Keep the measured poisoned job out of the retry loop.
IRREVERSIBLY_REJECTED_JOB_IDS = frozenset({
    "0x1805b3e6ade54278289d35a78ea154ae755b533d1a620fb8cd32dd640ad9a480",
})
STATE_PATH = Path(os.environ.get("A2A_PROVIDER_STATE")
                  or (config.ROOT / ".a2a_provider_applied.json"))
_TX_HASH = re.compile(r'0x[a-fA-F0-9]{64}')


def _run(*args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["onchainos", "agent", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def apply_amount(task: dict[str, Any]) -> str | None:
    """The task's own posted budget, or None when it is absent, unparseable or not positive.

    Applying at anything ABOVE the posted budget is what got attempt one irreversibly rejected,
    so the amount we ask for is the amount already on chain — never a policy price of our own.
    """
    try:
        posted = Decimal(str(task.get("tokenAmount")))
    except (InvalidOperation, TypeError):
        return None
    if not posted.is_finite() or posted <= 0:
        return None
    return format(posted, "f")


def eligible(task: dict[str, Any]) -> bool:
    """Match the OKX review route by stable identity; near-matches fail closed.

    Identity — reviewer #6058 designating ASP #9274 — is stable across attempts. The budget is
    not, so it is checked for being fundable (positive USDT), not for equalling a fixed price.
    """
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "created"
        and str(task.get("counterpartyAgentId")) == REVIEW_BUYER_ID
        and str(task.get("tokenSymbol")).upper() == CURRENCY
        and apply_amount(task) is not None
    )


def _read_applied() -> dict[str, str]:
    try:
        value = json.loads(STATE_PATH.read_text())
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_applied(applied: dict[str, str]) -> None:
    """Atomically persist successful submissions so ``created`` does not mean ``not applied``."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(applied, sort_keys=True))
    temporary.replace(STATE_PATH)


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
    submitted = _read_applied()
    results: list[dict[str, str]] = []
    for task in candidates:
        job_id = str(task["jobId"])
        if job_id in IRREVERSIBLY_REJECTED_JOB_IDS:
            results.append({"job_id": job_id, "action": "irreversibly_rejected"})
            continue
        if job_id in submitted:
            results.append({
                "job_id": job_id, "action": "already_submitted", "tx_hash": submitted[job_id],
            })
            continue
        amount = apply_amount(task)
        if amount is None:  # unreachable via eligible(); fail closed rather than guess a price
            continue
        applied = _run(
            "apply", job_id,
            "--agent-id", PROVIDER_ID,
            "--token-amount", amount,
            "--token-symbol", CURRENCY,
        )
        combined = f"{applied.stdout}\n{applied.stderr}".strip()
        tx_match = _TX_HASH.search(combined)
        if applied.returncode == 0 and tx_match:
            submitted[job_id] = tx_match.group(0)
            _record_applied(submitted)
            results.append({
                "job_id": job_id, "action": "applied", "price": amount,
                "tx_hash": tx_match.group(0),
            })
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
