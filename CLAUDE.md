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

| Phase | State | Gate |
|---|---|---|
| 0 Foundations + live verification | done | 9 pass / 2 info |
| 1 Tenant model + isolation | done | 11 pass, 9 of them attacks |
| 2 Vertical-aware onboarding | done | 11 pass / 1 info |
| 3 State machine + guardrails | **next** | decisive; needs no credentials |
| 4 Email (Postmark) | blocked | operator items 1-3 |
| 5 Booking (Cal.com) | blocked | operator item 4 |
| 6 Receipts on X Layer **mainnet** | blocked | operator item 6 |
| 7 A2A escrow + settlement | blocked | operator item 5, ledger U3 |
| 8 Summary + scheduled actions | not started | |
| 9 Hardening + submission | not started | deadline 2026-07-27 22:59 UTC |

## Rules that have already been tested and must not regress

- **No price from a language model.** Prices, floors and commitments derive from the tenant's
  stored profile through code. Not in the profile → escalate, never invent.
- **No template example may become tenant data.** `onboarding.build_profile()` reads
  `self.answers` and must never reach `Field.example`. GATE 2 check 9 greps for this.
- **Isolation is Postgres RLS, not application predicates.** `store.py` deliberately contains no
  `WHERE tenant_id = ?` clause. Do not "fix" that by adding them — the absence is the proof.
  Never grant the app role table ownership or `BYPASSRLS`.
- **Mainnet only for receipts** (X Layer 196). A testnet receipt proves nothing to a customer or
  an arbitrator. Ledger §9 records the measurement that settles the cost argument: one anchor is
  ~$0.0001, so testnet was never buying anything but a weaker proof.
- **Never fabricate a credential or a placeholder that looks live.** Missing domain yields
  `PENDING-DOMAIN.invalid` (RFC 2606), which fails loudly by construction.
- **AI disclosure in the first line of every outbound message**, with a route to a human.

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
