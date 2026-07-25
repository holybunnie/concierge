"""Confidence-scored autonomy (Feature 2, the autonomy suite).

onboarding's vertical classifier already refuses to guess when a score is too close to call
("5 vs 4, too close — ask instead"). This module extends that same discipline from onboarding
into every pricing/negotiation decision the engine makes: a number, arithmetic over concrete,
named, stored facts, never an LLM's self-reported certainty and never a factor in what price is
offered. It decides one thing only — whether the reply pricing/guardrails already computed may
be sent, or must be held for the tenant to approve.

## The formula — fixed, documented, not tuned by anything

    score = 0.40 * profile_completeness + 0.45 * floor_proximity + 0.15 * precedent

  **profile_completeness** — did the tenant actually fill in the fields this decision leans on
  (floor, discount cap, escalation triggers, booking rules, ICP, their own lexicon), or is this
  reply resting on a profile still running mostly on fallbacks? A thin profile is the single
  biggest way an autonomous reply goes wrong — the guardrail it is being checked against may
  not even be set.

  **floor_proximity** — how far the agreed figure sits from the binding floor, as a fraction of
  the distance back to the quoted price. Weighted highest: this is the one signal that is
  specific to THIS decision rather than to the tenant in general, and it is the closest thing to
  a direct measure of "how risky is this exact figure". A quote at the ceiling is safe; a
  counter-offer sitting on the floor is one keystroke from a breach, so it scores least
  confident. No floor applies (nothing to sit close to, or nothing has been asked yet) scores
  full confidence on this signal alone.

  **precedent** — has this exact service, at a price within `PRECEDENT_BAND_PCT` of this one,
  been quoted and successfully booked before, for this tenant? A brand-new tenant has none yet.
  Weighted lowest, deliberately: it is a nudge that lets a proven price point clear the bar even
  when it sits close to the floor, never a suite a new tenant is blocked behind — a complete
  profile making a comfortable, non-marginal offer must still clear the threshold with zero
  precedent, or the feature would make every brand-new tenant's very first real negotiation
  wait on the owner, which is not what "conservative" is meant to buy.

Every signal that fired, and the score itself, is attached to the `Decision` and written into
the receipt (`receipts.record(..., confidence=...)`) — never just rendered for a screen. The
autonomy suite proves this by reading the score back out of the database row, not off a transcript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import lexicon, pricing
from .models import Receipt

WEIGHT_COMPLETENESS = 0.40
WEIGHT_PROXIMITY = 0.45
WEIGHT_PRECEDENT = 0.15

# Layer 3 (the comprehension suite): the share of the client's own words a decision must account for before it
# may send autonomously. Not a weight — a floor. See `score()` for why it caps rather than votes.
COMPREHENSION_FLOOR = 0.85

# "Defaults conservative" per the spec, calibrated against two real scenarios rather than
# picked in the abstract:
#   - a complete profile (completeness=1.0) making a COMFORTABLE offer well clear of its floor
#     (proximity >= ~0.35) must clear this with ZERO precedent — a brand-new tenant's first
#     real negotiation should not need the owner's sign-off just for being new, or the feature
#     would block the exact autonomy the engine already proved. 0.40 + 0.45*0.35 ≈ 0.56.
#   - a complete profile making a MARGINAL offer sitting close to its floor (proximity <= ~0.22)
#     does NOT clear this without precedent (0.40 + 0.45*0.22 ≈ 0.50) — that is the case this
#     feature exists to catch, and precedent (built from prior successful bookings at the same
#     band) is what lets it clear on repetition rather than on hope.
# 0.55 sits between those two, so the dividing line is "was this a real concession, comfortably
# inside the rules" vs "did this land right on the edge of them" — not "is this tenant new".
DEFAULT_AUTONOMY_THRESHOLD = 0.55

# Close enough to call the same offer, not so wide that a different tier of service counts as
# precedent for this one.
PRECEDENT_BAND_PCT = 0.10

# Three independently booked engagements at this band is enough to call this exact price point
# proven with this tenant's own clients. Deliberately small: this signal is a fifth of a fifth
# less consequential than a chosen weight, and a new tenant should not stay capped on it for
# long — it only ever nudges the total, never gates it alone (see WEIGHT_PRECEDENT).
PRECEDENT_FULL_CONFIDENCE_COUNT = 3

_COMPLETENESS_CHECKS: tuple[tuple[str, Any], ...] = (
    # A floor is "set" whether it's the flat rule or Feature 5's decaying curve — both are the
    # tenant stating a real floor, just in a richer shape for the curve.
    ("pricing_rules.floor", lambda p: bool(pricing.rule(p, pricing.RULE_FLOOR))
                                       or pricing.floor_curve(p) is not None),
    ("pricing_rules.max_discount", lambda p: bool(pricing.rule(p, pricing.RULE_MAX_DISCOUNT))),
    ("escalation_triggers", lambda p: bool(p.get("escalation_triggers"))),
    ("calendar_ref.booking_rules",
     lambda p: bool((p.get("calendar_ref") or {}).get("booking_rules"))),
    ("icp", lambda p: bool(p.get("icp"))),
    ("lexicon (tenant's own words, not the generic fallback)",
     lambda p: not lexicon.words(p).gaps),
)


def service_key(name: str) -> str:
    """A stable key for `profile.autonomy_thresholds`, since services have a name but no id."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "unknown_service"


def threshold_for(profile: dict[str, Any], key: str) -> float:
    """The tenant's own threshold for this service, or the conservative default.

    Never inferred, never adjusted mid-negotiation — set at onboarding or in settings, read
    here as-is. Absence of a tenant-set number is not read as permission to loosen it.
    """
    thresholds = profile.get("autonomy_thresholds") or {}
    value = thresholds.get(key)
    return float(value) if isinstance(value, (int, float)) else DEFAULT_AUTONOMY_THRESHOLD


@dataclass(frozen=True)
class Signal:
    name: str
    value: float          # 0..1
    weight: float
    detail: str

    def __str__(self) -> str:
        return f"{self.name}={self.value:.2f} (weight {self.weight:.2f}) — {self.detail}"


@dataclass(frozen=True)
class Confidence:
    """The result of scoring one pricing/negotiation decision."""

    score: float
    threshold: float
    autonomous: bool
    service_key: str
    signals: tuple[Signal, ...]

    def as_dict(self) -> dict[str, Any]:
        """The exact shape persisted onto the receipt (§8) — auditable, not just displayed."""
        return {
            "score": round(self.score, 4),
            "threshold": self.threshold,
            "autonomous": self.autonomous,
            "service_key": self.service_key,
            "signals": [
                {"name": s.name, "value": round(s.value, 4), "weight": s.weight,
                 "detail": s.detail}
                for s in self.signals
            ],
        }


def _completeness(profile: dict[str, Any]) -> Signal:
    present = [label for label, test in _COMPLETENESS_CHECKS if test(profile)]
    missing = [label for label, _ in _COMPLETENESS_CHECKS if label not in present]
    value = len(present) / len(_COMPLETENESS_CHECKS)
    return Signal(
        "profile_completeness", value, WEIGHT_COMPLETENESS,
        f"{len(present)}/{len(_COMPLETENESS_CHECKS)} relevant profile fields set"
        + (f"; missing: {', '.join(missing)}" if missing else " — nothing missing"),
    )


def _proximity(*, amount: float | None, agreed: float | None, floor: float | None) -> Signal:
    if floor is None or amount is None or agreed is None or amount <= floor:
        return Signal(
            "floor_proximity", 1.0, WEIGHT_PROXIMITY,
            "no binding floor applies to this figure, so there is nothing to sit close to",
        )
    frac = max(0.0, min(1.0, (agreed - floor) / (amount - floor)))
    return Signal(
        "floor_proximity", frac, WEIGHT_PROXIMITY,
        f"{agreed:,.2f} sits {frac * 100:.0f}% of the way from the floor ({floor:,.2f}) back "
        f"up to the quoted price ({amount:,.2f})",
    )


def _precedent(receipts: list[Receipt], *, service: str, agreed: float | None) -> Signal:
    if agreed is None:
        return Signal("precedent", 0.0, WEIGHT_PRECEDENT,
                       "no numeric figure to match precedent against")
    band = abs(agreed) * PRECEDENT_BAND_PCT
    matches = 0
    for rec in receipts:
        if rec.action != "booked":
            continue
        detail = (rec.decision or {}).get("detail") or {}
        if detail.get("service") != service:
            continue
        figure = detail.get("agreed")
        if figure is None:
            figure = detail.get("amount")
        if figure is None:
            continue
        if abs(float(figure) - agreed) <= band:
            matches += 1
    value = min(matches / PRECEDENT_FULL_CONFIDENCE_COUNT, 1.0)
    return Signal(
        "precedent", value, WEIGHT_PRECEDENT,
        f"{matches} prior booking(s) for {service!r} within "
        f"{PRECEDENT_BAND_PCT * 100:.0f}% of {agreed:,.2f} "
        f"(of {PRECEDENT_FULL_CONFIDENCE_COUNT} needed for full confidence on this signal)",
    )


def score(
    *, profile: dict[str, Any], service: str, amount: float | None, agreed: float | None,
    floor: float | None, receipts: list[Receipt], comprehension: float | None = None,
) -> Confidence:
    """Score one pricing/negotiation decision. Pure arithmetic over stored facts.

    `amount` is the quoted price; `agreed` is the figure actually on the table (equal to
    `amount` for a fresh quote, lower for an in-progress negotiation); `floor` is the binding
    guardrail limit in the same unit, or None when nothing constrains this figure. `receipts`
    is this tenant's own history, already RLS-scoped by the caller — never fetched here.
    """
    signals = [
        _completeness(profile),
        _proximity(amount=amount, agreed=agreed, floor=floor),
        _precedent(receipts, service=service, agreed=agreed),
    ]
    total = sum(s.value * s.weight for s in signals)
    key = service_key(service)
    threshold = threshold_for(profile, key)
    autonomous = total >= threshold

    # Layer 3 (the comprehension suite). Comprehension enters as a CAP, not as a fourth weighted term, and the
    # distinction is deliberate twice over.
    #
    # Arithmetically: re-weighting three signals to make room for a fourth would move every
    # existing decision, and this formula is calibrated against two named scenarios that the
    # autonomy, engine and booking suites all depend on — the last recalibration broke both of them. A cap
    # leaves every score in this codebase exactly where it was and can only ever withhold a
    # send, never authorise one.
    #
    # In principle: the other three signals answer "how safe is this figure?". Comprehension
    # answers "is this the right question?" — and no amount of confidence in a price rescues an
    # answer to a question nobody asked. That is not a term to be outvoted by a strong floor
    # position; it is a precondition. Layers 1 and 2 catch the qualifiers we anticipated. This
    # catches the ones we did not, in a trade we have never seen, without needing to know what
    # the unrecognised words MEAN — only that the client spent words we cannot account for.
    if comprehension is not None:
        signals.append(Signal(
            "comprehension", round(comprehension, 3), 0.0,
            f"{comprehension:.0%} of the client's own words were accounted for by the service "
            f"match and known qualifiers"
            + ("" if comprehension >= COMPREHENSION_FLOOR else
               f" — below the {COMPREHENSION_FLOOR:.0%} floor, so this queues for the owner "
               f"however strong the other signals are"),
        ))
        if comprehension < COMPREHENSION_FLOOR:
            autonomous = False

    return Confidence(
        score=total, threshold=threshold, autonomous=autonomous,
        service_key=key, signals=tuple(signals),
    )
