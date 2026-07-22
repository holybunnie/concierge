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

**Phase 0 complete — GATE 0 passed.** Foundations and external verification only. No engine yet.

```
python3 verify.py --phase 0
```

Makes real network calls to Cal.com and X Layer and prints the raw evidence behind every claim.
There are no mocks and no fixtures in it; if the network is down it reports FAIL rather than
falling back to a cached answer.

Phases 1–9 are not started. Nothing in this repo simulates a phase it has not built.

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
