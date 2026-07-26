# Architecture

Three diagrams. The first is what the system is, the second is the one boundary everything else
rests on, and the third is what happens to a single stranger's email — because that path is where
every claim in the README is either true or not.

Every box below is a real module in this repo. Nothing is aspirational: where a capability is
blocked, it is drawn as blocked.

---

## 1. The system

Four ways in, one decision core, one database, and nothing in the core that can reach a network.

```mermaid
flowchart TB
    subgraph clients [" "]
        direction LR
        prospect["A prospect<br/><i>emails the business</i>"]
        buyer["A buying agent<br/><i>funds an OKX A2A job</i>"]
        anyone["Anyone with a link<br/><i>checks a receipt</i>"]
        clock["systemd timer<br/><i>every 15 minutes</i>"]
    end

    subgraph edge ["Edge — the only code that touches a network"]
        direction LR
        webhook["app.py<br/>POST /inbound/postmark<br/><small>Basic-auth, password half</small>"]
        pubpage["app.py<br/>GET /r/{receipt_id}<br/><small>unauthenticated, read-only</small>"]
        daemon["a2a.py<br/><small>okx-a2a CLI transport</small>"]
        worker["scheduler.py<br/><small>python3 -m concierge.scheduler</small>"]
    end

    subgraph routing ["Routing — resolve exactly one tenant before any logic runs"]
        direction LR
        mail["mail.py<br/><small>recipient → tenant</small>"]
        prov["provision.py<br/><small>accepted job → tenant → interview</small>"]
    end

    subgraph core ["Decision core — deterministic, imports nothing that can reach a network"]
        direction TB
        engine["engine.py<br/><b>the state machine</b><br/><small>NEW → QUALIFIED → QUOTED →<br/>NEGOTIATING → BOOKED / ESCALATED</small>"]
        subgraph rules ["the rules engine.step consults, in order"]
            direction LR
            comp["comprehension.py<br/><small>what was actually asked</small>"]
            price["pricing.py<br/><small>the figure, from the profile</small>"]
            guard["guardrails.py<br/><small>floor + floor_curve</small>"]
            conf["confidence.py<br/><small>send, or hold for the owner</small>"]
            lex["lexicon.py<br/><small>the tenant's own nouns</small>"]
        end
        engine --> rules
    end

    subgraph side ["Attached to decisions the core already made"]
        direction LR
        rcpt["receipts.py<br/><small>hash + ECDSA sign</small>"]
        gapsm["gaps.py<br/><small>unmet demand, verbatim</small>"]
        fup["followup.py<br/><small>re-engage existing threads</small>"]
        summ["summary.py<br/><small>arithmetic over stored rows</small>"]
    end

    subgraph out ["Outbound — real external systems"]
        direction LR
        pm["Postmark<br/><small>the reply, the owner alert</small>"]
        cal["Cal.com v2<br/><small>calcom.py — real bookings</small>"]
        chain["X Layer mainnet 196<br/><small>xlayer.py → ReceiptAnchor</small>"]
        llm["Anthropic API<br/><small>gaps.py only — optional</small>"]
    end

    db[("PostgreSQL 16<br/><b>row-level security</b><br/><small>tenants · threads · receipts · gap_events</small>")]

    prospect --> webhook --> mail --> engine
    buyer --> daemon --> prov --> engine
    anyone --> pubpage
    clock --> worker

    worker --> fup --> engine
    worker --> rcpt
    worker --> summ
    worker --> gapsm

    engine --> side
    core <--> db
    routing <--> db
    side <--> db
    pubpage --> db

    engine --> pm
    engine --> cal
    rcpt --> chain
    gapsm -.->|"absent key → raw text,<br/>never a fabricated label"| llm

    escrow["A2A escrow + settlement<br/><small>BLOCKED — OKX Agentic Wallet, ledger U3</small>"]
    buyer -.-> escrow

    classDef blocked stroke-dasharray: 5 5,color:#888
    class escrow,llm blocked
```

**The line that matters** is the one between `core` and `out`: `pricing.py`, `guardrails.py`,
`comprehension.py`, `confidence.py` and `lexicon.py` import nothing that could reach a network.
A price is arithmetic over the tenant's stored profile or it does not exist. The single LLM
consumer in the whole codebase is `gaps.py`, and it runs *after* the fact, on a schedule, to put a
coarse label on an escalation that already happened — with no key it returns `None` and the owner's
summary shows the prospect's raw words instead.

---

## 2. The isolation boundary

`store.py` contains no `WHERE tenant_id = ?` clause anywhere. That absence is the proof, not an
oversight: isolation is a PostgreSQL policy on a transaction-scoped setting, so a query that forgets
to scope itself returns **zero rows**, never someone else's.

Which leaves one problem — resolution has to happen *before* a scope exists. That gap is crossed by
five named `SECURITY DEFINER` functions and nothing else.

```mermaid
flowchart LR
    subgraph unscoped ["No tenant resolved yet"]
        addr["an email recipient<br/><small>halcyon-rooms@inbox…</small>"]
        job["an A2A job id"]
        eng["an engagement ref"]
        rid["a receipt id from a link"]
        tick["a scheduler tick<br/><small>nothing hands it a tenant</small>"]
    end

    subgraph doors ["The five deliberate doors — schema.sql"]
        d1["resolve_tenant_by_inbound_address()<br/><small>→ one opaque uuid</small>"]
        d2["resolve_tenant_by_a2a_job()<br/><small>→ one opaque uuid</small>"]
        d3["resolve_tenant_by_engagement()<br/><small>→ one opaque uuid</small>"]
        d4["public_receipt()<br/><small>→ ≤1 curated row,<br/>never tenant_id/thread_id</small>"]
        d5["scheduler_tenant_ids()<br/><small>→ uuids and nothing else</small>"]
    end

    subgraph scoped ["SET LOCAL app.tenant_id — every policy keys on this"]
        rls["tenant_isolation<br/>ON tenants · threads · receipts · gap_events"]
    end

    addr --> d1 --> rls
    job --> d2 --> rls
    eng --> d3 --> rls
    rid --> d4
    tick --> d5 --> rls

    rls --> rows[("the one tenant's rows")]
    d4 --> one[("one receipt,<br/>if it is a public commitment")]

    noscope["no scope set"] --> rls
    rls -.-> zero["0 rows"]
    noscope -.-> zero
```

**Two roles, and neither can do the whole job.**

| | `concierge_app` — internet-facing | `concierge_worker` — the timer |
|---|---|---|
| table grants | SELECT/INSERT/UPDATE, under RLS | **none at all** |
| `scheduler_tenant_ids()` | REVOKEd | EXECUTE |
| can pin `app.tenant_id` | yes | — |
| owns tables / BYPASSRLS | never | never |

The webhook role can read a tenant it has resolved but cannot enumerate tenants; the worker role can
list every tenant id and do nothing with them. Per-tenant work goes back through the normal
RLS-fenced session as `concierge_app`. A compromise of the public webhook is therefore not "read
every tenant" — which is exactly what a single role holding both capabilities would have made it.

---

## 3. One stranger's email, end to end

The path all of the above exists to make safe. Note where it *stops*.

```mermaid
sequenceDiagram
    autonumber
    participant P as Prospect
    participant PM as Postmark
    participant A as app.py
    participant M as mail.py
    participant E as engine.py
    participant DB as Postgres, RLS-fenced
    participant X as X Layer 196
    participant O as Owner

    P->>PM: "How much for a deep-tissue massage for my cat?"
    PM->>A: POST /inbound/postmark (Basic auth)
    A->>A: check_webhook_auth — password half only
    A->>M: parsed inbound document
    M->>DB: resolve_tenant_by_inbound_address(recipient)
    DB-->>M: one opaque uuid (or nothing → refuse)
    M->>DB: SET LOCAL app.tenant_id — every read below is fenced
    M->>E: step(tenant, thread, message)

    E->>E: human requested? → escalate, before anything else
    E->>E: comprehension: which words did the service match NOT consume?
    Note over E: "for my cat" — a qualifier this profile has no rule for
    E->>E: pricing: the figure from the profile, or Unquotable
    E->>E: guardrails: floor / floor_curve — most restrictive binds
    E->>E: confidence: 0.85 vs 0.55 threshold — but 75% comprehension
    Note over E: below the 85% floor → CAP autonomy. Drafted, not sent.

    E->>DB: thread AWAITING_OWNER_APPROVAL + receipt (signature NULL for now)
    E->>DB: gap_events — the prospect's verbatim words
    E-->>P: nothing. No figure reaches the client.
    E->>O: "a reply is waiting for you"

    Note over A,X: after the response is already returned — never blocking it
    A->>X: _anchor_in_background → anchorReceipt(bytes32)
    X-->>A: poll until status 0x1 — never assumed from a broadcast
    A->>DB: mark_anchored(tx)
```

Steps 9–14 are the whole product. A quote that clears every check sends itself, carries the AI
disclosure on line one and a link to its own public receipt, and is anchored on mainnet moments
later. A quote that does not clear them goes to the owner instead — and the *reason* it stopped is
recorded on the receipt as three named signals with their weights, not as a model's opinion of its
own certainty.

---

## Where each claim is proven

Every box above is exercised by a suite that prints its raw evidence. `python3 verify.py --suite <name>`:

| Diagram element | Suite |
|---|---|
| the RLS boundary, the doors, the two roles | `isolation`, `scheduler` (checks 8–9) |
| profile → questions → stored profile | `onboarding` |
| the state machine and its guardrails | `engine` |
| comprehension caps autonomy | `comprehension` |
| confidence sends or holds | `autonomy` |
| the decaying floor | `floor-curve` |
| re-engagement, never cold outbound | `follow-up` |
| Postmark in and out | `email` (live round-trip proven 2026-07-25) |
| Cal.com — a real booking, then cancelled | `booking` |
| X Layer mainnet anchoring + tamper attacks | `receipts` |
| the public receipt page and what it refuses | `public-receipts` |
| the timer's three jobs | `scheduler` |
| unmet demand, verbatim | `product-gaps` |
| accepted A2A job → working tenant, unattended | `provisioning` |
