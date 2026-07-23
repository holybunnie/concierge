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

## Status

**Phases 0–2 complete. GATE 0, 1 and 2 passed.** Foundations, external verification, the tenant
model with structural isolation, and vertical-aware onboarding. The deal engine itself (Phase 3)
is next.

```bash
pip install -r requirements.txt
docker compose up -d postgres        # GATE 1 needs a real Postgres; SQLite cannot do this

python3 verify.py --phase 0          # live calls to Cal.com and X Layer, raw evidence printed
python3 verify.py --phase 1          # 11 checks, 9 of which are attacks on tenant isolation
python3 verify.py --phase 2          # 11 checks on real estate / barrister / spa onboarding
```

Neither harness contains a mock or a fixture response. Phase 0 makes real network calls and
reports FAIL if the network is down rather than falling back to a cached answer. Phase 1 runs
real SQL against a real PostgreSQL 16 server as the real unprivileged application role.

Phases 2–9 are not started. Nothing in this repo simulates a phase it has not built.

### How isolation is actually enforced (GATE 1)

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

### Onboarding, and the price that must never be invented (GATE 2)

Onboarding classifies the tenant's vertical and briefs them the way you'd brief a new sales hire:
the right questions for their trade, a worked example beside each one, and every gap named with
what it will cost them ("no cancellation policy — clients will ask, and I'll escalate every one").

The failure this phase exists to prevent is a business quoting £85 because the template's
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
That is partly auditability and partly that the LLM key hasn't arrived, and a classifier that
needs a credential we don't have is a classifier that doesn't exist.

## Reproduce every claim

| Claim | How to check it yourself |
|---|---|
| Cal.com slots contract | `curl "https://api.cal.com/v2/slots?eventTypeId=1&start=2026-07-23&end=2026-07-30" -H "cal-api-version: 2024-09-04"` |
| Cal.com bookings contract | `curl -X POST https://api.cal.com/v2/bookings -H "cal-api-version: 2026-02-25" -H "Content-Type: application/json" -d '{}'` — it will tell you its own rules |
| X Layer is chain 196 | `curl -X POST https://rpc.xlayer.tech -d '{"jsonrpc":"2.0","method":"eth_chainId","id":1}' -H 'Content-Type: application/json'` |
| Everything else | [`docs/VERIFICATION_LEDGER.md`](docs/VERIFICATION_LEDGER.md) — every external fact with its live proof and date, including the four places reality differed from the build spec |

## What is missing

[`docs/OPERATOR_PROVIDES.md`](docs/OPERATOR_PROVIDES.md) — 8 items, currently 0 provided. Each one
lists exactly which phase it blocks. Absent credentials are reported as absent; none are stubbed.

## Disclosure

Every outbound message discloses that it is an AI agent, in the first line, unskippably. See
ledger §6 for the statute that actually governs this (California's B.O.T. Act, §17941 — not SB 243,
which most likely exempts transactional bots like this one).

## License

MIT — see [LICENSE](LICENSE). Credits in [CREDITS.md](CREDITS.md).
