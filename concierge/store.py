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

from .models import GapEvent, Receipt, Tenant, Thread


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
    confidence: dict[str, Any] | None = None,
    receipt_id: uuid.UUID | None = None,
) -> Receipt:
    """`receipt_id` is normally left to generate here. Feature 3 (public verification, the public-receipt suite)
    is the one caller that pre-generates it — the verify link has to be embedded in the same
    outbound email the receipt describes, which is rendered before the row exists — so the
    engine mints the id first and this just uses it instead of a fresh one."""
    cur.execute(
        """
        INSERT INTO receipts (receipt_id, tenant_id, thread_id, action, decision, rule_checked,
                              within_rules, content_hash, signature, xlayer_tx, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (receipt_id or uuid.uuid4(), tenant_id, thread_id, action, Jsonb(decision), rule_checked,
         within_rules, content_hash, signature, xlayer_tx,
         Jsonb(confidence) if confidence is not None else None),
    )
    return Receipt.from_row(cur.fetchone())


def mark_anchored(
    cur: Cursor, *, receipt_id: uuid.UUID, signature: str, xlayer_tx: str,
) -> Receipt:
    """Fill in the two columns receipt anchoring exists to fill. Never called with a fabricated value —
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


# ---------------------------------------------------------------- gap events (Feature 1)

def insert_gap_event(
    cur: Cursor, *, tenant_id: uuid.UUID, thread_id: uuid.UUID | None, raw_query_text: str,
) -> GapEvent:
    """Written as a side effect of the engine's existing ESCALATE-on-Unquotable transition —
    see `engine.step`. `classified_category` starts NULL; `gaps.classify_pending` fills it in
    later, only if an LLM key is configured."""
    cur.execute(
        """
        INSERT INTO gap_events (gap_id, tenant_id, thread_id, raw_query_text)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (uuid.uuid4(), tenant_id, thread_id, raw_query_text),
    )
    return GapEvent.from_row(cur.fetchone())


def update_gap_category(cur: Cursor, *, gap_id: uuid.UUID, category: str) -> GapEvent:
    cur.execute(
        "UPDATE gap_events SET classified_category = %s WHERE gap_id = %s RETURNING *",
        (category, str(gap_id)),
    )
    return GapEvent.from_row(cur.fetchone())


def list_gap_events(cur: Cursor) -> list[GapEvent]:
    cur.execute("SELECT * FROM gap_events ORDER BY escalated_at")
    return [GapEvent.from_row(r) for r in cur.fetchall()]
