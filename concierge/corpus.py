"""A comprehension corpus, generated from each tenant's OWN profile.

The point of this module is to answer a question no existing gate asks: when a real client sends
a real question, does CONCIERGE answer it *correctly*, escalate it, or answer it **confidently and
wrongly**? The third outcome is the only genuinely dangerous one — a quote is signed, receipted,
and anchored on-chain as a commitment, so a right price attached to a misunderstood question is
worse than no answer at all.

**Nothing here is hardcoded to a trade.** There is no list of spa questions or legal questions in
this file, because nobody knows what a tenant will sell — the same reason `engine.PROSE` contains
no trade nouns. Instead a question is a TEMPLATE with a `{service}` hole, and the hole is filled
from `profile.services` — whatever that tenant happens to have said. A tenant selling drone surveys
gets drone-survey questions for free, from the same templates, with no code change.

Two generated cases deserve explanation:

- **"asks for something not on the menu"** cannot be written down without knowing the trade. It is
  generated instead by borrowing a service name from a DIFFERENT tenant in the same run. Whatever
  tenant B sells, tenant A almost certainly does not — so the case stays honest for any pair of
  trades, including two this codebase has never seen.
- **The qualifier cases** ("for two people", "90 minutes", "at my home") use generic commercial
  English — quantity, duration, location, timing — not trade vocabulary. "Two people" means the
  same thing to a barrister and a boat mechanic. `verify_comprehension` greps this module against
  `engine.TRADE_NOUNS` to keep it that way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from . import comprehension

# What the harness expects CONCIERGE to do with a question. Deliberately coarse: this corpus
# measures comprehension, not phrasing.
QUOTE = "quote"                # a figure may be sent — the question is unambiguous
NO_CONFIDENT_QUOTE = "hold"    # escalate, ask, or queue for the owner — but do NOT send a figure
ESCALATE = "escalate"          # must reach the owner
HUMAN = "human"                # the human-request path, checked before everything else

_WORD = re.compile(r"[a-z0-9]+")

# How a client phrases each qualifier class. The classes themselves are NOT redefined here — they
# are `comprehension.QUALIFIERS`, the same four the engine acts on — so a class added there
# without phrasings written for it here shows up as an untested class rather than passing
# silently. `verify_comprehension` check 1 asserts that correspondence in both directions.
#
# The phrasings are generic commercial English: a quantity changes who it is for, a duration how
# long, a location where. None names a trade, a service or a profession.
_PHRASINGS: dict[str, tuple[str, ...]] = {
    "quantity": ("for two people", "for a group of six", "for both of us"),
    "duration": ("for two hours", "as a 90 minute session", "for the whole day"),
    "location": ("at my home", "at our offices", "if you travel to me"),
    "timing": ("on a Sunday", "late in the evening", "on a bank holiday"),
}

QUALIFIER_CLASSES: dict[str, tuple[str, ...]] = {
    cls: _PHRASINGS.get(cls, ()) for cls in comprehension.QUALIFIERS
}


@dataclass(frozen=True)
class Question:
    """One generated client question and what a safe system must do with it."""
    text: str
    expect: str
    kind: str
    service: str | None = None

    @property
    def is_dangerous_if_quoted(self) -> bool:
        return self.expect in (NO_CONFIDENT_QUOTE, ESCALATE, HUMAN)


def service_names(profile: dict[str, Any]) -> list[str]:
    """Service names as the tenant stored them — dicts (a priced menu) or bare strings."""
    out = []
    for item in profile.get("services") or []:
        name = item.get("name", "") if isinstance(item, dict) else str(item)
        if name:
            out.append(name)
    return out


def _distinctive(name: str) -> list[str]:
    return [w for w in _WORD.findall(name.lower()) if len(w) >= 3]


def generate(profile: dict[str, Any], *, foreign_services: list[str] | None = None
             ) -> Iterator[Question]:
    """Every question this corpus can ask of ONE tenant, derived from that tenant's own profile.

    `foreign_services` are service names belonging to a different tenant — used to construct the
    "asked for something you do not offer" case without this file having to know what any trade
    sells.
    """
    names = service_names(profile)

    for name in names:
        # --- unambiguous price questions. These SHOULD quote; failing to is a usability bug,
        # and this corpus reports it, but it is not the dangerous direction.
        yield Question(f"How much is {name}?", QUOTE, "price_direct", name)
        yield Question(f"What does {name} cost?", QUOTE, "price_direct", name)
        yield Question(f"Hi, I'd like to book {name}. What's the price?", QUOTE, "price_polite", name)

        # --- the dangerous family: a real service PLUS a qualifier the profile may not cover.
        # A system that ignores the qualifier answers the wrong question with a real figure.
        for cls, phrasings in QUALIFIER_CLASSES.items():
            # Whether a qualified question is answerable is a property of THIS tenant's profile,
            # not of the question. A tenant who recorded a travel policy can and should answer
            # "can you come to me?" — with their own sentence beside the figure. A tenant who
            # recorded nothing must send it to a human. Same question, same code, opposite
            # correct outcomes, so the expectation is computed per tenant rather than fixed.
            covered = comprehension.covers(profile, cls) is not None
            for phrasing in phrasings:
                yield Question(f"How much is {name} {phrasing}?",
                               QUOTE if covered else NO_CONFIDENT_QUOTE,
                               f"qualifier_{cls}", name)

        # --- a question ABOUT the service that is not a price question at all. Answering these
        # with a price is the "duration question answered with a figure" defect.
        yield Question(f"How long does {name} take?", NO_CONFIDENT_QUOTE, "intent_duration", name)
        yield Question(f"Is {name} suitable for me?", NO_CONFIDENT_QUOTE, "intent_suitability", name)

        # --- a partial name. Whether this is ambiguous depends on the tenant's OWN menu, so the
        # expectation is computed per tenant rather than assumed.
        words = _distinctive(name)
        if len(words) > 1:
            fragment = words[-1]
            shared = [n for n in names if fragment in _distinctive(n)]
            yield Question(
                f"How much for a {fragment}?",
                QUOTE if len(shared) == 1 else NO_CONFIDENT_QUOTE,
                "partial_name_unique" if len(shared) == 1 else "partial_name_ambiguous",
                name if len(shared) == 1 else None,
            )

    # --- something this tenant does not sell, borrowed from another tenant's menu.
    for foreign in (foreign_services or []):
        if foreign not in names and not (set(_distinctive(foreign)) & {w for n in names
                                                                       for w in _distinctive(n)}):
            yield Question(f"Do you do {foreign}?", ESCALATE, "not_offered", None)

    # --- the tenant's own escalation triggers, in the client's words rather than the owner's.
    for trigger in (profile.get("escalation_triggers") or [])[:3]:
        yield Question(f"Quick question — {str(trigger).lower()}?", ESCALATE, "trigger", None)

    # --- paths that must work in every trade, from any state.
    yield Question("Can I speak to a human please?", HUMAN, "human_request", None)
    yield Question("What's your address and are you open today?", NO_CONFIDENT_QUOTE,
                   "unknown_logistics", None)


def summarise(results: list[tuple[Question, str, bool, bool]]) -> dict[str, Any]:
    """Fold per-question outcomes into the numbers the suite judges.

    `results` is (question, action_taken, a_price_was_sent, answered_autonomously). Two numbers
    matter and they pull against each other, which is the point of reporting both:

      * `wrong_confident` — a price sent for a question it does not answer. Target: ZERO. This
        is the number that costs money, because the figure is anchored as a commitment.
      * `autonomy` — the share answered without pulling in a human. Target: high. A system that
        escalated everything would score a perfect zero on the first number and be worthless.

    Autonomy is reported over the ANSWERABLE subset as well as overall. This corpus is an
    adversarial sweep, not a sample of real traffic — it deliberately loads in questions no
    stored profile could answer, so overall autonomy here understates what a real inbox would
    see. The answerable-subset figure is the honest one to hold to a target.
    """
    wrong_confident = [(q, a) for q, a, sent, _ in results if sent and q.is_dangerous_if_quoted]
    missed = [(q, a) for q, a, sent, _ in results if not sent and q.expect == QUOTE]
    autonomous = [r for r in results if r[3]]
    answerable = [r for r in results if r[0].expect == QUOTE
                  or r[0].kind in ("intent_duration", "partial_name_unique")]
    answerable_auto = [r for r in answerable if r[3]]
    by_kind: dict[str, dict[str, int]] = {}
    for q, _, sent, auto in results:
        bucket = by_kind.setdefault(q.kind, {"total": 0, "price_sent": 0, "autonomous": 0})
        bucket["total"] += 1
        bucket["price_sent"] += 1 if sent else 0
        bucket["autonomous"] += 1 if auto else 0
    return {
        "total": len(results),
        "wrong_confident": wrong_confident,
        "missed_quotes": missed,
        "autonomy": len(autonomous) / len(results) if results else 0.0,
        "answerable_total": len(answerable),
        "answerable_autonomy": (len(answerable_auto) / len(answerable)) if answerable else 0.0,
        "by_kind": by_kind,
    }
