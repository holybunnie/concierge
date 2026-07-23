"""Negotiation bounds (§2, §9) — the rule that cannot be talked past.

One function matters here: `negotiate`. A prospect names a number, and this decides whether
CONCIERGE may agree to it or must hand the conversation to the owner. The decision is
arithmetic over the tenant's stored rules. There is no persuasion surface: the prospect's
wording is not an input, so "I'm a returning client", "the place down the road does it for
£60" and "final offer, take it or leave it" all reduce to the same comparison against the same
floor. That is the point of doing it in code rather than in a prompt.

Like `pricing`, nothing here knows what trade the tenant is in. It reads the canonical rules —
`floor` and `max_discount` — so an estate agent's commission floor and a dog groomer's cash
floor are the same branch.

Three decisions that are deliberate, and each has a harness check:

**The most restrictive rule binds.** A tenant with a £70 floor and a 15% discount cap on an £85
treatment can go to £72.25, not £70 — the cap binds first. Taking the floor alone would give
away £2.25 the owner said was not on offer. `Ruling.binding_rule` names which one bound, so the
owner can see why, and the harness proves both orderings.

**A breach escalates; it does not counter.** When the ask is below the floor the reply is not
"I can do £72.25" — it is a hand-off. Countering at the floor would publish the tenant's
reservation price to anyone willing to lowball once, which converts a floor into the new list
price within a week of real traffic.

**No rule means no negotiation.** A profile with no floor and no cap does not get an
"reasonable" default discount. Absence of a rule is never read as permission (§2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import pricing
from .pricing import Quote


@dataclass(frozen=True)
class Bound:
    """One rule constraining a negotiation, reduced to the quote's own unit."""

    rule_name: str          # canonical name, e.g. "floor" / "max_discount"
    profile_path: str
    raw: Any                # what the tenant actually typed
    limit: float            # the lowest acceptable value this rule permits, in the quote's unit
    derivation: str         # how `limit` was reached, in plain English

    def __str__(self) -> str:
        return f"{self.profile_path} = {self.raw!r} -> lowest acceptable {self.limit:,.2f}  ({self.derivation})"


@dataclass(frozen=True)
class Ruling:
    """The outcome of one negotiation step, with the reasoning attached."""

    allowed: bool
    agreed: float | None        # what CONCIERGE may commit to, in the quote's unit
    floor: float | None         # the binding limit
    binding_rule: str | None    # which rule set that limit
    bounds: tuple[Bound, ...]   # every rule considered, including the ones that did not bind
    explanation: str            # for the owner and the harness — not sent to the prospect

    @property
    def breach(self) -> bool:
        return not self.allowed


def bounds_for(profile: dict[str, Any], quote: Quote) -> list[Bound]:
    """Every stored rule that constrains this quote, expressed in the quote's own unit.

    A floor is only comparable to a quote in the same unit: a 1.2% commission floor says
    nothing about an £85 cash price. A mismatched floor is dropped here and reported rather
    than coerced — coercing it would invent a bound the tenant never set.
    """
    out: list[Bound] = []
    if quote.amount is None:
        return out

    floor = pricing.rule(profile, pricing.RULE_FLOOR)
    if floor and floor.get("value") is not None:
        floor_unit = "cash" if floor["kind"] == "cash" else "percent"
        if floor_unit == quote.unit:
            out.append(Bound(
                rule_name=pricing.RULE_FLOOR,
                profile_path=f"pricing_rules.{pricing.RULE_FLOOR}",
                raw=floor.get("raw"), limit=float(floor["value"]),
                derivation="the tenant's stated floor, used as-is",
            ))

    cap = pricing.rule(profile, pricing.RULE_MAX_DISCOUNT)
    if cap and cap.get("value") is not None:
        pct = float(cap["value"])
        limit = quote.amount * (1 - pct / 100)
        out.append(Bound(
            rule_name=pricing.RULE_MAX_DISCOUNT,
            profile_path=f"pricing_rules.{pricing.RULE_MAX_DISCOUNT}",
            raw=cap.get("raw"), limit=limit,
            derivation=f"{pricing.fmt_amount(quote.amount)} less {pricing.fmt_amount(pct)}% "
                       f"= {limit:,.2f}",
        ))
    return out


def negotiate(profile: dict[str, Any], quote: Quote, asked: float) -> Ruling:
    """May CONCIERGE agree to `asked`? Arithmetic only — the prospect's wording is not an input."""

    if quote.unit == "prose" or quote.amount is None:
        return Ruling(
            allowed=False, agreed=None, floor=None, binding_rule=None, bounds=(),
            explanation=(
                f"The tenant stated this price in words — “{quote.stated_terms}” — rather than "
                f"as a number, so there is nothing to compare an offer against. Any negotiation "
                f"on it goes to the owner."),
        )

    bounds = bounds_for(profile, quote)
    if not bounds:
        return Ruling(
            allowed=False, agreed=None, floor=None, binding_rule=None, bounds=(),
            explanation=(
                "This tenant set no floor and no discount cap that applies to this quote, so "
                "there is no rule permitting a reduction. I do not infer a reasonable discount "
                "from silence — the request goes to the owner."),
        )

    # The most restrictive rule wins. Ties go to the floor, which is the rule the owner is
    # most likely to be able to recite from memory if a client ever challenges it.
    binding = max(bounds, key=lambda b: (b.limit, b.rule_name == pricing.RULE_FLOOR))
    floor = binding.limit

    if asked >= quote.amount:
        return Ruling(
            allowed=True, agreed=quote.amount, floor=floor, binding_rule=binding.rule_name,
            bounds=tuple(bounds),
            explanation=(
                f"The prospect asked for {asked:,.2f}, which is at or above the quoted "
                f"{quote.amount:,.2f}. No discount is needed, so the quoted price stands — I do "
                f"not charge more than the profile says, even when offered."),
        )

    if asked >= floor:
        return Ruling(
            allowed=True, agreed=asked, floor=floor, binding_rule=binding.rule_name,
            bounds=tuple(bounds),
            explanation=(
                f"The prospect asked for {asked:,.2f}. The binding rule is "
                f"{binding.profile_path} ({binding.derivation}), which permits going as low as "
                f"{floor:,.2f}. {asked:,.2f} is within that, so it may be agreed without the "
                f"owner."),
        )

    return Ruling(
        allowed=False, agreed=None, floor=floor, binding_rule=binding.rule_name,
        bounds=tuple(bounds),
        explanation=(
            f"The prospect asked for {asked:,.2f}, which is below {floor:,.2f} — the limit set "
            f"by {binding.profile_path} ({binding.derivation}). This is a breach, so it "
            f"escalates to the owner. I do not counter at {floor:,.2f}: countering would tell "
            f"the prospect exactly where the floor is, and a floor that is public becomes the "
            f"list price."),
    )
