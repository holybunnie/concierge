# HANDOFF

Resume point for a session starting cold. **Current as of 2026-07-24.**

**Deadline: 2026-07-27 22:59 UTC — 4 days 20 hours remain.**

---

## Start here after a break

```bash
cd /workspaces/concierge
docker compose up -d postgres      # the container does not survive a codespace rebuild
pip install -r requirements.txt    # if the container was rebuilt
python3 verify.py --phase 0        # expect 9 pass / 0 fail / 2 info
python3 verify.py --phase 1        # expect 11 pass / 0 fail
python3 verify.py --phase 2        # expect 11 pass / 0 fail / 1 info
python3 verify.py --phase 3        # expect 16 pass / 0 fail / 3 info
python3 verify.py --phase 3b-2     # expect 7 pass / 0 fail / 2 info — Feature 2, confidence-scored autonomy
python3 verify.py --phase 3b-3     # expect 4 pass / 0 fail / 1 info — Feature 5, the decaying floor
python3 verify.py --phase 3b-4     # expect 3 pass / 0 fail / 1 info — Safe Follow-Up
python3 verify.py --phase 4        # expect 8 pass / 0 fail / 3 info
python3 verify.py --phase 5        # expect 5 pass / 0 fail / 2 info — makes+cancels a real booking
python3 verify.py --phase 6        # expect 8 pass / 0 fail / 1 info — anchors 2 real receipts on X Layer mainnet
python3 verify.py --phase 6b       # expect 6 pass / 0 fail / 1 info — anchors 2 more real receipts; public verify page
```

`--phase` now takes a string, so sub-gates from the feature addendum sit alongside the numbered
phases (`3b-2` today; `6b`, `7b`, `8b-1`, `3b-3`, `3b-4` as they're built — see "Feature addendum"
below). The plain numeric phases are unchanged in behavior and in what "pass" means.

All must be green before writing new code. They make real network calls and run real SQL — no
fixtures, no mocks, with one declared exception: GATE 3's calendar, which is a fixture living in
the harness rather than the package, named as such in every check that uses it. A network failure
reports FAIL rather than passing from cache. **Phase 6 spends real (tiny) mainnet gas every run**
— each pass anchors two receipts, ~0.0000021 OKB total.

**Then: Phase 4 go-live the moment items 1–3 land, or Phase 7 once item 5 + ledger U3 resolve.**
Everything unblocked has been built.

## Git — already configured, but fragile across codespace rebuilds

Remote: **https://github.com/holybunnie/concierge** (public). `main` tracks `origin/main`.

`git push` works as-is. If it starts returning **403**, the cause is known: GitHub injects a
`GITHUB_TOKEN` into Codespaces that is hard-scoped to the codespace's *own* repo, and both `gh`
and the Codespaces credential helper prefer it over your real credentials — regardless of what
the REST API reports for permissions. The fix:

```bash
GITHUB_TOKEN= GH_TOKEN= gh auth login     # the empty vars are the load-bearing part
```

Web browser → HTTPS → yes to authenticating git. A repo-local credential helper is already pinned
in `.git/config` to route pushes through that stored token. If it ever needs rebuilding, note
that **the empty entry must come first** — git treats `credential.helper` as a list and appends,
so adding one without resetting leaves the Codespaces helper ahead of it and the 403 persists:

```bash
git config --local --unset-all credential.helper
git config --local --add credential.helper ""          # resets the chain — load-bearing
git config --local --add credential.helper \
  '!f(){ echo username=holybunnie; echo "password=$(GITHUB_TOKEN= GH_TOKEN= gh auth token)"; };f'
```

- **All commits authored by `holybunnie`** (`122739099+holybunnie@users.noreply.github.com`).
- **No AI attribution anywhere** — no `Co-Authored-By`, no "generated with" footer, no tool credit
  in commits, PR bodies, README or CREDITS.
- The stored gh token is plain text in `~/.config/gh/hosts.yml` with `repo`/`gist`/`read:org`
  scope. Revoke at github.com/settings/tokens when the build is done.

---

## What is done

| Phase | Gate result | Notes |
|---|---|---|
| 0 Foundations + live verification | **9 pass / 2 info** | 4 build-spec corrections found; see ledger |
| 1 Tenant model + isolation | **11 pass**, 9 of them attacks | Postgres RLS, not app predicates |
| 2 Vertical-aware onboarding | **11 pass / 1 info** | real estate, legal, spa + generic |
| 3 State machine + guardrails | **16 pass / 3 info** | 10 of them attacks |
| 4 Email connector (Postmark) | **8 pass / 3 info** | code complete; live inbox delivery pending items 1-3 |
| 5 Booking (live Cal.com) | **5 pass / 2 info** | real booking created + cancelled against live Cal.com |
| 6 Receipts on X Layer mainnet | **8 pass / 1 info** | ReceiptAnchor deployed, 2 real receipts anchored, tamper + forgery attacks caught |
| 3b-2 Confidence-scored autonomy (Feature 2) | **7 pass / 2 info** | thin profile queues, complete profile auto-sends, precedent moves a marginal figure over the line, GATE 3 regression re-proved |
| 3b-3 Decaying floor (Feature 5) | **4 pass / 1 info** | 5 real negotiation rounds tracked the curve exactly, 6 rounds past it the absolute floor still never broke, flat-floor tenant regression re-proved |
| 3b-4 Safe Follow-Up | **3 pass / 1 info** | real stalled thread nudged once from its own history, second stall marks it DEAD, a thread with no genuine prior contact never triggers one however far the clock is pushed |
| 6b Public receipt verification (Feature 3) | **6 pass / 1 info** | real anchored receipt reads back correctly on the public page; nonexistent id, malformed id, and a real internal-only (floor-breach) receipt all render the identical clean 404; two tenants' pages never cross |

## Feature addendum (Phases 3b/6b/8b/7b) — status

Five features attach to the existing phase plan rather than restarting it (full spec: the
addendum message itself, not reproduced here). Sequencing per the addendum's Part IV:

| Feature | Attaches to | Gate | State |
|---|---|---|---|
| 2 — Confidence-scored autonomy | Phase 3 | 3b-2 | **done**, 7 pass / 0 fail / 2 info |
| 5 — Decaying floor | Phase 3 | 3b-3 | **done**, 4 pass / 0 fail / 1 info |
| Safe Follow-Up | Phase 3 | 3b-4 | **done**, 3 pass / 0 fail / 1 info |
| 3 — Public receipt verification | Phase 6 | 6b | **done**, 6 pass / 0 fail / 1 info |
| 1 — Product-gap intelligence | Phase 8 | 3b/8b-1 | not started (needs Phase 8) |
| 4 — Cross-tenant benchmarking | Phase 7 | 7b | blocked — needs real Phase 7 engagement data first, per the addendum's own §0.2 |

The Phase-3 family (Features 2, 5, and Safe Follow-Up) and Feature 3 (Phase 6's family) are both
complete. Only Feature 1 (needs Phase 8) and Feature 4 (blocked on Phase 7) remain.

**What Feature 3 added:** a third "deliberate door" in `schema.sql` — `public_receipt(rid uuid)`,
a `SECURITY DEFINER` function scoped by receipt_id alone, returning at most one row and never
`tenant_id`/`thread_id` (unlike the inbound-address resolvers, which return an opaque uuid, this
one returns curated receipt columns — but the scoping principle is identical: untrusted input in,
exactly one thing out). `receipts.public_view()` is the whitelist: only `PUBLIC_ACTIONS =
{"quoted", "counter_within_rules", "booked"}` are ever shown — a floor breach, an escalation, or a
Feature-2-queued draft carries internal guardrail reasoning (exact floor figures, refusal
rationale) and is treated exactly like a receipt that does not exist, indistinguishably. `app.py`
gained `GET /r/{receipt_id}` — plain server-rendered HTML on the existing FastAPI app, no new
service, no framework. The outbound quote/counter/booked templates gained one line
(`PROSE["verify_line"]`) linking to it, appended only when `config.public_base_url()` is
configured (honest degradation, same pattern as `PENDING-DOMAIN.invalid`) — the receipt_id is
pre-generated in `engine.step()` before `render()` runs so the SAME id appears in the email and
the database row (`store.insert_receipt`/`receipts.record` both gained an optional `receipt_id`
passthrough for this).

One real external fact verified live before building on it: X Layer's block explorer is OKLink,
and the obvious URL guess (`oklink.com/xlayer/tx/...`) 301-redirects to the real path
(`oklink.com/x-layer/evm/tx/...`) — confirmed against a real anchored tx from this repo's own
Phase 6 run. See `docs/VERIFICATION_LEDGER.md`, Feature 3 section.

**What Safe Follow-Up added:** `concierge/followup.py` — `due_threads()` (pure arithmetic over
stored timestamps, an injectable clock so the harness never has to sleep through a real week),
`draft()` (builds the nudge via the SAME `engine.render()` every other reply uses, so it inherits
the AI disclosure, the tenant's own nouns, and — via the existing `terms_line` mechanism — whatever
is actually on the table for that thread), and `process_tenant()`/`dispatch()` (persist + receipt,
then send outside any open transaction, mirroring `mail.handle_inbound`'s separation of DB work
from network I/O). Tenant-configurable via `profile.follow_up_policy` (`quiet_hours` default 48,
`dead_after_hours` default 168), same seam as `autonomy_thresholds` and `floor_curve`.

**The hard boundary against cold outbound is `followup._has_real_contact()`** — checked on the
thread's own stored history (a `direction: in` entry must actually exist), not trusted from the
caller or from `state == AWAITING_REPLY` alone. GATE 3b-4 check 3 proves it with a thread
constructed directly (bypassing `engine.step` entirely, so no real inbound ever happened) and
pushed 10 years into the future — it is never touched, no email ever sent to it. Cold outbound
itself — a function accepting a bare address and an "send an intro" instruction — was not built;
there is no code path in this module that could do it, per the addendum's own §0.3.

**What Feature 5 added:** `pricing.floor_curve()` / `pricing.floor_curve_value()` (an OPTIONAL,
richer `pricing_rules.floor_curve` shape — `{initial, floor, kind, decay_trigger, decay_steps}` —
read only when a tenant sets one; absent, behavior is byte-identical to the flat floor that
already existed), `guardrails.bounds_for`/`negotiate` gained `round_index`/`days_elapsed`
parameters so the SAME "most restrictive rule binds" logic can evaluate against a moving point on
the curve instead of a static number, and `engine.py`'s negotiation branch now tracks
`negotiation_round` on the thread's own offer (same pattern as `timezone_attempts`) and computes
elapsed days from `thread.created_at`. The absolute floor (`floor_curve.floor`) is a hard clamp
applied inside `pricing.floor_curve_value` itself — no caller can construct a bound below it, even
by feeding it a malformed curve. `verify_phase3b3.py` / GATE 3b-3 proves the curve is followed
point-by-point (not jumped to the eventual floor early, not stuck at the starting point late), red
-teams six rounds past where the curve runs out, and re-proves a curve-less tenant is unaffected.

One real cross-feature bug found and fixed while building this: `confidence.py`'s completeness
signal (Feature 2) only recognized the flat `pricing_rules.floor`, so a tenant using ONLY a
`floor_curve` scored as if they'd never set a floor at all — correctly caught by GATE 3b-3's own
harness when every negotiation round unexpectedly queued for owner approval instead of sending.
Fixed in `confidence.py` to recognize either shape as "a floor is set". Worth remembering when
building Safe Follow-Up or the remaining features: anything Feature 2 reads out of the profile
needs to know about every OTHER feature's optional profile shapes, or its completeness signal
silently under-scores tenants using them.

**What Feature 2 added:** `concierge/confidence.py` (three deterministic signals — profile
completeness, floor proximity, precedent — combined by a fixed, documented weighted formula;
never an LLM call), a new `AWAITING_OWNER_APPROVAL` thread state, a `confidence jsonb` column on
`receipts` (persisted alongside the decision, not just rendered for display), and the gating
logic in `engine.step()` that drafts-but-holds a reply scoring below the tenant's per-service
`profile.autonomy_thresholds` (default 0.55, conservative). `verify_phase3b.py` / GATE 3b-2 proves
it end to end, including a real regression re-run of GATE 3's own NEW→BOOKED journey.

One correction made *while building this feature*, worth recording here since the ledger is for
external facts and this isn't one — it's an internal calibration note: the first weighting
(0.40/0.35/0.25, threshold 0.70) broke GATE 3's and GATE 5's existing "full autonomous journey"
fixtures, because with a 0.25 precedent weight no brand-new tenant could ever clear 0.70 on a real
negotiated discount. Recalibrated to 0.40/0.45/0.15 with a 0.55 threshold (see `confidence.py`'s
own docstring for the two scenarios it's calibrated against) and adjusted GATE 3/5's demo
counter-offer from £75 to £80 so their "comfortable, non-marginal negotiation" fixtures stay
comfortable rather than sitting on the boundary this feature exists to flag. The £75-against-a-
£72.25-floor case is now what GATE 3b-2 checks 3-4 use on purpose, to prove that exact boundary.

### What Phase 4 added

`postmark.py` (parse a real inbound document; send via the Postmark API on stdlib only),
`mail.py` (route by recipient → run the engine tenant-scoped → dispatch reply + owner alert;
webhook auth), `app.py` (the FastAPI inbound webhook systemd keeps alive on the VPS),
`verify_phase4.py`. Config gained `inbound_domain()` = `inbox.<CONCIERGE_DOMAIN>`, and
`onboarding.allocate_inbound_address` now uses it.

What the gate proves now, against real Postgres and Postmark's real payload schema: an inbound
email is parsed, routed to the one tenant that owns the address, quoted from that tenant's
profile, and answered FROM the tenant's own inbox with the disclosure on line one — plus attacks
(orphan recipient refused, +tag/case leak attempts, unauthenticated webhook, email threading).
The **one** stand-in is a recording mailer, declared a fixture exactly as GATE 3's calendar;
production sends through `postmark.PostmarkMailer`, which refuses to run without a real token.

**What is NOT yet proven** and is the remaining GATE 4 requirement: a real reply landing in a
real inbox, not spam. That needs the Postmark token (item 3, account still in approval), the
DKIM/Return-Path/MX DNS on `inbox.quietdesks.com` (item 2, domain now bought), and the webhook
deployed on the VPS (item 1). See the Phase 4 go-live checklist below.

### Phase 4 go-live checklist (operator + deploy)

1. Postmark account **approval** (submitted; "still reviewing" as of last check).
2. In Postmark, add and verify the **sending domain `inbox.quietdesks.com`** → paste its DKIM
   TXT + Return-Path CNAME into Cloudflare DNS. (Replies are FROM `<slug>@inbox.quietdesks.com`,
   so the *inbox* subdomain is the sending domain, not the apex.)
3. DNS at Cloudflare: **MX** on `inbox.quietdesks.com` → `inbound.postmarkapp.com` (pri 10);
   a **DMARC** TXT; the DKIM/Return-Path from step 2; and **A** `app.quietdesks.com` → the VPS.
4. Deploy the webhook on the VPS: own dir + own user, `uvicorn concierge.app:app` under systemd,
   nginx vhost `app.quietdesks.com` → 127.0.0.1:8000 with TLS. (Shared box — see repo notes; a
   free port is 8000; do not reuse another project's Postgres.)
5. `.env` on the VPS: `CONCIERGE_DOMAIN=quietdesks.com`, `POSTMARK_SERVER_TOKEN=…`,
   `POSTMARK_INBOUND_WEBHOOK_SECRET=…`, `APP_DATABASE_URL`/`DATABASE_URL` for CONCIERGE's own PG.
6. Point Postmark's inbound webhook at
   `https://<user>:<secret>@app.quietdesks.com/inbound/postmark`.
7. Live test: onboard a tenant, email its address, confirm the reply lands and is not in spam.

### What Phase 6 added

`contracts/ReceiptAnchor.sol` (minimal Solidity: `anchorReceipt(bytes32)` → event + storage
write, compiled with Foundry), `concierge/xlayer.py` (signs and sends real transactions against
X Layer mainnet — RPC transport on stdlib `urllib`, signing via `eth_account`, the one dependency
this phase adds and the reasoning for taking it is in the module docstring), new functions in
`receipts.py` (`anchor()`, `recover_signer()`), `store.mark_anchored`, `verify_phase6.py`.

Deployed contract: `0x9b3C500C59CEC55036e3839091f7C5B2cD9D0587` on chain 196. Every receipt now
carries two independent proofs once anchored: an offline ECDSA signature over the content hash
(recoverable with no RPC call), and the same hash anchored on-chain, confirmed by polling the
transaction receipt for `status: 0x1` — never assumed from a broadcast succeeding. Recording a
receipt and anchoring it are deliberately two separate steps (`receipts.record` then
`receipts.anchor`), so a customer-facing reply is never blocked on a mainnet confirmation.

**Wired into the live webhook, not the shared engine path.** `app.py`'s `/inbound/postmark` route
fires `_anchor_in_background` on its own daemon thread after the reply is already sent, only when
a receipt exists and both `XLAYER_PRIVATE_KEY`/`XLAYER_CONTRACT` are configured. This is
deliberately *not* inside `engine.step` or `mail.handle_inbound` — both are called directly, with
real chain credentials now present in `.env`, by `verify_phase3.py`, `verify_phase4.py` and
`verify_phase5.py`, and a gate run must never have the side effect of spending real mainnet gas.
Only the actual FastAPI process spends gas, and only on a real inbound email. Confirmed by a
direct call to `_anchor_in_background` (NULL → real signature + real tx) and a mocked
`TestClient` request proving the thread only fires under the right conditions — not yet proven
against a real Postmark-delivered email, since that still needs item 3.

One finding not in any doc: **the public RPC (`rpc.xlayer.tech`) is eventually consistent across
nodes** — an `eth_call` issued immediately after a confirmed write can hit a node that hasn't
seen it yet. `verify_phase6.py` polls rather than reading once; production code anchoring
synchronously would need the same care.

`XLAYER_PRIVATE_KEY` and `XLAYER_CONTRACT` are in `.env` (gitignored). The signer
(`0x69eb1bAA26BffCD0fA9089aa2187F6Ca3e2A54f6`) holds only gas money (~0.0103 OKB after the
deploy) — never a key holding meaningful assets, per OPERATOR_PROVIDES' own advice.

### What Phase 3 added

`pricing.py` (quote derivation), `guardrails.py` (negotiation bounds), `lexicon.py` (the
tenant's own nouns), `receipts.py` (hashing + tamper detection), `engine.py` (the state
machine), `verify_phase3.py`.

The design decision worth knowing before touching any of it: **the engine is trade-neutral, and
the words are the tenant's.** Pricing reads a canonical vocabulary — `pricing_rules.headline` /
`floor` / `max_discount` — that vertical templates map onto via `Field.maps_to`, so a trade with
no template quotes exactly as well as one with. And every noun in an outbound message comes from
the profile rather than from a string literal, so a dentist says "consultation" and an estate
agent says "viewing" without either word appearing in the code. GATE 3 check 2 proves it with a
veterinary practice; check 3 greps `engine.PROSE` against `engine.TRADE_NOUNS` to stop the
regression. See CLAUDE.md for the rule in full.

Booking is real state machine work against a **declared fixture calendar** that lives in the
harness, not the package. The production default is `engine.NoCalendar`, which refuses rather
than inventing times — check 12.

## What is left

### Phase 4 — email connector (Postmark) · CODE COMPLETE, live delivery pending items 1, 2, 3
Built and passing GATE 4 (8/0/3): inbound parse, tenant resolution from the recipient address,
outbound send with the AI disclosure as the first line, webhook authenticity, email threading.
Two reality corrections from the build spec, both in the ledger: **Postmark inbound has no HMAC
signature** — authenticity is HTTP Basic Auth carried in the webhook URL, which `mail.check_webhook_auth`
verifies; and the sending domain is **`inbox.quietdesks.com`** (replies come FROM the inbox
subdomain), so that is the domain to verify in Postmark, not the apex. Remaining to go live:
items 1-3 and the go-live checklist above. Domain (item 2) is bought (quietdesks.com); Postmark
(item 3) is in approval.

### Phase 5 — booking (Cal.com) · DONE, GATE 5 passed 2026-07-23
`calcom.py` fills the `engine.Calendar` seam with live Cal.com v2 calls; `verify_phase5.py`
runs the full NEW→BOOKED journey against the real API, creates a real booking (UTC start, nested
attendee, prospect timezone), confirms it by the API's own `accepted` status, and **cancels it**
so a real calendar is left clean. Versions pinned: slots `2024-09-04`, bookings `2026-02-25`
(ledger proves a stale pin silently downgrades). Credentials come from `profile.calendar_ref`
with a `CAL_API_KEY`/`CAL_EVENT_TYPE_ID` env fallback for the single-operator demo. Event type
6433300, connected to a Google Calendar. **The Cal.com key is `cal_live_` and was exposed in
chat — rotate before submission.**

### Phase 6 — receipts on X Layer **mainnet (196)** · DONE, GATE 6 passed 2026-07-24
`xlayer.py` fills the anchoring seam with live X Layer calls against a deployed `ReceiptAnchor`
contract; `verify_phase6.py` runs a real conversation through the real engine, anchors the
resulting quote receipt and a floor-breach receipt, confirms both on-chain independently, and
red-teams a decision tamper and a signature-forgery attempt. Real measured gas: 224,160 for the
one-time deploy, 51,849 per anchor — both cheaper than the pre-deploy estimates in ledger §9.
**Not yet wired into the live request path** — `engine.step` still writes `signature`/`xlayer_tx`
as NULL; a background worker calling `receipts.anchor()` on unanchored rows is Phase 8 territory
(see "Workers" in CLAUDE.md §12), so replies stay fast and are never blocked on a mainnet
confirmation.

### Phase 7 — A2A escrow + settlement · BLOCKED on operator item 5 **and ledger U3**
U3 (the OKX escrow API call signatures) is unresolved — the OnchainOS docs cover wallet install
but not the escrow credentials. **No escrow code may be written against a guessed API shape.**
Resolve U3 first.

### Phase 8 — summary + scheduled actions + product-gap intelligence
Needs live Phase 4 + Phase 7 to produce real end-to-end data (5 and 6 already do). Also where the
receipt-anchoring background worker belongs — Phase 6 built `receipts.anchor()` but nothing calls
it automatically yet.

### Phase 9 — hardening + submission
Public repo ✓, OSI licence ✓, CREDITS ✓, ledger ✓, operator-provides ✓. Still needed: ~90s demo
video, architecture diagram, "reproduce every claim" README pass, Google form before the deadline.

---

## The critical path is not code

**3 of 8 operator credentials have arrived: items 4 (Cal.com), 6 (funded X Layer signer), 7 (LLM
key).** Phases 5 and 6 are proven live because of them. Items 1, 2 (partial), 3, 5 remain — full
instructions in `docs/OPERATOR_PROVIDES.md`.

The binding constraint is **Postmark**: it manually reviews new accounts before they may send
outside domains you own — stated at under 24h on weekdays, longer at weekends. The domain
(`quietdesks.com`) is bought; DNS and the Postmark approval are still the gate on Phase 4 going
live, and Phase 7 is gated separately on the OKX wallet plus resolving ledger U3. Phase 4 is what
the rest of the demo video needs — booking and receipts already have their real footage.

Mitigation if approval runs late: while pending you can still configure inbound, use the API, and
send to your own verified domain — so Phase 4 is buildable and demoable provided the test
"prospect" address is on the operator's own domain.

---

## Invariants already proven — do not regress them

Each has a harness check that fails loudly if broken.

1. **No price from a language model.** Everything quotable derives from the tenant's stored
   profile through code. Not in the profile → escalate.
2. **`store.py` contains no `WHERE tenant_id = ?` clause, deliberately.** Isolation is PostgreSQL
   row-level security. Adding predicates is not a fix — the absence is the proof. Never grant the
   app role table ownership or `BYPASSRLS`.
3. **A template example can never become tenant data.** `onboarding.build_profile()` reads
   `self.answers` and must not reach `Field.example`.
4. **Receipts anchor on X Layer mainnet (196), never a testnet.**
5. **No fabricated credential, and no placeholder that looks live.**
6. **AI disclosure in the first line of every outbound message**, with a route to a human.
   Asking for a human is checked before qualification, pricing and all state logic, so it works
   from any state (GATE 3 checks 13 and 14).
7. **No trade vocabulary in `engine.PROSE`.** Domain nouns come from `profile.lexicon` and
   `profile.services`, never from a string literal. GATE 3 check 3 greps for the regression.
8. **A floor breach never receives a counter-offer.** Countering at the floor publishes the
   tenant's reservation price; the breach escalates and no figure is sent (GATE 3 check 7).
9. **Nothing is claimed as booked without the calendar API confirming it**, and with no calendar
   connected the engine escalates rather than inventing an appointment (GATE 3 check 12).
10. **No confidence score, floor-curve point or benchmark aggregate comes from a language
    model.** `confidence.py` is arithmetic over three named, stored signals; it may only decide
    whether a reply sends or queues, never what the reply says (GATE 3b-2).

## Open questions for the operator

- **`CLAUDE.md` is a visible tell in a public repo.** Rename to `CONVENTIONS.md`? Cost: it stops
  being auto-loaded as repo conventions, so `docs/HANDOFF.md` would have to carry them. Undecided.
- Ledger **U3** (OKX escrow API shape) blocks all of Phase 7 and needs resolving before that phase
  can start.

## Known gaps, stated plainly

- The vertical lexicon knows three trades. A dentist or plumber falls to the generic template —
  which Phase 3 proved is a first-class path, not a degraded one: a veterinary practice runs the
  full journey and enforces its floor correctly. What the generic path loses is the *sharpness*
  of the questions, not the ability to quote. Once operator item 7 lands, an LLM should propose
  a template for an unseen trade — the questions only, never the values.
- **Free-text ICP is not machine-evaluated.** Qualification currently means "is this in the
  catalogue, and does it trip an escalation trigger" — it does not read the ICP prose. Honest
  work for an LLM under §2 (understanding only, never pricing).
- Service matching and escalation-trigger matching are word-overlap heuristics. Both fail
  towards the owner's inbox rather than towards a wrong answer, which is the correct direction,
  but they will sometimes escalate something answerable.
- No concurrency test on `SET LOCAL` scoping under a connection pool. The mechanism is proven;
  the concurrent case is not.
- The app role's password is literal in `schema.sql`. Fine for a local container, **wrong for the
  VPS** — must become an env-supplied secret before Phase 4 exposes anything publicly.
