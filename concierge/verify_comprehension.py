"""the comprehension suite — comprehension: does the agent answer the question it was actually asked?

Every other suite proves CONCIERGE cannot invent a figure. That is a real property and it holds:
there is exactly one language-model call in the package (`gaps.classify_gap`, offline, post-hoc)
and it can never reach a reply. But "never invents a number" is not the same as "never answers
wrongly", and the gap between those two is this suite's entire subject.

The dangerous outcome is a REAL price attached to a MISUNDERSTOOD question. It is worse than a
refusal, because a quote is signed, receipted and anchored on-chain as a commitment — an escalation
costs the owner a minute of attention, while a confidently wrong quote costs them the difference,
in public, with a receipt proving they said it.

So the pass condition here is deliberately asymmetric:

  * answered correctly                      -> pass
  * escalated / asked / queued for the owner -> PASS. Failing toward the owner is the system
                                                working, not a defect.
  * a figure sent for a question that could not be answered from the profile -> FAIL

The questions are generated from each tenant's own profile by `concierge.corpus` — see that
module for why nothing here is written in any trade's vocabulary. This suite runs three tenants,
one of which (check 4) has no vertical template at all, to prove the corpus and the comprehension
behaviour are trade-neutral rather than tuned to the trades that happen to have templates.
"""

from __future__ import annotations

import re

from . import corpus, db, engine, store
from . import verify_engine as p3


def _ask(tenant_id, question: str) -> tuple[str, bool, bool, str]:
    """One question, one fresh thread. Returns (action, a_price_was_sent, autonomous, body).

    A fresh thread per question is deliberate: this suite measures how a question is understood
    on its own, not how a negotiation evolves — the engine and 3b-3 already prove the latter.

    "A price was sent" is deliberately narrower than "the reply contains a digit". Answering
    "X takes 60 minutes" to a duration question is the CORRECT behaviour and contains a digit;
    counting it as a price would score the fix as the defect it repairs. The harm this suite
    exists to measure is specifically a *monetary commitment* reaching a prospect, so the test
    is whether the rendered price from the decision's own quote appears in the message body.
    """
    _, thread, outs = p3._converse(tenant_id, [question], p3.FixtureCalendar())
    out = outs[-1]
    body = p3._body(out.reply or "")
    # `quote.amount is not None` would be the wrong test: a prose rule ("£250 + VAT for the
    # first hour, fixed") carries no parsed amount and is quoted back verbatim, but it is every
    # bit as much a monetary commitment. Judging on the rendered text catches both kinds.
    rendered = out.quote.render() if out.quote else None
    price_sent = bool(rendered and rendered in body)
    # Autonomy: the client got a real answer without a human being pulled in. Queued-for-owner
    # (Feature 2) and escalated both count as "needed a human", because both mean the owner has
    # to spend attention before the prospect hears anything useful.
    autonomous = out.action not in ("unquotable", "uncovered_qualifier", "suitability_question",
                                    "unanswerable_duration", "human_requested", "hold",
                                    "escalated", "spam") and thread.state != "AWAITING_OWNER_APPROVAL"
    return out.action, price_sent, autonomous, body


def _run_corpus(tenant_id, profile, foreign):
    questions = list(corpus.generate(profile, foreign_services=foreign))
    results = []
    for q in questions:
        action, price_sent, autonomous, _body = _ask(tenant_id, q.text)
        results.append((q, action, price_sent, autonomous))
    return corpus.summarise(results)


def _profile(tenant_id):
    with db.tenant_session(tenant_id) as cur:
        return store.get_tenant(cur).profile


def run(r) -> None:
    db.migrate()

    # ---- 1. the corpus itself carries no trade vocabulary
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).with_name("corpus.py").read_text())
    # Grep the string LITERALS rather than the raw file: those are the only text that can ever
    # reach a generated question. Greping the whole file instead would flag Python's own
    # vocabulary (`@property`) as trade vocabulary, and a check that cries wolf gets weakened
    # later by someone adding an exception list — which is how this rule would actually die.
    # Docstrings are excluded for the same reason: this module's prose has to name trades to
    # explain the rule it keeps.
    # clean=False matters: the default dedents the text, so the comparison below would never
    # match and every docstring would be greped as if it were a question template.
    docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
    literals = [n.value.lower() for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]
    offenders = sorted({n for n in engine.TRADE_NOUNS for lit in literals
                        if re.search(rf"\b{re.escape(n)}\b", lit)})
    r.check(
        "The question corpus contains no trade vocabulary — questions come from the tenant",
        not offenders,
        "Same rule `engine.PROSE` lives under, applied to the harness this time. Nobody knows\n"
        "what a tenant will sell, so a corpus with a spa's questions written into it would prove\n"
        "nothing about the barrister, and less about the trade neither of us has thought of. Every\n"
        "question is a template with a {service} hole filled from `profile.services`. The\n"
        "'asked for something you don't offer' case — which cannot be written down without\n"
        "knowing the trade — is generated by borrowing another tenant's service name.",
        f"| trade nouns searched for: {len(engine.TRADE_NOUNS)}\n"
        f"| offenders in corpus.py templates: {offenders or 'none'}\n"
        f"| qualifier classes (generic commercial English): {', '.join(corpus.QUALIFIER_CLASSES)}",
    )

    # ---- build three tenants: two with templates, one with none
    spa_id, legal_id, vet_id = p3._onboard(p3.SPA), p3._onboard(p3.LEGAL), p3._onboard(p3.VET)
    spa_p, legal_p, vet_p = _profile(spa_id), _profile(legal_id), _profile(vet_id)

    spa = _run_corpus(spa_id, spa_p, corpus.service_names(legal_p))
    legal = _run_corpus(legal_id, legal_p, corpus.service_names(spa_p))
    vet = _run_corpus(vet_id, vet_p, corpus.service_names(legal_p))

    def _evidence(label, res):
        lines = [f"| {label}: {res['total']} questions, "
                 f"{len(res['wrong_confident'])} answered with a PRICE that should not have been"]
        for q, action in res["wrong_confident"][:6]:
            lines.append(f"|    [{q.kind}] {q.text!r} -> action={action}, a price was sent")
        return "\n".join(lines)

    # ---- 2. the headline: a figure is never sent for a question the profile cannot answer
    total_wrong = (len(spa["wrong_confident"]) + len(legal["wrong_confident"])
                   + len(vet["wrong_confident"]))
    r.check(
        "No figure is ever sent in answer to a question the stored profile cannot answer",
        total_wrong == 0,
        "This is the check this suite exists for. A qualifier the profile does not cover ('for\n"
        "two people', 'at my home', 'on a bank holiday'), or a question that is not about price\n"
        "at all ('how long does it take'), must not come back with a figure. Escalating is a\n"
        "pass. Asking a clarifying question is a pass. Sending a real price for a question that\n"
        "was not the one asked is the failure, because that price is then signed, receipted and\n"
        "anchored as a commitment the owner has to honour.",
        f"{_evidence('tenant A (has template)', spa)}\n"
        f"{_evidence('tenant B (has template)', legal)}\n"
        f"{_evidence('tenant C (NO template)', vet)}\n"
        f"| total across all three: {total_wrong}",
    )

    # ---- 3. the other direction: plain price questions still get answered
    missed = len(spa["missed_quotes"]) + len(legal["missed_quotes"]) + len(vet["missed_quotes"])
    asked = spa["total"] + legal["total"] + vet["total"]
    r.check(
        "An unambiguous price question is still answered — safety has not become refusal",
        missed == 0,
        "A system that escalates everything would pass check 2 trivially and be worthless. This\n"
        "is the counterweight: a direct, unqualified question naming a service the tenant\n"
        "actually sells must still produce a figure, autonomously.",
        f"| questions asked across three tenants: {asked}\n"
        f"| unambiguous price questions that failed to get a figure: {missed}\n"
        + "\n".join(f"|    {q.text!r} -> {a}" for q, a in
                    (spa["missed_quotes"] + legal["missed_quotes"] + vet["missed_quotes"])[:6]),
    )

    # ---- 4. trade-neutrality of the behaviour, not just the corpus
    vet_wrong = len(vet["wrong_confident"])
    r.check(
        "The tenant with NO vertical template comprehends exactly as safely as the ones with",
        vet_wrong == 0 and vet["total"] > 0,
        "the engine suite check 2 proves a template-less trade can QUOTE as well as one with a template.\n"
        "This proves it also REFUSES as well — that comprehension safety is a property of the\n"
        "engine rather than something a vertical template confers. If this ever fails while\n"
        "checks 2 and 3 pass, the fix has been tuned to the trades that have templates, which is\n"
        "the failure mode the whole trade-neutral design exists to prevent.",
        f"| template-less tenant: {vet['total']} questions, {vet_wrong} wrongly answered\n"
        f"| per-question-kind (kind: figure_sent/total):\n"
        + "\n".join(f"|    {k}: price_sent={v['price_sent']}/{v['total']}, autonomous={v['autonomous']}/{v['total']}"
                    for k, v in sorted(vet["by_kind"].items())),
    )

    # ---- 5. autonomy — safety must not have been bought by escalating everything
    AUTONOMY_TARGET = 0.85
    answerable = spa["answerable_total"] + legal["answerable_total"] + vet["answerable_total"]
    answerable_auto = sum(res["answerable_autonomy"] * res["answerable_total"]
                          for res in (spa, legal, vet))
    rate = answerable_auto / answerable if answerable else 0.0
    overall = sum(res["autonomy"] * res["total"] for res in (spa, legal, vet)) / (
        spa["total"] + legal["total"] + vet["total"])
    r.check(
        f"At least {AUTONOMY_TARGET:.0%} of answerable questions are handled without a human",
        rate >= AUTONOMY_TARGET,
        "Check 2 alone is trivially passable by escalating every question ever asked, which\n"
        "would be a safe and useless product. This is the counterweight that makes check 2 mean\n"
        "something: of the questions a tenant's stored profile genuinely CAN answer, the great\n"
        "majority must be answered by the agent alone.\n"
        "The denominator is the answerable subset on purpose. This corpus is an adversarial\n"
        "sweep, not a sample of real traffic — roughly half of it is questions deliberately\n"
        "constructed so that no stored profile could answer them (a qualifier the tenant never\n"
        "priced, a service they do not sell, a request for a human). Measuring autonomy against\n"
        "that denominator would report a number no real inbox would ever produce. Both figures\n"
        "are shown so neither can flatter the other.",
        f"| answerable questions across three tenants: {answerable}\n"
        f"| answered autonomously: {answerable_auto:.0f} ({rate:.1%}) — target {AUTONOMY_TARGET:.0%}\n"
        f"| autonomy over the FULL adversarial corpus (incl. the unanswerable half): {overall:.1%}\n"
        f"| what a human is pulled in for, by design: uncovered qualifiers, suitability\n"
        f"|   judgements, services not offered, explicit human requests, low comprehension",
    )

    # ---- 6. the payoff: answering the onboarding policy questions buys autonomy back
    policies = {
        "group_pricing": "Two people at the same time: both prices, plus £15 for the room.",
        "travel_policy": "I can come to you within 10 miles — add £25 for the visit.",
        "availability_policy": "Evenings until 8pm at no extra charge. Sundays are +20%.",
        "duration_options": "Everything is available at 60 or 90 minutes; the 90 adds £30.",
    }
    stocked_id = p3._onboard(p3.SPA)
    with db.tenant_session(stocked_id) as cur:
        tenant = store.get_tenant(cur)
        profile = dict(tenant.profile)
        profile.update(policies)
        store.update_profile(cur, profile)
    stocked = _run_corpus(stocked_id, _profile(stocked_id), corpus.service_names(legal_p))

    # The same tenant, the same trade, the same generated questions — the only difference is
    # whether the owner answered four optional questions at onboarding.
    gained = stocked["autonomy"] - spa["autonomy"]
    r.check(
        "Answering the optional policy questions converts directly into autonomy, with no new code",
        gained > 0 and len(stocked["wrong_confident"]) == 0,
        "`verticals._qualifier_policies` asks four optional questions — more than one person,\n"
        "travelling to the client, out-of-hours, different lengths — and each maps onto a key\n"
        "`comprehension.QUALIFIER_COVERAGE` already reads. This check proves the loop closes:\n"
        "two tenants in the same trade with the same menu and the same questions, one of whom\n"
        "filled those four in, and the difference is measured rather than asserted.\n"
        "Crucially the answer is the tenant's OWN sentence, quoted verbatim into the reply\n"
        "beside the figure — never paraphrased and never folded into a new number. A policy\n"
        "reading '+£25 for the visit' would otherwise be silently dropped while the base price\n"
        "went out alone, which is the same confidently-wrong answer in a new costume. And the\n"
        "wrong-price count stays at zero, so the autonomy was bought with real stored answers\n"
        "rather than by loosening the bar.",
        f"| identical tenant WITHOUT the four policies: {spa['autonomy']:.1%} autonomous\n"
        f"| identical tenant WITH them:                 {stocked['autonomy']:.1%} autonomous\n"
        f"| gained: {gained:+.1%}, wrong prices either way: "
        f"{len(spa['wrong_confident'])} / {len(stocked['wrong_confident'])}\n"
        f"| policies answered (the tenant's own words, quoted verbatim in replies):\n"
        + "\n".join(f"|    {k}: {v!r}" for k, v in policies.items()),
    )

    r.note(
        "What this suite deliberately does not do",
        "It does not check phrasing, tone or helpfulness — only whether a figure was sent for a\n"
        "question that could not be answered from stored data. Those are real qualities and this\n"
        "gate would pass a system that is safe and curt. It also runs each question on a fresh\n"
        "thread, so it measures first-contact comprehension; multi-turn negotiation is the engine suite's\n"
        "and the floor-curve suite's subject.",
        f"| questions generated per tenant is a function of that tenant's own menu size\n"
        f"| tenants exercised: 3 (two with a vertical template, one without)",
    )
