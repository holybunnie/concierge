"""Phase 8 — the tenant activity summary.

Every number here is a count or a sum over receipts and threads already in Postgres — arithmetic
over the tenant's own real history, the same Deterministic-Decision Law that governs pricing,
guardrails and confidence scoring. There is no LLM call anywhere in this module, and there could
not be: it imports nothing that reaches a network. If Product-Gap Intelligence (addendum Feature
1) is layered on later, it attaches here — the escalation receipts this module already counts are
exactly the rows that feature reads.

This is deliberately an OWNER-facing report, not a client-facing message: it does not carry the
AI disclosure (§ SB 243 governs what CONCIERGE says to a *prospect*, not an internal digest to the
business that owns the account) and it does not read from `profile.lexicon` — "bookings" and
"escalations" are precise operational words for the owner, not the trade-specific nouns a
prospect hears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any

from . import pricing, store
from .models import Receipt, Tenant, Thread

# Any receipt whose decision ended the thread in ESCALATED — the one state that always means
# "the owner has to look at this themselves" (SB 243's human route, a floor breach, an unknown
# service, a tripped escalation trigger). Counting the STATE rather than a hand-picked list of
# action names means a new escalation path added later is counted automatically.
_ESCALATED_STATE = "ESCALATED"


@dataclass
class Summary:
    period_start: datetime
    period_end: datetime
    inquiries: int = 0                  # new threads opened in the period
    quotes_sent: int = 0
    negotiations: int = 0
    bookings: int = 0
    booked_value: float = 0.0           # sum of the agreed/quoted figure on booked receipts
    booked_currency: str | None = None
    escalations: int = 0                # receipts whose decision ended the thread ESCALATED
    queued_for_approval: int = 0        # Feature 2 — drafted but held for the owner
    follow_ups_sent: int = 0            # Safe Follow-Up nudges
    threads_gone_dead: int = 0          # Safe Follow-Up — went quiet, no reply
    escalation_examples: list[str] = field(default_factory=list)  # verbatim, most recent first


def _in_period(ts_raw: Any, start: datetime, end: datetime) -> bool:
    if not ts_raw:
        return False
    ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt_timezone.utc)
    return start <= ts <= end


def build_summary(threads: list[Thread], receipts: list[Receipt], *,
                   since: datetime, until: datetime | None = None) -> Summary:
    """Pure aggregation over already-fetched rows — the caller (`scheduler.py`) fetches them via
    `store.list_threads`/`store.list_receipts`, already RLS-scoped to one tenant. This function
    never touches a cursor, so it is trivial to test against a hand-built list of real rows.
    """
    until = until or datetime.now(dt_timezone.utc)
    s = Summary(period_start=since, period_end=until)

    s.inquiries = sum(1 for t in threads if _in_period(t.created_at, since, until))

    for rec in receipts:
        if not _in_period(rec.created_at, since, until):
            continue
        decision = rec.decision or {}
        detail = decision.get("detail") or {}

        if rec.action == "quoted":
            s.quotes_sent += 1
        elif rec.action == "counter_within_rules":
            s.negotiations += 1
        elif rec.action == "booked":
            s.bookings += 1
            amount = detail.get("agreed")
            if amount is None:
                amount = detail.get("amount")
            if amount is not None:
                s.booked_value += float(amount)
                if s.booked_currency is None:
                    s.booked_currency = (detail.get("currency")
                                          or (detail.get("service") and "GBP"))
        elif rec.action == "follow_up_sent":
            s.follow_ups_sent += 1
        elif rec.action == "marked_dead":
            s.threads_gone_dead += 1

        if decision.get("queued_for_approval"):
            s.queued_for_approval += 1

        if decision.get("state_after") == _ESCALATED_STATE:
            s.escalations += 1
            body = (decision.get("inbound_body") or "").strip()
            if body:
                s.escalation_examples.append(body)

    s.escalation_examples = s.escalation_examples[-5:][::-1]   # most recent first, capped
    return s


def render_summary_text(tenant: Tenant, s: Summary) -> str:
    """A plain-English report. No AI disclosure, no lexicon substitution — see module docstring
    for why: this is an internal digest to the business, not a message to a prospect."""
    currency_symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(s.booked_currency or "", "")
    lines = [
        f"CONCIERGE summary for {tenant.business_name}",
        f"{s.period_start.date()} to {s.period_end.date()}",
        "",
        f"- {s.inquiries} new inquiries",
        f"- {s.quotes_sent} quotes sent",
        f"- {s.negotiations} negotiations handled",
        f"- {s.bookings} bookings confirmed"
        + (f" (total {currency_symbol}{pricing.fmt_amount(s.booked_value)})"
           if s.bookings else ""),
        f"- {s.escalations} sent to you directly (a floor breach, an unknown request, "
        f"or someone asking for a person)",
        f"- {s.queued_for_approval} replies held for your approval rather than sent automatically",
        f"- {s.follow_ups_sent} follow-up nudges sent to quiet leads",
        f"- {s.threads_gone_dead} threads marked as gone quiet with no reply",
    ]
    if s.escalation_examples:
        lines += ["", "What came to you directly, most recent first:"]
        lines += [f"  - {ex}" for ex in s.escalation_examples]
    return "\n".join(lines)
