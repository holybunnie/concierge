"""LOOP 1 (§10) — onboarding a tenant, vertical-aware.

    classify → present template + worked example → extract candidates → tenant confirms
    → flag gaps → read back the rules → allocate the inbound address

The load-bearing property of this module is negative: **no profile value originates here.**
Everything in a finished profile came from a string the tenant typed. The template supplies
questions; the extractor supplies *candidates the tenant must confirm*; neither supplies data.

That is stricter than §2 requires — §2 forbids the LLM from producing prices — but the failure
mode is identical whether a wrong price comes from a model, from a template's example, or from
a regex that read "£85" out of a sentence about something else. The profile is what Phase 3
quotes from, so it may contain only confirmed facts.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import db, store
from .classify import Classification, classify
from .verticals import Field, VerticalTemplate, template_for

# RFC 2606 reserves .invalid precisely so it can never resolve. We use it as the domain half of
# an inbound address until the operator supplies a real one (OPERATOR_PROVIDES item 2). The local
# part is real and permanently reserved; the address as a whole is visibly, structurally unusable
# rather than plausible-looking. A placeholder that could be mistaken for live is worse than none.
PENDING_DOMAIN = "PENDING-DOMAIN.invalid"


@dataclass
class Candidate:
    """Something we think we saw in the tenant's description. Not a fact until confirmed."""

    kind: str          # money | percent | duration
    value: Any
    raw: str           # the exact substring matched
    context: str       # surrounding words, so the tenant can see what it referred to
    confirmed: bool = False


@dataclass
class Gap:
    field_key: str
    label: str
    question: str
    consequence: str


@dataclass
class OnboardingSession:
    """One tenant's onboarding, from description to inbound address."""

    description: str
    classification: Classification
    template: VerticalTemplate
    candidates: list[Candidate] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)

    # ---- step 1: what we ask, and the worked example we show alongside it

    def briefing(self) -> str:
        """The template presented to the tenant, with a filled example for their vertical."""
        t = self.template
        out = [
            f"I've read your description. {self.classification.reason}",
            "",
            f"Here is what I need to work your inbound as {t.label}. This is the same brief I'd",
            f"want if I were a new person joining your sales desk.",
            "",
            f"Clients in your line typically ask:",
        ]
        out += [f"  · {q}" for q in t.typical_inquiries]
        out += ["", f"What I need from you ({len(t.required_fields())} required):", ""]

        for f in t.fields:
            mark = "REQUIRED" if f.required else "optional"
            out += [
                f"  {f.label}  [{mark}]",
                f"    Q: {f.question}",
                f"    Why: {f.why}",
                f"    Example ({t.example_business}): {_render_example(f.example)}",
                "",
            ]

        if t.hard_escalations:
            out += ["Regardless of what you tell me, I will always escalate these to you:"]
            out += [f"  · {e}" for e in t.hard_escalations]
            out += [""]

        out += [
            "Every example above belongs to a fictional business and is shown only so you can see",
            "the shape of a good answer. None of it will end up in your profile. I quote from what",
            "you tell me and from nothing else.",
        ]
        return "\n".join(out)

    # ---- step 2: candidates found in their own words, for confirmation

    def confirm(self, index: int) -> Candidate:
        self.candidates[index].confirmed = True
        return self.candidates[index]

    # ---- step 3: answers in, gaps out

    def answer(self, key: str, value: Any) -> None:
        if self.template.field(key) is None:
            raise KeyError(f"{key!r} is not a field in the {self.template.key} template")
        self.answers[key] = value

    def gaps(self) -> list[Gap]:
        """Required fields the tenant has not answered, and what each one costs them."""
        out = []
        for f in self.template.required_fields():
            v = self.answers.get(f.key)
            if v is None or (isinstance(v, (str, list, dict)) and len(v) == 0):
                out.append(Gap(f.key, f.label, f.question, f.gap_consequence))
        return out

    def gap_report(self) -> str:
        gaps = self.gaps()
        if not gaps:
            return "Nothing missing. Every required field is answered."
        lines = [f"{len(gaps)} thing(s) still missing, and what each one will cost you:", ""]
        for g in gaps:
            lines += [f"  {g.label} — {g.question}", f"    If you leave it: {g.consequence}", ""]
        return "\n".join(lines)

    # ---- step 4: the profile, built only from answers

    def build_profile(self) -> dict[str, Any]:
        """Map confirmed answers onto the profile shape. Reads self.answers and nothing else.

        Note what is not referenced anywhere in this method: `Field.example`. The examples are
        not reachable from here, so a template example cannot become a tenant's price even if
        every field is left blank — a blank field yields a missing key, which Phase 3 escalates.
        """
        profile: dict[str, Any] = {}
        for f in self.template.fields:
            if f.key not in self.answers:
                continue
            value = self.answers[f.key]
            if f.key == "artifact_sample":
                value = [{"kind": "reply_sample", "text": value}]
            _set_path(profile, f.maps_to, value)

        profile["_meta"] = {
            "vertical": self.template.key,
            "classified_confidently": self.classification.confident,
            "classifier_evidence": self.classification.matched_terms,
            "hard_escalations": list(self.template.hard_escalations),
            "answered": sorted(self.answers),
            "unanswered_required": [g.field_key for g in self.gaps()],
            "provenance": "every value supplied by the tenant; no value from a template example "
                          "or a language model",
        }
        return profile

    # ---- step 5: read the rules back for confirmation

    def read_back(self) -> str:
        """Plain-English restatement of the rules Phase 3 will actually enforce.

        Rendered from the built profile, not from the answers, so what the tenant confirms is
        literally what the engine will read. A read-back generated from a different source than
        the engine consumes would be a reassuring lie.
        """
        p = self.build_profile()
        rules = p.get("pricing_rules", {})
        cal = p.get("calendar_ref", {})
        lines = ["These are the rules I will hold you to. Correct anything that is wrong.", ""]

        services = p.get("services")
        if services:
            lines.append("I will quote only these:")
            for s in services if isinstance(services, list) else [services]:
                lines.append(f"  · {_render_example(s)}")
            lines.append("  Anything not on this list, I escalate to you instead of quoting.")
            lines.append("")

        if rules:
            lines.append("Money:")
            for k, v in rules.items():
                lines.append(f"  · {_rule_english(k, v)}")
            lines.append("")

        if cal.get("booking_rules") or cal.get("timezone"):
            lines.append("Booking:")
            if cal.get("booking_rules"):
                lines.append(f"  · Slots only within: {cal['booking_rules']}")
            if cal.get("timezone"):
                lines.append(f"  · Your timezone: {cal['timezone']}. I ask each prospect their "
                             f"own timezone and never infer it.")
            lines.append("")

        if p.get("icp"):
            lines.append(f"Qualifying: {p['icp']}")
            lines.append("")

        triggers = list(p.get("escalation_triggers", [])) + list(p["_meta"]["hard_escalations"])
        if triggers:
            lines.append("I stop and hand over to you when:")
            lines += [f"  · {t}" for t in triggers]
            lines.append("")

        unanswered = p["_meta"]["unanswered_required"]
        if unanswered:
            lines.append(f"Still unanswered: {', '.join(unanswered)}. Anything that depends on "
                         f"these escalates to you rather than being answered.")
            lines.append("")

        lines.append("Every message I send opens by disclosing that I am an AI agent acting for "
                     "you, and offers a route to a human. That is not configurable.")
        return "\n".join(lines)


# ---------------------------------------------------------------- entry point

def start(description: str) -> OnboardingSession:
    c = classify(description)
    t = template_for(c.vertical if c.confident else "generic")
    return OnboardingSession(
        description=description,
        classification=c,
        template=t,
        candidates=extract_candidates(description),
    )


# ---------------------------------------------------------------- extraction

_MONEY = re.compile(r"(?P<cur>[£$€])\s?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)\b")
_PERCENT = re.compile(r"(?P<amt>\d+(?:\.\d+)?)\s?%")
_DURATION = re.compile(r"\b(?P<amt>\d{1,3})\s?(?P<unit>min(?:ute)?s?|hours?|hrs?)\b", re.I)


def extract_candidates(text: str) -> list[Candidate]:
    """Pull money, percentages and durations out of the tenant's own prose.

    These are *candidates*, and `build_profile` cannot see them. They exist only to save the
    tenant typing: onboarding shows each one with its surrounding words and asks "is this your
    massage price, or something else?" A regex cannot tell a price from a competitor's price
    quoted in passing, so it is never trusted to decide.
    """
    out: list[Candidate] = []
    for rx, kind in ((_MONEY, "money"), (_PERCENT, "percent"), (_DURATION, "duration")):
        for m in rx.finditer(text or ""):
            raw = m.group(0)
            amt = float(m.group("amt").replace(",", ""))
            value: Any = amt
            if kind == "duration" and m.group("unit").lower().startswith(("hour", "hr")):
                value = amt * 60
            out.append(Candidate(
                kind=kind, value=value, raw=raw,
                context=_context(text, m.start(), m.end()),
            ))
    return out


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    return ("…" if start > width else "") + \
        text[max(0, start - width):min(len(text), end + width)].replace("\n", " ") + \
        ("…" if end + width < len(text) else "")


# ---------------------------------------------------------------- inbound address

def slugify(business_name: str) -> str:
    """A stable, mail-safe local part. Deterministic: same name always yields the same slug."""
    s = unicodedata.normalize("NFKD", business_name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    s = re.sub(r"-+", "-", s)
    if not s:
        raise ValueError(f"{business_name!r} has no mail-safe characters to build an address from")
    return s[:32].strip("-")


def allocate_inbound_address(business_name: str, domain: str | None = None) -> str:
    """Reserve a unique local part. Collisions get a numeric suffix, never a shared address.

    Uniqueness is checked through the SECURITY DEFINER resolver — the same one that routes real
    inbound mail — so "is this address free?" and "who does this address belong to?" can never
    disagree. It is also enforced a second time by a UNIQUE constraint on the column, which is
    what actually wins a race between two simultaneous onboardings.
    """
    domain = domain or db.config.get("CONCIERGE_DOMAIN") or PENDING_DOMAIN
    base = slugify(business_name)
    for n in range(1, 100):
        local = base if n == 1 else f"{base}-{n}"
        address = f"{local}@{domain}".lower()
        try:
            db.resolve_tenant_by_inbound_address(address)
        except db.TenantUnresolved:
            return address          # nobody owns it — it's ours
    raise RuntimeError(f"Could not find a free inbound address for {business_name!r} after 99 tries")


def finalise(
    session: OnboardingSession,
    *,
    business_name: str,
    owner_email: str,
    owner_wallet: str,
    domain: str | None = None,
) -> tuple[uuid.UUID, str, str]:
    """Create the tenant and return (tenant_id, inbound_address, read_back).

    Returning the address is load-bearing (§10): it is the only way anyone can send this tenant
    an inquiry. If the domain is not yet provided, the address comes back on PENDING-DOMAIN.invalid
    — a reserved TLD that can never resolve, so it cannot be mistaken for a working address.
    """
    tenant_id = uuid.uuid4()
    address = allocate_inbound_address(business_name, domain)
    profile = session.build_profile()

    with db.tenant_session(tenant_id) as cur:
        store.create_tenant(
            cur, tenant_id=tenant_id, owner_wallet=owner_wallet, owner_email=owner_email,
            business_name=business_name, vertical=session.template.key,
            inbound_address=address, profile=profile,
        )
    return tenant_id, address, session.read_back()


def address_is_live(address: str) -> bool:
    return not address.lower().endswith(PENDING_DOMAIN.lower())


# ---------------------------------------------------------------- rendering helpers

def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for p in parts[:-1]:
        target = target.setdefault(p, {})
    target[parts[-1]] = value


def _render_example(value: Any) -> str:
    if isinstance(value, dict):
        if "name" in value:
            bits = [str(value["name"])]
            if value.get("duration_min"):
                bits.append(f"{value['duration_min']} min")
            if value.get("price") is not None:
                bits.append(f"{_symbol(value.get('currency'))}{value['price']}")
            return ", ".join(bits)
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, list):
        return "; ".join(_render_example(v) for v in value)
    return str(value)


def _symbol(currency: str | None) -> str:
    return {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency or "", "")


def _rule_english(key: str, value: Any) -> str:
    """Render a stored rule the way the engine will apply it — no softening."""
    match key:
        case "listing_fee_pct":
            return f"I quote {value}% commission."
        case "floor_pct":
            return (f"I will never go below {value}%. A prospect who pushes past it gets handed "
                    f"to you, not a lower number.")
        case "floor_price":
            return (f"I will never go below {_symbol('GBP')}{value}. Past that, I stop and "
                    f"escalate to you.")
        case "max_discount_pct":
            return f"The most I may take off without asking you is {value}%."
        case "consultation_fee":
            return f"I quote {value} for an initial consultation."
        case "floor_consultation_fee":
            return f"Floor on that fee: {value}."
        case _:
            return f"{key}: {value}"
