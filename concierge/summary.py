"""the scheduler — the tenant activity summary.

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
from .models import GapEvent, Receipt, Tenant, Thread

# Any receipt whose decision ended the thread in ESCALATED — the one state that always means
# "the owner has to look at this themselves" (SB 243's human route, a floor breach, an unknown
# service, a tripped escalation trigger). Counting the STATE rather than a hand-picked list of
# action names means a new escalation path added later is counted automatically.
_ESCALATED_STATE = "ESCALATED"

# Feature 1 (Product-Gap Intelligence) — human labels for the coarse categories
# `concierge/gaps.py` writes. A gap with no category (no LLM key configured) is shown as raw
# text and never dropped — see `render_summary_text`.
_GAP_LABELS = {
    "service_not_offered": "a service you don't offer",
    "pricing_tier_not_offered": "a pricing tier you don't offer",
    "geography_not_served": "an area you don't serve",
    "other": "other",
}


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
    # Feature 1 (Product-Gap Intelligence) — inquiries that asked for something the tenant's
    # profile could not answer. `gap_examples` is verbatim prospect text, never synthesized.
    product_gaps: int = 0
    gap_patterns: list[tuple[str, int]] = field(default_factory=list)  # (category, count) desc
    gap_examples: list[str] = field(default_factory=list)             # verbatim, most recent first


def _in_period(ts_raw: Any, start: datetime, end: datetime) -> bool:
    if not ts_raw:
        return False
    ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt_timezone.utc)
    return start <= ts <= end


def build_summary(threads: list[Thread], receipts: list[Receipt], *,
                   since: datetime, until: datetime | None = None,
                   gap_events: list[GapEvent] | None = None) -> Summary:
    """Pure aggregation over already-fetched rows — the caller (`scheduler.py`) fetches them via
    `store.list_threads`/`store.list_receipts`/`store.list_gap_events`, already RLS-scoped to one
    tenant. This function never touches a cursor, so it is trivial to test against a hand-built
    list of real rows. `gap_events` is optional so pre-Feature-1 callers keep working unchanged.
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

    # Feature 1 (Product-Gap Intelligence): the same ESCALATE-on-Unquotable transition counted
    # above, but read from its own instrumentation table so the verbatim text and the optional
    # category survive. `gap_events` arrive ordered oldest-first from `store.list_gap_events`.
    gaps = [g for g in (gap_events or []) if _in_period(g.escalated_at, since, until)]
    s.product_gaps = len(gaps)
    s.gap_examples = [g.raw_query_text.strip() for g in gaps][-5:][::-1]  # most recent first
    counts: dict[str, int] = {}
    for g in gaps:
        if g.classified_category:
            counts[g.classified_category] = counts.get(g.classified_category, 0) + 1
    s.gap_patterns = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
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

    # Feature 1 (Product-Gap Intelligence) — the market signal in the escalations: what
    # prospects asked for that this business does not sell. Verbatim, never synthesized.
    if s.product_gaps:
        lines += ["",
                  f"{s.product_gaps} inquiries this period asked for something you don't offer."]
        if s.gap_patterns:
            lines += ["Top patterns:"]
            lines += [f"  - {_GAP_LABELS.get(cat, cat)}: {n}" for cat, n in s.gap_patterns]
            categorized = sum(n for _, n in s.gap_patterns)
            if categorized < s.product_gaps:
                lines += [f"  - not yet categorized: {s.product_gaps - categorized}"]
        else:
            # Honest degradation (addendum §Feature 1): no LLM key, so gaps stay unclustered
            # rather than being dropped or given a fabricated category.
            lines += ["(Shown as raw text — connect an LLM key to cluster these into patterns.)"]
        lines += ["Verbatim examples, most recent first:"]
        lines += [f"  - {ex}" for ex in s.gap_examples]
    return "\n".join(lines)
