"""the isolation suite — tenant model and isolation, proven by attack rather than by assertion.

Two real tenants are created in a real Postgres. Then, as the application role, we try eight
distinct ways to read or write across the boundary. Every one must fail. A test that merely
shows the happy path proves nothing about isolation, so the happy path is one check out of ten.
"""

from __future__ import annotations

import uuid

import psycopg

from . import db, store
from .db import TenantUnresolved

# Fixed ids so the report is readable and reruns are idempotent.
ACME = uuid.UUID("11111111-1111-4111-8111-111111111111")   # tenant A
BELLA = uuid.UUID("22222222-2222-4222-8222-222222222222")  # tenant B


def _reset() -> None:
    """Drop the two fixture tenants as owner. Cascades to their threads and receipts."""
    with psycopg.connect(db.config.owner_database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id = ANY(%s)", ([ACME, BELLA],))


def seed() -> dict:
    """Create two tenants with real, different profiles, each with a thread and a receipt."""
    _reset()
    out: dict = {}

    with db.tenant_session(ACME) as cur:
        store.create_tenant(
            cur, tenant_id=ACME,
            owner_wallet="0xAcME000000000000000000000000000000000001",
            owner_email="owner@acme-estates.example",
            business_name="Acme Estates",
            vertical="real_estate",
            inbound_address="acme@inbox.example.com",
            profile={
                "services": [{"name": "Viewing", "price": 0, "currency": "GBP"}],
                "pricing_rules": {"listing_fee_pct": 1.5, "floor_pct": 1.2},
                "icp": "buyers and landlords in Zone 2",
            },
            engagement={"escrow_ref": "escrow-acme-0001", "status": "active"},
        )
        t = store.create_thread(cur, tenant_id=ACME, client_contact="buyer@example.net",
                                client_name="A Buyer", external_ref="msg-acme-1")
        out["acme_thread"] = t.thread_id
        r = store.insert_receipt(cur, tenant_id=ACME, thread_id=t.thread_id, action="QUOTE",
                                 decision={"fee_pct": 1.5}, rule_checked="pricing_rules.floor_pct",
                                 within_rules=True, content_hash="acme-hash-1")
        out["acme_receipt"] = r.receipt_id

    with db.tenant_session(BELLA) as cur:
        store.create_tenant(
            cur, tenant_id=BELLA,
            owner_wallet="0xBELLa000000000000000000000000000000000002",
            owner_email="owner@bella-spa.example",
            business_name="Bella Spa",
            vertical="spa_beauty",
            inbound_address="bella@inbox.example.com",
            profile={
                "services": [{"name": "Deep tissue 60m", "price": 85, "currency": "GBP"}],
                "pricing_rules": {"floor_price": 70, "max_discount_pct": 15},
                "icp": "local repeat clients",
            },
            engagement={"escrow_ref": "escrow-bella-0002", "status": "active"},
        )
        t = store.create_thread(cur, tenant_id=BELLA, client_contact="client@example.org",
                                client_name="A Client", external_ref="msg-bella-1")
        out["bella_thread"] = t.thread_id

    return out


def run(r) -> None:
    """Populate a Report (from verify.py) with the isolation suite checks."""

    # ---- 0. schema
    try:
        path = db.migrate()
        r.check("Schema applies to a real Postgres",
                True,
                "Ran the real schema against a real PostgreSQL 16 server — no ORM, no in-memory\n"
                "substitute. Row-level security is enabled on all three tenant tables and the\n"
                "application role is created without BYPASSRLS.",
                f"applied {path}")
    except Exception as e:
        r.check("Schema applies to a real Postgres", False, f"Migration failed: {e}")
        return

    ids = seed()
    acme_thread, bella_thread = ids["acme_thread"], ids["bella_thread"]
    acme_receipt = ids["acme_receipt"]

    # ---- 1. happy path: each tenant sees its own business, and only its own.
    with db.tenant_session(ACME) as cur:
        a = store.get_tenant(cur)
        a_threads = store.list_threads(cur)
        cur.execute("SELECT count(*) AS n FROM tenants")
        a_visible = cur.fetchone()["n"]
    with db.tenant_session(BELLA) as cur:
        b = store.get_tenant(cur)
        b_threads = store.list_threads(cur)
        cur.execute("SELECT count(*) AS n FROM tenants")
        b_visible = cur.fetchone()["n"]

    ok = (a.business_name == "Acme Estates" and b.business_name == "Bella Spa"
          and a_visible == 1 and b_visible == 1
          and len(a_threads) == 1 and len(b_threads) == 1)
    r.check(
        "Two real tenants exist, and each session sees exactly one of them",
        ok,
        "Acme Estates (real estate, 1.5% listing fee, 1.2% floor) and Bella Spa (spa, £85\n"
        "deep tissue, £70 floor) are both stored. `SELECT count(*) FROM tenants` — a query with\n"
        "no WHERE clause at all — returns 1, not 2, in both sessions. The database, not the\n"
        "query, applied the filter.",
        f"Acme session:  get_tenant() -> {a.business_name} ({a.vertical}), "
        f"count(*) FROM tenants = {a_visible}, threads = {len(a_threads)}\n"
        f"Bella session: get_tenant() -> {b.business_name} ({b.vertical}), "
        f"count(*) FROM tenants = {b_visible}, threads = {len(b_threads)}\n"
        f"Acme profile:  {a.profile['pricing_rules']}\n"
        f"Bella profile: {b.profile['pricing_rules']}",
    )

    # ---- 2. RED TEAM: B fetches A's thread by primary key.
    with db.tenant_session(BELLA) as cur:
        stolen = store.get_thread(cur, acme_thread)
        cur.execute("SELECT * FROM threads WHERE thread_id = %s", (str(acme_thread),))
        raw = cur.fetchall()
    r.check(
        "RED TEAM — tenant B asks for tenant A's thread by its exact primary key",
        stolen is None and raw == [],
        f"Bella's session knows Acme's thread_id ({acme_thread}) and asks for it directly. The\n"
        "query is `SELECT * FROM threads WHERE thread_id = <A's id>` — it is not filtered by\n"
        "tenant and does not need to be. Postgres returned zero rows. Knowing an identifier is\n"
        "not authority to read it.",
        f"as BELLA: get_thread({acme_thread}) -> {stolen!r}\n"
        f"as BELLA: raw SELECT ... WHERE thread_id = '{acme_thread}' -> {len(raw)} rows",
    )

    # ---- 3. RED TEAM: B reads A's receipts (the on-chain-anchored commitment log).
    with db.tenant_session(BELLA) as cur:
        cur.execute("SELECT * FROM receipts")
        all_receipts = cur.fetchall()
        cur.execute("SELECT * FROM receipts WHERE receipt_id = %s", (str(acme_receipt),))
        by_id = cur.fetchall()
    r.check(
        "RED TEAM — tenant B enumerates receipts, and targets A's receipt by id",
        all_receipts == [] and by_id == [],
        "Receipts contain a competitor's pricing decisions — the single most sensitive table.\n"
        "Bella has none of her own yet, so an unfiltered `SELECT * FROM receipts` is the purest\n"
        "possible test: any leak shows up as a row. Zero rows, both unfiltered and by id.",
        f"as BELLA: SELECT * FROM receipts -> {len(all_receipts)} rows\n"
        f"as BELLA: SELECT * FROM receipts WHERE receipt_id = '{acme_receipt}' -> {len(by_id)} rows\n"
        f"(that receipt does exist — Acme's session created it and can read it)",
    )

    # ---- 4. RED TEAM: B writes a row labelled as A's.
    wrote = None
    try:
        with db.tenant_session(BELLA) as cur:
            store.create_thread(cur, tenant_id=ACME, client_contact="attacker@example.net")
            wrote = "INSERT SUCCEEDED — LEAK"
    except psycopg.errors.InsufficientPrivilege as e:
        wrote = f"{type(e).__name__}: {str(e).splitlines()[0]}"
    except Exception as e:  # any refusal is acceptable; a success is not
        wrote = f"{type(e).__name__}: {str(e).splitlines()[0]}"
    r.check(
        "RED TEAM — tenant B tries to insert a row stamped with tenant A's id",
        wrote is not None and "LEAK" not in wrote,
        "Poisoning another tenant's data is as damaging as reading it: a forged thread could\n"
        "put words in Acme's mouth. Bella's session explicitly set tenant_id = Acme and the\n"
        "policy's WITH CHECK clause rejected the write at the database level.",
        f"as BELLA: INSERT INTO threads (tenant_id = ACME, ...) -> {wrote}",
    )

    # ---- 5. RED TEAM: B mass-updates without a tenant predicate.
    with db.tenant_session(BELLA) as cur:
        cur.execute("UPDATE threads SET state = 'DEAD'")
        killed = cur.rowcount
        cur.execute("DELETE FROM receipts")
        deleted = cur.rowcount
    with db.tenant_session(ACME) as cur:
        survivor = store.get_thread(cur, acme_thread)
        remaining = store.list_receipts(cur)
    r.check(
        "RED TEAM — a predicate-free UPDATE and DELETE from tenant B cannot touch A",
        killed == 1 and deleted == 0 and survivor.state == "NEW" and len(remaining) == 1,
        "The worst realistic bug in a multi-tenant system is a mass write with a forgotten\n"
        "WHERE clause. Bella ran `UPDATE threads SET state='DEAD'` and `DELETE FROM receipts`\n"
        "with no predicate whatsoever. It affected exactly her own one thread and zero receipts.\n"
        "Acme's thread is still NEW and Acme's receipt is still there.",
        f"as BELLA: UPDATE threads SET state='DEAD'  -> {killed} row(s) affected\n"
        f"as BELLA: DELETE FROM receipts             -> {deleted} row(s) affected\n"
        f"as ACME:  Acme's thread state is still '{survivor.state}', "
        f"receipts remaining = {len(remaining)}",
    )

    # ---- 6. RED TEAM: no tenant resolved at all — the fail-closed property.
    with db.unscoped_session() as cur:
        cur.execute("SELECT count(*) AS n FROM tenants")
        t_n = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM threads")
        th_n = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM receipts")
        rc_n = cur.fetchone()["n"]
    r.check(
        "A session with no tenant resolved sees nothing at all (fail-closed)",
        (t_n, th_n, rc_n) == (0, 0, 0),
        "This is the property that makes §2b structural rather than aspirational. If a future\n"
        "code path forgets to resolve a tenant, it does not fall back to 'all tenants' — it\n"
        "falls back to nothing, because NULL = uuid is never true. A forgotten scope becomes an\n"
        "empty result and a visible bug, not a silent cross-tenant read.",
        f"unscoped session: tenants={t_n}, threads={th_n}, receipts={rc_n} (rows do exist)",
    )

    # ---- 7. RED TEAM: the application role tries to switch RLS off.
    with db.unscoped_session() as cur:
        try:
            cur.execute("SET row_security = off")
            cur.execute("SELECT * FROM tenants")
            escape = f"BYPASS SUCCEEDED — {len(cur.fetchall())} rows LEAKED"
        except Exception as e:
            escape = f"{type(e).__name__}: {str(e).splitlines()[0]}"
        cur.connection.rollback()
    r.check(
        "RED TEAM — the application role cannot turn row-level security off",
        "SUCCEEDED" not in escape,
        "An attacker with SQL execution inside the app (an injection, a compromised dependency)\n"
        "would reach for `SET row_security = off`. concierge_app owns no tables and has\n"
        "NOBYPASSRLS, so Postgres refuses the resulting query outright rather than serving rows.",
        f"as concierge_app: SET row_security = off; SELECT * FROM tenants;\n  -> {escape}",
    )

    # ---- 8. resolution: the only bridge from untrusted input to a tenant.
    resolved_plain = db.resolve_tenant_by_inbound_address("acme@inbox.example.com")
    resolved_messy = db.resolve_tenant_by_inbound_address("Acme Estates <ACME+quote@Inbox.Example.COM>")
    unknown = None
    try:
        db.resolve_tenant_by_inbound_address("nobody@inbox.example.com")
        unknown = "RESOLVED — WRONG"
    except TenantUnresolved as e:
        unknown = f"TenantUnresolved: {e}"
    by_escrow = db.resolve_tenant_by_engagement("escrow-bella-0002")
    r.check(
        "Inbound addresses and A2A engagements resolve to exactly one tenant, or raise",
        resolved_plain == ACME and resolved_messy == ACME
        and by_escrow == BELLA and "WRONG" not in unknown,
        "Resolution happens before any business logic and returns an opaque id — never a\n"
        "profile. Real MTAs mangle addresses, so display names, mixed case and plus-tags all\n"
        "normalise to the same tenant. An address nobody owns raises instead of falling back to\n"
        "a default tenant: an unattributable message is escalated, never answered by guesswork.",
        f"'acme@inbox.example.com'                        -> {resolved_plain}\n"
        f"'Acme Estates <ACME+quote@Inbox.Example.COM>'   -> {resolved_messy}\n"
        f"'nobody@inbox.example.com'                      -> {unknown}\n"
        f"engagement 'escrow-bella-0002'                  -> {by_escrow} (Bella)",
    )

    # ---- 9. the resolver leaks an id and nothing else.
    with db.unscoped_session() as cur:
        cur.execute("SELECT resolve_tenant_by_inbound_address('acme@inbox.example.com') AS t")
        leaked_id = cur.fetchone()["t"]
        cur.execute("SELECT * FROM tenants WHERE tenant_id = %s", (str(leaked_id),))
        leaked_rows = cur.fetchall()
    r.check(
        "The SECURITY DEFINER resolver is the only door, and it is a keyhole",
        leaked_id == ACME and leaked_rows == [],
        "The resolver runs as the table owner, so it is the one function that crosses the RLS\n"
        "boundary — twenty lines, and the entire audit surface for tenant isolation. Proof it\n"
        "is narrow: holding the uuid it returns, the very next query for that tenant's row\n"
        "still returns nothing. The id is not a capability.",
        f"resolver returned {leaked_id}\n"
        f"then, same unscoped session: SELECT * FROM tenants WHERE tenant_id = "
        f"'{leaked_id}' -> {len(leaked_rows)} rows",
    )

    # ---- 10. a non-uuid never reaches SQL.
    injected = None
    try:
        with db.tenant_session("11111111-1111-4111-8111-111111111111' OR '1'='1"):
            injected = "ACCEPTED — INJECTION SURFACE"
    except ValueError as e:
        injected = f"ValueError: {e}"
    r.check(
        "A tenant id that is not a uuid is rejected before it reaches the database",
        injected is not None and "ACCEPTED" not in injected,
        "tenant_session parses its argument with uuid.UUID() before doing anything else, and\n"
        "then binds it as a parameter to set_config rather than interpolating it into SQL. Two\n"
        "independent defences; the first one alone stops this.",
        f"tenant_session(\"...' OR '1'='1\") -> {injected}",
    )
