"""Product-Gap Intelligence (addendum Feature 1) — optional categorization.

Phase 3's state machine already has the one decision this feature instruments: an inquiry the
tenant's profile cannot answer escalates rather than inventing a service (`Unquotable`, never
guessed — see `pricing.match_service`). `engine.step` writes exactly one new row for that
existing transition (`store.insert_gap_event`) — no new decision logic there, and no network
call from inside the no-network decision path GATE 3 check 5 proves.

This module is the OPTIONAL enrichment half, split out on purpose: turning a gap's raw text into
a coarse category ("service not offered", "pricing tier not offered", "geography not served") is
narration, not a decision — it never feeds back into pricing, guardrails, or what CONCIERGE says
to a prospect. It runs later, on a schedule (`classify_pending`, called from
`concierge.scheduler`), against real stored gaps — never inline with the escalation itself.

Absent an LLM key (OPERATOR_PROVIDES item 7), `classify_gap` returns None and stays that way: the
report shows the gap as raw, unclustered text and says so plainly, per the addendum's explicit
"must degrade honestly" requirement. A raw-text gap is never silently dropped.
"""

from __future__ import annotations

import json

from psycopg import Cursor

from . import config, store
from .models import GapEvent, Tenant

CATEGORIES = (
    "service_not_offered",
    "pricing_tier_not_offered",
    "geography_not_served",
    "other",
)

# Simple text classification — a single Claude API call, no tools, no agent loop. Opus per
# default policy; this is cheap (max_tokens=50, structured output) and runs on a schedule, not
# per-request.
_MODEL = "claude-opus-4-8"

_SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": list(CATEGORIES)}},
    "required": ["category"],
    "additionalProperties": False,
}


def classify_gap(raw_text: str) -> str | None:
    """One coarse label for one gap's raw text, or None.

    None means either no LLM key is configured, or the call failed for any reason — both are
    the same honest outcome from the report's point of view. The broad `except` here is
    deliberate: this function is narration, never a decision, so no failure inside it may ever
    propagate into breaking the report or the scheduler that calls it.
    """
    api_key = config.get("LLM_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=50,
            system=(
                "A prospect asked a business something its stored profile could not answer. "
                "Classify the request into exactly one category. Reply with the structured "
                "JSON only — no other text."
            ),
            messages=[{"role": "user", "content": raw_text}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        category = json.loads(text).get("category")
        return category if category in CATEGORIES else None
    except Exception:
        return None


def classify_pending(cur: Cursor, tenant: Tenant) -> list[GapEvent]:
    """Enrich every one of this tenant's gaps that has no category yet.

    Returns the rows actually updated — empty, honestly, when no LLM key is configured, rather
    than looping through every pending gap just to learn that N times over.
    """
    if not config.get("LLM_API_KEY"):
        return []
    pending = [g for g in store.list_gap_events(cur) if g.classified_category is None]
    updated = []
    for gap in pending:
        category = classify_gap(gap.raw_query_text)
        if category is not None:
            updated.append(store.update_gap_category(cur, gap_id=gap.gap_id, category=category))
    return updated
