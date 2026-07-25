# CONCIERGE — working conventions

Read this before touching the repo. The build spec lives in the operator's brief; this file
records only the things a fresh session cannot re-derive from the code.

## Git identity — non-negotiable

**All commits are authored by `holybunnie`.**

```
user.name  = holybunnie
user.email = 122739099+holybunnie@users.noreply.github.com
```

Already set locally (`git config user.name` to confirm). The five pre-existing commits authored
as "CONCIERGE Builder" were rewritten on 2026-07-23; `backup-pre-author-rewrite` holds the
originals until the first push is confirmed good.

**No AI attribution anywhere.** No `Co-Authored-By` trailer, no "generated with" footer, no tool
credit in commits, PR bodies, README or CREDITS. `holybunnie` is the sole author of record.

GitHub account: `holybunnie` (Emerald Bunnie, id 122739099), authenticated via `gh`.

## Phase discipline

Work proceeds in numbered phases, each ending at a GATE. A gate is passed only by
`python3 verify.py --phase N` printing real evidence, plus a written self-audit (§6 of the brief).
The operator cannot read code — the harness output is the entire interface.

Current state and the next action live in **`docs/HANDOFF.md`** — read it first, it is kept
current. Summary:

| Phase | State | Gate |
|---|---|---|
| 0 Foundations + live verification | done | 9 pass / 2 info |
| 1 Tenant model + isolation | done | 11 pass, 9 of them attacks |
| 2 Vertical-aware onboarding | done | 11 pass / 1 info |
| 3 State machine + guardrails | done | 16 pass / 3 info, 10 of them attacks |
| 4 Email (Postmark) | **done + LIVE** (2026-07-25) | 8 pass / 3 info + real inbound→reply round-trip on the VPS |
| 5 Booking (Cal.com) | done | 5 pass / 2 info |
| 6 Receipts on X Layer **mainnet** | done | 8 pass / 1 info |
| 7 A2A escrow + settlement | blocked | operator item 5, ledger U3 |
| 8 Summary + scheduled actions | done | 9 pass / 0 fail |
| 3c Comprehension + qualifier safety | done | 6 pass / 1 info |
| 9 Hardening + submission | not started | deadline 2026-07-27 22:59 UTC |
| 3b-2 Confidence-scored autonomy (addendum Feature 2) | done | 7 pass / 2 info |
| 3b-3 Decaying floor (addendum Feature 5) | done | 4 pass / 1 info |
| 3b-4 Safe Follow-Up (addendum) | done | 3 pass / 1 info |
| 6b Public receipt verification (addendum Feature 3) | done | 6 pass / 1 info |

Feature addendum (Product-Gap Intelligence, Confidence-Scored Autonomy, Public Receipt
Verification, Cross-Tenant Benchmarking, Decaying Floor, Safe Follow-Up) attaches to the phases
above rather than replacing them — see `docs/HANDOFF.md`'s "Feature addendum" section for the
per-feature gate table. `verify.py --phase` takes a string now (`3b-2`, `6b`, … alongside `0`-`6`).

Repo: **github.com/holybunnie/concierge** (public). If `git push` returns 403, see the git
section of `docs/HANDOFF.md` — the Codespaces `GITHUB_TOKEN` shadows real credentials.

## Rules that have already been tested and must not regress

- **No price from a language model.** Prices, floors and commitments derive from the tenant's
  stored profile through code. Not in the profile → escalate, never invent.
- **The engine is trade-neutral; the words are the tenant's.** Two halves, both load-bearing.
  (a) `pricing.py` / `guardrails.py` / `engine.py` read a canonical vocabulary —
  `pricing_rules.headline` / `floor` / `max_discount` — never a trade-specific key like
  `listing_fee_pct`. A vertical template's job is to ask its trade's question and point
  `Field.maps_to` at one of those three. (b) Every noun in an outbound message comes from the
  profile: `{service}` from `services`, `{engagement}` / `{client}` from `profile.lexicon`.
  `engine.PROSE` is the complete set of words CONCIERGE can say and GATE 3 check 3 greps it
  against `engine.TRADE_NOUNS`. Do **not** "fix" bland replies by putting a domain word in
  `PROSE` — a dentist should say "consultation" because their profile says so. GATE 3 check 2
  proves the point with a veterinary practice, a trade that has no template at all.
- **No template example may become tenant data.** `onboarding.build_profile()` reads
  `self.answers` and must never reach `Field.example`. GATE 2 check 9 greps for this.
- **Isolation is Postgres RLS, not application predicates.** `store.py` deliberately contains no
  `WHERE tenant_id = ?` clause. Do not "fix" that by adding them — the absence is the proof.
  Never grant the app role table ownership or `BYPASSRLS`.
- **Enumeration and reading are split across two DB roles, and must stay split.** The scheduled
  worker needs a tenant list (nothing hands it one — no inbound message to scope to), so
  `schema.sql` has a fourth deliberate door, `scheduler_tenant_ids()`, returning opaque uuids and
  nothing else. EXECUTE is granted to `concierge_worker` ONLY and explicitly REVOKEd from
  `concierge_app` and PUBLIC, because `concierge_app` — the internet-facing webhook role — can pin
  `app.tenant_id` to any value, so enumeration in that role would turn a web-app compromise into
  "read every tenant". In exchange `concierge_worker` holds **no table grants at all**; it can list
  ids and do nothing with them, and per-tenant work goes back through the normal RLS-fenced
  `tenant_session` as `concierge_app`. Neither role alone can both list tenants and read rows —
  do not "simplify" this by granting the app role EXECUTE or the worker role SELECT. GATE 8
  checks 8 and 9 prove both halves.
- **Mainnet only for receipts** (X Layer 196). A testnet receipt proves nothing to a customer or
  an arbitrator. Ledger §9 records the measurement that settles the cost argument: one anchor is
  ~$0.0001, so testnet was never buying anything but a weaker proof.
- **Never fabricate a credential or a placeholder that looks live.** Missing domain yields
  `PENDING-DOMAIN.invalid` (RFC 2606), which fails loudly by construction.
- **AI disclosure in the first line of every outbound message**, with a route to a human.
- **No confidence score, floor-curve point or benchmark aggregate comes from a language model.**
  `confidence.py` computes a decision-confidence score from three named, stored signals (profile
  completeness, floor proximity, precedent) via a fixed weighted formula — it may only decide
  whether an already-computed reply sends immediately or queues in `AWAITING_OWNER_APPROVAL` for
  the owner. It never touches the price, the rule, or the state the pricing/guardrail decision
  already produced. GATE 3b-2 proves both directions (thin profile queues, complete + precedent-
  rich profile sends) and re-proves GATE 3's own NEW→BOOKED journey is unaffected.
- **A price is never sent for a question the profile cannot answer.** `comprehension.py` (GATE 3c)
  adds the two reads `pricing.match_service` never had: which words the client spent that the
  service match did NOT consume (`QUALIFIERS` — quantity/duration/location/timing, generic
  commercial English, never trade vocabulary), and what is being asked ABOUT the service
  (`INTENT_CUES` — price/duration/suitability/logistics). A qualifier the profile has no rule for
  escalates and is recorded as a product gap; a suitability question ALWAYS escalates, even when
  the profile looks like it could answer; a duration question is answered from
  `services[].duration_min` rather than with a price. Layer 3 is the backstop for the qualifiers
  nobody enumerated: `confidence.COMPREHENSION_FLOOR` caps autonomy when the share of the client's
  own words we can account for falls below 85% — a CAP, never a fourth weighted term, so Feature
  2's calibration is untouched and comprehension can only ever withhold a send. Do not "fix" a
  queued reply by lowering that floor. GATE 3c proves 0 wrong prices AND >=85% autonomy on
  answerable questions — both directions, because escalating everything would pass the first
  trivially and be worthless.

- **A decaying floor (`pricing_rules.floor_curve`) is OPTIONAL and never inferred.** Absent, a
  tenant negotiates on the flat floor exactly as before. Set, the curve only controls how fast
  CONCIERGE may move toward the absolute floor as rounds/days pass — the absolute floor itself is
  a hard clamp inside `pricing.floor_curve_value`, not a convention callers have to honor. Nothing
  adjusts the curve mid-negotiation, ever — not a "the conversation feels promising" exception.
  GATE 3b-3 proves point-by-point tracking, red-teams the absolute floor six rounds past where the
  curve runs out, and re-proves a curve-less tenant is untouched.
- **Safe Follow-Up re-engages existing threads only — cold outbound is explicitly out of scope.**
  `followup.dispatch` reads a tenant's existing threads (`store.list_threads`); there is no
  function anywhere in this codebase that accepts a bare email address and sends an introduction.
  `followup._has_real_contact` enforces this on the thread's own stored history (a real `direction:
  in` entry must exist), not on `state == AWAITING_REPLY` alone or on trust in the caller. If asked
  to build cold outbound, decline and point at `concierge/followup.py`'s module docstring — GATE
  3b-4 check 3 proves the boundary holds even for a thread pushed 10 years past any quiet/dead
  threshold, as long as it never received an inbound message from that contact.
- **The public receipt page shows only commitments, and only by exact receipt_id.** `/r/{id}`
  (`app.py`) is scoped by `schema.sql`'s `public_receipt` function, which returns at most one row
  and never `tenant_id`/`thread_id`. `receipts.public_view` further whitelists which actions are
  eligible (`PUBLIC_ACTIONS` — quotes, negotiated counters, bookings); anything else — a floor
  breach, an escalation, a Feature-2-queued draft — renders the identical "not found" page a
  nonexistent or malformed id gets. Do not "fix" a missing field on that page by widening
  `PUBLIC_ACTIONS` or by returning `tenant_id`/`thread_id` from `public_receipt` — GATE 6b's
  check 5 anchors a real floor-breach receipt and proves it is unreachable by its own real id.
- **Product-Gap Intelligence is instrumentation on an existing decision, never a new one.** A
  `gap_events` row is written by `engine.step` as ONE side effect of Phase 3's existing
  `Unquotable`→ESCALATE transition — `engine.decide` sets `Decision.product_gap` only on that
  branch, never on a floor breach, a human request, or a tripped trigger (recording "too cheap" or
  "wants a person" as unmet market demand would be a lie). `gap_events` carries the same
  `tenant_isolation` RLS policy as every other tenant table — do not add a new isolation mechanism
  for it. Categorization (`gaps.classify_gap`) is OPTIONAL enrichment that runs later on a schedule;
  absent or invalid `LLM_API_KEY` it returns `None` and the summary shows the gap as raw, verbatim
  text — never a fabricated category, never silently dropped. GATE 8b-1 proves all of this, and its
  check 5 proves the honest no-key degradation deterministically.
- **The summary is arithmetic over stored receipts, never a separate ledger that could drift.**
  `summary.build_summary` counts the same rows every other gate already writes and verifies — no
  parallel bookkeeping. `scheduler.anchor_pending`/`followup.process_tenant` calls, run on a
  schedule, feed the exact receipts the summary counts, so "what the scheduler did" and "what the
  owner reads" cannot disagree. GATE 8 proves this end to end and deliberately spends no NEW real
  mainnet gas doing it — the anchoring mechanism itself is GATE 6/6b's job, proven repeatedly.

## Local setup

```bash
pip install -r requirements.txt
docker compose up -d postgres        # RLS is why this is Postgres and not SQLite
python3 verify.py --phase 0|1|2
```

## Where things are recorded

- `docs/VERIFICATION_LEDGER.md` — every external fact with its live proof and date, including the
  places reality differed from the build spec. No fact enters without a live call.
- `docs/OPERATOR_PROVIDES.md` — the 8 credentials, what each blocks, and how to obtain them.
- `docs/HANDOFF.md` — current state and the next action, for a session starting cold.
