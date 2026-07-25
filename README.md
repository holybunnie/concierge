# CONCIERGE

**Your inbound, closed while you're away — every quote, negotiation, and booking, provably within your rules.**

An autonomous inbound-deal agent. It reads a business's inquiries, qualifies them, quotes from the
owner's own pricing rules, negotiates inside hard floors, books the call, and signs an on-chain
receipt proving every commitment stayed within the rules it was given.

Built as an ASP for the OKX.AI Genesis Hackathon. Target categories: **Revenue Rocket** and
**Best Product**. (The build spec targeted a "Business Potential" track — that track does not
exist; see [`docs/VERIFICATION_LEDGER.md`](docs/VERIFICATION_LEDGER.md) §7.)

## The claim, and why you should not take my word for it

Anyone can say their agent "quotes intelligently." The interesting question is whether it can quote
*wrongly*. CONCIERGE is built so it structurally cannot:

- **No price ever comes from a language model.** Prices, floors, availability and commitments derive
 from the tenant's stored profile through deterministic code. The LLM understands text, drafts
 prose, and classifies verticals. If a client asks about something not in the profile, the answer
 is escalation to the owner — never an invention.
- **Tenant isolation is schema-level, not prompt-level.** Every request resolves exactly one
 `tenant_id` before any business logic runs, and every query is filtered by it at the data layer.
- **Every commitment is a signed receipt anchored on X Layer**, recording which rule was checked and
 whether the action stayed inside it. Tampering is detectable; "within rules" is verifiable by a
 third party who trusts neither the tenant nor us.

Three diagrams of how that is arranged — the system, the isolation boundary, and what happens to one
stranger's email — are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Status

**Live.** CONCIERGE runs at `https://app.quietdesks.com` — a real email from a stranger reaches a
real tenant, is answered from that tenant's own inbox, and the commitment is anchored on X Layer
mainnet. It is listed on the OKX A2A marketplace as agent **#9274**, and a buying agent that
subscribes gets a working business with nobody in the room.

Everything is built and verified except **A2A escrow**, which is blocked on a vendor API shape no
document we can reach actually specifies (ledger U3) plus the OKX Agentic Wallet, and
**cross-tenant benchmarking**, which needs real escrow data to exist first. No part of this repo
simulates a capability it has not built.

```bash
pip install -r requirements.txt
docker compose up -d postgres # isolation needs a real Postgres; SQLite cannot do this

python3 verify.py --suite foundations # live calls to Cal.com and X Layer, raw evidence printed
python3 verify.py --suite isolation # 11 checks, 9 of them attacks on tenant isolation
python3 verify.py --suite onboarding # 11 checks across three real business descriptions
python3 verify.py --suite engine # the state machine + guardrails, 16 checks
python3 verify.py --suite comprehension # answering the question actually asked, 6 checks
python3 verify.py --suite autonomy # confidence-scored autonomy, 7 checks
python3 verify.py --suite floor-curve # the decaying floor, 4 checks
python3 verify.py --suite follow-up # safe follow-up, 3 checks
python3 verify.py --suite email # the email connector (Postmark), 8 checks
python3 verify.py --suite booking # live Cal.com — makes and cancels a real booking
python3 verify.py --suite receipts # X Layer mainnet — spends real (tiny) gas
python3 verify.py --suite public-receipts # public receipt verification, anchors 2 more receipts
python3 verify.py --suite scheduler # summary + scheduled worker, 10 checks
python3 verify.py --suite product-gaps # unmet demand captured from real escalations, 5 checks
python3 verify.py --suite provisioning # a subscription becomes a working tenant, 9 checks
```

Each suite is named for the capability it proves, and prints the raw evidence behind every pass.
How the pieces fit together: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Full state in
[`docs/HANDOFF.md`](docs/HANDOFF.md).

No harness contains a mock or a fixture response, with three narrowly declared exceptions, named
as such in every check that touches them: the engine suite's calendar (a fixture that lives in the
harness, not the package — the booking suite replaces it with live Cal.com calls), the email
suite's recording mailer (production sends through `postmark.PostmarkMailer`, which refuses to run
without a real token, and the live round-trip is proven separately), and the provisioning suite's
okx-a2a CLI, stubbed at the `a2a.send` seam because the live daemon's own event payloads cannot be
confirmed until a real buyer subscribes. The foundations suite makes real network calls and reports
FAIL if the network is down rather than falling back to a cached answer. Every other suite runs
real SQL against a real PostgreSQL 16 server as the real unprivileged application role.

### How isolation is actually enforced (the isolation suite)

Every tenant table has a PostgreSQL row-level security policy keyed on a transaction-scoped
setting. The application connects as a role that owns no tables and has `NOBYPASSRLS`, so:

- `store.py` contains **no `WHERE tenant_id = ?` clauses anywhere** — and still cannot leak.
- A session with **no tenant resolved sees zero rows**, not all rows. A forgotten scope is a
 visible empty result, never a silent cross-tenant read.
- `SET row_security = off` from the app role is refused by Postgres outright.
- Tenant B running `UPDATE threads SET state='DEAD'` with no WHERE clause affects exactly one
 row: her own.

The single deliberate crossing point is address→tenant resolution, which must run before a scope
exists. It is two `SECURITY DEFINER` functions that return an opaque uuid and nothing else — and
the harness proves that holding that uuid still reads no rows.

### Onboarding, and the price that must never be invented (the onboarding suite)

Onboarding classifies the tenant's vertical and briefs them the way you'd brief a new sales hire:
the right questions for their trade, a worked example beside each one, and every gap named with
what it will cost them ("no cancellation policy — clients will ask, and I'll escalate every one").

The failure onboarding exists to prevent is a business quoting £85 because the template's
*fictional example* said £85. So:

- **`build_profile()` cannot see `Field.example`.** A tenant who answers nothing gets an empty
 profile — no services, no pricing rules — and a profile with no prices cannot quote. The harness
 runs exactly that case and greps the result for the example's values.
- **Numbers scraped from the tenant's own description are candidates, not facts.** A regex cannot
 tell your price from a competitor's price quoted in passing, so each is shown with its
 surrounding words and must be confirmed before it enters the profile.
- **The read-back is rendered from the built profile**, not from the answers — so what the tenant
 confirms is literally the object the engine will quote from.
- **Ambiguity is refused.** "Property matters and litigation for landlords" scores 5 vs 4, too
 close to call, so onboarding asks instead of guessing. The vertical decides which questions get
 asked; a wrong guess collects the wrong profile entirely.

Classification is a weighted lexicon that returns the exact terms behind its decision — no LLM.
It began that way because the LLM key hadn't arrived and a classifier that needs a credential we
don't have is a classifier that doesn't exist. The key has since arrived, and it stayed that way
anyway: a classification you can read the reasoning of, and which abstains when two verticals score
too close, is worth more here than one you have to trust.

### Confidence-scored autonomy — not every reply earns the right to send itself (the autonomy suite)

Every quote and counter-offer CONCIERGE computes now carries a **decision-confidence score**,
worked out from three things that are actually checkable, not felt: how complete the tenant's own
profile is, how close the agreed figure sits to their floor, and whether this exact price has been
booked before. A reply scoring below the tenant's own per-service threshold (conservative by
default) is **drafted but not sent** — it queues for the owner to approve or edit, in a new thread
state, rather than going out on a guess.

- **The score is arithmetic, never a model's self-reported certainty.** `concierge/confidence.py`
 imports nothing that could reach a network, exactly like `pricing.py` and `guardrails.py`. It
 may only decide whether an already-computed reply sends or waits — never the price, the rule, or
 the words in it.
- **It is persisted, not just rendered once.** The score and every signal that produced it are
 written onto the same receipt the engine already writes, so it can be read back and argued over
 later — the harness proves this with a fresh database query, not the in-memory object.
- **A thin profile queues; a complete one, doing the same kind of quote, sends immediately** — and
 a *complete* profile still queues a negotiation sitting right on the floor, because completeness
 alone isn't the whole story. Three real prior bookings at that exact price move the same
 previously-marginal figure into auto-send territory. the autonomy suite proves both directions, plus a
 regression re-run of the engine suite's own fully-autonomous booking journey to show it's unaffected.

### The decaying floor — a richer floor shape, still a hard bound (the floor-curve suite)

A tenant's floor doesn't have to be one flat number. `pricing_rules.floor_curve` lets a tenant
optionally declare a curve — start at a higher figure, allow more room as a negotiation goes on
(measured in rounds or days), but never below an absolute floor — and the *same* guardrail check
the engine suite already proved evaluates the current point on that curve instead of a static one.

- **Optional, and backward compatible.** A tenant who never sets a curve negotiates on the flat
 floor exactly as before — nothing about `bounds_for` or `negotiate` changes for them, proven by
 re-running the engine suite's flat-floor tenant at round 0 and round 9 and getting byte-identical rulings.
- **The absolute floor is a hard clamp, not a convention.** It's enforced inside the function that
 reads the curve, so no caller — however far into a negotiation, however the curve was authored —
 can construct a bound below it. the floor-curve suite's red-team pushes six real negotiation rounds past
 where the curve's defined points run out and confirms the floor never moves a penny further.
- **The curve is set by the tenant, never inferred.** Nothing adjusts it mid-negotiation — "be
 more flexible because the conversation feels promising" is exactly the failure mode this design
 refuses to implement, and there is no function in this codebase that could do it.

### Safe Follow-Up — re-engaging a thread that already exists, never cold outbound (the follow-up suite)

A prospect asks a question, gets a quote, then goes quiet. Safe Follow-Up nudges them once — from
the *same* thread's own history, not a generic template — and marks the thread `DEAD` if a second,
longer silence follows. It is deliberately **not** cold outbound (prospecting a stranger who never
contacted the tenant): that's a different product decision, with a different legal footing, and
this build refuses to blur the two.

- **The nudge is drawn from the actual conversation.** `followup.draft` calls the same
 `engine.render` every other reply uses, so it inherits the AI disclosure, the tenant's own
 nouns, and — via the same mechanism the `booked` confirmation already uses — whatever price is
 actually on the table for that thread. Never "just checking in" with nothing behind it.
- **The boundary against cold outbound is enforced in code, not policy.** A follow-up may reach
 only a thread whose own stored history already contains a real message *from* that contact —
 checked directly on the thread's data, never trusted from the caller or inferred from its state
 alone. the follow-up suite's negative test builds a thread with no genuine inbound message at all, pushes
 the clock ten years forward, and confirms it is never touched and no email is ever sent to it.
- **There is no function anywhere in this codebase that accepts a bare email address and sends an
 introduction.** If that's ever wanted, it's a separate product built on the same engine — not a
 gap quietly left in this one.

### Public receipt verification — the client gets to check the receipt too (the public-receipts suite)

Every commitment CONCIERGE makes is already a signed, on-chain-anchored receipt. This
feature turns that into something the *client* can look at, not just an internal audit trail:
`GET /r/{receipt_id}` is a public, unauthenticated, read-only page showing exactly what was
committed — and the outbound quote/negotiation email now carries a link straight to it.

- **A third narrowly-scoped door, same pattern as tenant resolution.** `public_receipt(rid uuid)`
 is a `SECURITY DEFINER` SQL function scoped by receipt_id alone — it returns at most one row,
 and never `tenant_id` or `thread_id`. There is no query shape that turns "I have one receipt
 id" into "show me this tenant's other receipts."
- **Only real commitments are shown — never internal reasoning.** `receipts.public_view`
 whitelists exactly three actions (a quote, a negotiated counter, a booking). A floor breach or
 an escalation carries the tenant's actual floor figure and the reasoning behind a refusal —
 never meant for a stranger with a link — so it renders **the identical "not found" page** a
 nonexistent or malformed id gets. the public-receipts suite proves this with a real, anchored floor-breach
 receipt: it exists, it's on-chain, and it is still unreachable by its own real id.
- **The link is never fabricated.** It only appears when a real public base URL is configured;
 absent one, the email sends exactly as it did before this feature existed — the same honest
 degradation as a tenant address with no domain yet (`PENDING-DOMAIN.invalid`).
- **The transaction link goes to a verified real URL**, not a guessed one: the obvious
 `oklink.com/xlayer/tx/...` pattern actually 301-redirects to `oklink.com/x-layer/evm/tx/...` —
 found by checking live against a real anchored transaction, not assumed (ledger, Feature 3).

### The summary, and the scheduler that runs it on a timer

Two jobs existed only as functions nothing ever called on a schedule: signing and anchoring a
receipt (`receipts.anchor()` existed; nothing ran it automatically), and Safe Follow-Up's
`dispatch()`. The scheduler calls both, plus a periodic per-tenant summary.

- **Every number in the summary is arithmetic over rows every other suite already writes and
 verifies.** `summary.build_summary` counts real threads and receipts — quotes, negotiations,
 bookings and their value, escalations (with the prospect's actual words carried through,
 verbatim), how many replies Feature 2 held for approval, how many leads Safe Follow-Up nudged
 or marked gone quiet. No parallel bookkeeping that could drift from what actually happened.
- **The scheduler doesn't block a database transaction on a network call it doesn't need to.**
 Decide and persist first, send after — the same discipline `mail.handle_inbound` and
 `followup.dispatch` already use. A summary fires once per period (tracked on the tenant's own
 profile, read back and re-checked, not assumed from memory), never once per scheduler tick.
- **the scheduler suite proves the anchoring job's honest missing-credential skip without spending any NEW
 real mainnet gas.** The mechanism itself — signing and confirming a transaction — is the receipts suite and
 the public-receipts suite's job, proven repeatedly against the real deployed contract; the scheduler only needed to
 prove the scheduled wrapper picks the right rows and degrades exactly as honestly as every
 other missing-credential path in this codebase when there's no signer configured.

### Unmet demand, in the prospect's own words (the product-gaps suite)

Every time CONCIERGE escalates because a business's profile cannot answer something, that is a
customer asking for a thing the business doesn't sell. Those escalations were already happening and
were already being thrown away. Now they're counted and quoted back to the owner.

- **It is instrumentation on an existing decision, never a new one.** A `gap_events` row is written
  as one side effect of the "unknown query → escalate" transition that Phase 3 already proved — and
  only that one. A floor breach, a request for a human, or a tripped escalation trigger writes
  nothing, because recording "too cheap" or "wants a person" as unmet market demand would be a lie.
- **The prospect's words are carried verbatim into the owner's summary.** Clustering them into
  coarse categories is optional enrichment that runs later on a schedule; with no LLM key it returns
  nothing at all and the summary shows the raw text — never a fabricated label, never silently
  dropped. The harness proves that degradation deterministically rather than describing it.

### Auto-provisioning — a subscription becomes a working business, unattended (the provisioning suite)

Listing on a marketplace opens a door strangers walk through on their own schedule. Until this
landed, a subscription produced a notification and a human created the tenant, issued the address
and asked the onboarding questions by hand — the one place a person was still load-bearing in a
product whose entire premise is that nobody's presence is.

- **The tenant row is created first, with an empty profile**, so in-flight onboarding state has an
  RLS-fenced home rather than needing a second isolation mechanism. That window is safe because an
  empty profile is *already* unquotable — the harness fires a real priced enquiry at the half-built
  tenant and proves it escalates without one digit reaching the client.
- **A replayed subscription event is idempotent because of a database constraint**, not because of
  control flow: `tenants.a2a_job_id` is UNIQUE, and the harness forces a duplicate insert to watch
  Postgres refuse it.
- **A malformed answer is refused, never inferred.** The buyer being a machine makes loose parsing
  tempting; a service parsed slightly wrong is a wrong price sent to a real client under the
  tenant's name. Refusing costs one round trip.
- **The finding worth reading twice:** the first real enquiry to the auto-provisioned tenant is
  *held for its owner*, scoring 0.85 against a 0.55 threshold — stopped not by the price but by the
  comprehension floor, because the client wrote "for my cat" and 75% of their words could be
  accounted for. Two defences built for the email path, holding on a channel neither was written
  for. A business set up entirely by machine does not start firing prices at strangers.

## Reproduce every claim

Nothing here asks you to take the repo's word for it. The first four are live systems you can hit
right now; the rest are commands in this repo that print their own evidence.

| Claim | How to check it yourself |
|---|---|
| It is actually running | `curl https://app.quietdesks.com/healthz` |
| A commitment is publicly verifiable | `GET https://app.quietdesks.com/r/{receipt_id}` — the id from any quote CONCIERGE has sent. A receipt that isn't a public commitment, a malformed id and a nonexistent id all render the identical "not found" page |
| The receipt contract is real, on mainnet | `0x9b3C500C59CEC55036e3839091f7C5B2cD9D0587` on [OKLink](https://www.oklink.com/x-layer/evm/address/0x9b3C500C59CEC55036e3839091f7C5B2cD9D0587), chain 196. Deploy tx `0xda15e052…3f95b0`, 224,160 gas; each anchor measured at 51,849 |
| X Layer is chain 196 | `curl -X POST https://rpc.xlayer.tech -d '{"jsonrpc":"2.0","method":"eth_chainId","id":1}' -H 'Content-Type: application/json'` |
| Cal.com slots contract | `curl "https://api.cal.com/v2/slots?eventTypeId=1&start=2026-07-23&end=2026-07-30" -H "cal-api-version: 2024-09-04"` |
| Cal.com bookings contract | `curl -X POST https://api.cal.com/v2/bookings -H "cal-api-version: 2026-02-25" -H "Content-Type: application/json" -d '{}'` — it will tell you its own rules |
| `store.py` has no tenant predicate | `grep -rniE 'where +tenant_id' concierge/store.py` — the only hit is the docstring line saying so. Isolation lives in `concierge/sql/schema.sql`, not here |
| No trade vocabulary in the engine's prose | `python3 verify.py --suite engine` check 3 greps `engine.PROSE` against `engine.TRADE_NOUNS` and fails on a hit |
| No price comes from a language model | `grep -rln "LLM_API_KEY\|anthropic" concierge/*.py` returns exactly one file, `gaps.py`, which runs after the fact and touches no price |
| Everything else | [`docs/VERIFICATION_LEDGER.md`](docs/VERIFICATION_LEDGER.md) — every external fact with its live proof and date, including the places reality differed from the build spec |

## What is missing

[`docs/OPERATOR_PROVIDES.md`](docs/OPERATOR_PROVIDES.md) — 8 items, **7 provided**. What remains:
the OKX Agentic Wallet, which with ledger U3 is what still blocks A2A escrow, and an optional
web-search key that onboarding says out loud it doesn't have. Each item lists exactly which
capability it blocks. Absent credentials are reported as absent; none are stubbed.

## Disclosure

Every outbound message discloses that it is an AI agent, in the first line, unskippably. See
ledger §6 for the statute that actually governs this (California's B.O.T. Act, §17941 — not SB 243,
which most likely exempts transactional bots like this one).

## License

MIT — see [LICENSE](LICENSE). Credits in [CREDITS.md](CREDITS.md).
