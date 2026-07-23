# HANDOFF

Resume point for a session starting cold. **Current as of 2026-07-23 02:13 UTC.**

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
```

All four must be green before writing new code. They make real network calls and run real SQL —
no fixtures, no mocks, with one declared exception: GATE 3's calendar, which is a fixture living
in the harness rather than the package, named as such in every check that uses it. A network
failure reports FAIL rather than passing from cache.

**Then: Phase 4 or 5 the moment credentials land.** Everything unblocked has been built.

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

### Phase 4 — email connector (Postmark) · BLOCKED on operator items 1, 2, 3
Inbound webhook + signature verification, tenant resolution from the recipient address, outbound
send with AI disclosure as the first line, SPF/DKIM/DMARC. **Until item 2 (domain) lands, every
tenant's inbound address is `<slug>@PENDING-DOMAIN.invalid` and nobody can email them at all.**

### Phase 5 — booking (Cal.com) · BLOCKED on operator item 4
Ask the prospect's timezone explicitly (never infer), fetch slots in their timezone, apply the
tenant's booking rules, offer 3, **re-fetch on selection** to catch the slot race, POST with UTC
start + nested attendee, confirm status via the API response. Pin `cal-api-version` — ledger §1
proves a stale pin silently downgrades to a different validation schema rather than erroring.

### Phase 6 — receipts on X Layer **mainnet (196)** · BLOCKED on operator item 6
Contract, signing, content hashing, tamper detection can all be written and unit-tested now;
deployment and real anchoring need a funded signer. Ledger §9: one anchor ≈ $0.0001, 1 OKB ≈
909,000 anchors. **Never anchor to a testnet and present it as proof.**

### Phase 7 — A2A escrow + settlement · BLOCKED on operator item 5 **and ledger U3**
U3 (the OKX escrow API call signatures) is unresolved — the OnchainOS docs cover wallet install
but not the escrow credentials. **No escrow code may be written against a guessed API shape.**
Resolve U3 first.

### Phase 8 — summary + scheduled actions + product-gap intelligence
Needs 4–7 to produce real data.

### Phase 9 — hardening + submission
Public repo ✓, OSI licence ✓, CREDITS ✓, ledger ✓, operator-provides ✓. Still needed: ~90s demo
video, architecture diagram, "reproduce every claim" README pass, Google form before the deadline.

---

## The critical path is not code

**0 of 8 operator credentials have arrived.** Full instructions in `docs/OPERATOR_PROVIDES.md`.

The binding constraint is **Postmark**: it manually reviews new accounts before they may send
outside domains you own — stated at under 24h on weekdays, longer at weekends. It is Thursday.
The domain purchase and the Postmark approval request must happen **first**; everything else can
slip a day. Phases 4 and 5 are what the demo video actually shows, and both are credential-blocked.

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
