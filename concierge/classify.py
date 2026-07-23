"""Vertical classification — deterministic, and able to say "I don't know".

§2 permits an LLM to reason about vertical onboarding, and a later phase may use one to handle
descriptions this lexicon misses. It is deliberately not the primary path, for two reasons:

  * **It must work with no LLM key.** The operator has none yet. A classifier that needs a
    credential we don't have is a classifier that doesn't exist.
  * **It must be auditable.** This returns the exact terms that drove the decision, so a
    misclassification is a five-second diagnosis and a one-line lexicon fix. An LLM returning
    "real_estate" with no evidence is not debuggable, and the vertical choice determines which
    questions the tenant is asked — get it wrong and you collect the wrong profile entirely.

Abstention is a first-class outcome. When the top two verticals are close, or nothing scores,
the result is `unclear` and onboarding ASKS rather than proceeding on a coin flip. §2's spirit is
that an unknown is escalated, not invented; this is the same rule applied to classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Weights: 3 = decisive on its own (a word only this trade uses), 2 = strong, 1 = suggestive.
# Terms are matched on word boundaries, case-insensitively, against the tenant's own description.
LEXICON: dict[str, dict[str, int]] = {
    "real_estate": {
        "estate agent": 3, "estate agency": 3, "lettings": 3, "letting agent": 3,
        "conveyancing": 1, "property": 2, "properties": 2, "landlord": 2, "landlords": 2,
        "tenant": 1, "tenants": 1, "viewing": 2, "viewings": 2, "listing": 2, "listings": 2,
        "asking price": 3, "freehold": 3, "leasehold": 3, "vendor": 2, "buyer": 1, "buyers": 1,
        "flat": 1, "flats": 1, "house": 1, "houses": 1, "rental": 2, "rentals": 2,
        "sale": 1, "sales": 1, "commission": 1, "realtor": 3, "real estate": 3, "pcm": 2,
    },
    "legal": {
        "barrister": 3, "solicitor": 3, "chambers": 3, "counsel": 2, "litigation": 3,
        "tribunal": 3, "claimant": 3, "defendant": 3, "jurisdiction": 2, "conflict check": 3,
        "legal": 2, "law": 2, "law firm": 3, "practice areas": 3, "instructed": 2,
        "advice": 1, "employment law": 3, "unfair dismissal": 3, "contract dispute": 2,
        "hearing": 2, "proceedings": 2, "client care": 2, "retainer": 2, "attorney": 3,
        "consultation fee": 2, "matter": 1, "matters": 1,
    },
    "spa_beauty": {
        "spa": 3, "salon": 3, "massage": 3, "facial": 3, "facials": 3, "treatment": 2,
        "treatments": 2, "therapist": 2, "beauty": 3, "manicure": 3, "pedicure": 3,
        "waxing": 3, "aesthetics": 2, "hot stone": 3, "deep tissue": 3, "aromatherapy": 3,
        "wellness": 2, "nails": 2, "brows": 2, "lashes": 2, "cancellation policy": 1,
        "booking": 1, "appointment": 1, "appointments": 1, "clinic": 1,
    },
}

# Below this, nothing recognisable was said about the trade.
MIN_SCORE = 3
# Top two this close means the description genuinely reads as either. Ask, don't guess.
MIN_MARGIN = 2


@dataclass(frozen=True)
class Classification:
    vertical: str                       # a template key, or "unclear"
    confident: bool
    scores: dict[str, int]
    evidence: dict[str, list[str]]      # vertical -> the exact terms that matched
    reason: str                         # plain English, shown to the tenant

    @property
    def matched_terms(self) -> list[str]:
        return self.evidence.get(self.vertical, [])


def classify(description: str) -> Classification:
    """Score a free-text business description against each vertical's lexicon."""
    text = (description or "").lower()

    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for vertical, terms in LEXICON.items():
        hits, total = [], 0
        for term, weight in terms.items():
            if re.search(rf"\b{re.escape(term)}\b", text):
                hits.append(f"{term} (+{weight})")
                total += weight
        scores[vertical] = total
        evidence[vertical] = hits

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_score), (second, second_score) = ranked[0], ranked[1]

    if top_score < MIN_SCORE:
        return Classification(
            "unclear", False, scores, evidence,
            "Nothing in that description told me what trade you're in, so I am not going to "
            "guess — the guess would decide which questions I ask you, and the wrong questions "
            "produce the wrong profile. I'll use the general template and ask you directly.",
        )

    if top_score - second_score < MIN_MARGIN:
        return Classification(
            "unclear", False, scores, evidence,
            f"That description reads as both {top} ({top_score}) and {second} ({second_score}), "
            f"and the gap is too small for me to call it. Rather than pick one and ask you the "
            f"wrong set of questions, I'll ask you which it is.",
        )

    return Classification(
        top, True, scores, evidence,
        f"Classified as {top} on {len(evidence[top])} matching terms, scoring {top_score} against "
        f"{second_score} for the next closest ({second}).",
    )
