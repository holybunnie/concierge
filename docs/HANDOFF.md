# HANDOFF

Resume point for a session starting cold. **Current as of 2026-07-23 02:13 UTC.**

**Deadline: 2026-07-27 22:59 UTC — 4 days 20 hours remain.**

---

## Start here after a break

```bash
cd /workspaces/codespaces-blank
docker start concierge-pg          # or: docker compose up -d postgres
pip install -r requirements.txt    # if the container was rebuilt
python3 verify.py --phase 0        # expect 9 pass / 0 fail / 2 info
python3 verify.py --phase 1        # expect 11 pass / 0 fail
python3 verify.py --phase 2        # expect 11 pass / 0 fail / 1 info
```

All three must be green before writing new code. They make real network calls and run real SQL —
no fixtures, no mocks. A network failure reports FAIL rather than passing from cache.

**Then: start Phase 3.** It is the decisive gate and needs nothing from the operator.

## Git — already configured, but fragile across codespace rebuilds

Remote: **https://github.com/holybunnie/concierge** (public). `main` tracks `origin/main`.

`git push` works as-is. If it starts returning **403**, the cause is known: GitHub injects a
`GITHUB_TOKEN` into Codespaces that is hard-scoped to the codespace's *own* repo, and both `gh`
and the Codespaces credential helper prefer it over your real credentials — regardless of what
the REST API reports for permissions. The fix:

```bash
GITHUB_TOKEN= GH_TOKEN= gh auth login     # the empty vars are the load-bearing part
```

Web browser → HTTPS → yes to authenticating git. A repo-local credential helper is already
pinned in `.git/config` to route pushes through that stored token.

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

## What is left

### Phase 3 — state machine + deterministic guardrails · NEXT · unblocked
The decisive gate. Needs no credentials, no network, no operator input.
- Thread state machine (§9): NEW → ENGAGED → AWAITING_REPLY → NEGOTIATING → BOOKED, plus
  ESCALATED / IGNORED / DEAD.
- Quote derivation: every figure computed from `profile.services` + `profile.pricing_rules` by
  code, with the derivation recorded so it can be shown, not asserted.
- Negotiation guardrail: counter-offers bounded by `floor_price` / `floor_pct` /
  `max_discount_pct`. A breach forces ESCALATE — it cannot be talked past.
- Unknown-service handling: not in profile → ESCALATE, never invent.
- Driven by fixture inquiries; no channel yet (email is Phase 4).
- **GATE 3 must prove:** a full NEW→BOOKED run; a floor-breach forced to ESCALATE; an
  unknown-service query escalated rather than answered; and that no price came from an LLM,
  shown by derivation.

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

## Open questions for the operator

- **`CLAUDE.md` is a visible tell in a public repo.** Rename to `CONVENTIONS.md`? Cost: it stops
  being auto-loaded as repo conventions, so `docs/HANDOFF.md` would have to carry them. Undecided.
- Ledger **U3** (OKX escrow API shape) blocks all of Phase 7 and needs resolving before that phase
  can start.

## Known gaps, stated plainly

- The vertical lexicon knows three trades. A dentist or plumber falls to the generic template.
  This is where an LLM would earn its keep once operator item 7 lands.
- No concurrency test on `SET LOCAL` scoping under a connection pool. The mechanism is proven;
  the concurrent case is not.
- The app role's password is literal in `schema.sql`. Fine for a local container, **wrong for the
  VPS** — must become an env-supplied secret before Phase 4 exposes anything publicly.
