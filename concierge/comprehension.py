"""Did we understand the question? — layers 1 and 2 of the comprehension fix (GATE 3c).

`pricing.match_service` answers "which service is this about". It scores what the inquiry and a
service name have in common, and — this was the defect GATE 3c measured — it says nothing at all
about the words it did NOT consume. "How much is X for two people?" and "How much is X?" produce
an identical match, so they produced an identical reply: one real price, one of them for the wrong
question. 70 of 103 generated questions failed that way.

This module supplies the two missing reads, both deterministic, both trade-neutral:

  1. **Qualifiers** — words the inquiry spent that the service name did not consume, and which
     change what a correct answer is: a quantity, a duration, a place, a time. These are generic
     commercial English. "For two people" means the same thing to a barrister and a boat mechanic,
     which is why this can be a fixed list without becoming one trade's product.

  2. **Intent** — what is being asked ABOUT the service. A price question, a duration question, a
     suitability question, a logistics question. Today everything that matches a service is
     treated as "what does it cost", which is why "how long does it take?" came back with a price.

Neither read invents an answer. Each one either finds the answer already sitting in the tenant's
stored profile — `duration_min` is right there, and answering from it turns an escalation into a
real reply — or reports that the profile cannot answer it, and the caller escalates to the owner.
That split is the whole design: escalate only what is genuinely unanswerable, so the agent stays
useful rather than becoming uniformly cautious.

Nothing here reaches a language model, and no word in this file names a trade — GATE 3c check 1
and GATE 3's TRADE_NOUNS grep both hold it to that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import pricing

_WORD = re.compile(r"[a-z0-9]+")

# Ordinary politeness and ordinary price-question vocabulary. Neither says anything about WHICH
# service is meant or WHAT is being asked, so neither should count as an unexplained word.
#
# `pricing.STOPWORDS` is the same list `match_service` already uses, reused rather than copied:
# the first version of this module kept its own, omitted "fee" and "rate", and scored "what's
# your fee for X?" at 0.67 comprehension — queueing a completely clear question for the owner.
_FILLER = frozenset("""
hi hello thanks thank please id ill im interested keen wondering
""".split()) | pricing.STOPWORDS

# ---------------------------------------------------------------- layer 1: qualifiers
#
# Each class is generic commercial English, and each one changes what a correct answer is:
#   quantity — for how many; duration — for how long; location — where; timing — when.
# A qualifier is only ACTED on when the profile has nothing that answers it (see `_covered_by`),
# so a tenant who records a travel policy or a per-group rate keeps answering these autonomously.

QUALIFIERS: dict[str, frozenset[str]] = {
    "quantity": frozenset("""
        two three four five six seven eight nine ten both couple couples group groups party
        pair each per everyone additional extra another second people person persons
    """.split()),
    "duration": frozenset("""
        hour hours hourly minute minutes min mins day days week weeks month months session
        sessions half full whole longer shorter overnight
    """.split()),
    "location": frozenset("""
        home house office offices site venue place travel travelling traveling mobile remote
        onsite premises address away local nearby distance mile miles km
    """.split()),
    "timing": frozenset("""
        today tonight tomorrow weekend weekends saturday sunday monday tuesday wednesday
        thursday friday morning afternoon evening night late early urgent urgently asap
        holiday holidays bank christmas easter
    """.split()),
}

# Where in the profile an answer to each qualifier class could live. Absent → the class is not
# covered and a question carrying it escalates. Present → the owner has already answered it once,
# at onboarding, and the agent may proceed. This is the seam that turns better onboarding data
# directly into higher autonomy, with no code change.
QUALIFIER_COVERAGE: dict[str, tuple[str, ...]] = {
    "quantity": ("group_pricing", "party_pricing", "per_person_pricing"),
    "duration": ("duration_options", "session_lengths"),
    "location": ("service_area", "travel_policy", "callout_policy"),
    "timing": ("availability_policy", "out_of_hours_policy"),
}

# ---------------------------------------------------------------- layer 2: intent
#
# Cue phrases, longest-first so "how long" wins over "how". Generic English: none of these name a
# trade. Intent is only consulted once a service has already matched — this decides what is being
# asked ABOUT that service, not which service it is.

PRICE = "price"
DURATION = "duration"
SUITABILITY = "suitability"
LOGISTICS = "logistics"

INTENT_CUES: dict[str, tuple[str, ...]] = {
    DURATION: ("how long", "how many hours", "how many minutes", "duration", "take long",
               "last for", "how much time"),
    SUITABILITY: ("suitable", "right for", "safe for", "safe to", "ok for", "okay for",
                  "recommend", "should i", "would you advise", "appropriate for", "allergic",
                  "can i still", "am i able"),
    LOGISTICS: ("where are you", "your address", "opening hours", "are you open", "parking",
                "how do i find", "directions"),
    PRICE: ("how much", "what does it cost", "what do you charge", "price", "cost", "quote",
            "fee", "rate", "charge"),
}


# Every word appearing in an intent cue. These are ACCOUNTED FOR by definition: a word that told
# us what was being asked ("fee", "cost", "long", "suitable") has done its job and is not an
# unexplained leftover. Deriving this from INTENT_CUES rather than writing a third word list
# means adding a cue can never again leave a word silently unexplained — which is exactly how
# "what's your fee for X?" came to score 0.67 and queue for the owner.
_CUE_WORDS = frozenset(w for cues in INTENT_CUES.values() for cue in cues
                       for w in _WORD.findall(cue))


@dataclass(frozen=True)
class Assessment:
    """What we understood, and — the part that was missing — what we did not."""
    intent: str
    qualifiers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    uncovered: tuple[str, ...] = ()
    # class -> the tenant's own policy text, stated verbatim in the reply. Never paraphrased,
    # never turned into a new figure.
    covered: dict[str, str] = field(default_factory=dict)
    unconsumed: tuple[str, ...] = ()
    comprehension: float = 1.0

    @property
    def answerable(self) -> bool:
        """True when nothing in the question is left unanswered by the stored profile."""
        return not self.uncovered

    def reason(self, service: str) -> str:
        """Why this is going to a human, in the owner's words — never the client's to read."""
        bits = []
        for cls in self.uncovered:
            found = ", ".join(self.qualifiers.get(cls, ()))
            bits.append(f"{cls} ({found})")
        return (
            f"Asked about {service}, but the message qualifies it in a way the stored profile "
            f"does not cover: {'; '.join(bits)}. Answering with the standard figure would quote "
            f"a price for a different question than the one asked, so this goes to you instead."
        )


def tokens(text: str) -> list[str]:
    """The words that carry meaning, filtered exactly as `pricing._tokens` filters them.

    The >= 3 length floor is not a rounding-off — it is what drops "is", "it", "me", "do" and
    the rest of English's function words without anyone having to enumerate them. Matching
    pricing's rule exactly matters: this function decides what counts as UNEXPLAINED, and
    pricing decides what counts as MATCHED. If the two disagree about which words are worth
    reading, a word can be invisible to the matcher and unexplained here at the same time, and
    every clear question scores as half-understood.
    """
    return [t for t in _WORD.findall((text or "").lower()) if t not in _FILLER and len(t) >= 3]


def classify_intent(text: str) -> str:
    """What is being asked about the service. Defaults to PRICE — the historical behaviour.

    Ordering matters: a message can contain both "how long" and "cost", and the more specific
    reading wins. DURATION/SUITABILITY/LOGISTICS are checked before PRICE for that reason.
    """
    low = (text or "").lower()
    for intent in (DURATION, SUITABILITY, LOGISTICS, PRICE):
        if any(cue in low for cue in INTENT_CUES[intent]):
            return intent
    return PRICE


def covers(profile: dict[str, Any], cls: str) -> str | None:
    """The tenant's OWN words for this qualifier class, or None if they never gave any.

    Returns the text rather than a boolean on purpose. "Covered" cannot mean "proceed and quote
    the base price as though the qualifier were not there" — a tenant whose travel policy is
    "+£40 outside the city" would have that answer silently dropped, which is the same class of
    wrong-but-confident answer this whole module exists to prevent. So coverage produces the
    policy text, and the caller states it verbatim alongside the figure.
    """
    pricing_rules = profile.get("pricing_rules") or {}
    for key in QUALIFIER_COVERAGE.get(cls, ()):
        value = profile.get(key) or pricing_rules.get(key)
        if value:
            return str(value).strip()
    return None


def assess(profile: dict[str, Any], text: str, *, service_name: str,
           matched_on: tuple[str, ...] = ()) -> Assessment:
    """Read the question for everything the service match threw away.

    `matched_on` are the words `pricing.match_service` consumed. Everything else the client
    spent — minus filler and minus the service's own remaining words — is what this looks at.
    """
    asked = tokens(text)
    service_words = set(_WORD.findall((service_name or "").lower()))
    consumed = set(matched_on) | service_words
    leftover = tuple(t for t in asked if t not in consumed)

    found: dict[str, tuple[str, ...]] = {}
    for cls, vocabulary in QUALIFIERS.items():
        hits = tuple(t for t in leftover if t in vocabulary)
        if hits:
            found[cls] = hits

    intent = classify_intent(text)
    # A duration question is not an uncovered duration qualifier — it is a question this profile
    # may well be able to answer outright (`services[].duration_min`). The caller decides that;
    # counting it here as "uncovered" would escalate a question we can answer.
    covered: dict[str, str] = {}
    uncovered_list = []
    for cls in found:
        if cls == "duration" and intent == DURATION:
            # Not an uncovered qualifier — it is a duration question this profile may be able to
            # answer outright from `services[].duration_min`. The caller decides.
            continue
        policy = covers(profile, cls)
        if policy:
            covered[cls] = policy
        else:
            uncovered_list.append(cls)
    uncovered = tuple(uncovered_list)

    # Comprehension: the share of what the client actually said that we accounted for. Feeds
    # Feature 2's fourth signal — see `confidence.py`. Deliberately blunt: it does not need to
    # know what an unrecognised word MEANS, only that the client spent words we cannot explain.
    explained = [t for t in asked if t in consumed or t in _CUE_WORDS
                 or any(t in vocab for vocab in QUALIFIERS.values())]
    score = len(explained) / len(asked) if asked else 1.0

    return Assessment(intent=intent, qualifiers=found, uncovered=uncovered, covered=covered,
                      unconsumed=leftover, comprehension=round(score, 3))
