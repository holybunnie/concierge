"""the receipts suite — receipts signed and anchored on X Layer mainnet (196), for real.

The receipts anchored here are not hand-built for the harness — they are `Outcome.receipt` as
`engine.step` actually writes it in production (same as the engine suite), so what gets anchored is
exactly what a real conversation produces. Two independent proofs are then checked: an offline
signature over the content hash (verifiable by anyone who knows the operator's address, no RPC
needed), and an on-chain transaction anchoring the same hash in `ReceiptAnchor` (verifiable by
anyone, forever, on a public explorer). Mainnet only — see docs/VERIFICATION_LEDGER.md §9. Every
gas figure, tx hash and block number below is read back from a live RPC call made during this
run, never carried over from an earlier deploy.
"""

from __future__ import annotations

import time
import uuid

from eth_utils import keccak

from . import config, db, engine, onboarding, receipts, store, xlayer
from .xlayer import ANCHORED_EVENT_TOPIC

PROSPECT = "nadia.okoro@example.com"

SPA = dict(
    description="We run a day spa offering massage, facials, waxing and nails.",
    business="Halcyon Rooms Verify6",
    answers={
        "service_menu": [
            {"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
        ],
        "floor_price": 70,
        "max_discount_pct": 15,
        "booking_lead_time": "Minimum 24 hours notice",
        "cancellation_policy": "48 hours' notice or 50% is charged",
        "timezone": "Europe/London",
        "icp": "Local clients within a few miles",
        "escalation_triggers": ["Anything about pregnancy, allergies or medical conditions"],
        "artifact_sample": "Hi — yes, we do that. I've got Saturday 2pm free.",
        "engagement_noun": "treatment",
        "client_noun": "client",
    },
)


def _onboard() -> uuid.UUID:
    session = onboarding.start(SPA["description"])
    for key, value in SPA["answers"].items():
        session.answer(key, value)
    tenant_id, _, _ = onboarding.finalise(
        session, business_name=SPA["business"],
        owner_email=f"owner@{uuid.uuid4().hex[:8]}.example",
        owner_wallet="0x" + uuid.uuid4().hex[:40].ljust(40, "0"))
    return tenant_id


def _converse(tenant_id, messages):
    """Same helper as the engine suite — a real conversation through the real engine, real DB, real RLS."""
    outcomes = []
    with db.tenant_session(tenant_id) as cur:
        tenant = store.get_tenant(cur)
        thread = engine.open_thread(cur, tenant, engine.Inbound(
            body="", from_address=PROSPECT, external_ref=f"ref-{uuid.uuid4().hex[:8]}"))
        for body in messages:
            outcome = engine.step(cur, tenant, thread,
                                   engine.Inbound(body=body, from_address=PROSPECT,
                                                  from_name="Nadia Okoro"))
            thread = outcome.thread
            outcomes.append(outcome)
    return outcomes


def _receipt_count() -> int:
    contract = config.xlayer_contract()
    selector = keccak(text="receiptCount()")[:4]
    raw = xlayer._rpc("eth_call", [{"to": contract, "data": "0x" + selector.hex()}, "latest"])
    return int(raw, 16)


def _hash_of(anchor_id: int) -> str:
    contract = config.xlayer_contract()
    selector = keccak(text="hashOf(uint256)")[:4]
    data = selector + anchor_id.to_bytes(32, "big")
    raw = xlayer._rpc("eth_call", [{"to": contract, "data": "0x" + data.hex()}, "latest"])
    return raw[2:] if raw.startswith("0x") else raw


def _event_id_and_hash(tx_hash: str) -> tuple[int, str]:
    tx_receipt = xlayer._wait_for_receipt(tx_hash, timeout_s=30)
    for log in tx_receipt.get("logs", []):
        topics = log.get("topics", [])
        if topics and topics[0].lower() == "0x" + ANCHORED_EVENT_TOPIC.hex():
            return int(topics[1], 16), topics[2][2:]
    raise AssertionError(f"No Anchored event found in logs of {tx_hash}.")


def run(r) -> None:
    db.migrate()
    contract = config.xlayer_contract()

    # ---- 1. connected to the real thing, checked live, not assumed from an earlier deploy
    live_chain_id = xlayer.chain_id()
    balance = xlayer.balance_wei()
    r.check(
        "Connected to X Layer MAINNET (196), not a testnet, with a funded signer and a deployed contract",
        live_chain_id == 196 and balance > 0 and bool(contract),
        "docs/VERIFICATION_LEDGER.md §9 recorded the decision: a testnet receipt proves nothing\n"
        "to a customer disputing a quote or an arbitrator ruling on an escrow, so CONCIERGE\n"
        "anchors on chain 196 only, from receipt anchoring onward. Re-checked live at the start of this\n"
        "run, not cached from the deploy.",
        f"| eth_chainId (live) = {live_chain_id}\n"
        f"| signer = {xlayer.address()}\n"
        f"| balance = {balance / 1e18:.8f} OKB\n"
        f"| ReceiptAnchor = {contract}",
    )

    count_before = _receipt_count()

    # ---- 2. a real conversation produces a real receipt, unsigned and unanchored at first
    tenant_id = _onboard()
    outs = _converse(tenant_id, ["Hi, how much is a deep tissue massage?"])
    good_receipt = outs[0].receipt
    r.check(
        "A quote from a real conversation writes a receipt with signature and xlayer_tx both NULL",
        good_receipt is not None and good_receipt.signature is None
        and good_receipt.xlayer_tx is None and good_receipt.within_rules is True,
        "This is `Outcome.receipt` exactly as `engine.step` writes it in production (§9 of\n"
        "engine.py) — not a receipt hand-built for this harness. Recording and anchoring are two\n"
        "separate steps on purpose: a customer-facing reply is never blocked on a mainnet\n"
        "confirmation. Nothing is written to those two columns until `anchor()` confirms a\n"
        "transaction.",
        f"| receipt_id = {good_receipt.receipt_id}\n"
        f"| action = {good_receipt.action}, rule_checked = {good_receipt.rule_checked}\n"
        f"| content_hash = {good_receipt.content_hash}\n"
        f"| signature = {good_receipt.signature!r}, xlayer_tx = {good_receipt.xlayer_tx!r}",
    )

    # ---- 3. anchor it for real
    with db.tenant_session(tenant_id) as cur:
        anchored = receipts.anchor(cur, good_receipt)

    recovered = receipts.recover_signer(anchored)
    # `anchor()` already waited for a receipt (xlayer._send), but a public multi-node RPC can be
    # eventually consistent across nodes, so a re-fetch immediately afterward is polled the same
    # way rather than assumed to see what the write path just saw.
    fresh_tx_receipt = xlayer._wait_for_receipt(anchored.xlayer_tx, timeout_s=30)
    independently_confirmed = (fresh_tx_receipt is not None
                                and int(fresh_tx_receipt["status"], 16) == 1)
    r.check(
        "The receipt is now both signed and anchored — confirmed by a fresh RPC call, not reused state",
        bool(anchored.signature) and bool(anchored.xlayer_tx)
        and recovered == xlayer.address() and independently_confirmed,
        "Two proofs, checked two different ways. The signature is recovered offline with\n"
        "eth_keys — no network call — and must resolve to the operator's own address. The\n"
        "transaction is re-fetched by hash from the RPC inside this check, separately from the\n"
        "TxResult `anchor()` returned internally, so this is not trusting our own in-process\n"
        "claim of success.",
        f"| signature (65 bytes) = {anchored.signature}\n"
        f"| recovered signer     = {recovered}\n"
        f"| expected signer      = {xlayer.address()}\n"
        f"| xlayer_tx            = {anchored.xlayer_tx}\n"
        f"| re-fetched status    = {fresh_tx_receipt['status']} (1 = success)\n"
        f"| block                = {int(fresh_tx_receipt['blockNumber'], 16)}\n"
        f"| gas used             = {int(fresh_tx_receipt['gasUsed'], 16)}",
    )

    # ---- 4. the hash on-chain, in the event log, and in the DB are the same value
    time.sleep(2)  # a public multi-node RPC can lag on eth_call reads just after a write
    event_id, onchain_hash_from_log = _event_id_and_hash(anchored.xlayer_tx)
    onchain_hash = _hash_of(event_id)
    recomputed = receipts.content_hash(anchored.decision)
    matches = onchain_hash == anchored.content_hash == onchain_hash_from_log == recomputed
    r.check(
        "The hash anchored on-chain, the hash in the event log, and the DB content_hash are identical",
        matches,
        "This is the actual within-rules proof: `decision.within_rules` is part of the hashed\n"
        "payload, so anyone — CONCIERGE, the tenant, an arbitrator — can recompute\n"
        "content_hash(decision) from the DB row and compare it against what is permanently on\n"
        "chain. Edit the row after the fact and this equality breaks; nothing needs to trust the\n"
        "database on its word.",
        f"| DB content_hash              = {anchored.content_hash}\n"
        f"| event log topics[2]          = {onchain_hash_from_log}\n"
        f"| ReceiptAnchor.hashOf({event_id})       = {onchain_hash}\n"
        f"| recomputed from decision     = {recomputed}\n"
        f"| decision.within_rules        = {anchored.decision.get('within_rules')}",
    )

    count_mid = _receipt_count()
    r.check(
        "receiptCount() on the contract increased by exactly one, read back independently",
        count_mid == count_before + 1,
        "Confirms this run added exactly the one anchor it claims — not zero (a silent failure)\n"
        "and not more (a retry double-spending gas).",
        f"| receiptCount() before = {count_before}, after = {count_mid}",
    )

    # ---- 5. a floor breach anchors with within_rules = False, and that survives on-chain too
    push_outs = _converse(tenant_id, ["How much is a deep tissue massage?", "I can only do 55"])
    breach_receipt = push_outs[1].receipt
    with db.tenant_session(tenant_id) as cur:
        breach_anchored = receipts.anchor(cur, breach_receipt)
    breach_tx_receipt = xlayer._wait_for_receipt(breach_anchored.xlayer_tx, timeout_s=30)
    r.check(
        "A floor-breach decision (within_rules=False, ESCALATED) anchors and confirms exactly like a good one",
        int(breach_tx_receipt["status"], 16) == 1
        and breach_anchored.decision["within_rules"] is False
        and receipts.verify(breach_anchored),
        "The chain does not know or care what the decision was — it anchors whatever hash it is\n"
        "given. The engine suite already proved the £55 ask against a £70 floor is refused with no\n"
        "counter-offer; this proves that refusal is now provably on the record, immutable, not\n"
        "just sitting in a database a rogue insider could edit.",
        f"| action = {breach_anchored.action}, within_rules = {breach_anchored.decision['within_rules']}\n"
        f"| state_after = {breach_anchored.decision.get('state_after')}\n"
        f"| xlayer_tx = {breach_anchored.xlayer_tx}\n"
        f"| status = {breach_tx_receipt['status']}",
    )

    # ---- 6. ATTACK — a decision edited after anchoring fails hash verification
    tampered = anchored.__class__(**{
        **anchored.__dict__,
        "decision": {**anchored.decision, "reply_sent": "£1 — everything must go"},
    })
    r.check(
        "ATTACK — a decision edited after anchoring fails hash verification",
        receipts.verify(anchored) and not receipts.verify(tampered),
        "verify() recomputes content_hash from the decision actually stored and compares it to\n"
        "the hash that was signed and anchored. The genuine row passes; a one-field edit — the\n"
        "kind of tamper a rogue insider with DB access could attempt — fails immediately, before\n"
        "anyone even needs to check the chain.",
        f"| genuine row verifies:            {receipts.verify(anchored)}\n"
        f"| tampered (reply text swapped) verifies: {receipts.verify(tampered)}",
    )

    # ---- 7. ATTACK — a signature does not transfer to a different receipt's hash
    forged = anchored.__class__(**{**anchored.__dict__, "content_hash": breach_anchored.content_hash})
    forged_signer = receipts.recover_signer(forged)
    r.check(
        "ATTACK — reusing a valid signature against a different receipt's hash does not recover the operator's address",
        forged_signer != xlayer.address(),
        "Takes a real, valid (signature, hash) pair and swaps in an unrelated hash — simulating\n"
        "an attacker with one genuine anchored receipt trying to claim its signature also covers\n"
        "a different decision. ECDSA recovery is bound to the exact 32 bytes signed, so this\n"
        "recovers to an unrelated address rather than silently reading as 'valid'.",
        f"| signature genuinely covers: {anchored.content_hash}\n"
        f"| hash substituted:           {forged.content_hash}\n"
        f"| recovered address:          {forged_signer}\n"
        f"| operator address:           {xlayer.address()}",
    )

    # ---- 8. measured gas, not estimated
    good_gas = int(fresh_tx_receipt["gasUsed"], 16)
    breach_gas = int(breach_tx_receipt["gasUsed"], 16)
    gas_price = int(xlayer._rpc("eth_gasPrice", []), 16)
    r.note(
        "Real anchoring gas vs. the earlier estimate (ledger §9)",
        "§9 estimated 55,000 gas per anchor before any contract existed, from OKX's fee data\n"
        "alone. These are the first two real anchors against the deployed contract on mainnet.",
        f"| anchor #1 (quote)  gas used = {good_gas}\n"
        f"| anchor #2 (breach) gas used = {breach_gas}\n"
        f"| live gas price = {gas_price} wei\n"
        f"| cost per anchor ≈ {(good_gas * gas_price) / 1e18:.8f} OKB",
    )
