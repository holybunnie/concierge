"""The tenant's own words for the things CONCIERGE has to name (§11).

There are exactly two concepts the engine cannot avoid naming in a reply: the thing being
booked, and the person it is being booked for. A dentist calls those a *consultation* and a
*patient*. An estate agent calls them a *viewing* and a *buyer*. A spa says *appointment* and
*client*; a plumber says *call-out* and *customer*; a barrister says *conference* and
*instructing solicitor*.

Two wrong answers, and this module exists to avoid both.

**Hardcode one trade's words** and the product is that trade's product. A reply that offers a
"treatment" to a legal client is not a small blemish — it tells the prospect the business is
using something generic and badly configured, which is precisely the impression a £250/hour
practice is paying to avoid.

**Scrub every domain word out** and every business gets "your service" and "the customer". That
is trade-neutral in the sense that a blank page is trade-neutral. It reads as a form, it loses
the tenant's register, and for a clinic it is actively wrong.

So the words are *tenant data*, carried in the profile like a price is. Each vertical template
proposes the right word for its trade as an EXAMPLE and asks the tenant to confirm or correct
it — the same discipline as every other field, and `build_profile` still cannot reach
`Field.example`. A trade with no template asks the question outright.

When a tenant has not answered, `words()` falls back to the blandest usable term and reports
the gap. That fallback is a deliberate, narrow exception to "never supply a value the tenant
did not give": a missing noun is not a missing price. Naming an appointment "appointment"
commits the business to nothing, whereas refusing to reply until the word is confirmed would
strand real inquiries over a cosmetic gap. It is flagged everywhere it is used — in the gap
report to the tenant, and in the receipt for the message it appeared in — so it is visible
rather than silently papered over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Used only when the tenant has not supplied their own, and always reported as a gap.
# Chosen to be inoffensive in every trade rather than natural in any.
FALLBACK_ENGAGEMENT = "appointment"
FALLBACK_CLIENT = "client"


@dataclass(frozen=True)
class Words:
    """The nouns a reply is allowed to use, and where each one came from."""

    engagement: str          # what a booked slot is called
    client: str              # what the person being served is called
    supplied: frozenset[str] # which of the above the tenant actually set
    gaps: tuple[str, ...]    # plain-English note per fallback in use

    def is_tenant_word(self, concept: str) -> bool:
        return concept in self.supplied

    @property
    def all_supplied(self) -> bool:
        return not self.gaps


def words(profile: dict[str, Any]) -> Words:
    """Read the tenant's lexicon, falling back only where they left it blank."""
    lex = profile.get("lexicon") or {}
    engagement = _clean(lex.get("engagement_noun"))
    client = _clean(lex.get("client_noun"))

    supplied, gaps = set(), []
    if engagement:
        supplied.add("engagement")
    else:
        gaps.append(
            f"No word set for what a booking with this business is called, so replies say "
            f"“{FALLBACK_ENGAGEMENT}”. Correct if this trade calls it something else — a "
            f"viewing, a consultation, a call-out — and replies will use that instead."
        )
    if client:
        supplied.add("client")
    else:
        gaps.append(
            f"No word set for the people this business serves, so replies say "
            f"“{FALLBACK_CLIENT}”. Set it if they are patients, guests, buyers or tenants."
        )

    return Words(
        engagement=engagement or FALLBACK_ENGAGEMENT,
        client=client or FALLBACK_CLIENT,
        supplied=frozenset(supplied),
        gaps=tuple(gaps),
    )


def _clean(value: Any) -> str | None:
    """A noun, not a sentence. Anything long is a misunderstood question, not a word."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip().strip(".")
    if not text or len(text) > 40:
        return None
    return text
