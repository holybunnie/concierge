"""Receipts (§8) — the record that a commitment stayed inside the tenant's rules.

Every state transition writes one. A receipt is not a log line: it carries the decision, the
rule that was checked, whether the decision was within that rule, and a hash over the whole
thing, so a disagreement months later is settled by recomputation rather than by argument. That
is the product's trust claim, and in an escrow dispute (§7) it is the defence.

**What is real here and what is not, stated plainly.** The content hash is real and the tamper
check works. `signature` and `xlayer_tx` are NULL on every receipt this phase writes, because
signing needs a funded key on X Layer mainnet (OPERATOR_PROVIDES item 6) and that has not
arrived. Nothing here pretends otherwise: there is no placeholder transaction hash, and the
harness reports the NULLs rather than hiding them. Phase 6 fills those two columns in and adds
the anchoring; the hash written now is the value that will be anchored then, so receipts
written today remain verifiable afterwards.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from psycopg import Cursor

from . import store
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
) -> Receipt:
    """Write the receipt for one decision. The cursor is already tenant-scoped by RLS.

    `signature` and `xlayer_tx` are left NULL deliberately — see the module docstring. A
    fabricated transaction hash would make every receipt in the table untrustworthy, including
    the real ones added later.
    """
    return store.insert_receipt(
        cur, tenant_id=tenant_id, thread_id=thread_id, action=action,
        decision=decision, rule_checked=rule_checked, within_rules=within_rules,
        content_hash=content_hash(decision), signature=None, xlayer_tx=None,
    )


def verify(receipt: Receipt) -> bool:
    """Recompute the hash from the stored decision. False means the row was altered."""
    return content_hash(receipt.decision) == receipt.content_hash


def anchored(receipt: Receipt) -> bool:
    """Whether this receipt has an on-chain anchor. False for everything until Phase 6."""
    return bool(receipt.xlayer_tx)
