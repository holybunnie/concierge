"""the scheduler — the scheduled-worker entry point.

Two jobs already existed as callable functions with nothing calling them on a schedule:
`receipts.anchor()` (receipt anchoring — HANDOFF.md names this exact gap: "a background worker calling
receipts.anchor() on unanchored rows is the scheduler territory") and `followup.dispatch()` (the
addendum's Safe Follow-Up). This module is that scheduler, plus the third scheduled job the scheduler
itself adds: a periodic summary.

`dispatch(tenant_id)` is meant to run once per tenant on a timer — a cron job or systemd timer on
the VPS, the same deploy pattern as `app.py`'s webhook (§12). Actually installing that timer is a
VPS/operator action this code cannot perform from inside a suite; what's provable here is that the
function does the right thing when it runs, against real stored data.

Every job is arithmetic or a call to an already-proven function — `anchor_pending` is a thin
filter in front of `receipts.anchor()` (the receipts suite/6b already prove that mechanism twice over), and
`process_tenant` never blocks a database transaction on a network call to Postmark or a summary
send — decide and persist first, then send, exactly like `mail.handle_inbound` and
`followup.process_tenant`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from psycopg import Cursor

from . import config, db, followup, gaps, postmark, receipts, store, summary
from .followup import FollowUpResult
from .models import Receipt, Tenant
from .postmark import Mailer, OutboundEmail

DEFAULT_SUMMARY_PERIOD_DAYS = 7


def _summary_policy(profile: dict[str, Any]) -> tuple[float, datetime | None]:
    p = profile.get("summary_policy") or {}
    period_days = float(p.get("period_days", DEFAULT_SUMMARY_PERIOD_DAYS))
    raw = p.get("last_sent_at")
    last_sent = datetime.fromisoformat(raw) if raw else None
    return period_days, last_sent


def anchor_pending(cur: Cursor, tenant: Tenant) -> list[Receipt]:
    """Sign + anchor every receipt for this tenant that isn't yet.

    Not a new anchoring mechanism — `receipts.anchor()` is exactly what the receipts suite and the public-receipt suite
    already anchor real receipts with, called here from a scheduled sweep instead of `app.py`'s
    per-request background thread. Absent a configured signer/contract, this returns an empty
    list rather than raising — the same honest-skip already used everywhere a credential is
    missing (`engine.NoCalendar`, `postmark.PostmarkMailer`'s refusal without a token).
    """
    if not (config.xlayer_private_key() and config.xlayer_contract()):
        return []
    pending = [rec for rec in store.list_receipts(cur) if not receipts.anchored(rec)]
    return [receipts.anchor(cur, rec) for rec in pending]


@dataclass
class TenantRunError:
    """One tenant's run failed. Returned, not raised — see `run_all`."""
    tenant_id: Any
    error: str


@dataclass
class TenantRunResult:
    tenant_id: Any
    anchored: list[Receipt] = field(default_factory=list)
    follow_up_results: list[FollowUpResult] = field(default_factory=list)
    summary_due: bool = False
    summary_text: str | None = None


def process_tenant(cur: Cursor, tenant: Tenant, *,
                    now: datetime | None = None) -> tuple[TenantRunResult, list[OutboundEmail]]:
    """DB-side (and, for anchoring, on-chain) work. Returns drafted emails for the caller to
    send OUTSIDE any open transaction — the same split `mail.handle_inbound` and
    `followup.process_tenant` already use.
    """
    now = now or datetime.now(dt_timezone.utc)
    anchored = anchor_pending(cur, tenant)
    follow_up_results = followup.process_tenant(cur, tenant, now=now)
    emails = [res.email for res in follow_up_results if res.email is not None]

    period_days, last_sent = _summary_policy(tenant.profile or {})
    due = last_sent is None or (now - last_sent) >= timedelta(days=period_days)

    result = TenantRunResult(tenant_id=tenant.tenant_id, anchored=anchored,
                              follow_up_results=follow_up_results, summary_due=due)

    if due:
        since = last_sent or (now - timedelta(days=period_days))
        # Feature 1: enrich this tenant's uncategorized gaps before the report reads them. A
        # no-op (and no network call) when no LLM key is configured — the report then shows the
        # gaps as raw, unclustered text, per the addendum's "degrade honestly" requirement. This
        # is scheduled enrichment, not a decision, so `classify_pending` swallows its own errors.
        gaps.classify_pending(cur, tenant)
        with_threads = store.list_threads(cur)
        with_receipts = store.list_receipts(cur)
        with_gaps = store.list_gap_events(cur)
        s = summary.build_summary(with_threads, with_receipts, since=since, until=now,
                                  gap_events=with_gaps)
        text = summary.render_summary_text(tenant, s)
        result.summary_text = text

        if tenant.owner_email:
            emails.append(OutboundEmail(
                from_address=tenant.inbound_address, to_address=tenant.owner_email,
                subject=f"[{tenant.business_name}] CONCIERGE summary", text_body=text,
            ))

    return result, emails


def dispatch(tenant_id, *, mailer: Mailer | None = None,
             now: datetime | None = None) -> TenantRunResult:
    """The worker entry point for one tenant: DB (+ on-chain) work, then network, with no
    transaction held open across a send. `mailer=None` runs every DB-side decision — anchoring,
    follow-up due-ness, summary due-ness — without attempting any send; the caller (a cron job
    with no Postmark token configured yet) gets the same honest partial operation the rest of
    this codebase already reports rather than a crash.
    """
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        result, emails = process_tenant(cur, tenant, now=now)

    if mailer is not None:
        for email in emails:
            mailer.send(email)
        # `summary_policy.last_sent_at` is written HERE — after the send actually happened, in a
        # second transaction — and never in `process_tenant`. Writing it alongside the decision
        # looked tidier and was wrong in two ways that both silently lose a summary the owner is
        # entitled to: a `--dry-run` consumed the due summary without sending it, and so did a
        # box with no Postmark token configured, which then never sent one again. Marking work
        # as done is a claim about the outside world, so it waits for the outside world.
        if result.summary_text is not None:
            with db.tenant_session(tenant_id) as cur:
                tenant = store.get_tenant(cur)
                profile = dict(tenant.profile or {})
                period_days, _ = _summary_policy(profile)
                profile["summary_policy"] = {"period_days": period_days,
                                             "last_sent_at": (now or datetime.now(dt_timezone.utc)).isoformat()}
                store.update_profile(cur, profile)
    return result


def run_all(*, mailer: Mailer | None = None,
            now: datetime | None = None) -> list[TenantRunResult | TenantRunError]:
    """Every tenant, one timer tick. One tenant's failure never stops the rest.

    A worker that dies partway through leaves some tenants processed and others silently not —
    and because the summary's `last_sent_at` is only written by a run that completes, a tenant
    skipped this tick is picked up on the next one rather than lost. Failures are returned (and
    logged by `main`) rather than raised, so an unreachable Postmark for one tenant cannot stop
    another tenant's receipts from anchoring.
    """
    results: list[TenantRunResult | TenantRunError] = []
    for tenant_id in db.list_tenant_ids():
        try:
            results.append(dispatch(tenant_id, mailer=mailer, now=now))
        except Exception as exc:  # one tenant's bad day is not every tenant's
            results.append(TenantRunError(tenant_id=tenant_id, error=f"{type(exc).__name__}: {exc}"))
    return results


def main(argv: list[str] | None = None) -> int:
    """The cron/systemd entry point: `python3 -m concierge.scheduler`.

    Sends for real when a Postmark token is configured, and — consistent with the rest of this
    codebase — runs every DB-side decision honestly without one rather than refusing to start.
    `--dry-run` forces that no-send mode even when a token IS present.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="concierge.scheduler", description=__doc__)
    parser.add_argument("--tenant", help="run one tenant id instead of all of them")
    parser.add_argument("--dry-run", action="store_true",
                        help="make every decision and write every DB row, but send no email")
    args = parser.parse_args(argv)

    token = config.postmark_token()
    mailer = None if (args.dry_run or not token) else postmark.PostmarkMailer(token)

    started = datetime.now(dt_timezone.utc)
    if args.tenant:
        results: list[Any] = [dispatch(args.tenant, mailer=mailer)]
    else:
        results = run_all(mailer=mailer)

    errors = [x for x in results if isinstance(x, TenantRunError)]
    ok = [x for x in results if isinstance(x, TenantRunResult)]
    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(dt_timezone.utc).isoformat(),
        "sending": mailer is not None,
        "tenants": len(results),
        "anchored": sum(len(x.anchored) for x in ok),
        "follow_ups": sum(1 for x in ok for f in x.follow_up_results if f.email is not None),
        "summaries": sum(1 for x in ok if x.summary_due),
        "errors": [{"tenant_id": str(e.tenant_id), "error": e.error} for e in errors],
    }
    # One JSON line per run, to stdout — `journalctl -u concierge-scheduler` is then greppable
    # and machine-readable, rather than prose a human has to read to find out what happened.
    print(json.dumps(report), file=sys.stdout, flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
