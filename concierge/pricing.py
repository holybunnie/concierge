"""Quote derivation (§2) — every figure comes out of the tenant's stored profile, by code.

This module is the whole answer to "did a language model produce that price?". It cannot have,
because there is nothing here that could: no network client, no model call, no default, no
fallback constant. A quote is a *read* of the profile plus arithmetic, and every quote carries
the list of profile paths it was read from so the derivation can be shown rather than asserted.

**Nothing here knows what trade the tenant is in.** That is deliberate and it is load-bearing.
An engine that branches on `pricing_rules.listing_fee_pct` works for estate agents and silently
fails for dentists, plumbers, tutors, photographers and every other business that was not
anticipated when the branch was written — and it fails by refusing to quote a price the tenant
did in fact supply, which reads to them as the product being broken. So pricing reads a small
canonical vocabulary that every vertical template maps onto:

    pricing_rules.headline       what this tenant charges
    pricing_rules.floor          the lowest they will go, if they set one
    pricing_rules.max_discount   the most that may be taken off without asking them

Each is a `rule` dict — `{kind, value, currency, basis, raw, label}` — built by `as_rule` from
the template's declared `Field.kind`, not from the trade. Adding a vertical is therefore a
matter of writing questions and pointing their `maps_to` at these three names; no code here
changes. A trade with no template at all falls to the generic template and quotes exactly as
well, which the engine harness proves with a business no template has ever heard of.

A rule resolves to one of three `kind`s, all of them tenant-supplied:

  * `cash`    — a bare amount: a treatment at £85, a call-out at £60;
  * `percent` — a rate: 1.5% of a sale price, 20% of recovered damages. Quoted as a rate and
    never multiplied by a value nobody gave us;
  * `prose`   — terms that do not reduce to a number: "£250 + VAT for the first hour, fixed",
    "£90/hour, 2 hour minimum". Quoted back *verbatim*.

That last case matters more than it looks. "£250 + VAT for the first hour" is not the number
250; reducing it to 250 would drop the VAT and the hour, and we would be quoting terms the
tenant never set. So a fee that does not parse as a bare amount is repeated in the tenant's own
words and marked non-negotiable — we can restate a commitment we cannot do arithmetic on, but
we must not pretend to price it.

The other half of the job is refusing. `match_service` needs strong, unambiguous evidence that
the inquiry is about something in the profile. Weak evidence and near-ties both raise
`Unquotable`, which the engine turns into an escalation. Guessing the nearest service is how an
agent quotes £85 for a treatment the business does not perform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Words that carry no signal about *which* service is being asked about. Stripping them stops
# "how much does a facial cost?" from matching a service on the strength of the word "much".
_STOPWORDS = frozenset("""
about all and any are ask asked available book booking can charge charges cost costs could
did does doing for from get got has have how its just like long look looking make many
more much need needs offer offers one out please price priced prices pricing quote quoted
rate rates run runs say see should some take takes tell than that the them then there these
they thing things this those time times use used want wanted was were what when where which
who will with would you your yours
""".split())

# Public alias: `comprehension` reads the same list rather than keeping a second copy. Two
# stopword lists that are supposed to agree will eventually disagree, and the failure is silent —
# a word missing from one of them scores a perfectly ordinary question as half-understood.
STOPWORDS = _STOPWORDS

_WORD = re.compile(r"[a-z0-9]+")

# A quote is only issued when the best-matching service clearly wins. Both numbers are
# deliberately conservative: the cost of refusing a real match is one escalation, and the cost
# of accepting a wrong one is a business committed to a price for work it does not do.
MIN_SERVICE_SCORE = 0.5     # at least half the service's distinctive words must appear
MIN_SERVICE_MARGIN = 0.25   # and it must beat the runner-up by this, or it is a tie, not a match

# A bare amount and nothing else: "£250", "250", "£1,200.00". Anything with further words
# attached is prose and is quoted verbatim rather than parsed.
_BARE_MONEY = re.compile(r"^\s*(?P<cur>[£$€])?\s?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)\s*$")

_SYMBOL_TO_CODE = {"£": "GBP", "$": "USD", "€": "EUR"}
_CODE_TO_SYMBOL = {v: k for k, v in _SYMBOL_TO_CODE.items()}


# ---------------------------------------------------------------- canonical rule vocabulary
#
# The entire pricing vocabulary, and the only names any engine code may read. A vertical
# template's job is to ask its trade's question in its trade's language ("what's your
# commission?", "what do you charge for a call-out?", "what's your consultation fee?") and point
# `Field.maps_to` at one of these. Nothing downstream ever learns which trade it was.

RULE_HEADLINE = "headline"          # what this tenant charges
RULE_FLOOR = "floor"                # the lowest they will go, if they set one
RULE_MAX_DISCOUNT = "max_discount"  # most that may be taken off without asking them

RULE_NAMES = (RULE_HEADLINE, RULE_FLOOR, RULE_MAX_DISCOUNT)


def as_rule(*, field_kind: str, value: Any, basis: str = "", label: str = "") -> dict[str, Any]:
    """Coerce one tenant answer into the canonical rule shape. Interpretation, never invention.

    `field_kind` is the template's declared kind — "money" or "percent" — so how an answer is
    read is decided by the question that was asked, not by guessing from the trade or from the
    shape of the string. The tenant's raw answer is always kept in `raw`, because that is what
    gets quoted back when the value does not reduce to a number.

    A `money` answer that is not a bare amount resolves to `kind="prose"` rather than being
    part-parsed. That is the whole reason this function exists: "£90/hour, 2 hour minimum"
    must never become the number 90.
    """
    raw = value
    if field_kind == "percent":
        amount = _as_percent(value)
        kind = "percent" if amount is not None else "prose"
        currency = None
    else:
        amount, currency = _as_bare_money(value)
        kind = "cash" if amount is not None else "prose"
    return {
        "kind": kind, "value": amount, "currency": currency,
        "basis": basis, "raw": raw, "label": label,
    }


def render_rule(r: dict[str, Any]) -> str:
    """One canonical rule as the tenant would read it back — their figure, or their words."""
    if r.get("kind") == "percent" and r.get("value") is not None:
        return f"{_fmt(r['value'])}%"
    if r.get("kind") == "cash" and r.get("value") is not None:
        return f"{_CODE_TO_SYMBOL.get(r.get('currency') or 'GBP', '')}{_fmt(r['value'])}"
    return str(r.get("raw"))


def rule(profile: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Read one canonical rule. Returns None when the tenant never set it — never a default."""
    r = (profile.get("pricing_rules") or {}).get(name)
    return r if isinstance(r, dict) else None


# ---------------------------------------------------------------- decaying floor (Feature 5)
#
# `pricing_rules.floor_curve` is an OPTIONAL, richer shape for the same `floor` concept:
#     {"initial": X, "floor": Y, "kind": "cash"|"percent",
#      "decay_trigger": "rounds"|"days", "decay_steps": [...]}
# Absent, the tenant's flat `pricing_rules.floor` governs exactly as it always has —
# `guardrails.bounds_for` only reaches into this shape when it exists. `Y` (`floor`) is the one
# number that can never be crossed; the curve only controls how fast CONCIERGE may move toward
# it as the negotiation goes on. Defined by the tenant at onboarding or in settings — never
# inferred, and never adjusted mid-negotiation by anything, including an LLM asked to "be more
# flexible because the conversation feels promising" (§ explicitly rejected, see the addendum).

def floor_curve(profile: dict[str, Any]) -> dict[str, Any] | None:
    """The tenant's optional decaying floor, or None — in which case the flat floor rule applies."""
    curve = (profile.get("pricing_rules") or {}).get("floor_curve")
    if not isinstance(curve, dict) or curve.get("floor") is None:
        return None
    return curve


def floor_curve_value(curve: dict[str, Any], index: int) -> tuple[float, str]:
    """The floor permitted at this round/day index.

    The sequence is `[initial, *decay_steps]`, one point per index; past the end of it, the
    curve has fully decayed and rests at the absolute floor forever. Every value is clamped to
    never fall below `curve['floor']` regardless of what the sequence itself says — a tenant
    fat-fingering a decay step below their own stated floor must not be able to author their way
    past the one number that is supposed to be unconditional.
    """
    absolute_floor = float(curve["floor"])
    sequence: list[float] = []
    if curve.get("initial") is not None:
        sequence.append(float(curve["initial"]))
    sequence += [float(s) for s in (curve.get("decay_steps") or [])]

    if not sequence:
        return absolute_floor, "no curve points set ('initial'/'decay_steps' both empty) — resting at the absolute floor"
    if index < len(sequence):
        value = sequence[index]
        point = f"index {index} of the curve {sequence}"
    else:
        value = absolute_floor
        point = (f"index {index} is past the curve's {len(sequence)} defined point(s) — "
                 f"fully decayed, resting at the absolute floor")
    value = max(value, absolute_floor)
    return value, f"{point} = {value:,.2f}"


class Unquotable(Exception):
    """The profile does not support a quote. Always an escalation, never a guess (§2, §3).

    Carries the reason in the tenant's terms and, where relevant, what *was* in the profile —
    so the owner reading the escalation can see immediately whether this is a gap in their
    profile or genuinely not their line of work.
    """

    def __init__(self, reason: str, *, considered: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.considered = considered or []


@dataclass(frozen=True)
class Derivation:
    """One step of "where this number came from". Rendered straight into the harness report."""

    profile_path: str   # dotted path actually read out of the tenant's profile
    read: str           # what was found there, verbatim
    yielded: str        # what that produced

    def __str__(self) -> str:
        return f"profile.{self.profile_path} = {self.read}  ->  {self.yielded}"


@dataclass(frozen=True)
class Quote:
    """A price CONCIERGE is willing to state, and the audit trail that produced it."""

    service_name: str          # the profile's own name for the thing, not the prospect's words
    unit: str                  # "cash" | "percent" | "prose" — never a trade name
    amount: float | None       # None for prose terms — do not invent one
    currency: str | None       # ISO code for cash, None otherwise
    stated_terms: str | None   # the tenant's exact words, for prose fees
    negotiable: bool           # whether any floor exists to negotiate against
    derivation: tuple[Derivation, ...]
    basis: str = ""            # the tenant's own words for what the figure is charged against
    duration_min: int | None = None
    match_score: float = 0.0
    matched_on: tuple[str, ...] = ()

    def render(self) -> str:
        """The figure as it will appear in a message to the prospect."""
        if self.unit == "cash":
            out = f"{_CODE_TO_SYMBOL.get(self.currency or '', '')}{_fmt(self.amount)}"
        elif self.unit == "percent":
            out = f"{_fmt(self.amount)}%"
        else:
            return self.stated_terms or ""
        return f"{out} {self.basis}".strip()

    def derivation_report(self) -> str:
        return "\n".join(str(d) for d in self.derivation)


# ---------------------------------------------------------------- service matching

def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall((text or "").lower())
            if t not in _STOPWORDS and len(t) >= 3]


def _service_name(service: Any) -> str:
    """Services arrive as dicts (spa: priced menu) or strings (legal: practice areas)."""
    if isinstance(service, dict):
        return str(service.get("name", ""))
    return str(service)


def catalogue(profile: dict[str, Any]) -> list[Any]:
    """Everything this tenant sells, exactly as they entered it. Never extended with defaults."""
    services = profile.get("services")
    if services is None:
        return []
    return services if isinstance(services, list) else [services]


@dataclass(frozen=True)
class ServiceMatch:
    service: Any
    name: str
    score: float
    matched_on: tuple[str, ...]
    runner_up: tuple[str, float] | None


def match_service(profile: dict[str, Any], inquiry: str) -> ServiceMatch:
    """Find the one service this inquiry is about, or raise.

    Scoring is share-of-the-service's-own-words: "Signature facial" scores 0.5 against an
    inquiry mentioning "facial", and "Deep tissue massage" scores 0.33 against one mentioning
    only "massage" — which is why "do you do hot stone massage?" against a menu without hot
    stone refuses instead of selling a deep tissue.
    """
    items = catalogue(profile)
    if not items:
        raise Unquotable(
            "This tenant's profile lists no services at all, so there is nothing to quote from. "
            "Onboarding did not complete."
        )

    asked = set(_tokens(inquiry))
    scored: list[tuple[Any, str, float, tuple[str, ...]]] = []
    for item in items:
        name = _service_name(item)
        want = _tokens(name)
        if not want:
            continue
        hit = tuple(t for t in want if t in asked)
        scored.append((item, name, len(hit) / len(want), hit))

    scored.sort(key=lambda s: s[2], reverse=True)
    names = [s[1] for s in scored]

    # A distinctive word that belongs to exactly ONE service on this menu identifies it, even
    # when it is a small share of that service's own name. "How much for a {word}?" scores only
    # 1/3 against a three-word service and used to escalate — but if no other service on the
    # menu contains that word, there is nothing to confuse it with and no coin flip to lose.
    # Ambiguity is still handled below: a word appearing in two services matches neither here.
    if not scored or scored[0][2] < MIN_SERVICE_SCORE:
        unique = [s for s in scored if s[3] and
                  sum(1 for other in scored if set(s[3]) & set(other[3])) == 1]
        # The client must have contributed NO other distinctive word. This condition is the whole
        # safety of the shortcut, and it separates two questions a word-overlap score cannot tell
        # apart:
        #
        #   "how much for a massage?"       -> asked {massage}; nothing left over; exactly one
        #                                      thing on this menu it can mean.
        #   "do you do hot stone massage?"  -> asked {hot, stone, massage}; 'hot' and 'stone' are
        #                                      left over, and they are precisely the words that
        #                                      make it a DIFFERENT service. Refuse.
        #
        # Without it the shortcut re-opens the exact hole the engine suite's central attack exists to keep
        # shut: a real price quoted for work the business does not perform.
        leftover = set(asked) - {w for _, name, _, _ in scored for w in _tokens(name)}
        if len(unique) == 1 and not leftover:
            best = unique[0]
            return ServiceMatch(service=best[0], name=best[1], score=best[2], matched_on=best[3], runner_up=None)

    if not scored or scored[0][2] < MIN_SERVICE_SCORE:
        raise Unquotable(
            "Nothing in this inquiry matches a service in the profile, so there is no price to "
            "derive. Escalating rather than answering — inventing a service is the one thing "
            "that must never happen here.",
            considered=names,
        )

    best = scored[0]
    second = scored[1] if len(scored) > 1 else None
    if second and best[2] - second[2] < MIN_SERVICE_MARGIN:
        raise Unquotable(
            f"This inquiry matches {best[1]!r} and {second[1]!r} about equally well "
            f"({best[2]:.2f} vs {second[2]:.2f}). Quoting either would be a coin flip on which "
            f"service the client meant, so this goes to you.",
            considered=names,
        )

    return ServiceMatch(
        service=best[0], name=best[1], score=best[2], matched_on=best[3],
        runner_up=(second[1], second[2]) if second else None,
    )


# ---------------------------------------------------------------- quoting

def quote_for(profile: dict[str, Any], inquiry: str) -> Quote:
    """Derive a quotable price for whatever this inquiry is about, or raise `Unquotable`."""
    match = match_service(profile, inquiry)
    return quote_for_match(profile, match)


def match_named_service(profile: dict[str, Any], service_name: str) -> ServiceMatch:
    """Resolve one exact stored catalogue name. Similarity or model invention is not accepted."""
    matches = [(item, _service_name(item)) for item in catalogue(profile)
               if _service_name(item) == service_name]
    if len(matches) != 1:
        raise Unquotable(
            f"{service_name!r} is not one unique exact service in the stored profile.",
            considered=[name for _, name in matches],
        )
    item, name = matches[0]
    return ServiceMatch(item, name, 1.0, tuple(_tokens(name)), None)


def quote_for_match(profile: dict[str, Any], match: ServiceMatch) -> Quote:
    """Derive a price from a validated catalogue match; callers cannot supply the figure."""
    rules = profile.get("pricing_rules") or {}

    # --- shape 1: a priced service. The price is on the item the tenant typed.
    if isinstance(match.service, dict) and match.service.get("price") is not None:
        amount = _as_number(match.service["price"])
        if amount is None:
            raise Unquotable(
                f"{match.name!r} is on the menu but its price is not a number "
                f"({match.service['price']!r}), so it cannot be quoted or negotiated against.",
                considered=[match.name],
            )
        idx = catalogue(profile).index(match.service)
        currency = str(match.service.get("currency") or "GBP")
        return Quote(
            service_name=match.name, unit="cash", amount=amount, currency=currency,
            stated_terms=None, negotiable=_is_negotiable(rules),
            duration_min=_as_int(match.service.get("duration_min")),
            match_score=match.score, matched_on=match.matched_on,
            derivation=(
                Derivation(f"services[{idx}].name", repr(match.name),
                           f"matched the inquiry on {list(match.matched_on)} "
                           f"(score {match.score:.2f})"),
                Derivation(f"services[{idx}].price", repr(match.service["price"]),
                           f"{_CODE_TO_SYMBOL.get(currency, '')}{_fmt(amount)}"),
            ),
        )

    # --- shape 2: the tenant's headline rule. One code path for every trade.
    headline = rules.get(RULE_HEADLINE)
    if isinstance(headline, dict):
        negotiable = _is_negotiable(rules)
        matched = Derivation(
            "services", repr(match.name),
            f"inquiry is in scope, matched on {list(match.matched_on)} "
            f"(score {match.score:.2f})")

        if headline.get("kind") == "percent" and headline.get("value") is not None:
            pct = float(headline["value"])
            basis = headline.get("basis")
            if not basis:
                # "We charge 1.5%" — of what? A sale price, recovered damages, a project
                # budget, gross rent? The code cannot supply that noun without guessing what
                # the tenant sells, and a percentage of the wrong base is a wrong quote, not a
                # vague one. So this is a profile gap and it escalates.
                raise Unquotable(
                    f"pricing_rules.{RULE_HEADLINE} is a rate of {_fmt(pct)}% but the profile "
                    f"does not say what it is charged against. Stating a bare percentage would "
                    f"mean choosing that basis myself, so this goes to the owner.",
                    considered=[match.name],
                )
            return Quote(
                service_name=match.name, unit="percent", amount=pct, currency=None,
                stated_terms=None, basis=basis, negotiable=negotiable,
                match_score=match.score, matched_on=match.matched_on,
                derivation=(matched, Derivation(
                    f"pricing_rules.{RULE_HEADLINE}", repr(headline.get("raw")),
                    f"{_fmt(pct)}% {basis} — quoted as a rate, because no job value was "
                    f"supplied and I will not assume one")),
            )

        if headline.get("kind") == "cash" and headline.get("value") is not None:
            amount = float(headline["value"])
            currency = str(headline.get("currency") or "GBP")
            return Quote(
                service_name=match.name, unit="cash", amount=amount, currency=currency,
                stated_terms=None, basis=headline.get("basis") or "", negotiable=negotiable,
                match_score=match.score, matched_on=match.matched_on,
                derivation=(matched, Derivation(
                    f"pricing_rules.{RULE_HEADLINE}", repr(headline.get("raw")),
                    f"parsed as a bare amount: "
                    f"{_CODE_TO_SYMBOL.get(currency, '')}{_fmt(amount)}")),
            )

        # kind == "prose": terms that do not reduce to a number.
        return Quote(
            service_name=match.name, unit="prose", amount=None, currency=None,
            stated_terms=str(headline.get("raw")), basis=headline.get("basis") or "",
            negotiable=False,
            match_score=match.score, matched_on=match.matched_on,
            derivation=(matched, Derivation(
                f"pricing_rules.{RULE_HEADLINE}", repr(headline.get("raw")),
                "not a bare amount, so it is quoted back word for word. Reducing it to a "
                "number would silently drop terms the tenant set — VAT, a minimum, a "
                "duration — and commit them to something they never agreed to"),
            ),
        )

    raise Unquotable(
        f"{match.name!r} is in scope for this tenant, but their profile carries no pricing rule "
        f"that covers it. In scope and unpriced is still unquotable — it escalates.",
        considered=[match.name],
    )


# ---------------------------------------------------------------- escalation triggers

def matching_escalations(profile: dict[str, Any], inquiry: str) -> list[str]:
    """Tenant-set triggers and template hard-escalations that this inquiry trips.

    Deliberately loose. The two error directions are not symmetric: escalating something the
    profile could have answered costs the owner one email, while failing to escalate an offer,
    a medical question or a request for legal advice costs them a great deal more. Where this
    matcher is wrong, it is wrong towards the owner's inbox.
    """
    asked = set(_tokens(inquiry))
    triggers = list(profile.get("escalation_triggers") or [])
    triggers += list((profile.get("_meta") or {}).get("hard_escalations") or [])
    triggers += list(profile.get("never_advise_on") or [])

    hit = []
    for trigger in triggers:
        want = _tokens(str(trigger))
        if not want:
            continue
        overlap = sum(1 for t in want if t in asked)
        # One distinctive word is enough when the trigger is short ("Any existing client");
        # longer triggers need a third of their words so a single common noun cannot fire them.
        need = 1 if len(want) <= 3 else max(2, len(want) // 3)
        if overlap >= need:
            hit.append(str(trigger))
    return hit


# ---------------------------------------------------------------- small parsers

def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _as_bare_money(value)[0]
    return None


def _as_int(value: Any) -> int | None:
    n = _as_number(value)
    return int(n) if n is not None else None


def _as_bare_money(value: Any) -> tuple[float | None, str | None]:
    """Parse only a naked amount. `"£250"` → 250; `"£250 + VAT for the first hour"` → None.

    The refusal is the point. A partial parse of a compound fee is a quiet misquote.
    """
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    m = _BARE_MONEY.match(str(value))
    if not m:
        return None, None
    return float(m.group("amt").replace(",", "")), _SYMBOL_TO_CODE.get(m.group("cur") or "")


_PERCENT = re.compile(r"^\s*(?P<amt>\d+(?:\.\d+)?)\s*%?\s*$")


def _as_percent(value: Any) -> float | None:
    """`1.5`, `"1.5"`, `"1.5%"` → 1.5. `"1.5% but negotiable"` → None, deliberately."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _PERCENT.match(str(value))
    return float(m.group("amt")) if m else None


def _is_negotiable(rules: dict[str, Any]) -> bool:
    """Negotiable means a numeric bound exists to negotiate *against*.

    With no floor and no discount cap there is nothing to hold the line at, so CONCIERGE does
    not haggle at all — it escalates. Absence of a rule is never read as permission.
    """
    for name in (RULE_FLOOR, RULE_MAX_DISCOUNT):
        r = rules.get(name)
        if isinstance(r, dict) and r.get("value") is not None:
            return True
    return False


def fmt_amount(amount: float | None) -> str:
    """Public formatter — used wherever a figure is shown to a person."""
    return _fmt(amount)


def _fmt(amount: float | None) -> str:
    if amount is None:
        return "—"
    return f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"
