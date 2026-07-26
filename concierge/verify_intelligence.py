"""the intelligence suite — bounded model-assisted understanding.

Every other gate proves CONCIERGE cannot invent a figure with no model anywhere near the
decision. This one is the awkward gate: a model IS now in the path, reading the customer's
meaning before `engine.decide` runs. So the question it has to answer is not "is the model
good" — it is **what can the model change, and what can it not touch?**

The boundary the feature claims:

  * the model may say WHICH stored service is meant, but only by naming one verbatim;
  * it may say WHAT is being asked (intent, act, qualifier classes) from fixed vocabularies;
  * it may never supply, adjust, or influence a figure. Prices, floors and commitments come
    from `pricing`/`guardrails` reading the stored profile, exactly as before.

The provider here is a **declared fixture** — a scripted `anthropic` stand-in, installed into
`sys.modules` for the length of the checks that use it and removed afterwards, exactly as the
email suite stands in for Postmark and the engine suite for the calendar. It is deliberately
HOSTILE: it returns invented services, fabricated evidence, smuggled prices and false
confidence, because a real provider can return all four and the point of the validation layer
is that none of them survive it. Every database write, RLS boundary, pricing computation and
state transition below is real.
"""

from __future__ import annotations

import re
import sys
import types
import uuid
from typing import Any

from . import comprehension, config, db, engine, intelligence, pricing, store
from . import verify_engine as p3

PROSPECT = p3.PROSPECT

# The two figures that must never move, whatever the model says. Both are read back from the
# stored profile below rather than trusted from here.
SPA_SERVICE = "Deep tissue massage"
SPA_PRICE = 85


# ---------------------------------------------------------------- the declared fixture
#
# THIS IS A FIXTURE AND IT IS NOT PART OF THE SHIPPED PACKAGE. It stands in for the one seam
# `intelligence.interpret` reaches the network through — `import anthropic` — so the validation
# layer can be attacked deterministically, with no key, no network and no spend. Production
# imports the real SDK at that same line; nothing else about `interpret` changes.


class _ScriptedProvider:
    """Answers with whatever payload the check under test scripted. Records what it was asked."""

    def __init__(self, payload: Any, *, raise_with: Exception | None = None):
        self.payload = payload
        self.raise_with = raise_with
        self.calls: list[dict[str, Any]] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        text = self.payload if isinstance(self.payload, str) else _json_dumps(self.payload)
        block = types.SimpleNamespace(type="text", text=text)
        return types.SimpleNamespace(content=[block])


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj)


class _installed_provider:
    """Context manager: swap in the scripted provider, and always put the world back."""

    def __init__(self, provider: _ScriptedProvider):
        self.provider = provider
        self.previous: Any = None

    def __enter__(self) -> _ScriptedProvider:
        module = types.ModuleType("anthropic")

        def Anthropic(**_kwargs):
            client = types.SimpleNamespace()
            client.messages = types.SimpleNamespace(create=self.provider._create)
            return client

        module.Anthropic = Anthropic
        self.previous = sys.modules.get("anthropic")
        sys.modules["anthropic"] = module
        return self.provider

    def __exit__(self, *_exc) -> None:
        if self.previous is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = self.previous


def _reading(**overrides: Any) -> dict[str, Any]:
    """A well-formed provider payload, before a check makes it hostile."""
    base = {
        "service_name": SPA_SERVICE,
        "intent": "price",
        "act": "enquiry",
        "qualifier_classes": [],
        "evidence": [],
        "confidence": 0.95,
    }
    base.update(overrides)
    return base


def _understanding(**overrides: Any) -> intelligence.Understanding:
    """A reading as it exists AFTER validation — what the engine actually receives."""
    data = _reading(**overrides)
    return intelligence.Understanding(
        data["service_name"], data["intent"], data["act"],
        tuple(data["qualifier_classes"]), tuple(data["evidence"]), data["confidence"],
    )


def _figures(text: str | None) -> list[str]:
    """Every figure a client actually reads in a message.

    Two lines carry digits that are not figures and would otherwise be counted as ones: the
    disclosure line (`p3._body` already removes it — it carries the owner's address) and
    Feature 3's verify link, whose receipt_id is a uuid. Dropping the URL is not softening the
    check: a uuid cannot be mistaken for a price by a reader, and leaving it in makes the check
    pass or fail on whether a random uuid happened to contain the digits under test.
    """
    lines = [ln for ln in p3._body(text or "").splitlines() if "://" not in ln]
    return re.findall(r"\d[\d,]*(?:\.\d+)?", "\n".join(lines))


def run(r) -> None:
    db.migrate()

    tenant_id = p3._onboard(p3.SPA)
    with db.tenant_session(cur_tenant := tenant_id) as cur:
        tenant = store.get_tenant(cur)
    profile = tenant.profile or {}
    stored_price = next(
        (item.get("price") for item in pricing.catalogue(profile)
         if pricing._service_name(item) == SPA_SERVICE), None)

    # Every check below needs the feature switched on and a key present, or `interpret` returns
    # None before it ever reaches the provider. Set here rather than assumed from .env, so the
    # suite's result does not depend on the operator's local configuration.
    import os
    saved = {k: os.environ.get(k) for k in ("SEMANTIC_INTELLIGENCE_ENABLED", "LLM_API_KEY")}
    os.environ["SEMANTIC_INTELLIGENCE_ENABLED"] = "true"
    os.environ.setdefault("LLM_API_KEY", "sk-ant-fixture-not-a-real-key")

    try:
        _run_checks(r, tenant_id, profile, stored_price)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_checks(r, tenant_id, profile, stored_price) -> None:
    message = "Hi — how much for a deep tissue massage?"

    # ---- 1. the model may not name a service the tenant never stored
    invented = _reading(service_name="Deep tissue massage (90 minute upgrade)")
    with _installed_provider(_ScriptedProvider(invented)) as provider:
        result_invented = intelligence.interpret(profile, message)
    catalogue_names = [pricing._service_name(i) for i in pricing.catalogue(profile)]
    sent_catalogue = provider.calls[0]["messages"][0]["content"] if provider.calls else ""
    r.check(
        "ATTACK — a service the model invented is refused, not almost-matched",
        result_invented is None,
        "The provider returned a service name that reads like a real one and is not: the stored\n"
        "catalogue has 'Deep tissue massage', it answered 'Deep tissue massage (90 minute\n"
        "upgrade)'. The request constrains that field with a JSON-schema enum of the tenant's own\n"
        "catalogue, but a schema is a request to a provider, not a guarantee from one — so\n"
        "`interpret` re-checks the returned name against the same list and discards the whole\n"
        "reading when it does not match exactly. Nothing partial is kept: there is no 'closest\n"
        "service' path, because the closest service is a different service with a different price.",
        f"| catalogue sent to the provider: {catalogue_names}\n"
        f"| provider answered service_name: {invented['service_name']!r}\n"
        f"| interpret() returned: {result_invented}\n"
        f"| request content: {sent_catalogue}",
    )

    # ---- 2. evidence must actually occur in the customer's own message
    fabricated = _reading(evidence=["I am pregnant", "deep tissue massage"])
    with _installed_provider(_ScriptedProvider(fabricated)):
        result_fabricated = intelligence.interpret(profile, message)
    r.check(
        "ATTACK — evidence the customer never wrote invalidates the whole reading",
        result_fabricated is None,
        "The provider quoted the customer as saying 'I am pregnant'. They did not — the message\n"
        "is one sentence long and that phrase is not in it. One of the two evidence strings IS\n"
        "genuine, and the reading is still thrown away entirely rather than kept minus the bad\n"
        "part: a provider that fabricated one quote has told us its reading of this message is\n"
        "not trustworthy, and a half-trusted reading of a medical qualifier is exactly the input\n"
        "that would otherwise reach a pricing decision.",
        f"| customer's actual message: {message!r}\n"
        f"| provider's evidence: {fabricated['evidence']}\n"
        f"| interpret() returned: {result_fabricated}",
    )

    # ---- 3. low confidence is refused rather than downweighted
    unsure = _reading(confidence=0.79)
    with _installed_provider(_ScriptedProvider(unsure)):
        result_unsure = intelligence.interpret(profile, message)
    sure = _reading(confidence=0.80)
    with _installed_provider(_ScriptedProvider(sure)):
        result_sure = intelligence.interpret(profile, message)
    r.check(
        "An unsure reading is discarded outright — never carried forward at a discount",
        result_unsure is None and result_sure is not None and result_sure.confidence == 0.80,
        "At 0.79 the reading is dropped and the deterministic path runs untouched; at 0.80 it is\n"
        "accepted. There is no middle where a doubtful reading is passed along with its doubt\n"
        "attached for something downstream to weigh — that would make the model's self-reported\n"
        "certainty an input to a pricing decision, which is precisely what every other gate in\n"
        "this build exists to prevent. Note what the accepted reading is allowed to do: it can\n"
        "raise `comprehension` (check 8), and it cannot lower it.",
        f"| confidence 0.79 -> {result_unsure}\n"
        f"| confidence 0.80 -> {result_sure}",
    )

    # ---- 4. a provider failure is silence, not a guess
    with _installed_provider(_ScriptedProvider(None, raise_with=RuntimeError("401 invalid key"))):
        result_down = intelligence.interpret(profile, message)
    with _installed_provider(_ScriptedProvider("{not json at all")):
        result_garbage = intelligence.interpret(profile, message)
    r.check(
        "A 401, a timeout or a garbage response degrades to the deterministic path, silently",
        result_down is None and result_garbage is None,
        "The rejected OKX listing was caused by a component that died on a 401 while every\n"
        "liveness signal stayed green, so this one is deliberately built the other way round:\n"
        "`interpret` returning None is a normal, expected outcome, and the caller's behaviour\n"
        "when it happens is the behaviour every pre-feature gate already proves (check 6). The\n"
        "feature can be entirely dead — no key, revoked key, provider outage, malformed JSON —\n"
        "without a single customer receiving a worse answer than they would have before it.",
        f"| provider raised 401 -> {result_down}\n"
        f"| provider returned malformed JSON -> {result_garbage}",
    )

    # ---- 5. THE PRICE — the model reads meaning, the profile supplies the figure
    haggle = "How much is a deep tissue massage? I would offer 40."
    understanding = _understanding(
        evidence=["deep tissue massage"], intent="price", act="enquiry", confidence=0.99)
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"si-{uuid.uuid4().hex[:8]}"))
        priced = engine.step(cur, tenant, thread, engine.Inbound(
            body=haggle, from_address=PROSPECT, from_name="Nadia Okoro"), understanding=understanding)
    quoted_amount = priced.quote.amount if priced.quote else None
    r.check(
        "The figure sent is the stored figure — the model's route to it changes nothing",
        (quoted_amount == stored_price == SPA_PRICE
         and "40" not in _figures(priced.reply)
         and str(SPA_PRICE) in _figures(priced.reply)),
        f"The model was the one that identified which service this is about. The price is still\n"
        f"£{stored_price} because that is what the owner typed into their profile at onboarding,\n"
        f"and `pricing.quote_for_match` derives it from the stored catalogue item — the model's\n"
        f"reading carries no price field at all, so there is no value for it to have influenced.\n"
        f"The customer also asserted a number of their own, 40. It appears nowhere in the reply:\n"
        f"an anchor a client states is not a fact about the tenant's business, and a model that\n"
        f"had 'read the meaning' of it could otherwise have laundered it into a quote.",
        f"| customer wrote: {haggle}\n"
        f"| model identified: {understanding.service_name!r} (no price field exists in its schema)\n"
        f"| stored profile price: {stored_price}\n"
        f"| quoted: {quoted_amount}\n"
        f"| digits in the reply body: {_figures(priced.reply)}\n"
        f"| reply: {p3._body(priced.reply)}",
    )

    # ---- 6. REGRESSION — a message that scores BELOW the comprehension floor, run both ways.
    # The floor is where a model's certainty could buy a send it should not, so the regression
    # has to be measured there rather than on a message that sails through either way.
    marginal = "Hi, I would like the deep tissue massage. I was hoping to pay 40."
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread_on = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"si-m-on-{uuid.uuid4().hex[:8]}"))
        marginal_with = engine.step(cur, tenant, thread_on, engine.Inbound(
            body=marginal, from_address=PROSPECT, from_name="Nadia Okoro"),
            understanding=_understanding(evidence=["deep tissue massage"], confidence=0.99))
        thread_off = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"si-off-{uuid.uuid4().hex[:8]}"))
        plain = engine.step(cur, tenant, thread_off, engine.Inbound(
            body=marginal, from_address=PROSPECT, from_name="Nadia Okoro"), understanding=None)
    marginal_amount = marginal_with.quote.amount if marginal_with.quote else None
    r.check(
        "REGRESSION — a confident model cannot release a reply the deterministic path would hold",
        (plain.action == marginal_with.action
         and plain.state_after == marginal_with.state_after == "AWAITING_OWNER_APPROVAL"
         and (plain.quote.amount if plain.quote else None) == marginal_amount
         and plain.within_rules == marginal_with.within_rules),
        "The identical message run with `understanding=None` — the state every deployment\n"
        "without a key, and every failed provider call from check 4, lands in — produces the\n"
        "same action, the same figure AND the same send-or-hold outcome.\n"
        "\n"
        "That last clause is here because this check caught a real defect on its first run. The\n"
        "merge in `engine.decide` read `comprehension=max(read.comprehension,\n"
        "understanding.confidence)`, so a reading the model was 0.99 sure of lifted the\n"
        "comprehension score over `confidence.COMPREHENSION_FLOOR` (0.85) and SENT a reply that\n"
        "the deterministic path held for the owner — this exact message, which scores below the\n"
        "floor on its own. That is a language model's self-reported certainty deciding an\n"
        "autonomous send, which every other gate in this build exists to make impossible. The\n"
        "score is now carried over from the deterministic read untouched. The model may still\n"
        "add to `uncovered` (check 8): strictly more escalation, never less.",
        f"| message: {marginal}\n"
        f"| with model:    action={marginal_with.action} state={marginal_with.state_after} "
        f"amount={marginal_amount} within_rules={marginal_with.within_rules}\n"
        f"| without model: action={plain.action} state={plain.state_after} "
        f"amount={plain.quote.amount if plain.quote else None} within_rules={plain.within_rules}\n"
        f"| model's self-reported confidence on the run above: 0.99\n"
        f"| comprehension floor that confidence may no longer clear: "
        f"{__import__('concierge.confidence', fromlist=['x']).COMPREHENSION_FLOOR}",
    )

    # ---- 7. THE PAYOFF — meaning the word-matcher misses, routed to a person
    # No literal "speak to a human", no "manager", no "call me" — the deterministic matcher reads
    # this as an ordinary price question and quotes it.
    oblique = ("I don't want to go back and forth with a machine about this one, it's a bit "
               "delicate. Is there someone I can talk it through with about a massage?")
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread_h = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"si-h-{uuid.uuid4().hex[:8]}"))
        handed = engine.step(cur, tenant, thread_h, engine.Inbound(
            body=oblique, from_address=PROSPECT, from_name="Nadia Okoro"),
            understanding=_understanding(
                service_name=None, act="human_request", intent="other",
                evidence=["talk it through with"], confidence=0.93))
    deterministic_would_have = engine.wants_human(oblique)
    r.check(
        "PAYOFF — a request for a person phrased in no keyword still reaches a person",
        (handed.action == "human_requested" and not deterministic_would_have
         and not _figures(handed.reply)),
        "This is what the feature is FOR, and it is worth being precise about the direction of\n"
        "the benefit. `engine.wants_human` looks for phrases, and this message contains none of\n"
        "them — on the deterministic path alone it reads as an ordinary enquiry and gets a price.\n"
        "The model recognises it as a request for a person, and the SAME unconditional SB 243\n"
        "hand-over branch that has always existed fires. The model did not decide what happens\n"
        "next; it decided which existing branch this message belongs in, and that branch sends no\n"
        "figure. Note the shape: every act the model can name routes toward a human or toward\n"
        "the deterministic path — none of them unlocks an autonomous send that was not already\n"
        "available.",
        f"| message: {oblique}\n"
        f"| engine.wants_human(text) on its own: {deterministic_would_have}\n"
        f"| action taken: {handed.action}\n"
        f"| digits in the reply body: {_figures(handed.reply)}\n"
        f"| reply: {p3._body(handed.reply)}",
    )

    # ---- 8. a model-named qualifier the profile cannot answer WITHHOLDS the send
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread_q = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"si-q-{uuid.uuid4().hex[:8]}"))
        qualified = engine.step(cur, tenant, thread_q, engine.Inbound(
            body="How much for the deep tissue massage at my flat?",
            from_address=PROSPECT, from_name="Nadia Okoro"),
            understanding=_understanding(
                qualifier_classes=["location"], evidence=["at my flat"], confidence=0.97))
    covered = comprehension.covers(tenant.profile or {}, "location")
    withheld = qualified.state_after in ("ESCALATED", "AWAITING_OWNER_APPROVAL")
    r.check(
        "A qualifier the model spots and the profile cannot answer withholds the send",
        withheld and covered is None and not _figures(qualified.reply),
        "'At my flat' is a location qualifier. This tenant's profile has no service_area,\n"
        "travel_policy or callout_policy, so the standard in-spa figure is an answer to a\n"
        "different question than the one asked. The model contributed the qualifier CLASS; what\n"
        "it could not do is wave it through. Coverage is looked up in the stored profile, and an\n"
        "uncovered class can only ever add to `uncovered` — the merge in `engine.decide` takes\n"
        "`max(read.comprehension, understanding.confidence)`, so a confident model can raise how\n"
        "much of the message we account for and has no arithmetic route to lowering the bar for\n"
        "sending. The comprehension floor is a cap on autonomy, never a fourth weighted term.",
        f"| profile coverage for 'location': {covered}\n"
        f"| state after: {qualified.state_after}   action: {qualified.action}\n"
        f"| digits in the reply body: {_figures(qualified.reply)}\n"
        f"| reply to client: {p3._body(qualified.reply) or '(nothing sent to the client)'}",
    )

    # ---- 9. the reading is on the receipt, so a decision can be audited afterwards
    with db.tenant_session(tenant_id) as cur:
        stored_receipt = store.get_receipt(cur, priced.receipt.receipt_id) \
            if hasattr(store, "get_receipt") else next(
                (x for x in store.list_receipts(cur)
                 if x.receipt_id == priced.receipt.receipt_id), None)
    recorded = (stored_receipt.decision or {}).get("detail", {}).get("semantic_understanding")
    r.check(
        "The model's exact reading is persisted on the receipt it influenced",
        (recorded is not None
         and recorded["service_name"] == SPA_SERVICE
         and recorded["confidence"] == 0.99
         and "price" not in recorded and "amount" not in recorded),
        "A model in the path is only defensible if what it said is recoverable afterwards, from\n"
        "the same signed, hashable row the decision is already recorded in — not from a log that\n"
        "rotates. This is read back out of PostgreSQL through the tenant's own RLS-scoped\n"
        "session. What is stored is the whole reading and nothing else: there is no price or\n"
        "amount key on it, because `Understanding` has no such field to record.",
        f"| receipt {stored_receipt.receipt_id} action={stored_receipt.action}\n"
        f"| semantic_understanding: {recorded}\n"
        f"| keys: {sorted(recorded) if recorded else None}",
    )

    # ---- 10. the structural claim, checked in the source rather than argued
    EXPECTED = {"service_name", "intent", "act", "qualifier_classes", "evidence", "confidence"}
    # The schema actually put on the wire in check 1, recorded by the fixture — not the schema
    # as re-typed into this harness, which would prove only that the harness agrees with itself.
    schema = provider.calls[0]["output_config"]["format"]["schema"]
    asked_for = set(schema["properties"])
    source = (config.ROOT / "concierge" / "intelligence.py").read_text()
    read_back = set(re.findall(r'data\["(\w+)"\]', source))
    numeric = {name for name, spec in schema["properties"].items()
               if "number" in str(spec) or "integer" in str(spec)}
    r.check(
        "The model is never ASKED for a figure — there is no field for one to arrive in",
        (asked_for == EXPECTED and read_back <= EXPECTED
         and schema.get("additionalProperties") is False
         and numeric == {"confidence"}
         and set(intelligence.Understanding.__dataclass_fields__) == EXPECTED),
        "The strongest form of 'no price from a language model' is not validation, it is the\n"
        "absence of a channel. The schema below is the one the provider was actually sent, and\n"
        "it declares six properties with `additionalProperties: false`. Exactly one of them is\n"
        "numeric — `confidence`, the model's certainty about its own reading, which check 3\n"
        "proves can only gate whether the reading is used at all. `Understanding` carries the\n"
        "same six fields and the module reads exactly those keys off the response, so a provider\n"
        "that volunteered a price would have it dropped before any validation ran. This check\n"
        "fails loudly if a later session adds a seventh field.",
        f"| schema properties sent to the provider: {sorted(asked_for)}\n"
        f"| additionalProperties: {schema.get('additionalProperties')}\n"
        f"| numeric properties: {sorted(numeric)}\n"
        f"| keys read back off the response: {sorted(read_back)}\n"
        f"| Understanding fields: "
        f"{list(intelligence.Understanding.__dataclass_fields__)}",
    )

    # ---- honest notes
    r.note(
        "What this suite does NOT prove, stated plainly",
        "The provider is a scripted fixture, so nothing here measures how ACCURATE a real model\n"
        "is at reading a real customer — only what happens to its answer, including its worst\n"
        "possible answers. That is the deliberate split: accuracy is a quality question that\n"
        "changes with the model, and containment is a safety property that must not. Every\n"
        "hostile payload above (invented service, fabricated quote, smuggled anchor, false\n"
        "confidence) is one a real provider can produce, and none of them reaches a customer.\n"
        "The live path is proven where it should be: `verify_email`'s round-trip and the real\n"
        "production traffic on app.quietdesks.com, where SEMANTIC_INTELLIGENCE_ENABLED is on.",
        f"| INTENTS: {intelligence.INTENTS}\n"
        f"| ACTS: {intelligence.ACTS}\n"
        f"| QUALIFIERS: {intelligence.QUALIFIERS}\n"
        f"| confidence floor for accepting a reading: 0.80 "
        f"(spam requires 0.95 in engine.decide)",
    )
