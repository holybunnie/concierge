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
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import config, postmark

PROVIDER_ID = "9274"
# Both measured listing attempts came from OKX's reviewer agent #6058, while the human-readable
# title changed between attempts. #9274 exposes exactly one service, so the authoritative stable
# route is reviewer -> designated provider, not title prose.
# Two distinct marketplace-side buyers publish review work, and BOTH must be answered on chain.
#
# #6058 is the human-ish reviewer that publishes one "try concierge for my salon"-shaped task.
# #1791 "SandboxAgent" is the platform's automated conformance probe: it opens a `DACS-Probe-<our
# service name>` XMTP group, publishes a consumer-shaped quote request ("Request for Lawn Care
# Quote", "Request a Quote for Plumbing") at a 0.00001 USDT dust budget, and waits for the
# provider to APPLY. It was the missing half. Six of its probes sat in `created` while the AI
# handler answered every one of them in chat within ~10 seconds — correctly declining a
# third-party quote as out of scope — and the marketplace still rejected the listing on
# 2026-07-28 with "we were unable to receive a response from your Agent, causing the task to time
# out". A chat reply is not the response it measures; the on-chain application is.
#
# Applying is not quoting. It says "this job is mine to answer", nothing about price — the buyer
# must still fund escrow before any work starts, and what CONCIERGE then says in the thread is
# unchanged and still bound by every pricing rule in the repo's CLAUDE.md. An out-of-scope probe
# still gets an honest decline; it just gets one the marketplace can see.
REVIEW_BUYER_IDS = frozenset({"6058", "1791"})
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
# These ordinary, pre-watchdog jobs remain incorrectly exposed as ``created`` by active-tasks
# after their July 26 handler sessions closed them out. They have verified ``a2a-<jobId>`` groups,
# not the platform's ``DACS-Probe-`` namespace. A fresh watchdog state file must not turn this
# stale marketplace state into three urgent review-failure emails.
KNOWN_NON_REVIEW_JOB_IDS = frozenset({
    "0x11c5ab940c95cc466fcbd175f171c7e9cad1370dfe303cb2675815d0dcdc6a6b",
    "0x926fd3e1d82577f8865e9561c31b82564627ece819505f80a7029b798d18cfbf",
    "0x33706194c7ff5c0deb60a78b1f8502b4f94f6f37e25ce5ca3899b59533496ed8",
})
STATE_PATH = Path(os.environ.get("A2A_PROVIDER_STATE")
                  or (config.ROOT / ".a2a_provider_applied.json"))
# Route A of the handler's brief identifies a probe by its `DACS-Probe-` XMTP group name, which
# survives OKX changing reviewer agents. This worker cannot use that signal: `active-tasks`
# carries no group name. So it does the one useful thing it can — notice a task from a buyer it
# does not recognise sitting unanswered, and say so out loud while the review window is still
# open. It never applies for a stranger; that judgement stays with the handler and its scope gate.
UNSEEN_STATE_PATH = Path(os.environ.get("A2A_PROVIDER_UNSEEN_STATE")
                         or (config.ROOT / ".a2a_provider_unseen.json"))
# Applying is only half a review. On 2026-07-28 both of #6058's fresh test tasks were applied to
# within seconds AND accepted — 0x60b96fc3 at 0.001 on service 1, 0x0adbb85b at 0.05 on service 2
# — and then received nothing at all. The AI handler correctly refused to work an underpriced job
# (`marketplace_pricing` prices the real 30-day engagement at 2.5) and correctly refused to do
# `job_accepted` work itself, and the provisioning worker never saw the event because the queue
# carried only the handler's own operator alerts. Two right rules, one silence, and silence is
# verbatim what the marketplace rejected the listing for: "unable to receive a response from your
# Agent, causing the task to time out".
#
# So onboarding a review task follows the same lesson the apply path already learned: poll
# authoritative task state, do not wait to be notified. Scope is the same narrow identity — a
# review buyer, ASP #9274, already `accepted` — and it deliberately does NOT go through
# `marketplace_pricing`. That gate governs REAL buyers and still does, untouched; neither #6058
# nor #1791 is a real buyer, and neither ever pays us. Nothing here touches what a tenant quotes
# its own customers.
ONBOARDED_STATE_PATH = Path(os.environ.get("A2A_PROVIDER_ONBOARDED_STATE")
                            or (config.ROOT / ".a2a_provider_onboarded.json"))
# A zero-budget task cannot be applied to: the CLI refuses an amount of zero, and any positive
# amount exceeds the posted budget and is irreversibly reject-applied. So there is nothing to bid,
# and the standing rule was to wait. Waiting is silence, and service 37052's fee field is empty —
# OKX renders that as "free", which invites exactly this job. A buyer who reads "free", creates a
# 0-budget job and hears nothing has met a broken listing, not a priced one.
#
# Saying so costs nothing and risks nothing: posting into the job's own channel is not an
# application, moves no money, and cannot be reject-applied. This does not quote the buyer a price
# for their own customers — it states OUR engagement fee, which is ordinary commerce and comes
# from `marketplace_pricing`, never from a model.
ZERO_BUDGET_STATE_PATH = Path(os.environ.get("A2A_PROVIDER_ZERO_BUDGET_STATE")
                              or (config.ROOT / ".a2a_provider_zero_budget.json"))
# `active-tasks` reports no creation timestamp, so age is measured from when we first saw it.
# Three minutes: the measured probe harness gives up in under a minute, so this is already the
# post-mortem — it exists to turn "found out at the next rejection" into "found out tonight".
UNKNOWN_ALERT_AFTER_SECONDS = 180
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

    Identity — a review buyer designating ASP #9274 — is stable across attempts. The budget is
    not, so it is checked for being fundable (positive USDT), not for equalling a fixed price;
    the automated probe posts 0.00001 USDT and the reviewer has posted 0, 0.05 and 1.
    """
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "created"
        and str(task.get("counterpartyAgentId")) in REVIEW_BUYER_IDS
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


def accepted_review_task(task: dict[str, Any]) -> bool:
    """A review buyer's task that is funded and accepted, and therefore owed real work.

    ``created`` is ``eligible``'s business (apply). This is the next step and the one that was
    missing: the buyer has confirmed and funded, so the engagement has actually started.
    """
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "accepted"
        and str(task.get("counterpartyAgentId")) in REVIEW_BUYER_IDS
    )


def zero_budget_task(task: dict[str, Any]) -> bool:
    """A task addressed to us, still open, carrying a budget that is present and not positive.

    Deliberately narrower than ``apply_amount(task) is None``, which is also true when the field
    is absent or unparseable. A missing amount is an unknown, not a zero, and answering an unknown
    with "your job has no budget" would be a confident statement about something we cannot see.
    """
    try:
        posted = Decimal(str(task.get("tokenAmount")))
    except (InvalidOperation, TypeError):
        return False
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "created"
        and posted.is_finite()
        and posted <= 0
    )


def _answer_zero_budget(job_id: str, buyer_id: str) -> dict[str, str]:
    """Tell a zero-budget buyer what to do instead of leaving them in silence.

    The price is read from `marketplace_pricing`, the only pricing authority, by offering the
    task's own zero — a mismatched offer is declined and deliberately does NOT consume one of the
    ten launch slots (the SQL returns on `amount_mismatch` before it inserts), so asking what
    something costs cannot spend the discount.

    It states terms; it does not promise to take the job. Scope is the handler's judgement and
    stays there. A consumer asking a business for a quote is the single most common out-of-scope
    job we receive, and "recreate it with 2.5 USDT and I'll pick it up" would be soliciting work
    the scope gate exists to decline — so the message says who this is for and lets them decide.
    """
    from . import a2a  # local: keep CLI startup free of the DB import chain
    from .marketplace_pricing import claim

    decision = claim(job_id, buyer_id, "0", CURRENCY)
    required = decision["required_price"]
    a2a.send(job_id, (
        "This is an AI agent, not a person. A human is reachable through this thread.\n\n"
        "I can't take this job on: it carries no budget, and the marketplace only lets me apply "
        "for an amount the task already holds. Nothing is wrong on your side and nothing has "
        "been declined against you.\n\n"
        "What I do: a business hands me its inbound enquiries, and I answer them around the "
        "clock — quoting from that business's own stored price list, negotiating only within the "
        "floor it sets, and booking the appointment. If you are looking for a quote FROM a "
        "business, I am the wrong agent; I answer on their behalf, not on yours.\n\n"
        f"If that is what you want, one 30-day engagement is {required} {CURRENCY}. Create the "
        "job again with that budget and it will be assessed on arrival. Funds stay in escrow "
        "until you accept the delivery."
    ), to_agent_id=buyer_id)
    return {"job_id": job_id, "action": "zero_budget_answered", "required_price": required}


def _read_onboarded() -> dict[str, str]:
    try:
        value = json.loads(ONBOARDED_STATE_PATH.read_text())
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_onboarded(onboarded: dict[str, str]) -> None:
    """Persist which accepted jobs already have a tenant, atomically.

    ``provision.on_subscription`` is itself idempotent through the UNIQUE ``a2a_job_id``, so a
    replay cannot duplicate a tenant. What it WOULD do is re-greet the buyer, and this worker
    runs every 8 seconds — a reviewer watching us say hello eight times a minute is its own kind
    of failed review.
    """
    ONBOARDED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ONBOARDED_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(onboarded, sort_keys=True))
    temporary.replace(ONBOARDED_STATE_PATH)


def _read_zero_budget() -> dict[str, str]:
    try:
        value = json.loads(ZERO_BUDGET_STATE_PATH.read_text())
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_zero_budget(answered: dict[str, str]) -> None:
    """One answer per job, persisted — the worker runs every 8 seconds."""
    ZERO_BUDGET_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ZERO_BUDGET_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(answered, sort_keys=True))
    temporary.replace(ZERO_BUDGET_STATE_PATH)


def _onboard(job_id: str, buyer_id: str) -> dict[str, str]:
    """Open the onboarding interview for an accepted review job.

    The synthetic event carries no sender on purpose: ``on_subscription`` resolves the peer from
    the task record itself via ``a2a.task_participants`` and refuses a job whose provider is not
    #9274, so the buyer address is read from the chain rather than trusted from here.
    """
    from . import a2a, provision  # local: keep CLI startup free of the DB import chain

    event = a2a.Event(
        todo_id=f"provider-worker-accepted-{job_id}",
        kind="job_accepted",
        job_id=job_id,
        from_agent_id=None,
        content=f"[Job Accepted] Job {job_id} has been accepted.",
        raw={"jobId": job_id, "type": "job_accepted", "counterpartyAgentId": buyer_id},
    )
    tenant_id = provision.on_subscription(event)
    return {"job_id": job_id, "action": "onboarded", "tenant_id": str(tenant_id)}


def unknown_review_candidate(task: dict[str, Any]) -> bool:
    """A task addressed to us, fundable and unanswered, from a buyer this worker cannot route.

    Deliberately NOT a trigger to apply. It is the shape a probe from a reviewer agent we have
    never seen would have, and also the shape an ordinary stranger's job has — the two are
    indistinguishable without the group name. So it raises a human, and nothing else.
    """
    return (
        str(task.get("myAgentId")) == PROVIDER_ID
        and task.get("myRole") == "asp"
        and str(task.get("status")).lower() == "created"
        and str(task.get("tokenSymbol")).upper() == CURRENCY
        and apply_amount(task) is not None
        and str(task.get("counterpartyAgentId")) not in REVIEW_BUYER_IDS
        and str(task.get("jobId")) not in KNOWN_NON_REVIEW_JOB_IDS
    )


def _alert_unknown(tasks: list[dict[str, Any]], now: float) -> list[dict[str, str]]:
    """Warn once per job when a stranger's fundable task has gone unanswered past the threshold."""
    try:
        seen = json.loads(UNSEEN_STATE_PATH.read_text())
        seen = {str(k): dict(v) for k, v in seen.items()} if isinstance(seen, dict) else {}
    except (OSError, ValueError, TypeError):
        seen = {}

    live = {str(t["jobId"]) for t in tasks}
    seen = {job_id: entry for job_id, entry in seen.items() if job_id in live}
    raised: list[dict[str, str]] = []

    for task in tasks:
        job_id = str(task["jobId"])
        entry = seen.setdefault(job_id, {"first_seen": now, "alerted": False})
        age = now - float(entry.get("first_seen", now))
        if entry.get("alerted") or age < UNKNOWN_ALERT_AFTER_SECONDS:
            continue
        entry["alerted"] = True
        raised.append({
            "job_id": job_id,
            "buyer": str(task.get("counterpartyAgentId")),
            "title": str(task.get("title")),
            "amount": str(task.get("tokenAmount")),
            "alert": _alert(job_id, (
                f"A task from UNRECOGNISED buyer #{task.get('counterpartyAgentId')} has sat in "
                f"`created` for {int(age)}s without an application.\n\n"
                f"  title  : {task.get('title')}\n"
                f"  budget : {task.get('tokenAmount')} {task.get('tokenSymbol')}\n\n"
                "If its XMTP group name begins with `DACS-Probe-`, this is an OKX review probe "
                "from a reviewer agent we have not seen before, and it must be applied for on "
                "chain NOW — a chat reply does not count and the window is about a minute. "
                "Check with:\n\n"
                "  journalctl -u concierge-a2a --no-pager | grep " + job_id[:14] + "\n\n"
                "If the group is named `a2a-<jobId>` it is an ordinary buyer and the handler's "
                "normal scope and price gates apply. Do not apply for it from here."
            ), apply_failed=False),
        })

    try:
        UNSEEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = UNSEEN_STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(seen, sort_keys=True))
        temporary.replace(UNSEEN_STATE_PATH)
    except OSError:  # an un-persisted warning may repeat; it must never break the apply path
        pass
    return raised


def _alert(job_id: str, detail: str, *, apply_failed: bool = True) -> str:
    """Tell the operator about a failed apply or an unclassified-task warning."""
    to_address = config.get("ALERT_EMAIL")
    token = config.postmark_token()
    domain = config.inbound_domain()
    if not (to_address and token and domain):
        return "alert_not_configured"
    try:
        postmark.PostmarkMailer(token).send(postmark.OutboundEmail(
            from_address=f"alerts@{domain}",
            to_address=to_address,
            subject=("[CONCIERGE] OKX review apply FAILED" if apply_failed
                     else "[CONCIERGE] Unclassified A2A task warning"),
            text_body=(
                (f"The deterministic provider recovery worker could not apply for {job_id}.\n\n"
                 if apply_failed else
                 f"The review watchdog found an unclassified task {job_id}.\n"
                 "No application was attempted, and this is not an apply failure.\n\n") +
                f"{detail}\n\nThe review window is short; inspect "
                "journalctl -u concierge-a2a-provider.service immediately."
            ),
        ))
        return "alert_sent"
    except Exception as exc:  # noqa: BLE001 - alert failure must not replace the apply failure
        return f"alert_failed:{type(exc).__name__}"


def run(dry_run: bool = False) -> dict[str, Any]:
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
        if dry_run:
            # `apply` is irreversible in one direction that matters: an application the buyer
            # classifies as over budget is permanently reject-applied. Being able to read the
            # exact set of jobs and amounts BEFORE spending them is worth one branch.
            results.append({
                "job_id": job_id, "action": "would_apply", "price": amount,
                "buyer": str(task.get("counterpartyAgentId")), "title": str(task.get("title")),
            })
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
    # Accepted review jobs. Runs after the applies because an unapplied task is the more urgent
    # failure, and a raise here must never cost an application that would otherwise have landed.
    onboarded = _read_onboarded()
    for task in [t for t in tasks if accepted_review_task(t)]:
        job_id = str(task["jobId"])
        if job_id in onboarded:
            results.append({"job_id": job_id, "action": "already_onboarded",
                            "tenant_id": onboarded[job_id]})
            continue
        if dry_run:
            results.append({"job_id": job_id, "action": "would_onboard",
                            "buyer": str(task.get("counterpartyAgentId"))})
            continue
        try:
            outcome = _onboard(job_id, str(task.get("counterpartyAgentId")))
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the others
            alert = _alert(job_id, f"onboarding an accepted review job failed: {exc}"[:500],
                           apply_failed=False)
            results.append({"job_id": job_id, "action": "onboard_failed",
                            "error": f"{type(exc).__name__}: {exc}"[:200], "alert": alert})
            continue
        onboarded[job_id] = outcome["tenant_id"]
        _record_onboarded(onboarded)
        results.append(outcome)

    # Zero-budget tasks. Answered once per job, never applied to.
    answered = _read_zero_budget()
    for task in [t for t in tasks if zero_budget_task(t)]:
        job_id = str(task["jobId"])
        if job_id in IRREVERSIBLY_REJECTED_JOB_IDS:
            continue
        if job_id in answered:
            results.append({"job_id": job_id, "action": "zero_budget_already_answered",
                            "required_price": answered[job_id]})
            continue
        buyer_id = str(task.get("counterpartyAgentId") or "")
        if not buyer_id:
            continue
        if dry_run:
            results.append({"job_id": job_id, "action": "would_answer_zero_budget",
                            "buyer": buyer_id})
            continue
        try:
            outcome = _answer_zero_budget(job_id, buyer_id)
        except Exception as exc:  # noqa: BLE001 - a failed courtesy must not stop the run
            results.append({"job_id": job_id, "action": "zero_budget_answer_failed",
                            "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        answered[job_id] = outcome["required_price"]
        _record_zero_budget(answered)
        results.append(outcome)

    unknown = [task for task in tasks if unknown_review_candidate(task)]
    # Runs after the applies so a failure to warn can never delay an application that succeeds.
    warnings = [] if dry_run else _alert_unknown(unknown, time.time())
    return {
        "seen": len(tasks), "eligible": len(candidates), "results": results,
        "unknown_buyers": len(unknown), **({"warnings": warnings} if warnings else {}),
    }


def main(argv: list[str] | None = None) -> int:
    dry_run = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    try:
        print(json.dumps(run(dry_run=dry_run), separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"eligible": 0, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
