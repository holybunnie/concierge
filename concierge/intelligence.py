"""Bounded model-assisted understanding.

The model identifies meaning but cannot create business facts. Services must be selected
verbatim from the tenant catalogue, evidence must occur in the inbound message, and no
model-produced number is accepted. Pricing and commitments remain deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import config, pricing

INTENTS = ("price", "duration", "suitability", "logistics", "other")
ACTS = ("enquiry", "acceptance", "negotiation", "human_request", "spam", "other")
QUALIFIERS = ("quantity", "duration", "location", "timing", "personal_circumstance")


@dataclass(frozen=True)
class Understanding:
    service_name: str | None
    intent: str
    act: str
    qualifier_classes: tuple[str, ...]
    evidence: tuple[str, ...]
    confidence: float


def interpret(profile: dict[str, Any], text: str) -> Understanding | None:
    """Return a validated reading, or ``None`` on uncertainty or provider failure."""
    if not config.enabled("SEMANTIC_INTELLIGENCE_ENABLED"):
        return None
    key = config.get("LLM_API_KEY")
    names = [pricing._service_name(item) for item in pricing.catalogue(profile)
             if pricing._service_name(item)]
    if not key or not names or not (text or "").strip():
        return None
    schema = {
        "type": "object",
        "properties": {
            "service_name": {"anyOf": [
                {"type": "string", "enum": names}, {"type": "null"},
            ]},
            "intent": {"type": "string", "enum": list(INTENTS)},
            "act": {"type": "string", "enum": list(ACTS)},
            "qualifier_classes": {
                "type": "array", "items": {"type": "string", "enum": list(QUALIFIERS)},
            },
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": [
            "service_name", "intent", "act", "qualifier_classes", "evidence", "confidence",
        ],
        "additionalProperties": False,
    }
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=10.0)
        response = client.messages.create(
            model=config.get("LLM_MODEL") or "claude-opus-4-8",
            max_tokens=300,
            system=(
                "Read the customer's meaning. Select service_name only from the supplied exact "
                "catalogue or null. Evidence must be short verbatim substrings of the message. "
                "Do not calculate, infer, repeat, or propose any price. Personal, medical, or "
                "legal fitness is suitability. A request for a person is human_request."
            ),
            messages=[{"role": "user", "content": json.dumps(
                {"catalogue": names, "message": text}, ensure_ascii=False)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        raw = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(raw)
        evidence = tuple(str(x).strip() for x in data["evidence"] if str(x).strip())
        low = text.lower()
        if any(piece.lower() not in low for piece in evidence):
            return None
        service = data["service_name"]
        confidence = float(data["confidence"])
        if service is not None and service not in names or confidence < 0.80:
            return None
        return Understanding(
            service, data["intent"], data["act"], tuple(data["qualifier_classes"]),
            evidence, confidence,
        )
    except Exception:
        return None
