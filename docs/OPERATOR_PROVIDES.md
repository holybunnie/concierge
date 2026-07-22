# OPERATOR PROVIDES

What CONCIERGE needs from you, what each thing unblocks, and what stays broken until it arrives.
Nothing here is faked, stubbed, or simulated. A missing credential is reported as missing.

**Status as of 2026-07-22: 0 of 8 provided.** Everything below is MISSING. Phase 0 (foundations +
verification) completed without any of them because it only needed public endpoints. **Phase 4 and
everything after it is blocked.**

| # | Item | Status | Blocks | Notes |
|---|---|---|---|---|
| 1 | VPS — 2 vCPU / 4GB / 40GB, Ubuntu 24.04, 24/7 | ❌ MISSING | P4, P7, P8 | Inbound webhook must be publicly reachable and always up. Workers run follow-ups + progress monitor. |
| 2 | Domain + DNS access | ❌ MISSING | P4 | Needs MX on `inbox.<domain>`, plus SPF/DKIM/DMARC on the sending domain. |
| 3 | Postmark server API token (inbound + outbound) | ❌ MISSING | P4 | SendGrid Inbound Parse is a documented alternative — **say the word and I'll switch.** |
| 4 | Cal.com account + API key (`cal_...`) + event type ID | ❌ MISSING | P5 | Public slot reads work without it (proven); real bookings do not. Calendly is an alternative — **ask me.** |
| 5 | OKX Agentic Wallet + creation email; `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | ❌ MISSING | P7 | Also blocks verifying the escrow API shape at all (ledger U3). |
| 6 | Funded OKB on X Layer (chain 196) | ❌ MISSING | P6 mainnet, P7 | P6 will be built and proven on **testnet 1952** first, which needs no real funds. |
| 7 | LLM API key | ❌ MISSING | P2, P3 drafting | Understanding / drafting / vertical classification **only**. Never a price (§2). |
| 8 | Web-search / retrieval API key | ❌ MISSING | P2 enrichment | **Optional.** Without it, onboarding uses built-in vertical templates and says so out loud. |

---

## What I can build while these are missing

Real work, no fabrication, no credentials needed:

- **Phase 1** — tenant model + isolation, on local Postgres. Fully provable.
- **Phase 2** — vertical-aware onboarding. Template/gap/read-back logic is deterministic code and is
  testable without an LLM; only the free-text classification step needs item 7.
- **Phase 3** — the state machine and deterministic guardrails, driven by fixture inquiries. This is
  the **decisive** gate (every price provably from the profile) and it needs **nothing from you**.
- **Phase 6** — receipt signing + anchoring against X Layer **testnet 1952**, which is live and free.

So: Phases 1, 2, 3, 6 can proceed now. **4, 5, 7, 8 cannot start.**

---

## Two findings you need to decide on before I keep going

**1. The deadline is 2026-07-27 22:59 UTC. That is ~5 days away.**
Verified live on HackQuest today; no extension is listed. The 10-phase plan as written does not fit
in 5 days, and several phases are gated on credentials I don't have yet. This is a scope call and
it is yours to make, not mine — but the fastest credible path to a submittable, *honest* demo is:

> P1 + P3 + P6-on-testnet (all unblocked, all provable today) → then P4/P5 the moment items 1–4
> land → P7 last, since it's the most externally dependent.

Every hour items 1–4 are missing is an hour Phase 4/5 cannot start, and those are the phases the
90-second demo actually shows.

**2. There is no "Business Potential" track.**
The spec targets one. The real categories are Best Product, Creative Genius, Revenue Rocket,
Finance Copilot, Software Utility, Lifestyle Companion, Artistic Excellence, Social Buzz. I've
retargeted the README at **Revenue Rocket + Best Product**. Also: max individual prize is 10,000
USDT — the "$1M revenue" framing in the spec isn't a prize, so if that number was driving decisions
it should be re-examined. Say if you want a different track and I'll re-aim the positioning.

---

## How to hand credentials over

Copy `.env.example` to `.env` and fill what you have. `.env` is gitignored and never logged.
Partial is fine — send what exists, I'll build around the gaps and keep flagging them. I will not
invent a placeholder key to make a test go green.
