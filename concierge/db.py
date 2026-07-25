"""Database access. The only way to touch tenant data is through a scoped session.

Design rule (§2b): there is no public function here that returns a bare connection or cursor.
`tenant_session(tenant_id)` opens a transaction, pins `app.tenant_id` for its lifetime, and
yields a cursor that the database itself has already fenced. Forgetting to filter by tenant_id
in a query is therefore not a leak — the row-level security policy filters it regardless.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from . import config


class TenantUnresolved(Exception):
    """Raised when an inbound message cannot be attributed to exactly one tenant.

    This is a hard stop, never a fallback to a default tenant. An unattributable message
    is escalated to the operator; it is not answered by guesswork.
    """


def migrate() -> str:
    """Apply schema.sql as the owning role. Returns the applied SQL's path."""
    path = config.ROOT / "concierge" / "sql" / "schema.sql"
    sql = path.read_text()
    with psycopg.connect(config.owner_database_url(), autocommit=True) as conn:
        conn.execute(sql)
    return str(path)


@contextlib.contextmanager
def tenant_session(tenant_id: uuid.UUID | str) -> Iterator[psycopg.Cursor]:
    """A transaction fenced to exactly one tenant.

    `SET LOCAL` scopes the setting to this transaction, so it cannot leak into a pooled
    connection's next user. The value is passed as a bound parameter via set_config, not
    interpolated into SQL.
    """
    tid = str(uuid.UUID(str(tenant_id)))  # rejects anything that is not a uuid, before it hits SQL
    with psycopg.connect(config.app_database_url(), row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tid,))
                yield cur


@contextlib.contextmanager
def unscoped_session() -> Iterator[psycopg.Cursor]:
    """A session with NO tenant pinned.

    Used only by the resolver and by the red-team harness. Because no policy can match an
    unset app.tenant_id, this session can read no tenant rows at all — which is the point:
    it demonstrates that the fence holds by default rather than by discipline.
    """
    with psycopg.connect(config.app_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            yield cur


def resolve_tenant_by_inbound_address(address: str) -> uuid.UUID:
    """Map an inbound email address to a tenant id. Returns an id or raises — never a default.

    Handles the two ways a real MTA mangles an address before we see it: case, and the
    plus-tag that anyone can append to an address to try to slip past an exact-match lookup.
    """
    normalised = normalise_address(address)
    with unscoped_session() as cur:
        cur.execute("SELECT resolve_tenant_by_inbound_address(%s) AS tenant_id", (normalised,))
        row = cur.fetchone()
    if not row or not row["tenant_id"]:
        raise TenantUnresolved(
            f"No tenant owns the address {normalised!r}. Refusing to guess a recipient."
        )
    return row["tenant_id"]


def list_tenant_ids() -> list[uuid.UUID]:
    """Every tenant id, for the scheduled worker — the ONLY enumerating read in the system.

    Connects as `concierge_worker`, not `concierge_app`: the webhook role is deliberately not
    granted EXECUTE on `scheduler_tenant_ids()`, because it can pin `app.tenant_id` to any value
    and enumeration would therefore hand a web-app compromise every tenant's data. This role can
    enumerate but holds no table grants, so the ids are all it can ever obtain. Per-tenant work
    still goes through `tenant_session`, RLS-fenced exactly like every other read.
    """
    with psycopg.connect(config.worker_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT scheduler_tenant_ids() AS tenant_id")
            return [row["tenant_id"] for row in cur.fetchall()]


def resolve_tenant_by_engagement(escrow_ref: str) -> uuid.UUID:
    with unscoped_session() as cur:
        cur.execute("SELECT resolve_tenant_by_engagement(%s) AS tenant_id", (escrow_ref.strip(),))
        row = cur.fetchone()
    if not row or not row["tenant_id"]:
        raise TenantUnresolved(f"No tenant holds engagement {escrow_ref!r}. Refusing to guess.")
    return row["tenant_id"]


def get_public_receipt(receipt_id: str) -> dict[str, Any] | None:
    """Feature 3 (GATE 6b): the one public, unauthenticated read in the system.

    Scoped by `receipt_id` alone via the `public_receipt` SECURITY DEFINER function — no tenant
    context is ever resolved or needed. A malformed id (not even a UUID) is exactly as "not
    found" as one that is well-formed but does not exist: this never raises into a caller that
    might turn a parse error into a different, more revealing response.
    """
    try:
        rid = str(uuid.UUID(str(receipt_id)))
    except (ValueError, AttributeError, TypeError):
        return None
    with unscoped_session() as cur:
        cur.execute("SELECT * FROM public_receipt(%s)", (rid,))
        row = cur.fetchone()
    return dict(row) if row and row.get("receipt_id") else None


def normalise_address(address: str) -> str:
    """`Acme Ltd <ACME+anything@Inbox.Example.com>` → `acme@inbox.example.com`.

    The plus-tag is stripped deliberately: sub-addressing must not be a way to address a
    tenant that does not exist, nor to evade the uniqueness constraint on inbound_address.
    """
    addr = address.strip()
    if "<" in addr and ">" in addr:
        addr = addr[addr.rindex("<") + 1: addr.rindex(">")]
    addr = addr.strip().lower()
    if "@" not in addr:
        raise TenantUnresolved(f"{address!r} is not an email address.")
    local, _, domain = addr.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"
