# HANDOFF

For a session starting cold, or a human picking this up. Current as of **2026-07-23**.

## Conventions that are not negotiable

- **All commits authored by `holybunnie`** — `122739099+holybunnie@users.noreply.github.com`.
  Set locally already. See `CLAUDE.md`.
- **No AI attribution anywhere** — no `Co-Authored-By` trailers, no "generated with" footers, no
  tool credit in commits, PR bodies, README or CREDITS. `holybunnie` is the sole author of record.
  The five original commits were rewritten to comply; `backup-pre-author-rewrite` holds the
  originals until the first push is confirmed good, then delete it.

## Where the build actually is

Phases 0, 1 and 2 are done and gated. Phase 3 is next and needs nothing from the operator.

```bash
pip install -r requirements.txt
docker compose up -d postgres
python3 verify.py --phase 0      # 9 pass / 2 info — live Cal.com + X Layer calls
python3 verify.py --phase 1      # 11 pass — 9 of them attacks on tenant isolation
python3 verify.py --phase 2      # 11 pass / 1 info — three verticals onboarded
```

All three must stay green. They make real network calls and run real SQL; there are no fixtures
or mocks anywhere in them, and a network failure is reported as FAIL rather than passed from cache.

| Phase | State | What unblocks it |
|---|---|---|
| 0 Foundations + verification | done | — |
| 1 Tenant model + RLS isolation | done | — |
| 2 Vertical-aware onboarding | done | — |
| **3 State machine + guardrails** | **next — decisive gate** | nothing |
| 4 Email connector (Postmark) | blocked | operator items 1, 2, 3 |
| 5 Booking (Cal.com) | blocked | operator item 4 |
| 6 Receipts on X Layer mainnet | written but unprovable | operator item 6 |
| 7 A2A escrow + settlement | blocked | operator item 5, ledger U3 unresolved |
| 8 Summary + product-gap intel | not started | needs 4-7 |
| 9 Hardening + submission | not started | deadline **2026-07-27 22:59 UTC** |

## The critical path is not code

**0 of 8 operator credentials have arrived.** The binding constraint is Postmark: it manually
reviews new accounts before they may send outside domains you own, stated at under 24h on weekdays
and longer at weekends. The domain purchase and the Postmark approval request must happen first;
everything else can slip a day. Full instructions in `docs/OPERATOR_PROVIDES.md`.

Phases 4 and 5 are the ones the demo video actually shows, and both are credential-blocked.

## Invariants that are already proven — do not regress them

Each of these has a check in the harness that will fail loudly if broken.

1. **No price from a language model.** Everything quotable derives from the tenant's stored
   profile through code. Not in the profile → escalate.
2. **`store.py` contains no `WHERE tenant_id = ?` clause, deliberately.** Isolation is PostgreSQL
   row-level security. Adding predicates is not a fix; the absence is the proof. Never grant the
   app role table ownership or `BYPASSRLS`.
3. **A template example can never become tenant data.** `onboarding.build_profile()` reads
   `self.answers` and must not reach `Field.example`.
4. **Receipts anchor on X Layer mainnet (196), never a testnet.** Ledger §9 settles the cost
   argument: one anchor is ~$0.0001.
5. **No fabricated credential, and no placeholder that looks live.** A missing domain yields
   `PENDING-DOMAIN.invalid` (RFC 2606 — can never resolve).
6. **AI disclosure in the first line of every outbound message**, with a route to a human.

## Known gaps, stated plainly

- The vertical lexicon knows three trades. A dentist or plumber falls to the generic template.
  This is where an LLM would earn its keep once operator item 7 lands.
- No concurrency test on `SET LOCAL` scoping under a connection pool. The mechanism is proven;
  the concurrent case is not.
- The app role's password is literal in `schema.sql`. Fine for a local container, **wrong for the
  VPS** — must become an env-supplied secret before Phase 4 exposes anything publicly.
- Ledger U3 (OKX escrow API shape) is unresolved and blocks all of Phase 7. No escrow code may be
  written against a guessed API shape.
