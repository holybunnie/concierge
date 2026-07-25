"""Auto-provisioning — a marketplace subscription becomes a working tenant, with nobody watching.

Until this module existed, a tenant could only arrive because a human made one. Listing on OKX
opened a second door that strangers walk through on their own schedule, and a door nobody is
standing behind is not a product. This is the thing standing behind it.

    sub_asp_selected → create tenant → ask for the owner's address → ask what they do
                     → run the vertical interview → write the profile → hand back the inbox

Three properties hold this together, and each one is load-bearing:

1. **The tenant row is created FIRST, half-finished on purpose.** It would be tidier to interview
   first and insert a complete tenant at the end, but then in-flight onboarding state would have
   nowhere RLS-fenced to live, and we would need a second isolation mechanism for it. Instead the
   tenant exists from the first event with an empty profile, and a tenant with an empty profile is
   already safe by construction: `engine.decide` has escalated on an unanswerable profile since
   Phase 3. A half-provisioned tenant cannot quote a wrong price — it cannot quote at all.

2. **No answer is guessed.** The buyer is an agent, which makes it tempting to accept loose prose
   and infer structure. Every answer is parsed by the declared `Field.kind` or refused with the
   exact format we need. A refused answer re-asks; it never half-lands. This is the same rule
   `onboarding.build_profile` already enforces against template examples, applied to the wire.

3. **Nothing here is a language model.** The questions come from the vertical template, the
   parsing is `pricing.as_rule` and the small parsers below, and the reply text is
   `onboarding.briefing`/`read_back`. The A2A daemon has an AI provider bound to it for the
   platform's own message handling, and that binding has no part in any of this — the provisioning
   suite check 7 proves a provisioned profile contains only bytes the buyer sent.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from . import a2a, config, db, onboarding, store
from .models import Tenant

# A subscription tells us who is paying but not who to wake at 2am. We ask, and until they answer
# the address is structurally dead (RFC 2606) rather than plausible — the same choice, for the same
# reason, as `onboarding.PENDING_DOMAIN`. An owner alert bouncing loudly beats one delivered nowhere.
PENDING_OWNER = "PENDING-OWNER.invalid"

# What a tenant is called between "subscribed" and "told us their name". Never contains the job
# id: this string reaches clients, not just logs. See `on_subscription`.
PENDING_NAME = "Pending business name"

# Events that mean "a new buyer just bought". The platform names several subscription
# transitions; only these two create a tenant. Renewals and failures are not new customers.
SUBSCRIPTION_EVENTS = ("sub_asp_selected", "sub_trial_into_active")

STAGES = ("awaiting_owner_email", "awaiting_business_name", "awaiting_description",
          "interviewing", "live")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

_SKIP_WORDS = {"skip", "none", "n/a", "na", "no", "pass", "-"}


def _is_skip(content: str) -> bool:
    return (content or "").strip().strip(".").lower() in _SKIP_WORDS


class Refused(ValueError):
    """The answer did not parse. Carries the message we send back asking again."""


# ---------------------------------------------------------------- reading the interview state

def _state(tenant: Tenant) -> dict[str, Any]:
    return dict(tenant.engagement.get("provisioning") or {})


def _session(state: dict[str, Any]) -> onboarding.OnboardingSession:
    """Rebuild the onboarding session from stored state.

    `onboarding.start` is deterministic on the description, so replaying the stored answers over
    a fresh session reproduces it exactly. Nothing about the interview is held in memory between
    messages — the worker can be restarted mid-onboarding and lose nothing.
    """
    s = onboarding.start(state.get("description") or "")
    for key, value in (state.get("answers") or {}).items():
        try:
            s.answer(key, value)
        except KeyError:
            # A stored answer for a field the template no longer has. Drop it rather than crash:
            # it can only ever cost us a re-ask, and `gaps()` will surface it.
            continue
    return s


# ---------------------------------------------------------------- answer parsing

def _parse(kind: str, text: str) -> Any:
    """Turn one raw reply into a value of the declared kind, or refuse.

    Money and percent answers are passed through as the buyer's raw string on purpose:
    `pricing.as_rule` is the one place that decides whether "£90/hour, 2 hour minimum" is a
    number or prose, and duplicating that judgement here is how the two would drift apart.
    """
    text = (text or "").strip()
    if not text:
        raise Refused("That came through empty. Could you send the answer again?")

    if kind in ("money", "percent", "text"):
        return text
    if kind == "list":
        items = [p.strip(" -•\t") for p in re.split(r"[\n;]|,(?![^(]*\))", text) if p.strip(" -•\t")]
        if not items:
            raise Refused("I need at least one item. Separate them with commas or new lines.")
        return items
    if kind == "services":
        return _parse_services(text)
    if kind == "duration":
        m = re.search(r"\d+", text)
        if not m:
            raise Refused("I need a number of minutes, e.g. `60`.")
        return int(m.group(0))
    return text


_SERVICE_FMT = ("One service per line, pipe-separated, in this order:\n"
                "`name | price | duration in minutes | currency`\n"
                "For example: `Strategy day | 2400 | 480 | GBP`\n"
                "Leave duration blank if it does not apply: `Retainer | 1200 | | GBP`")


def _parse_services(text: str) -> list[dict[str, Any]]:
    """Strict, positional, and unforgiving — deliberately.

    This list is the only thing the engine will ever quote from, so a service parsed slightly
    wrong is a wrong price sent to a real client under the tenant's name. Asking a machine to
    re-send in a fixed format costs one round trip; guessing costs the tenant's credibility.
    """
    out: list[dict[str, Any]] = []
    for line in (l.strip() for l in text.splitlines()):
        if not line or line.startswith(("#", "//")):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            raise Refused(f"I could not read `{line[:60]}` as a service.\n\n{_SERVICE_FMT}")
        name, price = parts[0], parts[1]
        if not name:
            raise Refused(f"That service has no name.\n\n{_SERVICE_FMT}")
        money = re.search(r"\d[\d,]*(?:\.\d{1,2})?", price)
        if not money:
            raise Refused(f"`{price}` is not a price I can read for {name!r}.\n\n{_SERVICE_FMT}")
        entry: dict[str, Any] = {"name": name, "price": float(money.group(0).replace(",", ""))}
        if len(parts) > 2 and parts[2]:
            mins = re.search(r"\d+", parts[2])
            if not mins:
                raise Refused(f"`{parts[2]}` is not a duration in minutes for {name!r}.\n\n"
                              f"{_SERVICE_FMT}")
            entry["duration_min"] = int(mins.group(0))
        if len(parts) > 3 and parts[3]:
            entry["currency"] = parts[3].upper()
        out.append(entry)
    if not out:
        raise Refused(f"I did not find a service in that.\n\n{_SERVICE_FMT}")
    return out


def _format_hint(kind: str) -> str:
    if kind == "services":
        return f"\n\n{_SERVICE_FMT}"
    if kind == "list":
        return "\n\n(Separate items with commas or new lines.)"
    if kind == "money":
        return ("\n\n(A figure with your currency, e.g. `£1800`. If it isn't a flat number, say "
                "it in words — I'll quote your words rather than invent a figure.)")
    if kind == "percent":
        return "\n\n(A percentage, e.g. `10%`.)"
    return ""


# ---------------------------------------------------------------- the two entry points

def on_subscription(event: a2a.Event) -> uuid.UUID:
    """A buyer subscribed. Create their tenant and open the interview.

    Idempotent through the database, not through this function's control flow: `a2a_job_id` is
    UNIQUE, so a replayed event loses the insert race and we simply re-greet the existing tenant.
    That matters because events are only consumed after commit, so replays are normal, not rare.
    """
    if not event.job_id:
        raise ValueError(f"Subscription event {event.todo_id!r} carries no job id")

    try:
        existing = db.resolve_tenant_by_a2a_job(event.job_id)
        _say(event, "We're already set up — carry on where we left off below.")
        _advance(existing, event)
        return existing
    except db.TenantUnresolved:
        pass

    tenant_id = uuid.uuid4()
    # The buyer's agent id stands in for a wallet until they give us one. Prefixed so it can
    # never be mistaken for — or accidentally used as — an on-chain address.
    wallet = f"a2a:{event.from_agent_id or event.job_id}"
    # A holding name, deliberately free of the job id. `business_name` is not internal — it is
    # spoken aloud in owner alerts and in the escalation copy a real client can receive, and a
    # tenant who escalates during their first five minutes would otherwise have a marketplace
    # job id read out to their prospect. The provisioning suite check 2 greps that reply for digits.
    name = PENDING_NAME
    address = onboarding.allocate_inbound_address(name)

    with db.tenant_session(tenant_id) as cur:
        store.create_tenant(
            cur, tenant_id=tenant_id, owner_wallet=wallet,
            owner_email=f"owner@{PENDING_OWNER}", business_name=name,
            vertical="generic", inbound_address=address, profile={},
            engagement={"provisioning": {
                "channel": "a2a", "job_id": event.job_id,
                "buyer_agent_id": event.from_agent_id,
                "stage": "awaiting_owner_email", "answers": {}, "awaiting": None,
            }},
            a2a_job_id=event.job_id,
        )

    _say(event,
         "You're set up. I'm CONCIERGE — an AI agent that answers your inbound enquiries, quotes "
         "from rules you set, and escalates anything it cannot answer.\n\n"
         "Four short steps and you're live.\n\n"
         "**1 of 4.** What email address should I send owner alerts to? That is where escalations "
         "and your weekly summary go, so it needs to reach a person.")
    return tenant_id


def on_message(event: a2a.Event) -> None:
    """A reply arrived on a job we already own. Advance the interview by exactly one step."""
    if not event.job_id:
        return
    tenant_id = db.resolve_tenant_by_a2a_job(event.job_id)   # raises if not ours — correct
    _advance(tenant_id, event)


def on_unserved(event: a2a.Event) -> None:
    """A message on a job with no tenant behind it. Answer it; never leave it silent.

    This is the case that used to fall through `process_pending`'s `TenantUnresolved` branch into
    nothing at all. Refusing to act on it was right — there is no profile, so there is no price,
    and inventing one is the single thing this whole build exists to make impossible. But refusing
    to act and refusing to *speak* are different decisions, and conflating them meant a stranger
    who messaged the listing got silence, which reads as broken rather than as principled.

    So: a reply assembled here, from this file, deterministically. No model, no price, no figure
    of any kind — the same discipline as `engine.PROSE`, and for the same reason. What it says is
    what CONCIERGE is, why it will not answer the question as asked, and how to make it able to.

    Being asked to invent a price and declining is not a failure to demonstrate the product. It is
    the demonstration.
    """
    _say(event, unserved_reply())


def unserved_reply() -> str:
    """The words. Kept a pure function so the gate can assert on them without a transport."""
    contact = config.get("OPERATOR_CONTACT")
    human = (f"A human is reachable at {contact}."
             if contact else
             "Reply here and a human will pick this up.")
    return (
        # Invariant 6, on every channel: the disclosure is the first line, and a route to a
        # person comes with it. A marketplace buyer is owed this exactly as much as a client is.
        "This is an AI agent, not a person. " + human + "\n\n"
        "CONCIERGE answers inbound enquiries on behalf of a business — it quotes from prices that "
        "business has stored, negotiates down to a floor they set and no further, books the "
        "appointment, and writes a signed receipt anchored on X Layer for every commitment it "
        "makes.\n\n"
        "It has no prices of its own, which is why it cannot answer a request for a quote sent "
        "directly to it. There is no business behind this conversation yet, so there are no rules "
        "to quote from — and a number produced without them would be invented. Inventing one is "
        "the failure this system is built to make structurally impossible, so it declines instead. "
        "That refusal is the product working, not the product missing.\n\n"
        "To see it actually quote: subscribe, and I will interview you the way I would any "
        "business — what you sell, your prices, your floor, your cancellation policy. It takes "
        "four steps. From then on, an enquiry like the one you just sent gets a real quote from "
        "your own rules, a booking in your own calendar, and a receipt anyone can verify."
    )


# ---------------------------------------------------------------- the state machine

def _advance(tenant_id: uuid.UUID, event: a2a.Event) -> None:
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        if tenant is None:
            return
        state = _state(tenant)
        stage = state.get("stage")

        if stage == "live" or stage not in STAGES:
            return                                   # onboarding is done; the engine owns this now

        try:
            # `message_text()`, never `content`: a live notification wraps the buyer's actual
            # words in the platform's own rendering (arrows, agent ids, divider rules). Handing
            # all of that to `_step` would have the strict parsers reading platform chrome as the
            # tenant's answer — and a service line parsed out of a divider rule is a wrong price
            # sent later under that tenant's name.
            reply, state = _step(stage, state, event.message_text(), tenant)
        except Refused as r:
            _say(event, str(r))
            return

        # Transient keys carry the finished interview out of `_step` and are never persisted:
        # the profile belongs in its own column, not smuggled inside `engagement`.
        profile = state.pop("_profile", None)
        vertical = state.pop("_vertical", None)
        business_name = state.pop("_business_name", None)
        inbound_address = state.pop("_inbound_address", None)

        if state.get("owner_email") and tenant.owner_email.endswith(PENDING_OWNER):
            store.update_owner_email(cur, state["owner_email"])
        if profile is not None:
            store.update_profile(cur, profile)
        if vertical:
            store.update_vertical(cur, vertical)
        if business_name:
            store.update_business_name(cur, business_name)
        if inbound_address:
            tenant = store.update_inbound_address(cur, inbound_address) or tenant

        engagement = dict(tenant.engagement)
        engagement["provisioning"] = state
        store.update_engagement(cur, engagement)

    _say(event, reply)


def _step(stage: str, state: dict[str, Any], content: str, tenant: Tenant
          ) -> tuple[str, dict[str, Any]]:
    """One transition. Returns (what to say, new state). Raises Refused to re-ask unchanged."""
    state = dict(state)

    if stage == "awaiting_owner_email":
        addr = (content or "").strip().split()[-1] if content.strip() else ""
        if not _EMAIL.match(addr):
            raise Refused("I need a single email address that reaches a person — that is where "
                          "anything I can't answer gets sent. Could you send just the address?")
        state["owner_email"] = addr
        state["stage"] = "awaiting_business_name"
        return (f"Got it — alerts go to {addr}.\n\n"
                "**2 of 4.** What is the business called? I sign messages with it and build your "
                "inbound address from it.", state)

    if stage == "awaiting_business_name":
        name = " ".join((content or "").split())
        try:
            onboarding.slugify(name)
        except ValueError:
            raise Refused("I need a name I can build an email address from — at least one letter "
                          "or number. What is the business called?")
        state["_business_name"] = name
        # Re-derive the inbox now that it can be named after the business rather than a job id.
        # Nobody has been given the old address yet: it is only published at the "live" step.
        state["_inbound_address"] = onboarding.allocate_inbound_address(name)
        state["stage"] = "awaiting_description"
        return (f"**3 of 4.** Describe {name} in a few sentences: what you sell, who buys it, and "
                "anything that makes your enquiries unusual. I'll work out which questions to ask "
                "from that.", state)

    if stage == "awaiting_description":
        text = (content or "").strip()
        if len(text) < 20:
            raise Refused("I need a bit more than that to pick the right questions — a couple of "
                          "sentences about what you sell and who buys it.")
        state["description"] = text
        state["stage"] = "interviewing"
        state["answers"] = {}
        s = _session(state)
        return _next_question(s, state, tenant, preamble=(
            f"**4 of 4.** {s.classification.reason}\n\n"
            f"I have {len(s.template.required_fields())} questions. Every answer becomes a rule I "
            f"quote from — I invent nothing, so anything you skip becomes something I escalate to "
            f"you instead of answering.\n"))

    # stage == "interviewing"
    s = _session(state)
    awaiting = state.get("awaiting")
    if awaiting:
        f = s.template.field(awaiting)
        if f is None:
            state["awaiting"] = None
        elif _is_skip(content):
            if f.required:
                # "skip" must never become the answer. A required field left empty has a named
                # cost, so quote it back and ask again rather than storing the word.
                gap = next((g for g in s.gaps() if g.field_key == awaiting), None)
                raise Refused(
                    f"I can't skip that one. If you leave **{f.label}** empty: "
                    f"{gap.consequence if gap else 'I will have to escalate anything that needs it.'}"
                    f"\n\n{f.question}")
            state["skipped"] = sorted({*(state.get("skipped") or []), awaiting})
            state["awaiting"] = None
        else:
            value = _parse(f.kind, content)          # raises Refused, leaving state untouched
            answers = dict(state.get("answers") or {})
            answers[awaiting] = value
            state["answers"] = answers
            s = _session(state)

    return _next_question(s, state, tenant)


def _next_question(s: onboarding.OnboardingSession, state: dict[str, Any], tenant: Tenant,
                   preamble: str = "") -> tuple[str, dict[str, Any]]:
    """Ask for the next blocking gap, then the optional ones, then finish.

    Required fields gate going live, exactly as in the human flow. The optional ones are asked
    too, which the human flow does not do, and the reason is specific rather than thoroughness
    for its own sake: `confidence.py` scores profile completeness and counts a missing lexicon
    against it, so a tenant that skips its own vocabulary scores below the autonomy threshold and
    queues every reply for an owner who subscribed precisely so as not to be in the loop. Asking
    a human four more questions is friction; asking an agent four more questions is four more
    messages. Any of them can be declined with "skip" — declining is still allowed, it is just
    no longer the default.
    """
    gaps = s.gaps()
    if gaps:
        g = gaps[0]
        f = s.template.field(g.field_key)
        state["awaiting"] = g.field_key
        body = (f"**{g.label}** — {g.question}{_format_hint(f.kind if f else 'text')}\n\n"
                f"_Why: {f.why if f else ''}_")
        return ((preamble + "\n" + body).strip(), state)

    skipped = set(state.get("skipped") or [])
    for a in s.advisories():
        if a.field_key in skipped:
            continue
        f = s.template.field(a.field_key)
        state["awaiting"] = a.field_key
        body = (f"**{a.label}** _(optional — reply `skip` to leave it)_ — {a.question}"
                f"{_format_hint(f.kind if f else 'text')}\n\n"
                f"_Why: {f.why if f else ''}_")
        return ((preamble + "\n" + body).strip(), state)

    # Nothing blocking left: build the profile and hand back the inbox.
    profile = s.build_profile()
    state["awaiting"] = None
    state["stage"] = "live"
    state["_profile"] = profile
    state["_vertical"] = s.template.key

    live = onboarding.address_is_live(tenant.inbound_address)
    lines = ["You're live. Here is exactly what I will do, in your own words:", "",
             s.read_back(), ""]
    if live:
        lines += ["**Forward your enquiries to:**", f"`{tenant.inbound_address}`", ""]
    else:
        # The mail domain is not configured on this deployment. Say so plainly rather than hand
        # over an address that looks usable and silently is not.
        lines += [f"Your address is reserved as `{tenant.inbound_address}`, which is not yet "
                  f"deliverable — the mail domain is still being set up. I'll confirm the moment "
                  f"it is live.", ""]
    lines += ["Every message I send opens by saying I'm an AI acting for you and offers a route "
              "to a human. Anything I can't answer from the rules above goes to you instead of "
              "being guessed at."]
    advisories = s.advisories()
    if advisories:
        lines += ["", f"{len(advisories)} optional thing(s) still open — I work without them, but "
                      f"replies sound less like you:"]
        lines += [f"  · {a.label} — {a.question}" for a in advisories]
    return ("\n".join(lines), state)


def _say(event: a2a.Event, content: str) -> None:
    a2a.send(event.job_id or "", content, to_agent_id=event.from_agent_id)


# ---------------------------------------------------------------- the worker loop body

def process_pending() -> dict[str, int]:
    """Read every unhandled marketplace event, act on it, then mark it handled.

    Consume happens last and only on success, so a crash replays the event. Combined with the
    UNIQUE `a2a_job_id`, replaying is safe: the duplicate insert loses and the buyer is re-greeted
    rather than duplicated.
    """
    counts = {"seen": 0, "provisioned": 0, "advanced": 0,
              "unserved": 0, "echo": 0, "internal": 0, "skipped": 0, "failed": 0}
    for event in a2a.pending_events():
        counts["seen"] += 1

        # Our own outbound message, echoed back through the same queue. Consumed rather than left
        # pending: there is nothing to decide, and a queue that fills with our own sent mail
        # buries the next real buyer in it.
        if event.is_own_outbound():
            counts["echo"] += 1
            if event.todo_id:
                a2a.consume(event.todo_id)
            continue

        # The platform asking US something — a failed AI dispatch offering "retry / don't retry",
        # for instance. Deliberately left unconsumed rather than answered: choosing on the
        # operator's behalf could re-run a dispatch they meant to abandon, and consuming it would
        # hide a real operational problem. It stays visible in every run's log until resolved.
        if event.is_platform_internal():
            counts["internal"] += 1
            continue

        try:
            if a2a.KNOWN_EVENTS and (set(SUBSCRIPTION_EVENTS) & event.platform_events()):
                on_subscription(event)
                counts["provisioned"] += 1
            elif event.job_id:
                try:
                    on_message(event)
                    counts["advanced"] += 1
                except db.TenantUnresolved:
                    # Not our job — but a stranger is still owed an answer. Silence reads as a
                    # broken listing; this is the one branch that used to produce it.
                    on_unserved(event)
                    counts["unserved"] += 1
            else:
                counts["skipped"] += 1
        except Exception:
            counts["failed"] += 1           # leave it unconsumed so the next tick retries
            continue
        if event.todo_id:
            a2a.consume(event.todo_id)
    return counts
