# OPERATOR PROVIDES

What CONCIERGE needs from you, what each thing unblocks, and what stays broken until it arrives.
Nothing here is faked, stubbed, or simulated. A missing credential is reported as missing.

**Status as of 2026-07-25: 7 of 8 provided. Only item 5 (OKX wallet) remains; item 8 optional.**
- Item 1 (VPS): **PROVIDED + DEPLOYED** — `38.49.216.59`; CONCIERGE live at `https://app.quietdesks.com`.
- Item 2 (domain+DNS): **PROVIDED** — `quietdesks.com` at Cloudflare; all Phase 4 DNS records set.
- Item 3 (Postmark): **PROVIDED + LIVE** — approved, token in VPS `.env`, inbound round-trip proven.
- Item 4 (Cal.com): **PROVIDED** — key `cal_live_…` + event type `6433300`. the booking suite passed live.
  **Key was exposed in chat — rotate before submission.**
- Item 6 (X Layer signer): **PROVIDED** — the receipts suite live.
- Item 7 (LLM key): **PROVIDED (fixed 2026-07-25)** — the earlier value was truncated and 401'd; the
  full `sk-ant-…` now works; Feature 1 categorization is live.
- Item 5 (OKX Agentic Wallet): **MISSING** — blocks A2A escrow + ledger U3. Item 8 optional/skipped.

**Deadline 2026-07-27 22:59 UTC.**

Phases 0–6 and 8 are done; **Phase 4 is LIVE** (real email round-trip proven 2026-07-25). Feature 1
is done. **Only A2A escrow remains blocked** — on item 5 and ledger U3 (the OKX escrow API shape).

| # | Item | Status | Blocks | Notes |
|---|---|---|---|---|
| 1 | VPS — 2 vCPU / 4GB / 40GB, Ubuntu 24.04, 24/7 | ✅ PROVIDED + DEPLOYED | ~~P4~~, P7, P8 | `38.49.216.59` (shared `Jennycruzy` box). CONCIERGE deployed 2026-07-24 under a dedicated `concierge` user, own Postgres container on `127.0.0.1:5433`, systemd + nginx + TLS. Live at `https://app.quietdesks.com`. **Hardening TODO:** rotate the leaked root password, move to SSH-key auth. |
| 2 | Domain + DNS access | ✅ PROVIDED | ~~P4~~ | `quietdesks.com` at Cloudflare. All Phase 4 records set: A `app` → VPS (grey), MX `inbox` → inbound.postmarkapp.com, DKIM TXT + Return-Path CNAME (both verified). DMARC still optional. |
| 3 | Postmark server API token (inbound + outbound) | ✅ PROVIDED + LIVE | ~~P4~~ | Approved 2026-07-24 (ticket `[NVXMEE-2Z5W7]`). Token in VPS `.env`; server "Concierge" configured with `InboundHookUrl` + `InboundDomain=inbox.quietdesks.com`; sending domain DKIM + Return-Path verified. **Live round-trip proven 2026-07-25** (inbound email → webhook → tenant → reply Sent, not spam). Default **Transactional** stream. |
| 4 | Cal.com account + API key (`cal_...`) + event type ID | ✅ PROVIDED | P5 | In `.env`. **the booking suite passed live 2026-07-23** — real booking created and cancelled against event type 6433300. Key is `cal_live_` — rotate before submission, it was exposed in chat. |
| 5 | OKX Agentic Wallet + creation email; `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | ❌ MISSING | P7 | Also blocks verifying the escrow API shape at all (ledger U3). |
| 6 | Funded OKB on X Layer **mainnet (196)** | ✅ PROVIDED | ~~P6~~, P7 | In `.env` as `XLAYER_PRIVATE_KEY`. **the receipts suite passed live 2026-07-24** — `ReceiptAnchor` deployed at `0x9b3C500C59CEC55036e3839091f7C5B2cD9D0587`, two real receipts anchored, ~0.0103 OKB left (~9,900 more anchors at the measured 51,849 gas/anchor). |
| 7 | LLM API key | ✅ PROVIDED (fixed 2026-07-25) | P2, P3 drafting; Feature 1 categorization | Earlier value was **truncated** (missing the last chars → 401); the full `sk-ant-…` now works — verified with a live `gaps.classify_gap` call returning `service_not_offered`. the onboarding suite/the engine suite are deterministic and need no key; the one consumer is **Feature 1's optional gap categorization** (`concierge/gaps.py`), which is now live (and still degrades honestly to raw text if the key is ever absent). |
| 8 | Web-search / retrieval API key | ❌ MISSING | P2 enrichment | **Optional.** Without it, onboarding uses built-in vertical templates and says so out loud. |

---

## What I can build while these are missing

Real work, no fabrication, no credentials needed:

- ~~**Phase 1** — tenant model + isolation, on local Postgres. Fully provable.~~ **DONE, the isolation suite
  passed 2026-07-23.** 11 checks, 9 of them attacks. `python3 verify.py --suite isolation`.
- ~~**Phase 2** — vertical-aware onboarding.~~ **DONE, the onboarding suite passed 2026-07-23.** Classification
  turned out not to need item 7 at all: it is a weighted lexicon that reports the exact terms
  behind each decision, works with no key, and abstains rather than guessing when two verticals
  score too close. An LLM may still be added later to catch descriptions the lexicon misses.
  One consequence you should know about: **onboarding returns an inbound address on
  `PENDING-DOMAIN.invalid`** until item 2 arrives. The local part is real and reserved; the domain
  half is a TLD reserved by RFC 2606 so it can never resolve. Nobody can email a tenant until you
  provide the domain.
- **Phase 3** — the state machine and deterministic guardrails, driven by fixture inquiries. This is
  the **decisive** gate (every price provably from the profile) and it needs **nothing from you**.
- ~~**Phase 6** — the receipt contract, signing, hashing and tamper detection.~~ **DONE, the receipts suite
  passed live 2026-07-24**, once item 6 arrived. `ReceiptAnchor` deployed at
  `0x9b3C500C59CEC55036e3839091f7C5B2cD9D0587`; two real receipts anchored and independently
  re-verified on-chain; two tamper attacks caught. `python3 verify.py --suite receipts`.
- ~~**Phase 5** — booking against live Cal.com.~~ **DONE, the booking suite passed live 2026-07-23**, once
  item 4 arrived.

So: Phases 1, 2, 3, 5, 6 are done. **4 and 7 remain blocked** — 4 on items 1–3, 7 on item 5 and
ledger U3.

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

# HOW TO GET EACH KEY

Signup paths verified live 2026-07-23. Where a menu path was ambiguous in the vendor's own docs,
both possibilities are given rather than one guess. Nothing below is from memory.

## ⚠️ Read this first: Postmark is the long pole, and the weekend is in the way

**Postmark manually reviews every new account before it will send to addresses outside domains you
own.** Their stated turnaround is *"less than 24 hours on weekdays and a little longer on the
weekends."*

It is **Thursday 2026-07-23, 01:07 UTC**. The deadline is **Monday 2026-07-27, 22:59 UTC.**

- Sign up **today (Thursday)** → approval lands Friday, with the whole weekend to build. Fine.
- Sign up **Friday evening** → the review falls across the weekend, on their slower path. That is
  a coin flip against the deadline.
- Sign up **Saturday** → you may simply not be approved in time.

So: **step 1 and step 2 below, today.** Everything else can wait a day without hurting.

Useful mitigation if approval is slow: while pending, you *can* already set up inbound processing,
use the API, and send to any domain you've added and verified. So Phase 4 can be built and even
demoed end-to-end, provided the test "prospect" address is on your own domain. It is only sending
to a stranger's inbox that waits for approval.

---

## 1. Domain — do this first, ~10 minutes, ~$10/year

Everything email depends on this, so it blocks step 2.

1. Buy a domain at [Namecheap](https://www.namecheap.com), [Porkbun](https://porkbun.com) or
   [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/). Any is fine. Short and
   plausible-looking helps deliverability — `getconcierge.io`, not `xk3-test-99.xyz`.
2. **Use the registrar's own DNS.** Don't move nameservers to a third party; it's one more thing to
   go wrong and buys you nothing here. You need a control panel where you can add **MX**, **TXT**
   and **CNAME** records — all three registrars have one.
3. Send me the domain name. I'll tell you the exact records to paste; Postmark generates the real
   values and I will not invent them.

→ `CONCIERGE_DOMAIN` in `.env`.

## 2. Postmark — do this today, free tier, ~15 minutes

1. Sign up at [postmarkapp.com](https://postmarkapp.com). Free tier is 100 emails/month, which is
   ample for a demo.
2. **Immediately request account approval** (Account Owner only). It asks how many emails/month and
   what for. Answer honestly: *transactional replies to inbound business inquiries, low volume,
   under 100/month.* Postmark's whole business is transactional email — this is exactly their use
   case and it approves cleanly. **This starts the clock, so do it before anything else.**
3. Add your domain under **Sender Signatures / Domains** and verify it. Postmark shows you the
   exact DKIM `TXT` and Return-Path `CNAME` values — send me a screenshot or paste them, and I'll
   tell you where each goes.
4. **Inbound:** create a Server, open its **Inbound** stream, and set the Inbound Domain to
   `inbox.yourdomain.com`. Then add this DNS record at your registrar:

   | Type | Name | Value | Priority |
   |---|---|---|---|
   | MX | `inbox` | `inbound.postmarkapp.com` | 10 |

   Use the `inbox` subdomain, not the root — Postmark recommends it, and it keeps inbound parsing
   from interfering with normal mail on your domain.
5. **Server API Token:** Servers → your server → **API Tokens** tab. One token covers both inbound
   and outbound.

→ `POSTMARK_SERVER_TOKEN` in `.env`. The webhook URL needs the VPS (step 3), so we set that last.

## 3. VPS — ~10 minutes, $5–24/month

Spec minimum is 2 vCPU / 4 GB / 40 GB, Ubuntu 24.04.

- **[DigitalOcean](https://www.digitalocean.com)** — $24/mo for 2 vCPU / 4 GB. Instant with a card.
- **[Vultr](https://www.vultr.com)** — similar, also instant.
- **[Hetzner](https://www.hetzner.com/cloud)** CX22 — ~€4.50/mo for the same specs, by far the best
  value, **but new accounts are sometimes held for ID verification.** With four days left, that
  risk may not be worth €20. Your call.

Create the droplet/instance with **Ubuntu 24.04**, add an SSH key if the panel offers it, and send
me the **IP address** plus how to log in. Then point a DNS `A` record — `app.yourdomain.com` → that
IP — so Postmark's webhook has a stable HTTPS hostname to POST to.

→ `VPS_HOST` in `.env`.

## 4. Cal.com — free, ~5 minutes

1. Sign up at [cal.com](https://cal.com).
2. Create an **Event Type** — e.g. "Discovery call, 30 min". Open it; its numeric ID is in the
   browser URL (`.../event-types/<number>`). I need that number.
3. API key: go to **[app.cal.com/settings/developer/api-keys](https://app.cal.com/settings/developer/api-keys)**.
   Cal.com's own docs currently say *Settings → Security*; the developer path above is the one that
   has worked. Try the direct link first, and if it 404s, look under Settings → Security.
   Keys start with `cal_` (test) or `cal_live_` (live).
4. Set your **timezone** correctly in Cal.com settings. Phase 5 asks the prospect their timezone
   explicitly and never infers it, but your own availability windows are read from this setting.

→ `CAL_API_KEY` + the event type ID in `.env`.

## 5. LLM API key — ~5 minutes, $5 minimum

[console.anthropic.com](https://console.anthropic.com) → **API Keys** → Create Key. You must load
credit first (Billing, $5 minimum); a key without credit fails on the first call.

$5 is far more than this project will consume — the LLM only reads inquiries, drafts prose, and
classifies verticals. **It never produces a price** (§2), so its usage is small and its blast radius
is bounded by design.

→ `LLM_API_KEY` in `.env`.

## 6. OKX Agentic Wallet — ✅ PROVIDED 2026-07-25

Created via browser social login as `melindacharles82@gmail.com`; EVM address
`0x45818399a3e0f756cb26ff2fcd13a4824313df94`. ASP actions are gas-free (platform
paymaster), so this wallet needs no pre-funded USDT and no native balance — it is the
payee. See docs/HANDOFF.md for the listing (#9274).

### Original instructions

1. From this repo, run: `npx skills add okx/onchainos-skills` (needs Node.js 18+).
2. Log in with **email**, Google or Apple. The wallet is created automatically on first login —
   **no seed phrase to write down.** Keys live in a TEE; the agent can transact but cannot extract
   them.
3. It produces EVM and Solana addresses. Send me the **EVM** address.

⚠️ **Honest gap:** the OnchainOS docs describe the wallet install clearly but do **not** document
where the API key / secret / passphrase for the escrow and settlement calls come from — they likely
come from the OKX developer portal, but I have not confirmed that live. This is ledger item **U3**
and it is unresolved. I'll pin it down at the escrow suite and will not write escrow code against a guessed
API shape.

→ `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` in `.env` — once U3 is resolved.

## 7. OKB for gas — MAINNET, chain 196. Buy ~1 OKB (~$82)

**✅ PROVIDED 2026-07-24. the receipts suite passed live — see docs/VERIFICATION_LEDGER.md's Phase 6 entry
for the real deploy tx, contract address, and measured gas (cheaper than the estimate below).**

**This is a mainnet product. Receipts anchor on X Layer mainnet (196) from Phase 6 onward.**

A receipt anchored on a testnet proves nothing — not to a customer disputing a quote, not to an
arbitrator ruling on an escrow, not to a judge assessing the submission. The receipt *is* the trust
claim. Anchoring it somewhere with no economic weight would make the central claim of this product
theatre, and that is worse than not making the claim at all.

Cost is not a reason to do otherwise. Measured live on chain 196 at block 66,000,249:

| | Gas | OKB | USD @ $82.42 |
|---|---|---|---|
| Anchor one receipt | 55,000 | 0.0000011 | **$0.0001** |
| Anchor a batch of 50 | 90,000 | 0.0000018 | $0.00015 |
| Deploy the anchoring contract (once) | 900,000 | 0.000018 | $0.0015 |
| **1 OKB buys** | | | **~909,000 receipt anchors** |

X Layer's gas price is 0.02 gwei. At a receipt per inquiry, **1 OKB covers more inbound than this
product will handle in years.** Testnet would have saved roughly a dollar and cost the entire
credibility of the proof.

**How to get it:** buy OKB on OKX, then withdraw choosing the **X Layer** network (not ERC-20, not
BSC). ~1 OKB is plenty; 2 if you want headroom.

⚠️ Withdrawing on the wrong network destroys the funds. Send me the receiving address and let me
confirm the chain before you press send — this is the one step in the whole list that is not
reversible.

→ `XLAYER_PRIVATE_KEY` and `XLAYER_RPC=https://rpc.xlayer.tech`, `XLAYER_CHAIN_ID=196` in `.env`.

**On the signer key:** this key spends real funds and signs the receipts the product's credibility
rests on. It should be a fresh wallet holding only gas — never a key that also holds meaningful
assets. Blast radius of a VPS compromise should be ~1 OKB, not your treasury.

## 8. Web-search key — skip it

Genuinely optional. Without it, onboarding uses the built-in vertical templates and **says so out
loud** rather than pretending to have looked anything up. Not worth a signup with four days left.

---

## Do-this-now order

| When | Item | Why this slot |
|---|---|---|
| **Today** | 1. Domain | Blocks Postmark |
| **Today** | 2. Postmark + **request approval** | Manual review; the weekend is the risk |
| Today/Fri | 3. VPS | Needed before the inbound webhook has anywhere to point |
| Friday | 4. Cal.com, 5. LLM key | Quick, no review queue |
| Friday | 6. OKX wallet | Free, no waiting |
| Friday | 7. **Buy ~1 OKB, withdraw over X Layer** | Blocks Phase 6 — mainnet anchoring |
| — | 8. Search key | Skip |

Total to get everything moving: **~$100 up front** (domain $10 + LLM credit $5 + ~1 OKB at $82)
plus the VPS monthly. The OKB is a working balance, not a fee — it is spent a hundredth of a cent
at a time and 1 OKB covers ~909,000 receipts.

---

## How to hand credentials over

Copy `.env.example` to `.env` and fill what you have. `.env` is gitignored and never logged.
Partial is fine — send what exists, I'll build around the gaps and keep flagging them. I will not
invent a placeholder key to make a test go green.

**Never paste a key into the chat.** Put it in `.env` on the machine. If you have already pasted one
somewhere it shouldn't be, say so and rotate it — every service above can regenerate a key in one
click, and it is much cheaper to rotate now than to explain a leak later.

---

### Sources for the above (all fetched 2026-07-23)

- [Postmark — how does the account approval process work?](https://postmarkapp.com/support/article/1084-how-does-the-account-approval-process-work)
- [Postmark — inbound domain forwarding](https://postmarkapp.com/developer/user-guide/inbound/inbound-domain-forwarding)
- [Postmark — configure an inbound server](https://postmarkapp.com/developer/user-guide/inbound/configure-an-inbound-server)
- [Cal.com — API v2 introduction](https://cal.com/docs/api-reference/v2/introduction)
- [OKX — install your Agentic Wallet](https://web3.okx.com/onchainos/dev-docs/home/install-your-agentic-wallet)
- [OKX — onchainos-skills repo](https://github.com/okx/onchainos-skills)
- [X Layer — get testnet OKB from faucet](https://web3.okx.com/xlayer/docs/developer/bridge/get-testnet-okb-from-faucet)
