# HANDOFF

Resume point for a session starting cold. **Current as of 2026-07-24.**

**Deadline: 2026-07-27 22:59 UTC — 4 days 20 hours remain.**

---

## Start here after a break

```bash
cd /workspaces/concierge
docker compose up -d postgres      # the container does not survive a codespace rebuild
pip install -r requirements.txt    # if the container was rebuilt
python3 verify.py --suite foundations        # expect 9 pass / 0 fail / 2 info
python3 verify.py --suite isolation        # expect 11 pass / 0 fail
python3 verify.py --suite onboarding        # expect 11 pass / 0 fail / 1 info
python3 verify.py --suite engine        # expect 16 pass / 0 fail / 3 info
python3 verify.py --suite autonomy     # expect 7 pass / 0 fail / 2 info — Feature 2, confidence-scored autonomy
python3 verify.py --suite floor-curve     # expect 4 pass / 0 fail / 1 info — Feature 5, the decaying floor
python3 verify.py --suite follow-up     # expect 3 pass / 0 fail / 1 info — Safe Follow-Up
python3 verify.py --suite comprehension       # expect 6 pass / 0 fail / 1 info — comprehension (0 wrong prices; policies buy +55% autonomy)
python3 verify.py --suite email        # expect 8 pass / 0 fail / 3 info
python3 verify.py --suite booking        # expect 5 pass / 0 fail / 2 info — makes+cancels a real booking
python3 verify.py --suite receipts        # expect 8 pass / 0 fail / 1 info — anchors 2 real receipts on X Layer mainnet
python3 verify.py --suite public-receipts       # expect 6 pass / 0 fail / 1 info — anchors 2 more real receipts; public verify page
python3 verify.py --suite scheduler        # expect 9 pass / 0 fail / 0 info — summary + scheduled worker, no new gas spent
python3 verify.py --suite product-gaps     # expect 5 pass / 0 fail / 0 info — Feature 1, product-gap intelligence
```

`--suite` now takes a string, so sub-gates from the feature addendum sit alongside the numbered
phases (`3b-2` today; `6b`, `7b`, `8b-1`, `3b-3`, `3b-4` as they're built — see "Feature addendum"
below). The plain numeric phases are unchanged in behavior and in what "pass" means.

All must be green before writing new code. They make real network calls and run real SQL — no
fixtures, no mocks, with one declared exception: the engine suite's calendar, which is a fixture living in
the harness rather than the package, named as such in every check that uses it. A network failure
reports FAIL rather than passing from cache. **Phase 6 spends real (tiny) mainnet gas every run**
— each pass anchors two receipts, ~0.0000021 OKB total.

**Then: Phase 4 go-live the moment items 1–3 land, or A2A escrow once item 5 + ledger U3 resolve.**
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
| 4 Email connector (Postmark) | **8 pass / 3 info + LIVE** | the email suite harness green; **live round-trip proven 2026-07-25** — real inbound email → Postmark → webhook on the VPS → tenant resolved → AI reply delivered to Gmail (2 replies `Sent`, not spam) |
| 5 Booking (live Cal.com) | **5 pass / 2 info** | real booking created + cancelled against live Cal.com |
| 6 Receipts on X Layer mainnet | **8 pass / 1 info** | ReceiptAnchor deployed, 2 real receipts anchored, tamper + forgery attacks caught |
| 3b-2 Confidence-scored autonomy (Feature 2) | **7 pass / 2 info** | thin profile queues, complete profile auto-sends, precedent moves a marginal figure over the line, the engine suite regression re-proved |
| 3b-3 Decaying floor (Feature 5) | **4 pass / 1 info** | 5 real negotiation rounds tracked the curve exactly, 6 rounds past it the absolute floor still never broke, flat-floor tenant regression re-proved |
| 3b-4 Safe Follow-Up | **3 pass / 1 info** | real stalled thread nudged once from its own history, second stall marks it DEAD, a thread with no genuine prior contact never triggers one however far the clock is pushed |
| 6b Public receipt verification (Feature 3) | **6 pass / 1 info** | real anchored receipt reads back correctly on the public page; nonexistent id, malformed id, and a real internal-only (floor-breach) receipt all render the identical clean 404; two tenants' pages never cross |
| 8 Summary + scheduled actions | **9 pass / 0 fail** | worker entry point + enumeration-role split (checks 8-9, 2026-07-25); real conversation numbers counted exactly, escalation text carried verbatim, scheduler's anchor/follow-up/summary jobs all read+write the same real rows, no new mainnet gas spent proving it |
| 3c Comprehension (answering the question asked) | **6 pass / 0 fail / 1 info** | 103 generated questions per run across 3 tenants: 0 sent a price they should not have (was 70), 96% of answerable questions handled without a human; check 6 proves the four optional onboarding policy questions take the SAME tenant from 27.5% to 82.5% autonomy with no code change |

**Local DB hygiene:** gates create tenants and never clean up, and the inbound-address allocator
gives up after 99 same-name collisions — at ~227 tenants the email suite began failing to onboard at all.
`TRUNCATE tenants, threads, receipts, gap_events CASCADE` on the LOCAL container fixes it (checked
first that no receipt carried a real `xlayer_tx`). Do this before a long gate session.
| 8b-1 Product-gap intelligence (Feature 1) | **5 pass / 0 fail** | an unquotable inquiry writes one verbatim GapEvent and surfaces word-for-word in the owner summary; a floor breach writes none; a second tenant's summary never contains it; with no LLM key the gap shows as raw text, never a fabricated category |

## Feature addendum (Phases 3b/6b/8b/7b) — status

Five features attach to the existing phase plan rather than restarting it (full spec: the
addendum message itself, not reproduced here). Sequencing per the addendum's Part IV:

| Feature | Attaches to | Gate | State |
|---|---|---|---|
| 2 — Confidence-scored autonomy | Phase 3 | 3b-2 | **done**, 7 pass / 0 fail / 2 info |
| 5 — Decaying floor | Phase 3 | 3b-3 | **done**, 4 pass / 0 fail / 1 info |
| Safe Follow-Up | Phase 3 | 3b-4 | **done**, 3 pass / 0 fail / 1 info |
| 3 — Public receipt verification | Phase 6 | 6b | **done**, 6 pass / 0 fail / 1 info |
| 1 — Product-gap intelligence | Phase 8 | 8b-1 | **done**, 5 pass / 0 fail |
| 4 — Cross-tenant benchmarking | A2A escrow | 7b | blocked — needs real A2A escrow engagement data first, per the addendum's own §0.2 |

The Phase-3 family (Features 2, 5, and Safe Follow-Up), Feature 3 (Phase 6's family), Phase 8, and
Feature 1 are all complete. Only Feature 4 remains (blocked on A2A escrow).

**What Feature 1 added:** `concierge/gaps.py` and a `gap_events` table (same RLS `tenant_isolation`
policy as every other tenant table — no new isolation mechanism). The write path is exactly one
side effect on Phase 3's existing "unknown query → ESCALATE, never invent" transition:
`engine.decide` sets `Decision.product_gap` **only** on the `Unquotable` branch (never on a floor
breach, a human request, or a tripped trigger), and `engine.step` writes one `GapEvent` row with
the prospect's verbatim text. `summary.build_summary` now takes an optional `gap_events` list
(backward-compatible — pre-Feature-1 callers unchanged) and `render_summary_text` adds the payoff
section ("N inquiries asked for something you don't offer… verbatim examples"). `scheduler.process_tenant`
fetches the gaps and calls `gaps.classify_pending` before building the summary. the product-gaps suite
(`verify_product_gaps.py`) proves write → verbatim-in-summary → floor-breach-is-not-a-gap → cross-tenant
isolation → honest no-key degradation.

**Categorization is optional enrichment and is NOT currently working — the `LLM_API_KEY` in `.env`
is invalid.** `gaps.classify_gap` uses the correct current Anthropic API shape (`messages.create`
with `output_config={"format": {"type": "json_schema", …}}`, model `claude-opus-4-8`, SDK 0.119.0 —
verified against the claude-api reference), but a live call returns **401 "API key is invalid."**
The key is a real-format `sk-ant-…` (length 100) that the server rejects — expired, revoked, or
mistyped. Feature 1 degrades exactly as designed: `classify_gap` returns `None`, gaps render as raw,
unclustered text, and the summary says so — the product-gaps suite check 5 proves this deterministically. So the
feature is honest and complete; only the coarse category labels are missing until the operator
supplies a working key. **This is also the first code path that actually consumes item 7** —
`OPERATOR_PROVIDES.md` previously said it was "not currently consumed by any code path," which is
why the bad key went unnoticed until now.

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
caller or from `state == AWAITING_REPLY` alone. the follow-up suite check 3 proves it with a thread
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
by feeding it a malformed curve. `verify_floor_curve.py` / the floor-curve suite proves the curve is followed
point-by-point (not jumped to the eventual floor early, not stuck at the starting point late), red
-teams six rounds past where the curve runs out, and re-proves a curve-less tenant is unaffected.

One real cross-feature bug found and fixed while building this: `confidence.py`'s completeness
signal (Feature 2) only recognized the flat `pricing_rules.floor`, so a tenant using ONLY a
`floor_curve` scored as if they'd never set a floor at all — correctly caught by the floor-curve suite's own
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
`profile.autonomy_thresholds` (default 0.55, conservative). `verify_autonomy.py` / the autonomy suite proves
it end to end, including a real regression re-run of the engine suite's own NEW→BOOKED journey.

One correction made *while building this feature*, worth recording here since the ledger is for
external facts and this isn't one — it's an internal calibration note: the first weighting
(0.40/0.35/0.25, threshold 0.70) broke the engine suite's and the booking suite's existing "full autonomous journey"
fixtures, because with a 0.25 precedent weight no brand-new tenant could ever clear 0.70 on a real
negotiated discount. Recalibrated to 0.40/0.45/0.15 with a 0.55 threshold (see `confidence.py`'s
own docstring for the two scenarios it's calibrated against) and adjusted the engine suite/5's demo
counter-offer from £75 to £80 so their "comfortable, non-marginal negotiation" fixtures stay
comfortable rather than sitting on the boundary this feature exists to flag. The £75-against-a-
£72.25-floor case is now what the autonomy suite checks 3-4 use on purpose, to prove that exact boundary.

### What Phase 4 added

`postmark.py` (parse a real inbound document; send via the Postmark API on stdlib only),
`mail.py` (route by recipient → run the engine tenant-scoped → dispatch reply + owner alert;
webhook auth), `app.py` (the FastAPI inbound webhook systemd keeps alive on the VPS),
`verify_email.py`. Config gained `inbound_domain()` = `inbox.<CONCIERGE_DOMAIN>`, and
`onboarding.allocate_inbound_address` now uses it.

What the gate proves now, against real Postgres and Postmark's real payload schema: an inbound
email is parsed, routed to the one tenant that owns the address, quoted from that tenant's
profile, and answered FROM the tenant's own inbox with the disclosure on line one — plus attacks
(orphan recipient refused, +tag/case leak attempts, unauthenticated webhook, email threading).
The **one** stand-in is a recording mailer, declared a fixture exactly as the engine suite's calendar;
production sends through `postmark.PostmarkMailer`, which refuses to run without a real token.

**What is NOT yet proven** and is the remaining the email suite requirement: a real reply landing in a
real inbox, not spam. That needs the Postmark token (item 3, account still in approval), the
DKIM/Return-Path/MX DNS on `inbox.quietdesks.com` (item 2, domain now bought), and the webhook
deployed on the VPS (item 1). See the Phase 4 go-live checklist below.

### Phase 4 go-live checklist (operator + deploy)

**✅ COMPLETE — Phase 4 is LIVE (2026-07-25).** The app runs at `https://app.quietdesks.com` and a
real email round-trip was proven end to end (see the Phase 4 section above). All steps below are done.

1. ✅ Postmark account **approval** — approved 2026-07-24 (ticket `[NVXMEE-2Z5W7]`).
   `POSTMARK_SERVER_TOKEN` is in the VPS `.env`. Default **Transactional** stream (not Broadcast).
2. ✅ Sending domain **`inbox.quietdesks.com`** added in Postmark and **DKIM + Return-Path VERIFIED**.
   (Replies are FROM `<slug>@inbox.quietdesks.com` — the *inbox* subdomain, not the apex.)
3. ✅ DNS at Cloudflare: **MX** `inbox` → `inbound.postmarkapp.com` (pri 10, comma-bug fixed);
   DKIM TXT (`…pm._domainkey.inbox`) + Return-Path CNAME (`pm-bounces.inbox` → `pm.mtasv.net`);
   **A** `app.quietdesks.com` → `38.49.216.59` (grey/DNS-only) with TLS issued. (DMARC still optional
   — not required for delivery; add later for monitoring.)
4. ✅ Webhook deployed on the VPS: dedicated `concierge` user, `uvicorn concierge.app:app` under
   systemd (`concierge.service`), nginx vhost `app.quietdesks.com` → `127.0.0.1:8000` with Let's
   Encrypt TLS (auto-renew). CONCIERGE's own Postgres runs in a dedicated container `concierge-pg`
   on `127.0.0.1:5433` (NOT the shared system cluster — full isolation from `concrete_edu` etc.).
5. ✅ `/opt/concierge/.env` (chmod 600, owned by `concierge`) has `CONCIERGE_DOMAIN`,
   `POSTMARK_SERVER_TOKEN`, `POSTMARK_INBOUND_WEBHOOK_SECRET`, `XLAYER_*`, `CAL_*`, `LLM_API_KEY`, and
   `DATABASE_URL`/`APP_DATABASE_URL` on port 5433 with strong generated passwords.
6. ✅ Postmark inbound webhook set to
   `https://postmark:<POSTMARK_INBOUND_WEBHOOK_SECRET>@app.quietdesks.com/inbound/postmark` **and**
   `InboundDomain = inbox.quietdesks.com` set on the server. Webhook checks the **password half**
   only. Proven live: no-auth → 401, wrong secret → 401, correct secret → 200.
7. ✅ Live test PASSED: real Gmail → `halcyon-rooms@inbox.quietdesks.com` → webhook → tenant resolved
   → AI reply delivered to the Gmail inbox (2 replies `Sent`, not spam). the email suite requirement met.

### VPS deployment (live) — `38.49.216.59`

Deployed 2026-07-24 via SSH from the codespace (root password from `.env`, box is the shared
`Jennycruzy` box — see the `vps-shared-box` note). Layout:
- **User:** `concierge` (system user, home `/opt/concierge`). Code rsynced there (Feature 1
  included — the local commit, not `origin/main`, which hasn't been pushed). `.venv` with
  requirements installed.
- **Service:** `systemctl status concierge` (uvicorn on `127.0.0.1:8000`, `Restart=always`).
  `journalctl -u concierge` for logs. Reload after a code change: `systemctl restart concierge`.
- **DB:** container `concierge-pg` (`postgres:16-alpine`, `--restart unless-stopped`) on
  `127.0.0.1:5433`, volume `concierge-pgdata`. Migrated; RLS proven (app connects as
  `concierge_app`, unscoped → 0 rows). Both DB passwords are strong/generated, in `/opt/concierge/.env`.
- **nginx:** vhost `/etc/nginx/sites-enabled/app.quietdesks.com` → `127.0.0.1:8000`, TLS via
  certbot (cert `/etc/letsencrypt/live/app.quietdesks.com/`, expires 2026-10-22, auto-renew set).
- **Health:** `curl https://app.quietdesks.com/healthz` →
  `{"status":"ok","sending_configured":true,"inbound_auth_configured":true,"inbound_domain":"inbox.quietdesks.com"}`.
- **To redeploy code:** rsync `/workspaces/concierge/` → `root@38.49.216.59:/opt/concierge/`
  (exclude `.git .env __pycache__ .venv`), `chown -R concierge`, `systemctl restart concierge`.

**Security TODO (hardening, Phase 9):** the box root password is the leaked one — rotate it and move
to SSH-key auth for a dedicated deploy user. Postgres and the app port are localhost-only, which
bounds the exposure for now, but the root password is the outstanding item.

### What Phase 6 added

`contracts/ReceiptAnchor.sol` (minimal Solidity: `anchorReceipt(bytes32)` → event + storage
write, compiled with Foundry), `concierge/xlayer.py` (signs and sends real transactions against
X Layer mainnet — RPC transport on stdlib `urllib`, signing via `eth_account`, the one dependency
this phase adds and the reasoning for taking it is in the module docstring), new functions in
`receipts.py` (`anchor()`, `recover_signer()`), `store.mark_anchored`, `verify_receipts.py`.

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
real chain credentials now present in `.env`, by `verify_engine.py`, `verify_email.py` and
`verify_booking.py`, and a gate run must never have the side effect of spending real mainnet gas.
Only the actual FastAPI process spends gas, and only on a real inbound email. Confirmed by a
direct call to `_anchor_in_background` (NULL → real signature + real tx) and a mocked
`TestClient` request proving the thread only fires under the right conditions — not yet proven
against a real Postmark-delivered email, since that still needs item 3.

One finding not in any doc: **the public RPC (`rpc.xlayer.tech`) is eventually consistent across
nodes** — an `eth_call` issued immediately after a confirmed write can hit a node that hasn't
seen it yet. `verify_receipts.py` polls rather than reading once; production code anchoring
synchronously would need the same care.

`XLAYER_PRIVATE_KEY` and `XLAYER_CONTRACT` are in `.env` (gitignored). The signer
(`0x69eb1bAA26BffCD0fA9089aa2187F6Ca3e2A54f6`) holds only gas money (~0.0103 OKB after the
deploy) — never a key holding meaningful assets, per OPERATOR_PROVIDES' own advice.

### What Phase 3 added

`pricing.py` (quote derivation), `guardrails.py` (negotiation bounds), `lexicon.py` (the
tenant's own nouns), `receipts.py` (hashing + tamper detection), `engine.py` (the state
machine), `verify_engine.py`.

The design decision worth knowing before touching any of it: **the engine is trade-neutral, and
the words are the tenant's.** Pricing reads a canonical vocabulary — `pricing_rules.headline` /
`floor` / `max_discount` — that vertical templates map onto via `Field.maps_to`, so a trade with
no template quotes exactly as well as one with. And every noun in an outbound message comes from
the profile rather than from a string literal, so a dentist says "consultation" and an estate
agent says "viewing" without either word appearing in the code. the engine suite check 2 proves it with a
veterinary practice; check 3 greps `engine.PROSE` against `engine.TRADE_NOUNS` to stop the
regression. See CLAUDE.md for the rule in full.

Booking is real state machine work against a **declared fixture calendar** that lives in the
harness, not the package. The production default is `engine.NoCalendar`, which refuses rather
than inventing times — check 12.

## What is left

### Phase 4 — email connector (Postmark) · DONE + LIVE, proven 2026-07-25
Built and passing the email suite (8/0/3): inbound parse, tenant resolution from the recipient address,
outbound send with the AI disclosure as the first line, webhook authenticity, email threading.
Two reality corrections from the build spec, both in the ledger: **Postmark inbound has no HMAC
signature** — authenticity is HTTP Basic Auth carried in the webhook URL, which `mail.check_webhook_auth`
verifies (it checks the **password half** only; username is ignored); and the sending domain is
**`inbox.quietdesks.com`** (replies come FROM the inbox subdomain), so that is the domain to verify
in Postmark, not the apex.

**LIVE round-trip proven 2026-07-25.** A real email from `jennyoliver630@gmail.com` to the test
tenant `halcyon-rooms@inbox.quietdesks.com` was received by Postmark, POSTed to the webhook on the
VPS (`POST /inbound/postmark → 200`, twice), resolved to the tenant, and answered — Postmark
outbound shows 2 replies `Sent` from `halcyon-rooms@inbox.quietdesks.com` to the Gmail, landing in
the inbox (not spam, because DKIM + Return-Path on `inbox.quietdesks.com` are verified). All
operator items for Phase 4 are in: item 1 (VPS) deployed, item 2 (domain+DNS) done, item 3
(Postmark) approved + configured.

**Two go-live gotchas worth remembering** (both cost time on 2026-07-25):
1. **Postmark needs `InboundDomain` set** to `inbox.quietdesks.com` on the server (not just the MX +
   webhook). Without it Postmark rejects mail to `<slug>@inbox.quietdesks.com` and `TotalCount` of
   received messages stays 0. Set via the API: `PUT https://api.postmarkapp.com/server` with
   `{"InboundDomain":"inbox.quietdesks.com"}` and `X-Postmark-Server-Token`, or in the Inbound
   Stream → Settings UI. It 610s until the MX is clean (next point).
2. **The MX value had a trailing comma** (`inbound.postmarkapp.com,`) — pasted from Postmark's
   instruction sentence. Postmark's `InboundDomain` check failed with error 610 until the comma was
   removed so the MX target is exactly `inbound.postmarkapp.com`.

Diagnosing inbound is fastest via the Postmark API with the server token, not the UI:
`GET /server` (shows `InboundHookUrl`, `InboundDomain`), `GET /messages/inbound?count=5&offset=0`
(did mail arrive), `GET /messages/outbound?count=5&offset=0` (did a reply send — `offset` is
required). Box-side: `journalctl -u concierge | grep inbound/postmark`.

The **test tenant** `halcyon-rooms@inbox.quietdesks.com` (Halcyon Rooms spa, owner
`jennyoliver630@gmail.com`, services deep-tissue massage £85 / signature facial £70) lives on the
live DB — reuse it for demo footage, or onboard a fresh one.

### Phase 5 — booking (Cal.com) · DONE, the booking suite passed 2026-07-23
`calcom.py` fills the `engine.Calendar` seam with live Cal.com v2 calls; `verify_booking.py`
runs the full NEW→BOOKED journey against the real API, creates a real booking (UTC start, nested
attendee, prospect timezone), confirms it by the API's own `accepted` status, and **cancels it**
so a real calendar is left clean. Versions pinned: slots `2024-09-04`, bookings `2026-02-25`
(ledger proves a stale pin silently downgrades). Credentials come from `profile.calendar_ref`
with a `CAL_API_KEY`/`CAL_EVENT_TYPE_ID` env fallback for the single-operator demo. Event type
6433300, connected to a Google Calendar. **The Cal.com key is `cal_live_` and was exposed in
chat — rotate before submission.**

### Phase 6 — receipts on X Layer **mainnet (196)** · DONE, the receipts suite passed 2026-07-24
`xlayer.py` fills the anchoring seam with live X Layer calls against a deployed `ReceiptAnchor`
contract; `verify_receipts.py` runs a real conversation through the real engine, anchors the
resulting quote receipt and a floor-breach receipt, confirms both on-chain independently, and
red-teams a decision tamper and a signature-forgery attempt. Real measured gas: 224,160 for the
one-time deploy, 51,849 per anchor — both cheaper than the pre-deploy estimates in ledger §9.
**Not yet wired into the live request path** — `engine.step` still writes `signature`/`xlayer_tx`
as NULL; a background worker calling `receipts.anchor()` on unanchored rows is Phase 8 territory
(see "Workers" in CLAUDE.md §12), so replies stay fast and are never blocked on a mainnet
confirmation.

### A2A escrow — A2A escrow + settlement · BLOCKED on operator item 5 **and ledger U3**
U3 (the OKX escrow API call signatures) is unresolved — the OnchainOS docs cover wallet install
but not the escrow credentials. **No escrow code may be written against a guessed API shape.**
Resolve U3 first.

**Two different on-chain identities — do not conflate them.** (a) The **X Layer signer** (operator
item 6, `XLAYER_PRIVATE_KEY` in `.env`, addr `0x69eb…`) is a plain key holding ~0.01 OKB of gas
that signs and anchors *receipts* — **already provided**, which is why the receipts suite passes live. (b) "The
wallet" that A2A escrow waits on is the **OKX Agentic Wallet** (operator item 5) — the *A2A identity*
that funds/receives escrow and settles USDT/USDG, created via `npx skills add okx/onchainos-skills`
+ a creation email, keys in a TEE. **Missing.** The receipts wallet is done; the escrow wallet is not.

**U3 breaks into two unknowns, and the doc-research half is UNBLOCKED (needs no wallet):** (1) where
`OKX_API_KEY`/`OKX_SECRET_KEY`/`OKX_PASSPHRASE` come from (likely the OKX developer portal —
unconfirmed), and (2) the exact `a2a-pay` call signatures + USDT-vs-USDG settlement + dispute
mechanics. *Installing and reading* the skills package (`npx skills add okx/onchainos-skills`, then
its `SKILL.md` files for `okx-agent-payments-protocol` / `okx-ai`) needs no wallet — only *running*
an escrow CLI does — so the shape can be verified from the vendor's own shipped docs (not guessed,
which A2A escrow forbids) and the escrow module written ready-to-test. A second live source: the shared
VPS already has `okx-a2a` installed and running (`/usr/local/bin/okx-a2a`). What still needs the
wallet + credentials + funded USDT: any live call, and therefore the whole the escrow suite round-trip.

### VPS deploy — move NOW, don't wait for Postmark (operator item 1)
Nothing is deployed yet; everything runs locally in the codespace. **Almost none of the deploy
depends on Postmark** — waiting compresses all the risk into the final hours. Candidate box on
record: `38.49.216.59` (`Jennycruzy`, shared — see the `vps-shared-box` note: dedicated non-root
user, CONCIERGE's OWN RLS Postgres not kitchen-copilot's :5432, free port 8000, and rotate the
leaked root password first).

- **Deployable and health-checkable today (no Postmark):** the dedicated user + own RLS Postgres,
  the FastAPI app under systemd, nginx vhost + TLS for `app.quietdesks.com` (needs only one
  Cloudflare A record `app.quietdesks.com → 38.49.216.59`, which the operator controls — not
  Postmark), and the scheduler timer (follow-ups, anchoring sweep, summaries) against the live
  X Layer creds.
- **The ONLY step that waits for Postmark:** the inbound-email round-trip — paste
  `POSTMARK_SERVER_TOKEN`, add MX/DKIM DNS, point the webhook, run the "email lands, not spam" test.

So: deploy the infrastructure now so go-live is 3 steps (token + MX/DKIM + webhook) when Postmark
approves. Blocker on the agent side: no SSH access from the codespace — the operator either grants a
key or runs the deploy commands via `! <cmd>`. The full go-live gate runs are deferred to when
Postmark is live (operator's stated plan), but the deploy itself is not. Steps: the Phase 4 go-live
checklist above.

### Phase 8 — summary + scheduled actions · DONE, the scheduler suite passed 2026-07-24
`concierge/summary.py` (pure arithmetic over `store.list_threads`/`list_receipts` — inquiries,
quotes, negotiations, bookings + value, escalations with verbatim text, Feature-2 queued-for-
approval count, Safe Follow-Up nudges and DEAD threads) and `concierge/scheduler.py` (the one
per-tenant entry point: `anchor_pending()` finally calls `receipts.anchor()` on unanchored rows —
the exact gap this file used to describe — `followup.process_tenant` on a schedule instead of on
demand, and a periodic summary send gated by `profile.summary_policy.last_sent_at` so it fires
once per period, not every run). the scheduler suite proves the numbers against real conversations this gate
runs, and proves the anchoring job's honest no-credentials skip *without* spending any new real
mainnet gas — the receipts suite/6b already prove the anchoring mechanism itself, repeatedly.

**The worker entry point now exists (2026-07-25).** `scheduler.run_all()` +
`python3 -m concierge.scheduler` (`--tenant`, `--dry-run`; one JSON line per run to stdout, so
`journalctl -u concierge-scheduler` is greppable), with `deploy/concierge-scheduler.{service,timer}`
(oneshot, every 15min, `Persistent=true`). Enumerating tenants needed a fourth deliberate door —
`scheduler_tenant_ids()`, granted to a new enumeration-only `concierge_worker` role and REVOKEd
from `concierge_app`; see CLAUDE.md for why the split is load-bearing. the scheduler suite is now **9 pass**
(checks 8 and 9 are new). **Still a deploy action: installing the timer on the VPS** — that needs
`WORKER_DATABASE_URL` in `/opt/concierge/.env` and a real password for `concierge_worker` (the
schema creates it with a literal dev password, same known gap as `concierge_app`).

Not yet built: end-to-end proof against live Phase 4 email
+ A2A escrow A2A data, which is blocked on those phases the same way it always was. Feature 1
(product-gap intelligence) attached here and is **done** (the product-gaps suite) — see the Feature addendum
section above for what it added and the invalid-LLM-key finding.

### Phase 9 — hardening + submission
Public repo ✓, OSI licence ✓, CREDITS ✓, ledger ✓, operator-provides ✓. Still needed: ~90s demo
video, architecture diagram, "reproduce every claim" README pass, Google form before the deadline.

---

## The critical path is not code

**7 of 8 operator items are now in: 1 (VPS, deployed), 2 (domain+DNS), 3 (Postmark, live), 4
(Cal.com), 6 (X Layer signer), 7 (LLM key — fixed 2026-07-25; the old value was truncated and 401'd,
the full `sk-ant-…` now works, so Feature 1's optional gap categorization is live).** Only **item 5
(OKX Agentic Wallet)** remains, which — with ledger U3 — is what still blocks A2A escrow. Item 8
(web-search) stays optional/skipped. Full instructions in `docs/OPERATOR_PROVIDES.md`.

**Phase 4 is LIVE as of 2026-07-25** — Postmark approved, the app deployed on the VPS, DNS + DKIM +
Return-Path + MX + InboundDomain all set, and a real email round-trip proven end to end (see the
Phase 4 section for the evidence and the two go-live gotchas). Booking, receipts, and now email all
have real live footage for the demo. **A2A escrow** is the only remaining blocked phase — gated on the
OKX Agentic Wallet (item 5) plus resolving ledger U3 (the doc-research half of U3 is unblocked — see
the A2A escrow section).

**What's left is now mostly Phase 9 (submission):** the ~90s demo video, architecture diagram, the
"reproduce every claim" README pass, and the Google form before the deadline — plus the security
hardening TODO (rotate the VPS root password, move to SSH-key auth) and rotating the exposed
`cal_live_` Cal.com key. A2A escrow (+ Feature 4) only if item 5 + U3 land in time.

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
   from any state (the engine suite checks 13 and 14).
7. **No trade vocabulary in `engine.PROSE`.** Domain nouns come from `profile.lexicon` and
   `profile.services`, never from a string literal. the engine suite check 3 greps for the regression.
8. **A floor breach never receives a counter-offer.** Countering at the floor publishes the
   tenant's reservation price; the breach escalates and no figure is sent (the engine suite check 7).
9. **Nothing is claimed as booked without the calendar API confirming it**, and with no calendar
   connected the engine escalates rather than inventing an appointment (the engine suite check 12).
10. **No confidence score, floor-curve point or benchmark aggregate comes from a language
    model.** `confidence.py` is arithmetic over three named, stored signals; it may only decide
    whether a reply sends or queues, never what the reply says (the autonomy suite).

## Open questions for the operator

- **`CLAUDE.md` is a visible tell in a public repo.** Rename to `CONVENTIONS.md`? Cost: it stops
  being auto-loaded as repo conventions, so `docs/HANDOFF.md` would have to carry them. Undecided.
- Ledger **U3** (OKX escrow API shape) blocks all of A2A escrow and needs resolving before that phase
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
