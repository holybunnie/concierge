"""GATE 8b-1 — Product-Gap Intelligence (addendum Feature 1).

Phase 3 already escalates, never invents, when a prospect asks about something the tenant's
profile cannot answer (GATE 3 check 5). This feature turns that existing escalation into a market
signal: the question is recorded verbatim as a `GapEvent`, and surfaced — aggregated — in the
owner's summary: "here's what your market asked for that you don't sell."

Every check runs a real conversation against real PostgreSQL. The gap text shown in the summary
is the exact string the prospect sent, never synthesized. Check 4 is the isolation test, reusing
Phase 1's RLS fence unchanged: one tenant's gaps can never appear in another's report. Check 5
proves honest degradation — with no LLM key the category is None and the raw text still survives.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone as dt_timezone

from . import db, gaps, store, summary
from . import verify_phase3 as p3

GAP_QUERY = "Do you offer laser hair removal, and how much is it?"


class _NoLLMKey:
    """Temporarily blanks LLM_API_KEY so the no-key degradation path can be exercised
    deterministically, with no network call. Restores the original value on exit, always.
    `config.load_env` uses setdefault, so an empty string here is not re-populated from .env."""

    def __enter__(self):
        self._saved = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = ""
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self._saved
        return False


def run(r) -> None:
    db.migrate()
    since = datetime.now(dt_timezone.utc) - timedelta(hours=1)

    # ---- 1. an unquotable inquiry escalates AND writes a GapEvent, verbatim
    spa_id = p3._onboard(p3.SPA)
    _, _, outs = p3._converse(spa_id, [GAP_QUERY])
    last = outs[-1]
    with db.tenant_session(spa_id) as cur:
        spa_gaps = store.list_gap_events(cur)
    r.check(
        "An inquiry the profile can't answer escalates AND is recorded verbatim as a gap",
        (last.state_after == "ESCALATED"
         and len(spa_gaps) == 1
         and spa_gaps[0].raw_query_text == GAP_QUERY
         and spa_gaps[0].classified_category is None),
        "The spa sells massage and facials, not laser hair removal. Phase 3 escalates rather\n"
        "than inventing a price (GATE 3 check 5 already proves that). Feature 1 adds exactly one\n"
        "side effect to that existing transition: a GapEvent row carrying the prospect's exact\n"
        "words, with no category yet (categorization is a later, optional step — check 5). No new\n"
        "decision — the escalation happened for the same reason it always did.",
        f"| state: {last.state_before} -> {last.state_after} [{last.action}]\n"
        f"| gap rows: {len(spa_gaps)}\n"
        f"| raw_query_text: {spa_gaps[0].raw_query_text!r}\n"
        f"| classified_category: {spa_gaps[0].classified_category!r}",
    )

    # ---- 2. the gap surfaces, verbatim, in the owner summary
    with db.tenant_session(spa_id) as cur:
        tenant = store.get_tenant(cur)
        threads = store.list_threads(cur)
        receipts_ = store.list_receipts(cur)
        gaps_ = store.list_gap_events(cur)
    s = summary.build_summary(threads, receipts_, since=since, gap_events=gaps_)
    text = summary.render_summary_text(tenant, s)
    r.check(
        "The gap appears, verbatim, in the owner-facing summary — the feature's whole payoff",
        (s.product_gaps == 1 and GAP_QUERY in s.gap_examples and GAP_QUERY in text),
        "`build_summary` counts the gap and carries the prospect's own words into the report:\n"
        "the owner reads exactly what their market asked for that they don't sell. The example\n"
        "text is `GapEvent.raw_query_text`, the same string check 1 stored — never synthesized.",
        f"| product_gaps: {s.product_gaps}\n| gap_examples: {s.gap_examples}\n|\n"
        + "\n".join(f"| {ln}" for ln in text.splitlines()),
    )

    # ---- 3. a floor breach escalates but is NOT a product gap
    breach_id = p3._onboard(p3.SPA)
    p3._converse(breach_id, ["How much is a signature facial?", "I can only do 40"])
    with db.tenant_session(breach_id) as cur:
        breach_gaps = store.list_gap_events(cur)
    r.check(
        "A floor breach escalates but writes NO gap — 'not offered' is not the same as 'too cheap'",
        breach_gaps == [],
        "£40 against a £70 floor breaches and escalates (GATE 3 check 7). But the prospect asked\n"
        "for a service the tenant DOES sell — that is not a product gap, and `engine.decide` sets\n"
        "`product_gap` only on the Unquotable branch, never on a floor breach, a human request, or\n"
        "a tripped trigger. Recording it as demand for an unoffered service would be a lie; this\n"
        "check proves the boundary holds.",
        f"| gap rows for a floor-breach thread: {breach_gaps}",
    )

    # ---- 4. ISOLATION — a second tenant's gaps and summary never contain the first's
    other_id = p3._onboard(p3.LEGAL)
    p3._converse(other_id, ["Do you handle unfair dismissal claims, and what's your fee?"])
    with db.tenant_session(other_id) as cur:
        o_tenant = store.get_tenant(cur)
        o_threads = store.list_threads(cur)
        o_receipts = store.list_receipts(cur)
        o_gaps = store.list_gap_events(cur)
    s_other = summary.build_summary(o_threads, o_receipts, since=since, gap_events=o_gaps)
    other_text = summary.render_summary_text(o_tenant, s_other)
    spa_gap_texts = {g.raw_query_text for g in gaps_}
    other_gap_texts = {g.raw_query_text for g in o_gaps}
    r.check(
        "ISOLATION — the barrister's gaps and summary contain none of the spa's",
        (not (spa_gap_texts & other_gap_texts)
         and s_other.product_gaps == 0
         and GAP_QUERY not in other_text),
        "The barrister asked about a service it DOES offer, so it has no gap of its own — and it\n"
        "certainly cannot see the spa's. `store.list_gap_events` only ever returns rows its\n"
        "RLS-scoped session can read (the identical `tenant_isolation` policy GATE 1 proved for\n"
        "every tenant table — no new mechanism for this feature). The spa's laser-hair-removal\n"
        "gap cannot appear in the barrister's report under any query.",
        f"| spa gap texts: {sorted(spa_gap_texts)}\n"
        f"| barrister gap texts: {sorted(other_gap_texts)}\n"
        f"| overlap: {len(spa_gap_texts & other_gap_texts)}\n"
        f"| barrister product_gaps: {s_other.product_gaps}",
    )

    # ---- 5. honest degradation — no LLM key means raw text, never a fabricated category
    with _NoLLMKey():
        no_key_category = gaps.classify_gap(GAP_QUERY)
        with db.tenant_session(spa_id) as cur:
            enriched = gaps.classify_pending(cur, tenant)
        s_raw = summary.build_summary(threads, receipts_, since=since, gap_events=gaps_)
        raw_text = summary.render_summary_text(tenant, s_raw)
    r.check(
        "With no LLM key, the gap is shown as raw text — never dropped, never a fabricated category",
        (no_key_category is None and enriched == []
         and s_raw.gap_patterns == [] and GAP_QUERY in raw_text),
        "Categorization is optional enrichment (a later scheduled step, `gaps.classify_pending`),\n"
        "not a decision — so it must degrade honestly, per the addendum. With LLM_API_KEY blanked,\n"
        "`classify_gap` returns None, `classify_pending` updates nothing, `gap_patterns` is empty,\n"
        "and the report still shows the prospect's verbatim question with a plain note that a key\n"
        "would cluster them. A gap is never silently omitted and never given an invented label.",
        f"| classify_gap(no key) -> {no_key_category!r}\n"
        f"| classify_pending(no key) -> {enriched}\n"
        f"| gap_patterns: {s_raw.gap_patterns}\n"
        f"| report contains the verbatim gap: {GAP_QUERY in raw_text}",
    )
