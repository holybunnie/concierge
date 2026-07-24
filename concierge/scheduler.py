"""Phase 8 — the scheduled-worker entry point.

Two jobs already existed as callable functions with nothing calling them on a schedule:
`receipts.anchor()` (Phase 6 — HANDOFF.md names this exact gap: "a background worker calling
receipts.anchor() on unanchored rows is Phase 8 territory") and `followup.dispatch()` (the
addendum's Safe Follow-Up). This module is that scheduler, plus the third scheduled job Phase 8
itself adds: a periodic summary.

`dispatch(tenant_id)` is meant to run once per tenant on a timer — a cron job or systemd timer on
the VPS, the same deploy pattern as `app.py`'s webhook (§12). Actually installing that timer is a
VPS/operator action this code cannot perform from inside a gate; what's provable here is that the
function does the right thing when it runs, against real stored data.

Every job is arithmetic or a call to an already-proven function — `anchor_pending` is a thin
filter in front of `receipts.anchor()` (GATE 6/6b already prove that mechanism twice over), and
`process_tenant` never blocks a database transaction on a network call to Postmark or a summary
send — decide and persist first, then send, exactly like `mail.handle_inbound` and
`followup.process_tenant`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from psycopg import Cursor

from . import config, db, followup, gaps, receipts, store, summary
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

    Not a new anchoring mechanism — `receipts.anchor()` is exactly what GATE 6 and GATE 6b
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

        profile = dict(tenant.profile or {})
        profile["summary_policy"] = {"period_days": period_days, "last_sent_at": now.isoformat()}
        store.update_profile(cur, profile)

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
    return result
