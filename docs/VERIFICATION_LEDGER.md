# VERIFICATION LEDGER

Every external fact CONCIERGE depends on. No fact enters this file without a live call or a live
document fetch, dated. "The spec said so" is not verification. Where reality differed from the
build spec, the difference is recorded in **CORRECTION** rows — those are the ones that would have
caused a production failure.

Verification date for all rows below: **2026-07-22**. Re-verify at each gate that consumes the fact.

---

## 1. Cal.com API v2 — bookings

| Field | Value |
|---|---|
| Needed for | Phase 5 (§9b booking) |
| Verified where | Live `POST https://api.cal.com/v2/bookings` + docs `cal.com/docs/api-reference/v2/bookings/create-a-booking` |
| Date | 2026-07-22 |

**Found (live):**
```
$ curl -X POST https://api.cal.com/v2/bookings -H "cal-api-version: 2026-02-25" \
       -H "Content-Type: application/json" -d '{}'
{"status":"error","path":"/v2/bookings","error":{"code":"BadRequestException",
 "message":"start property is wrong,start must be a valid ISO 8601 date string ,
  attendee property is wrong,attendee should not be null or undefined ,
  eventTypeId or eventTypeSlug + username property is wrong,..."}}
```

- `cal-api-version: 2026-02-25` is the **current** bookings version. Docs: *"Must be set to 2026-02-25."*
- `start` — ISO 8601 **in UTC** (`2026-08-13T09:00:00Z`). Confirmed by the live validator.
- `attendee` — **nested object**, required. `name` + `timeZone` required; `email`, `phoneNumber`,
  `language` optional. Confirmed: server rejects a payload lacking `attendee`.
- Event target: `eventTypeId` **or** `eventTypeSlug`+`username` **or** `eventTypeSlug`+`teamSlug`.

> **CORRECTION TO BUILD SPEC (§0, §9b).** The spec proposed pinning `2024-08-13` for bookings.
> That is stale. Live proof that the pin matters — same empty body, different version header:
> ```
> cal-api-version: 2026-02-25 → "start must be a valid ISO 8601 date string"
> cal-api-version: 1999-01-01 → "start must be a string"   ← silently fell back to an OLD schema
> ```
> An unrecognised/stale version does **not** error. It downgrades you to a different contract with
> different validation. Building against `2024-08-13` would have produced bookings validated by the
> wrong schema. **We pin `2026-02-25` and assert the pin in the harness.**

---

## 2. Cal.com API v2 — slots

| Field | Value |
|---|---|
| Needed for | Phase 5 (slot offer + slot-race re-fetch) |
| Verified where | Live `GET https://api.cal.com/v2/slots` |
| Date | 2026-07-22 |

**Found (live):**
```
$ curl "https://api.cal.com/v2/slots?eventTypeId=1&start=2026-07-23&end=2026-07-30" \
       -H "cal-api-version: 2024-09-04"
HTTP 200
{"data":{"2026-07-27":[{"start":"2026-07-27T14:15:00.000Z"},{"start":"2026-07-27T14:30:00.000Z"},...],
         "2026-07-28":[{"start":"2026-07-28T14:00:00.000Z"},...]}}
```

- `cal-api-version: 2024-09-04` — **still current for slots**, confirmed by a real 200 with real data.
  Note the versions differ per endpoint: bookings `2026-02-25`, slots `2024-09-04`. Not a typo.
- Response shape: `data` → map keyed by **local date string** → array of `{start}` in **UTC with `Z`**.
- Public event types are readable **without auth** (this probe used no API key). Tenant event types
  will need `Authorization: Bearer cal_...`.
- Rate limit — **UNVERIFIED**. The spec claims 60/min/key. Not observed and not found in a doc.
  Treated as an assumption: the slot client is written with a conservative limiter and backoff, and
  the number is flagged rather than trusted. Re-verify at GATE 5 against response headers.

---

## 3. X Layer chain

| Field | Value |
|---|---|
| Needed for | Phase 6 (receipt anchoring), Phase 7 (settlement gas) |
| Verified where | Live JSON-RPC |
| Date | 2026-07-22 |

**Found (live):**
```
$ curl -X POST https://rpc.xlayer.tech -d '{"jsonrpc":"2.0","method":"eth_chainId","id":1}'
{"jsonrpc":"2.0","result":"0xc4","id":1}          → 196  ✅ matches spec
$ ... eth_blockNumber
{"jsonrpc":"2.0","result":"0x3eebe1e","id":1}     → 65,850,398 — chain is live and producing
$ curl -X POST https://testrpc.xlayer.tech ... eth_chainId
{"jsonrpc":"2.0","result":"0x7a0","id":1}         → 1952 (X Layer testnet)
```

- Mainnet chain ID **196**, RPC `https://rpc.xlayer.tech` — confirmed live, not from a doc.
- Testnet chain ID **1952**, RPC `https://testrpc.xlayer.tech` — confirmed live. We develop
  receipt anchoring against 1952 and cut over to 196 only when the operator funds OKB.
- Native gas token **OKB** — per OKX X Layer docs (fixed supply 21M post-burn). Doc-level only.

---

## 4. OKX A2A / OnchainOS skills

| Field | Value |
|---|---|
| Needed for | Phase 7 (engagement, escrow, settlement, progress monitor) |
| Verified where | `github.com/okx/onchainos-skills` (repo + CLAUDE.md), OKX APP whitepaper v1.0 |
| Date | 2026-07-22 |

**Found:**
- Install: `npx skills add okx/onchainos-skills` — matches spec.
- 8 skills ship. The two that matter to us:
  - **`okx-ai`** — *"ERC-8004 on-chain Agent identity (register/update/search/rate/service-list) +
    agent task marketplace (publish/accept/deliver/dispute) + live task-progress monitor."*
    Confirms every §7 lifecycle verb we need.
  - **`okx-agent-payments-protocol`** — unified dispatcher over `x402`, `MPP`, and **`a2a-pay`
    (paymentId-based create / pay / status)**. `a2a-pay` is our escrow path.
- Agent roles are first-class: ASP/Provider/Seller vs User/Buyer vs Evaluator/arbitrator.
  CONCIERGE registers as **ASP**.
- Progress monitor activation: `task watch` / `monitor task progress` / `outstanding decisions`.
  Documented platform note: *"Claude Code / Codex only for the monitor half (`CLAUDECODE=1` or
  `CODEX_THREAD_ID`)"* — **material for §12**: the monitor is not a plain daemon we can systemd on
  the VPS the way the spec assumes. Resolve at GATE 7 before promising "never offline."
- Credentials: `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE`.
- **UNVERIFIED:** exact escrow call signatures, USDT/USDG settlement currencies, dispute mechanics.
  The repo directs you to read each skill's `SKILL.md` before running any CLI command, and okx.ai
  returned **HTTP 403** to our fetch. These get verified by installing the skill with real
  credentials — blocked on operator item #5. **Nothing in Phase 7 will be built on assumption.**

---

## 5. Postmark inbound + outbound

| Field | Value |
|---|---|
| Needed for | Phase 4 (email connector) |
| Verified where | `postmarkapp.com/developer/user-guide/inbound/parse-an-email` |
| Date | 2026-07-22 |

**Found:**
- Inbound: Postmark receives at a per-server hash address, POSTs parsed JSON to our webhook.
  Custom domain works by MX/forwarding onto that hash — supports our `inbox.<domain>` design.
- Attachment ceiling: *"Total cumulative size for all Inbound attachment files may not exceed 35 MB."*
- Retries: *"A total of 10 retries will be made, with growing intervals from 1 minute to 6 hours"*;
  *"If we receive a 403 response, we will stop retries"*; 2-minute response timeout.

> **CORRECTIONS TO BUILD SPEC (§0).**
> 1. Size limit is **35 MB**, not "<30MB".
> 2. Retry policy is **10 retries over ~6h growing intervals**, not "3-day retry".
> 3. **A 403 from our webhook permanently kills the retry.** Direct consequence for our code: the
>    inbound endpoint must **never** answer 403 for a transient problem — a failed tenant lookup or a
>    DB blip must return 5xx so Postmark retries. Answering 403 on a bad signature silently discards
>    the email forever. This is a real data-loss bug the spec would have walked us into.
> 4. The spec says "verify signature." The inbound parse doc documents **no HMAC signature header.**
>    Postmark's actual mechanism is HTTP Basic auth embedded in the webhook URL plus IP allowlisting.
>    **UNVERIFIED / must confirm at GATE 4** before writing any signature-checking code. Do not
>    implement a signature check against a header that may not exist.
- Outbound SPF/DKIM/DMARC/Return-Path: standard Postmark sending-domain setup. Verified at GATE 4
  against live DNS, not before.

---

## 6. Legal — AI disclosure

| Field | Value |
|---|---|
| Needed for | Every outbound message, all phases |
| Verified where | SB 243 analyses (Skadden, Gunderson, Troutman, FPF); CA B&P Code §17941 (SB 1001) |
| Date | 2026-07-22 |

**Found:**
- **SB 243** took effect **2026-01-01** (signed 2025-10-13). It regulates **companion chatbots** —
  systems providing "adaptive, human-like social interactions." Private right of action, greater of
  actual damages or **$1,000/violation** + fees. Annual reporting from 2027-07-01.
- **SB 243 carves out transactional/utility bots**: those used *solely for customer service, business
  operations, productivity, or technical support* are **exempt**.

> **CORRECTION TO BUILD SPEC (§0).** The spec frames CONCIERGE as under a "lighter regime" of SB 243.
> On the published analyses, CONCIERGE — a sales/booking bot that never sustains a social
> relationship — most likely falls in the **exemption**, so SB 243 is probably not our operative
> statute at all. **The statute that does squarely apply is California's B.O.T. Act, SB 1001 /
> B&P Code §17941** (operative 2019-07-01): unlawful to use a bot to communicate with a person in
> California, misleading them about its artificial identity, *"in order to incentivize a purchase or
> sale of goods or services in a commercial transaction."* That is a literal description of
> CONCIERGE. Safe harbour: *"a person using a bot shall not be liable ... if the person discloses
> that it is a bot,"* disclosure being *"clear, conspicuous, and reasonably designed to inform."*
>
> **Net effect on the build: none — disclosure in the first message stays non-negotiable.** We were
> going to do the right thing for a partly wrong reason; now the compliance note cites the statute
> that actually governs us, and the "lighter regime" framing is not used to justify anything softer.
> Not legal advice — the operator should have counsel confirm before launch. Design so disclosure
> is enforced in code (unskippable), which satisfies both statutes regardless of which applies.

---

## 7. Hackathon logistics — ⚠️ TIME-CRITICAL

| Field | Value |
|---|---|
| Verified where | hackquest.io/hackathons/OKXAI-Genesis-Hackathon |
| Date | 2026-07-22 |

- Registration + submission window: **2026-07-02 11:00 UTC → 2026-07-27 22:59 UTC.**
  No extension mentioned. **Today is 2026-07-22 — ~5 days remain.**
- Prize announcement 2026-08-03 23:00 UTC. Pool $100,000, max individual 10,000 USDT.

> **CORRECTION TO BUILD SPEC (title line).** There is **no "Business Potential" track.** The eight
> listed categories are: Best Product ($20K), Creative Genius ($20K), Revenue Rocket ($20K),
> Finance Copilot ($7.5K), Software Utility ($7.5K), Lifestyle Companion ($7.5K), Artistic
> Excellence ($7.5K), Social Buzz ($10K × 10 winners). CONCIERGE targets **Revenue Rocket** and
> **Best Product**, with **Lifestyle Companion** reachable via the spa/real-estate verticals.
> Also note the spec's "$1M revenue (OKX OPC)" framing is not a hackathon prize — max individual
> prize is 10,000 USDT. Flagged so the operator is not optimising for a track that does not exist.

---

## OPEN / UNVERIFIED — nothing here may be built on until proven

| # | Fact | Blocks | Resolve at |
|---|---|---|---|
| U1 | Cal.com slots rate limit (spec claims 60/min/key) | slot-client tuning | GATE 5 |
| U2 | Postmark inbound auth mechanism (signature vs Basic vs IP) | inbound endpoint security | GATE 4 |
| U3 | OKX a2a-pay escrow call signatures + settlement currencies | all of Phase 7 | GATE 7 |
| U4 | Whether the progress monitor can run headless on a VPS | "never offline" claim | GATE 7 |
| U5 | okx.ai ASP listing steps (site returned 403 to fetch) | submission | GATE 7 |
| U6 | Live DNS/SPF/DKIM/DMARC for the operator's domain | outbound deliverability | GATE 4 |
