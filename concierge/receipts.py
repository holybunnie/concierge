"""Receipts (§8) — the record that a commitment stayed inside the tenant's rules.

Every state transition writes one. A receipt is not a log line: it carries the decision, the
rule that was checked, whether the decision was within that rule, and a hash over the whole
thing, so a disagreement months later is settled by recomputation rather than by argument. That
is the product's trust claim, and in an escrow dispute (§7) it is the defence.

**Two independent proofs, not one.** `signature` is CONCIERGE's own key signing the content hash
directly (`eth_account.Account.unsafe_sign_hash`) — verifiable offline, with no RPC call, by
anyone who knows the operator's address (`recover_signer`, below). `xlayer_tx` is the same hash
anchored on X Layer mainnet via `ReceiptAnchor.anchorReceipt` — verifiable on-chain, by anyone,
forever, independent of CONCIERGE staying up. Recording is synchronous (every state transition);
anchoring is a second step (`anchor`) so a customer-facing reply is never blocked on a mainnet
confirmation — see `verify_phase6.py` and HANDOFF's note on the receipt-batch worker.

Both are NULL until `anchor()` runs. There is no placeholder transaction hash and no placeholder
signature: the harness reports NULLs rather than hiding them, exactly as before Phase 6.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from eth_account import Account
from eth_keys import keys as eth_keys
from psycopg import Cursor

from . import store, xlayer
from .models import Receipt


def canonical(payload: dict[str, Any]) -> str:
    """A byte-for-byte reproducible rendering of a decision.

    Keys sorted and whitespace fixed, so the same decision hashes identically regardless of
    dict ordering — which matters because the hash has to be recomputable from a JSONB column
    that Postgres is free to reorder.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def record(
    cur: Cursor,
    *,
    tenant_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    action: str,
    decision: dict[str, Any],
    rule_checked: str,
    within_rules: bool,
    confidence: dict[str, Any] | None = None,
) -> Receipt:
    """Write the receipt for one decision. The cursor is already tenant-scoped by RLS.

    `signature` and `xlayer_tx` are left NULL deliberately — see the module docstring. A
    fabricated transaction hash would make every receipt in the table untrustworthy, including
    the real ones added later. `confidence` (Feature 2, GATE 3b-2) is `concierge.confidence`'s
    computed score for this decision, or None when the feature does not apply to it.
    """
    return store.insert_receipt(
        cur, tenant_id=tenant_id, thread_id=thread_id, action=action,
        decision=decision, rule_checked=rule_checked, within_rules=within_rules,
        content_hash=content_hash(decision), signature=None, xlayer_tx=None,
        confidence=confidence,
    )


def verify(receipt: Receipt) -> bool:
    """Recompute the hash from the stored decision. False means the row was altered."""
    return content_hash(receipt.decision) == receipt.content_hash


def anchored(receipt: Receipt) -> bool:
    """Whether this receipt has an on-chain anchor. False until `anchor()` has run on it."""
    return bool(receipt.xlayer_tx)


def anchor(cur: Cursor, receipt: Receipt) -> Receipt:
    """Sign the receipt's content hash and anchor it on X Layer mainnet, for real.

    Two chain interactions happen here: an offline signature over the hash (no network call,
    just the operator's key), and an on-chain `anchorReceipt` transaction, confirmed by polling
    its receipt — never assumed from a broadcast succeeding (see `xlayer._send`). Both results
    are written back to the row; nothing here can produce a NULL replaced by a fake value.
    """
    hash_bytes = bytes.fromhex(receipt.content_hash)
    signed = Account.unsafe_sign_hash(hash_bytes, xlayer._account().key)
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    tx = xlayer.anchor(receipt.content_hash)

    return store.mark_anchored(
        cur, receipt_id=receipt.receipt_id, signature=signature, xlayer_tx=tx.tx_hash,
    )


def recover_signer(receipt: Receipt) -> str | None:
    """Recover the address that produced `receipt.signature`, with no RPC call.

    Independent of `verify()`: `verify()` proves the row wasn't altered after signing;
    this proves *who* signed it. A row that fails this check was never anchored by
    CONCIERGE's own key, whatever the DB says.
    """
    if not receipt.signature:
        return None
    sig = receipt.signature[2:] if receipt.signature.startswith("0x") else receipt.signature
    r, s, v = int(sig[:64], 16), int(sig[64:128], 16), int(sig[128:130], 16)
    hash_bytes = bytes.fromhex(receipt.content_hash)
    pubkey = eth_keys.ecdsa_recover(hash_bytes, eth_keys.Signature(vrs=(v - 27, r, s)))
    return pubkey.to_checksum_address()
