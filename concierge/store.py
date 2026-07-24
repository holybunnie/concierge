"""Persistence. Every function takes an already-scoped cursor from `db.tenant_session`.

Note what is deliberately absent: none of these queries carry a `WHERE tenant_id = ...` clause.
That is not an oversight — it is the demonstration. The row-level security policy applies the
filter underneath, so a query written without a tenant predicate still cannot cross tenants.
Isolation does not depend on every future author remembering to write the clause.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import Cursor
from psycopg.types.json import Jsonb

from .models import Receipt, Tenant, Thread


# ---------------------------------------------------------------- tenants

def create_tenant(
    cur: Cursor,
    *,
    tenant_id: uuid.UUID,
    owner_wallet: str,
    owner_email: str,
    business_name: str,
    vertical: str,
    inbound_address: str,
    profile: dict[str, Any] | None = None,
    engagement: dict[str, Any] | None = None,
) -> Tenant:
    """The cursor must already be scoped to `tenant_id`; the RLS WITH CHECK clause enforces it."""
    cur.execute(
        """
        INSERT INTO tenants (tenant_id, owner_wallet, owner_email, business_name, vertical,
                             inbound_address, profile, engagement)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (tenant_id, owner_wallet, owner_email, business_name, vertical,
         inbound_address.lower().strip(), Jsonb(profile or {}), Jsonb(engagement or {})),
    )
    return Tenant.from_row(cur.fetchone())


def get_tenant(cur: Cursor) -> Tenant | None:
    """Returns *the* tenant this session is scoped to. There is no `get_tenant(id)` by design."""
    cur.execute("SELECT * FROM tenants")
    row = cur.fetchone()
    return Tenant.from_row(row) if row else None


def update_profile(cur: Cursor, profile: dict[str, Any]) -> Tenant | None:
    cur.execute("UPDATE tenants SET profile = %s RETURNING *", (Jsonb(profile),))
    row = cur.fetchone()
    return Tenant.from_row(row) if row else None


def update_engagement(cur: Cursor, engagement: dict[str, Any]) -> Tenant | None:
    cur.execute("UPDATE tenants SET engagement = %s RETURNING *", (Jsonb(engagement),))
    row = cur.fetchone()
    return Tenant.from_row(row) if row else None


# ---------------------------------------------------------------- threads

def create_thread(
    cur: Cursor,
    *,
    tenant_id: uuid.UUID,
    client_contact: str,
    client_name: str | None = None,
    external_ref: str | None = None,
    state: str = "NEW",
) -> Thread:
    cur.execute(
        """
        INSERT INTO threads (thread_id, tenant_id, client_contact, client_name,
                             external_ref, state)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (uuid.uuid4(), tenant_id, client_contact.lower().strip(), client_name,
         external_ref, state),
    )
    return Thread.from_row(cur.fetchone())


def get_thread(cur: Cursor, thread_id: uuid.UUID | str) -> Thread | None:
    """Note: keyed by primary key alone. Another tenant's thread_id returns None, not their row."""
    cur.execute("SELECT * FROM threads WHERE thread_id = %s", (str(thread_id),))
    row = cur.fetchone()
    return Thread.from_row(row) if row else None


def find_thread_by_external_ref(cur: Cursor, external_ref: str) -> Thread | None:
    cur.execute("SELECT * FROM threads WHERE external_ref = %s", (external_ref,))
    row = cur.fetchone()
    return Thread.from_row(row) if row else None


def list_threads(cur: Cursor) -> list[Thread]:
    cur.execute("SELECT * FROM threads ORDER BY created_at")
    return [Thread.from_row(r) for r in cur.fetchall()]


def save_thread(cur: Cursor, thread: Thread) -> Thread | None:
    cur.execute(
        """
        UPDATE threads SET state = %s, client_name = %s, client_timezone = %s,
                           history = %s, current_offer = %s, offered_slots = %s,
                           last_updated = now()
        WHERE thread_id = %s
        RETURNING *
        """,
        (thread.state, thread.client_name, thread.client_timezone,
         Jsonb(thread.history), Jsonb(thread.current_offer) if thread.current_offer else None,
         Jsonb(thread.offered_slots), str(thread.thread_id)),
    )
    row = cur.fetchone()
    return Thread.from_row(row) if row else None


# ---------------------------------------------------------------- receipts

def insert_receipt(
    cur: Cursor,
    *,
    tenant_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    action: str,
    decision: dict[str, Any],
    rule_checked: str,
    within_rules: bool,
    content_hash: str,
    signature: str | None = None,
    xlayer_tx: str | None = None,
) -> Receipt:
    cur.execute(
        """
        INSERT INTO receipts (receipt_id, tenant_id, thread_id, action, decision, rule_checked,
                              within_rules, content_hash, signature, xlayer_tx)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (uuid.uuid4(), tenant_id, thread_id, action, Jsonb(decision), rule_checked,
         within_rules, content_hash, signature, xlayer_tx),
    )
    return Receipt.from_row(cur.fetchone())


def mark_anchored(
    cur: Cursor, *, receipt_id: uuid.UUID, signature: str, xlayer_tx: str,
) -> Receipt:
    """Fill in the two columns Phase 6 exists to fill. Never called with a fabricated value —
    both arguments come from a confirmed on-chain transaction (see concierge/xlayer.py)."""
    cur.execute(
        "UPDATE receipts SET signature = %s, xlayer_tx = %s WHERE receipt_id = %s RETURNING *",
        (signature, xlayer_tx, str(receipt_id)),
    )
    return Receipt.from_row(cur.fetchone())


def list_receipts(cur: Cursor, thread_id: uuid.UUID | None = None) -> list[Receipt]:
    if thread_id is None:
        cur.execute("SELECT * FROM receipts ORDER BY created_at")
    else:
        cur.execute("SELECT * FROM receipts WHERE thread_id = %s ORDER BY created_at",
                    (str(thread_id),))
    return [Receipt.from_row(r) for r in cur.fetchall()]
