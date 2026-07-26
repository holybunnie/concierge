"""the provisioning suite — an accepted A2A job becomes a working tenant.

The question this suite answers is the one the operator asked: when an A2A job is accepted on OKX
while nobody is watching, does a working tenant exist at the end of it, and is it safe at every
point in between?

Both halves matter. A suite that only proved "a tenant appears" would pass on a build that
appears a *broken* tenant — one quoting prices nobody set. So check 2 attacks the half-finished
state directly, check 4 proves the finished profile contains only bytes the buyer actually sent,
and check 8 runs a real enquiry through the auto-provisioned tenant and demands the right price
out the other end.

The A2A CLI is not required to run this. `RecordingTransport` stands in for it exactly as
`RecordingMailer` stands in for Postmark in the email suite — the seam is `a2a.send`, and what
crosses it is asserted on.
"""

from __future__ import annotations

import uuid
from typing import Any

from . import a2a, db, engine, onboarding, provision, store
from . import verify_engine as p3


class RecordingTransport:
    """Stands in for the okx-a2a CLI. Records what we would have sent to the buying agent."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.delivered: list[str] = []

    def send(self, job_id: str, content: str, *, to_agent_id: str | None = None) -> None:
        self.sent.append(content)

    def deliver(self, job_id: str, content: str, *, provider_agent_id: str = "9274") -> None:
        self.delivered.append(content)

    @property
    def last(self) -> str:
        return self.sent[-1] if self.sent else ""


def _event(kind: str, job_id: str, content: str = "") -> a2a.Event:
    return a2a.Event(todo_id=f"todo-{uuid.uuid4().hex[:8]}", kind=kind, job_id=job_id,
                     from_agent_id="0xBUYERAGENT", content=content, raw={})


# The buyer is an agent, so it answers in the format asked for. These are the ONLY bytes that may
# ever appear in the resulting profile — check 4 holds the build to that.
BUYER_NAME = "Ashgrove Veterinary"
BUYER_EMAIL = "owner@ashgrove.example"
BUYER_DESCRIPTION = ("Ashgrove is a veterinary practice. We do vaccinations, dental work and "
                     "routine health checks for cats and dogs.")
BUYER_ANSWERS: dict[str, str] = {
    "services": ("Dental scale and polish | 180 | 45 | GBP\n"
                 "Annual health check | 55 | 20 | GBP"),
    "floor_price": "£160",
    "max_discount_pct": "10%",
    "availability": "Mon-Fri 09:00-17:00, minimum 24 hours notice",
    "timezone": "Europe/London",
    "icp": "Local pet owners",
    "escalation_triggers": "Any emergency, injury or unwell animal",
    "artifact_sample": "Hello - yes, we can see him next week.",
    "engagement_noun": "consultation",
    "client_noun": "patient",
    "group_policy": "Multiple pets seen in one visit are charged per animal.",
    "travel_policy": "We do not do home visits.",
    "hours_policy": "Weekday hours only.",
    "length_policy": "A health check is 20 minutes; a dental is 45.",
}


def _stage(tenant_id) -> dict[str, Any]:
    with db.tenant_session(tenant_id) as cur:
        t = store.get_tenant(cur)
        return dict((t.engagement or {}).get("provisioning") or {})


def _drive(tenant_id, job_id: str, transport: RecordingTransport, *, limit: int = 40) -> int:
    """Answer whatever is being asked until the tenant is live. Returns the number of turns.

    This loop is the whole claim: it contains no human step and no judgement. It reads which
    field is outstanding and sends the buyer's stored answer for it.
    """
    turns = 0
    for _ in range(limit):
        st = _stage(tenant_id)
        stage = st.get("stage")
        if stage == "live":
            return turns
        if stage == "awaiting_owner_email":
            reply = BUYER_EMAIL
        elif stage == "awaiting_business_name":
            reply = BUYER_NAME
        elif stage == "awaiting_description":
            reply = BUYER_DESCRIPTION
        else:
            awaiting = st.get("awaiting")
            reply = BUYER_ANSWERS.get(awaiting, "")
            if not reply:
                raise AssertionError(f"The interview asked for {awaiting!r}, which this buyer "
                                     f"has no scripted answer for.")
        provision.on_message(_event("message", job_id, reply))
        turns += 1
    raise AssertionError("The interview did not reach 'live' within the turn limit.")


# Keys whose values are structure, not tenant data: `pricing.as_rule` stamps every rule with the
# kind it resolved to and the template's field label, and `build_profile` wraps a writing sample
# in a typed envelope. Those are the schema, and they are identical for every tenant in the
# system. Check 4 is about tenant DATA — a price, a policy, a word for "client" — so the
# provenance grep skips them rather than pretending the buyer typed "cash".
_STRUCTURAL_KEYS = {"kind", "label", "basis"}


def _flatten(value: Any) -> list[str]:
    """Every scalar in a nested profile, as strings — for the provenance grep in check 4."""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _STRUCTURAL_KEYS:
                continue
            out += _flatten(v)
    elif isinstance(value, list):
        for v in value:
            out += _flatten(v)
    elif value is not None:
        out.append(str(value))
    return out


def run(r) -> None:
    db.migrate()

    transport = RecordingTransport()
    real_send = a2a.send
    real_deliver = a2a.deliver
    real_participants = a2a.task_participants
    a2a.send = transport.send                      # the one seam, as in the email suite
    a2a.deliver = transport.deliver
    a2a.task_participants = lambda job_id: ("9630", "9274")
    try:
        _run(r, transport)
    finally:
        a2a.send = real_send
        a2a.deliver = real_deliver
        a2a.task_participants = real_participants


def _run(r, transport: RecordingTransport) -> None:
    # The measured live event is a platform-authored notification: job id present, sender absent.
    # It must resolve the buyer from the task record and provision rather than becoming
    # ``unaddressable``.
    accepted_job = f"job-{uuid.uuid4().hex[:12]}"
    accepted = a2a.Event(
        todo_id=f"todo-{uuid.uuid4().hex[:8]}",
        kind="notification",
        job_id=accepted_job,
        from_agent_id=None,
        content=f"[Job Accepted] Job {accepted_job} has been accepted.",
        raw={},
    )
    accepted_tenant = provision.on_subscription(accepted)
    r.check(
        "A real sender-less Job Accepted notification provisions the correct buyer",
        (accepted.starts_tenant_engagement()
         and accepted.from_agent_id == "9630"
         and db.resolve_tenant_by_a2a_job(accepted_job) == accepted_tenant
         and _stage(accepted_tenant).get("buyer_agent_id") == "9630"
         and "1 of 4" in transport.last),
        "The live one-off A2A path emits a platform notification with a job id and no sender.\n"
        "Previously that was consumed as `unaddressable`, so an escrow-funded business never\n"
        "became a tenant. The worker now resolves user/ASP identities from the task record,\n"
        "checks that ASP #9274 owns it, creates the tenant, and sends the first interview question.",
        f"| job: {accepted_job}\n"
        f"| resolved buyer: {accepted.from_agent_id}\n"
        f"| tenant: {accepted_tenant}",
    )
    transport.sent.clear()

    # Platform lifecycle prose can mention an owned job but carry no sender. It is not an
    # onboarding answer and must never be allowed to become tenant data.
    chrome = a2a.Event(
        todo_id=f"todo-{uuid.uuid4().hex[:8]}",
        kind="notification",
        job_id=accepted_job,
        from_agent_id=None,
        content="[Buyer message received] Job metadata changed.",
        raw={},
    )
    before_chrome = _stage(accepted_tenant)
    r.check(
        "Sender-less platform prose cannot advance an owned tenant interview",
        (not chrome.starts_tenant_engagement()
         and chrome.from_agent_id is None
         and _stage(accepted_tenant) == before_chrome),
        "The live queue emits lifecycle summaries on the same job id as buyer messages. Without\n"
        "a received-peer header they are platform chrome, not answers. The dispatcher consumes\n"
        "them as unaddressable instead of storing the prose as a business name or service rule.",
        f"| content: {chrome.content}\n| stage unchanged: {before_chrome.get('stage')}",
    )

    job_id = f"job-{uuid.uuid4().hex[:12]}"

    # ---- 1. the legacy subscription spelling remains backward compatible
    tenant_id = provision.on_subscription(_event("sub_asp_selected", job_id))
    resolved = db.resolve_tenant_by_a2a_job(job_id)
    with db.tenant_session(tenant_id) as cur:
        fresh = store.get_tenant(cur)

    r.check(
        "The legacy subscription event still creates a tenant — backward compatible",
        (resolved == tenant_id and fresh is not None and fresh.profile == {}
         and fresh.a2a_job_id == job_id
         and _stage(tenant_id).get("stage") == "awaiting_owner_email"
         and len(transport.sent) == 1 and "1 of 4" in transport.last),
        "The supported live route is now `job_accepted`, but older platform versions emitted\n"
        "`sub_asp_selected`. Keeping that spelling readable avoids stranding an engagement\n"
        "during a platform rollout. It still yields a real tenant row and a first question\n"
        "sent back to the buying agent. The tenant is found again by the job id through\n"
        "`resolve_tenant_by_a2a_job` — the same SECURITY DEFINER shape as the inbound-address\n"
        "resolver, returning one opaque uuid, not a new isolation mechanism.",
        f"| tenant_id: {tenant_id}\n"
        f"| resolved by job id: {resolved}\n"
        f"| profile at creation: {fresh.profile if fresh else None}\n"
        f"| stage: {_stage(tenant_id).get('stage')}\n"
        f"| first message sent: {transport.last.splitlines()[0][:90]}",
    )

    # ---- 2. THE ATTACK: the half-provisioned tenant must not be able to quote
    with db.tenant_session(tenant_id) as cur:
        half = store.get_tenant(cur)
        thread = engine.open_thread(cur, half, engine.Inbound(
            body="", from_address=p3.PROSPECT, external_ref=f"prov-{uuid.uuid4().hex[:8]}"))
        out = engine.step(cur, half, thread, engine.Inbound(
            body="How much for a dental scale and polish?", from_address=p3.PROSPECT,
            from_name="Nadia Okoro"))
    reply_body = p3._body(out.reply)
    digits = [c for c in reply_body if c.isdigit()]

    r.check(
        "A half-provisioned tenant CANNOT quote — it escalates instead of inventing a price",
        (out.thread.state == "ESCALATED" and not digits),
        "The tenant row is created before the interview, so there is a window in which a\n"
        "tenant exists with an empty profile. That window is only safe if an empty profile is\n"
        "unquotable, so this check attacks it directly: a real priced enquiry, delivered\n"
        "through the real `engine.step`, against a tenant whose profile is `{}`. Phase 3's\n"
        "rule holds without amendment — nothing in the profile can answer, so it escalates,\n"
        "and not one digit reaches the client. Auto-provisioning adds no new way to be wrong.",
        f"| state: {out.thread.state}\n"
        f"| digits in reply body: {digits or 'none'}\n"
        f"| reply: {reply_body[:200]}",
    )

    # ---- 3. the interview completes on its own
    before = len(transport.sent)
    turns = _drive(tenant_id, job_id, transport)
    with db.tenant_session(tenant_id) as cur:
        live = store.get_tenant(cur)
    state = _stage(tenant_id)

    r.check(
        "The interview runs to completion autonomously and hands back a working inbox",
        (state.get("stage") == "live" and state.get("marketplace_delivered") is True
         and len(transport.delivered) == 1 and live is not None and live.profile
         and live.business_name == BUYER_NAME and live.owner_email == BUYER_EMAIL
         and live.vertical == "generic"
         and live.inbound_address.split("@")[0].startswith("ashgrove-veterinary")
         and "You're live" in transport.last),
        "Every turn in this loop is answered by the scripted buyer agent — there is no human\n"
        "step anywhere in it, which is the entire point of the feature. The trade was read\n"
        "from the buyer's own description: veterinary practice has no template, so it lands on\n"
        "the generic one, the same path the engine suite proves every unanticipated trade takes.\n"
        "The address is derived from the business name once known, not from the job id. On a\n"
        "machine with no INBOUND_DOMAIN set the domain half is the RFC 2606 placeholder, which\n"
        "is why this asserts the local part: a structurally dead domain is the correct local\n"
        "result, and the deployed box carries the real one.",
        f"| turns to live: {turns}\n"
        f"| messages sent to the buyer: {len(transport.sent) - before}\n"
        f"| business_name: {live.business_name}\n"
        f"| owner_email: {live.owner_email}\n"
        f"| vertical: {live.vertical}\n"
        f"| inbound_address: {live.inbound_address}\n"
        f"| address deliverable here: {onboarding.address_is_live(live.inbound_address)}",
    )

    # ---- 4. provenance: nothing in the profile came from anywhere but the buyer
    supplied = " ".join([BUYER_DESCRIPTION, BUYER_NAME, BUYER_EMAIL, *BUYER_ANSWERS.values()])
    supplied_norm = "".join(ch for ch in supplied.lower() if ch.isalnum())
    scalars = _flatten({k: v for k, v in (live.profile or {}).items() if k != "_meta"})
    unsupplied = []
    for s in scalars:
        norm = "".join(ch for ch in s.lower() if ch.isalnum())
        if norm and norm not in supplied_norm:
            unsupplied.append(s)
    # A number the buyer wrote as "£160" is stored as 160.0; that is the same bytes, reformatted.
    unsupplied = [u for u in unsupplied
                  if "".join(ch for ch in u.rstrip("0").rstrip(".").lower() if ch.isalnum())
                  not in supplied_norm]

    r.check(
        "Every value in the auto-built profile traces to bytes the buyer actually sent",
        not unsupplied,
        "The onboarding suite proves a template's worked example cannot become a tenant's price when a\n"
        "human is typing. This is the same proof for the wire: every scalar in the finished\n"
        "profile is matched back to the buyer's own messages. Nothing was inferred from the\n"
        "trade, defaulted because a field looked empty, or borrowed from the generic template's\n"
        "fictional consultancy — which is what makes the AI provider bound to the A2A daemon\n"
        "irrelevant to the outcome rather than merely discouraged from interfering.",
        f"| profile scalars checked: {len(scalars)}\n"
        f"| values with no source in the buyer's messages: {unsupplied or 'none'}\n"
        f"| services stored: {(live.profile or {}).get('services')}\n"
        f"| floor: {(live.profile or {}).get('pricing_rules', {}).get('floor')}",
    )

    # ---- 5. a malformed answer is refused outright, not half-accepted
    job2 = f"job-{uuid.uuid4().hex[:12]}"
    t2 = provision.on_subscription(_event("sub_asp_selected", job2))
    provision.on_message(_event("message", job2, "owner@second.example"))
    provision.on_message(_event("message", job2, "Second Practice"))
    provision.on_message(_event("message", job2, BUYER_DESCRIPTION))
    asked = _stage(t2).get("awaiting")
    before_state = _stage(t2)
    provision.on_message(_event("message", job2, "we charge whatever feels right, ask us"))
    after_state = _stage(t2)

    r.check(
        "An unparseable answer is REFUSED and re-asked — it never half-lands in the profile",
        (asked == "services" and after_state.get("awaiting") == "services"
         and (after_state.get("answers") or {}) == (before_state.get("answers") or {})
         and "|" in transport.last),
        "The buyer is a machine, which makes it tempting to accept loose prose and infer the\n"
        "structure. A service list parsed slightly wrong is a wrong price sent to a real client\n"
        "under the tenant's name, so `_parse_services` is strict and unforgiving: it refuses,\n"
        "restates the exact format, and leaves the stored answers byte-identical. The cost of\n"
        "refusing is one round trip; the cost of guessing is the tenant's credibility.",
        f"| field being asked: {asked}\n"
        f"| stored answers before: {sorted((before_state.get('answers') or {}))}\n"
        f"| stored answers after:  {sorted((after_state.get('answers') or {}))}\n"
        f"| response: {transport.last.splitlines()[0][:100]}",
    )

    # ---- 6. a replayed engagement event does not create a second tenant
    replay = provision.on_subscription(_event("sub_asp_selected", job_id))
    still_resolves_to = db.resolve_tenant_by_a2a_job(job_id)

    # And prove the constraint itself, not just the happy path: a second tenant claiming this job
    # id is rejected by the database even when the caller insists.
    duplicate_rejected = ""
    try:
        rogue = uuid.uuid4()
        with db.tenant_session(rogue) as cur:
            store.create_tenant(
                cur, tenant_id=rogue, owner_wallet="a2a:rogue", owner_email="rogue@example.invalid",
                business_name="Rogue", vertical="generic",
                inbound_address=f"rogue-{rogue.hex[:8]}@example.invalid", a2a_job_id=job_id)
        duplicate_rejected = "NOT REJECTED — a second tenant took the same job id"
    except Exception as exc:
        duplicate_rejected = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"

    r.check(
        "A replayed engagement event re-greets the same tenant instead of duplicating it",
        (replay == tenant_id and still_resolves_to == tenant_id
         and "NOT REJECTED" not in duplicate_rejected),
        "Events are consumed only after the database transaction commits, so a crash between\n"
        "the two replays the event — that is the normal case, not the rare one. Idempotency is\n"
        "enforced by a UNIQUE constraint on `a2a_job_id` rather than by this module's control\n"
        "flow, so two workers racing the same event cannot both win — and the second half of\n"
        "this check proves that by force, inserting a rogue tenant that claims the same job id\n"
        "and being refused by the constraint rather than by anything in `provision.py`. Note\n"
        "there is no 'count the tenants with this job id' assertion available here: RLS means no\n"
        "session in this system can see across tenants to count them, which is the isolation suite's\n"
        "whole result. The resolver returning one id, twice, is the strongest read there is.",
        f"| first provision:  {tenant_id}\n"
        f"| replayed event:   {replay}\n"
        f"| resolver still returns: {still_resolves_to}\n"
        f"| rogue duplicate insert: {duplicate_rejected}",
    )

    # ---- 7. the auto-provisioned tenant is inside the same RLS fence
    with db.tenant_session(t2) as cur:
        cur.execute("SELECT count(*) AS n FROM tenants")
        visible_from_other = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM tenants WHERE tenant_id = %s", (tenant_id,))
        can_see_neighbour = cur.fetchone()["n"]

    r.check(
        "An auto-created tenant is fenced by the same RLS policy as a hand-built one",
        (visible_from_other == 1 and can_see_neighbour == 0),
        "Auto-provisioning is a new way to make a tenant, and every new way to make one is a\n"
        "chance to make one outside the fence. Scoped to the second buyer, the tenants table\n"
        "contains exactly one row — its own — and the first buyer's row is not reachable even\n"
        "when named directly by its uuid. No predicate in the query does this; the policy the\n"
        "isolation suite proved does, unchanged and unextended.",
        f"| rows visible when scoped to the second tenant: {visible_from_other}\n"
        f"| rows returned asking for the first tenant by id: {can_see_neighbour}",
    )

    # ---- 8. THE PAYOFF: the auto-provisioned tenant actually works
    tenant, thread, outcomes = p3._converse(tenant_id, [
        "Hi - how much is a dental scale and polish for my cat?",
    ])
    last = outcomes[-1]
    pending = (last.thread.current_offer or {}).get("pending_approval") or {}
    drafted = pending.get("drafted_reply") or ""
    body = p3._body(drafted) if drafted else p3._body(last.reply)

    r.check(
        "A real enquiry to the auto-provisioned tenant produces the buyer's own price — held "
        "for the owner by the comprehension floor, not sent blind",
        ("180" in body and "consultation" in body.lower()
         and last.thread.state == "AWAITING_OWNER_APPROVAL"
         and last.reply is None),
        "The row existing is not the deliverable — a business that answers its enquiries is.\n"
        "This is the same `engine.step` the engine suite proves, against a profile no human ever typed:\n"
        "£180, because the buying agent said £180 four checks ago, and 'consultation' because\n"
        "this buyer's own lexicon answer said so. That is the trade-neutrality rule holding\n"
        "across a channel it was never written for.\n"
        "\n"
        "The state is the part worth reading twice, and it is not what this check first\n"
        "asserted. The weighted score is 0.85 against a 0.55 threshold — comfortably\n"
        "autonomous — and the reply is held anyway, by the comprehension floor: the client\n"
        "wrote 'for my cat', a qualifier this profile has no rule for, so only 75% of their\n"
        "words are accounted for and Layer 3 caps autonomy at exactly the point where the\n"
        "profile stops being able to speak for the tenant. Two independently built defences,\n"
        "written for the email path, holding on a channel neither was written for.\n"
        "\n"
        "This also proves the thing worth proving about auto-provisioning: a business set up by\n"
        "a machine, with no human in the loop at any point, does NOT start firing prices at\n"
        "strangers. Its first uncertain answer goes to its owner. The interview asks the\n"
        "optional questions for the same reason — a skipped lexicon costs profile completeness,\n"
        "and a tenant that queues everything forever is a tenant nobody bought.",
        f"| state: {last.thread.state}\n"
        f"| sent to the client: {last.reply!r}\n"
        f"| confidence: {pending.get('confidence')}\n"
        f"| drafted for the owner: {body[:240]}",
    )

    # ---- 9. no model anywhere in the provisioning path
    src = (provision.__file__, a2a.__file__)
    offenders = []
    for path in src:
        text = open(path).read().lower()
        for needle in ("llm_api_key", "anthropic", "openai", "import gaps", "from .gaps"):
            if needle in text.split("\"\"\"", 2)[-1]:      # ignore the module docstring
                offenders.append(f"{path.rsplit('/', 1)[-1]}: {needle}")

    r.check(
        "Nothing in the provisioning path calls a language model",
        not offenders,
        "The A2A daemon has an AI provider bound to it — the platform requires one before it\n"
        "will activate a listing — so the honest question is whether that model is anywhere\n"
        "near a tenant's rules. It is not: the questions come from the vertical template, the\n"
        "parsing from `pricing.as_rule` and the parsers in this module, and the reply text from\n"
        "`onboarding.briefing`/`read_back`. Check 4 proves this from the other direction, on the\n"
        "output; this proves it on the source.",
        f"| files scanned: {', '.join(p.rsplit('/', 1)[-1] for p in src)}\n"
        f"| model calls found: {offenders or 'none'}",
    )

    # ---- 10-13. the wire format, captured from live traffic on 2026-07-25
    #
    # Everything below is a structural copy of payloads a real marketplace agent actually
    # produced against this listing — not a guess at the documented shape. They are here because
    # every one of these cases was found by hand, in production, on the day a real agent messaged
    # the listing and got nothing back. A fault found by hand and not written into the harness is
    # a fault that comes back.
    real_inbound = {
        "id": "todo_1785004471408_bdb079b8", "kind": "notification", "status": "pending",
        "jobId": "0x4cb7dc24769104f13fd11dd406cbe77ce3102998cd6d3e954359dddfbbc25b30",
        "userContent": (
            "\U0001F4E5 [Received] SecAgent#1791 → CONCIERGE#9274 (you)\n"
            "Job: 0x4cb7...5b30\n────────────\n"
            "「Hello, I have a leaking kitchen faucet and would like a quote for the "
            "repair.」\n────────────"),
    }
    real_echo = {
        "id": "todo_1785016375055_512e6ac5", "kind": "notification", "status": "pending",
        "jobId": "0x4cb7dc24769104f13fd11dd406cbe77ce3102998cd6d3e954359dddfbbc25b30",
        "userContent": (
            "\U0001F4E4 [Sent] CONCIERGE#9274 (you) → SandboxAgent#1791\n"
            "Job: 0x4cb7...5b30\n────────────\n"
            "「This is an AI agent, not a person.」\n"
            "────────────"),
    }
    received_by_local_user = {
        "id": "todo_local_user", "kind": "notification", "status": "pending",
        "jobId": real_inbound["jobId"],
        "userContent": (
            "📥 [Received] CONCIERGE#9274 → Meridian Test Client#9630 (you)\n"
            "Job: 0x4cb7...5b30\n────────────\n"
            "「What is the business called?」\n────────────"
        ),
    }
    real_decision = {
        "id": "todo_1785004841636_54e817c9", "kind": "decision_request", "status": "pending",
        "jobId": "0x4cb7dc24769104f13fd11dd406cbe77ce3102998cd6d3e954359dddfbbc25b30",
        "choices": [{"key": "A", "label": "Retry after login"}, {"key": "B", "label": "Don't retry"}],
        # The event name really does arrive at this escaping depth: JSON, inside a shell command,
        # inside a JSON string. A regex written for bare quotes finds nothing and looks correct.
        "llmContent": ("[RECOVERABLE_AI_DISPATCH_FAILURE]\n{\"retryCommand\":\"okx-a2a session "
                       "send --content '{\\\"message\\\":{\\\"event\\\":\\\"job_asp_selected\\\"}}'\"}"),
    }

    ev_in = a2a.Event.from_payload(real_inbound)
    ev_echo = a2a.Event.from_payload(real_echo)
    ev_local_user = a2a.Event.from_payload(received_by_local_user)
    ev_dec = a2a.Event.from_payload(real_decision)

    r.check(
        "A real live payload is read correctly — the documented shape is NOT what arrives",
        (ev_in.message_text() == "Hello, I have a leaking kitchen faucet and would like a quote "
                                 "for the repair."
         and ev_in.from_agent_id == "1791"
         and ev_in.job_id and not ev_in.is_platform_internal() and not ev_in.is_own_outbound()),
        "The daemon does not send the shape the vendor documents. There is no top-level `type`\n"
        "or `event`: `kind` carries a broad category (`notification`), the buyer's words are\n"
        "wrapped in corner brackets inside a block of arrows and divider rules, and there is no\n"
        "`fromAgentId` field at all — the sender exists only inside the rendered header line.\n"
        "Handing that whole block to the strict parsers would have them reading a divider rule\n"
        "as a tenant's answer, and a service parsed out of chrome is a wrong price sent later\n"
        "under that tenant's name. This check holds the parser to the bytes that actually arrive.",
        f"| kind as sent      : {ev_in.kind}\n"
        f"| words extracted   : {ev_in.message_text()!r}\n"
        f"| sender recovered  : {ev_in.from_agent_id} (from the header, no field carries it)\n"
        f"| classified as     : buyer message",
    )

    r.check(
        "The platform's event name is found however deeply it is escaped",
        "job_asp_selected" in ev_dec.platform_events()
        and a2a.Event.from_payload({"type": "sub_asp_selected"}).platform_events() == {"sub_asp_selected"},
        "The real event name arrives as JSON inside a shell command inside a JSON string, so it\n"
        "reads `\\\\\\\"event\\\\\\\":\\\\\\\"…` by the time it lands. A lifecycle check looking for a\n"
        "top-level `sub_asp_selected` would never have fired on live traffic — auto-provisioning\n"
        "would have been silently dead on arrival for the first real buyer, and this suite could\n"
        "not have caught it, because this suite hands it the documented shape. Both are now read:\n"
        "the documented one, which the fixtures above use, and the escaped one, which the wire uses.",
        f"| found in the real decision_request : {sorted(ev_dec.platform_events())}\n"
        f"| documented top-level shape still ok: "
        f"{sorted(a2a.Event.from_payload({'type': 'sub_asp_selected'}).platform_events())}",
    )

    r.check(
        "CONCIERGE never answers its own message, and never answers the platform's",
        ev_echo.is_own_outbound() and ev_dec.is_platform_internal()
        and ev_in.receiving_agent_id() == "9274"
        and ev_local_user.receiving_agent_id() == "9630"
        and not ev_in.is_own_outbound() and not ev_in.is_platform_internal(),
        "Two ways to talk to yourself forever, both live on this box. The daemon echoes our own\n"
        "sent messages back through the same queue that carries inbound ones, distinguished only\n"
        "by `[Sent]` against `[Received]` in a rendered header — so an unguarded worker reads its\n"
        "own reply next tick and answers it, with the buyer copied in, every tick. And a\n"
        "`decision_request` is the platform asking US to choose (a failed dispatch offering\n"
        "'retry / don't retry'); answering one outward delivers our internal plumbing to a\n"
        "customer. Neither is prevented by a missing field or a lucky failure — both are named.",
        f"| our own echo    : is_own_outbound={ev_echo.is_own_outbound()}  (never answered)\n"
        f"| local User inbox: receiving_agent={ev_local_user.receiving_agent_id()}  (never answered by ASP)\n"
        f"| platform prompt : is_platform_internal={ev_dec.is_platform_internal()}  (never answered)\n"
        f"| a real buyer    : answered normally",
    )

    unserved = provision.unserved_reply()
    digits = [c for c in unserved if c.isdigit()]
    first_line = unserved.splitlines()[0]
    r.check(
        "The reply to a stranger carries the disclosure and not one digit",
        (not digits and "ai agent" in first_line.lower()
         and ("human" in first_line.lower() or "person" in first_line.lower())),
        "A message on a job with no tenant behind it used to fall through into nothing at all.\n"
        "Refusing to quote was right — no profile, no prices, and inventing one is the single\n"
        "thing this build exists to prevent — but refusing to act and refusing to speak are\n"
        "different decisions, and conflating them meant a stranger got silence, which reads as a\n"
        "broken listing rather than a principled one. So it answers, from stored prose, with the\n"
        "disclosure on line one exactly as the email path requires, and with no figure of any\n"
        "kind in it. Being asked to invent a price and declining is the demonstration.",
        f"| first line : {first_line}\n"
        f"| digits in the whole reply: {len(digits)}\n"
        f"| length     : {len(unserved)} chars, assembled in provision.py, no model",
    )

    # ---- info: the transport itself is not exercised here
    r.note(
        "The okx-a2a CLI itself is stubbed in this suite, deliberately",
        "Every check above runs against the real database, the real RLS policies and the real\n"
        "engine; only the CLI subprocess is stood in for, at the `a2a.send` seam, exactly as the\n"
        "email suite stands in for Postmark. What that leaves unproven is the wire format of the\n"
        "live daemon subprocess. The accepted-job payload itself has now been captured from a real\n"
        "paid private job. `a2a.Event.from_payload` still reads several spellings of each field\n"
        "and keeps the whole payload in `raw` rather than assuming one.",
        f"| messages the buyer would have received: {len(transport.sent)}\n"
        f"| transport binary expected at: {a2a.binary()}\n"
        f"| CLI present on this machine: {a2a.available()}",
    )
