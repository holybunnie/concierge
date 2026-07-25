-- CONCIERGE schema.
--
-- Tenant isolation here is a property of the database, not of the application code and
-- certainly not of a prompt. The application connects as `concierge_app`, a role that:
--   * does not own any table, so it cannot bypass row-level security;
--   * sees rows only where tenant_id = current_setting('app.tenant_id');
--   * sees NOTHING at all when app.tenant_id is unset, because NULL = uuid is NULL, not true.
--
-- That last property is what makes §2b's "no path runs without a resolved tenant_id" true in
-- the strong sense: a forgotten tenant scope is not a leak, it is an empty result.
--
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- tables

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id       uuid PRIMARY KEY,
    owner_wallet    text        NOT NULL,
    owner_email     text        NOT NULL,
    business_name   text        NOT NULL,
    vertical        text        NOT NULL,
    inbound_address text        NOT NULL UNIQUE,
    -- profile: services, pricing_rules, calendar_ref, icp, escalation_triggers, artifact_samples.
    -- Every price and commitment the engine emits is derived from this column by code (§2).
    profile         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- engagement: scope, escrow_ref, window, status (§7).
    engagement      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- The marketplace job that provisioned this tenant, for tenants that arrived by subscribing on
-- OKX A2A rather than being set up by hand. NULL for every hand-built tenant, and UNIQUE so a
-- replayed subscription event cannot create a second tenant for the same buyer — the database,
-- not the worker's control flow, is what makes provisioning idempotent.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS a2a_job_id text;
CREATE UNIQUE INDEX IF NOT EXISTS tenants_a2a_job_id_key ON tenants (a2a_job_id);

CREATE TABLE IF NOT EXISTS threads (
    thread_id       uuid PRIMARY KEY,
    tenant_id       uuid        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    client_contact  text        NOT NULL,
    client_name     text,
    -- Never inferred. Captured by asking the prospect outright (§9b.1); NULL until they answer.
    client_timezone text,
    state           text        NOT NULL,
    -- Provider-side conversation key (email Message-ID / A2A engagement ref).
    external_ref    text,
    history         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    current_offer   jsonb,
    offered_slots   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    last_updated    timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Threading key is scoped by tenant: two tenants may legitimately hold the same external_ref.
CREATE UNIQUE INDEX IF NOT EXISTS threads_tenant_external_ref
    ON threads (tenant_id, external_ref) WHERE external_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS threads_tenant_contact ON threads (tenant_id, client_contact);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id    uuid PRIMARY KEY,
    tenant_id     uuid        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    thread_id     uuid        REFERENCES threads(thread_id) ON DELETE CASCADE,
    action        text        NOT NULL,
    decision      jsonb       NOT NULL,
    rule_checked  text        NOT NULL,
    within_rules  boolean     NOT NULL,
    content_hash  text        NOT NULL,
    signature     text,
    xlayer_tx     text,
    -- Feature 2 (GATE 3b-2): {score, threshold, autonomous, service_key, signals}, computed by
    -- concierge/confidence.py. NULL for decisions the feature does not apply to (spam, prose
    -- fees, escalations) — never a fabricated score standing in for "not applicable".
    confidence    jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Idempotent add for a table that may already exist from before Feature 2.
ALTER TABLE receipts ADD COLUMN IF NOT EXISTS confidence jsonb;

CREATE INDEX IF NOT EXISTS receipts_tenant_thread ON receipts (tenant_id, thread_id);

-- Feature 1 (Product-Gap Intelligence, GATE 3b/8b-1). Instrumentation on the ESCALATE
-- transition Phase 3 already has for "asked about something not in the profile" — no new
-- decision logic, this table only records that it happened. Isolated by the SAME RLS policy
-- pattern as every other tenant table (§2b) — no new isolation mechanism for this feature.
CREATE TABLE IF NOT EXISTS gap_events (
    gap_id              uuid PRIMARY KEY,
    tenant_id           uuid        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    thread_id           uuid        REFERENCES threads(thread_id) ON DELETE CASCADE,
    raw_query_text      text        NOT NULL,
    -- NULL until concierge/gaps.py's optional LLM categorization runs (needs OPERATOR_PROVIDES
    -- item 7). Absent a key, gaps are reported as raw, unclustered text — never silently
    -- omitted and never a fabricated category.
    classified_category text,
    escalated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS gap_events_tenant ON gap_events (tenant_id, escalated_at);

-- ---------------------------------------------------------------- row-level security

ALTER TABLE tenants     ENABLE ROW LEVEL SECURITY;
ALTER TABLE threads     ENABLE ROW LEVEL SECURITY;
ALTER TABLE receipts    ENABLE ROW LEVEL SECURITY;
ALTER TABLE gap_events  ENABLE ROW LEVEL SECURITY;

-- current_setting(..., true) returns NULL rather than raising when the GUC is unset.
-- NULL = uuid evaluates to NULL, which is not true, so an unscoped session sees zero rows
-- and can write nothing. Fail-closed by construction.
CREATE OR REPLACE FUNCTION current_tenant() RETURNS uuid
    LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

DROP POLICY IF EXISTS tenant_isolation ON threads;
CREATE POLICY tenant_isolation ON threads
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

DROP POLICY IF EXISTS tenant_isolation ON receipts;
CREATE POLICY tenant_isolation ON receipts
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

DROP POLICY IF EXISTS tenant_isolation ON gap_events;
CREATE POLICY tenant_isolation ON gap_events
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

-- ---------------------------------------------------------------- the deliberate doors
--
-- Resolution is a chicken-and-egg problem: an inbound email arrives addressed to
-- acme@inbox.example.com and we must learn which tenant that is BEFORE we can scope a session.
--
-- These functions are the only way across that gap. They are SECURITY DEFINER (they run as the
-- table owner, so RLS does not apply to them) and they are deliberately narrow: they take
-- untrusted external input and return only what that one specific job needs. Even a total
-- compromise of a resolver leaks one identifier or one row, never a list, never a business.
--
-- They are also the only functions in the system with this property, which keeps the audit
-- surface for §2b and Feature 3's public verification (GATE 6b) to exactly this block.

CREATE OR REPLACE FUNCTION resolve_tenant_by_inbound_address(addr text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT tenant_id FROM tenants WHERE inbound_address = lower(btrim(addr)) $$;

-- Auto-provisioning (the provisioning suite) has the same chicken-and-egg problem as inbound mail, one step
-- earlier: a message arrives over A2A carrying a job id, and we must learn which tenant that job
-- belongs to before we can scope a session. This is the inbound-address resolver's exact shape —
-- untrusted external input in, one opaque uuid out — deliberately, rather than a new mechanism.
-- A caller who guesses job ids learns only whether one is taken, never whose or anything about it.
CREATE OR REPLACE FUNCTION resolve_tenant_by_a2a_job(job text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT tenant_id FROM tenants WHERE a2a_job_id = btrim(job) $$;

CREATE OR REPLACE FUNCTION resolve_tenant_by_engagement(ref text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT tenant_id FROM tenants WHERE engagement->>'escrow_ref' = btrim(ref) $$;

-- Feature 3 (public receipt verification, GATE 6b): the client who received a quote should be
-- able to verify it, without CONCIERGE handing out a general receipts-read capability. This
-- returns AT MOST ONE row, keyed only by receipt_id — never tenant_id, never thread_id, so
-- there is no column in its own output that could be used to ask for a second row. A caller who
-- does not already hold a receipt_id (a random UUID, not enumerable) learns nothing.
CREATE OR REPLACE FUNCTION public_receipt(rid uuid) RETURNS TABLE (
    receipt_id uuid, action text, decision jsonb, rule_checked text, within_rules boolean,
    content_hash text, signature text, xlayer_tx text, created_at timestamptz
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT receipt_id, action, decision, rule_checked, within_rules, content_hash, signature,
          xlayer_tx, created_at
   FROM receipts WHERE receipt_id = rid $$;

-- Phase 8's scheduled worker has the opposite problem to the resolvers above: it is not answering
-- an inbound message, so nothing hands it a tenant to scope to. It must ask "which tenants exist"
-- before it can open a scoped session for each.
--
-- That IS the list this block's comment says a door must never return, so it is fenced differently
-- rather than waved through. It returns ONLY opaque uuids — no address, no business name, no
-- profile — and EXECUTE is granted to `concierge_worker` alone, NEVER to `concierge_app`. That
-- split is the point: `concierge_app` is the role the internet-facing webhook runs as, and it can
-- set `app.tenant_id` to any value it likes, so handing IT enumeration would turn a compromise of
-- the web app into "read every tenant". The worker role, in exchange, is granted no table access
-- at all (see the grants below) — it can enumerate ids and do nothing else with them. Neither
-- role alone can both list tenants and read their rows.
CREATE OR REPLACE FUNCTION scheduler_tenant_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT tenant_id FROM tenants ORDER BY created_at $$;

-- ---------------------------------------------------------------- the application role

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'concierge_app') THEN
        CREATE ROLE concierge_app LOGIN PASSWORD 'concierge_app';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO concierge_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, threads, receipts, gap_events TO concierge_app;
GRANT EXECUTE ON FUNCTION resolve_tenant_by_inbound_address(text) TO concierge_app;
GRANT EXECUTE ON FUNCTION resolve_tenant_by_a2a_job(text) TO concierge_app;
GRANT EXECUTE ON FUNCTION resolve_tenant_by_engagement(text) TO concierge_app;
GRANT EXECUTE ON FUNCTION public_receipt(uuid) TO concierge_app;
GRANT EXECUTE ON FUNCTION current_tenant() TO concierge_app;

-- Explicitly withhold the escape hatches. Without BYPASSRLS (default) and without table
-- ownership, `SET row_security = off` does not help concierge_app: Postgres raises
-- "query would be affected by row-level security policy" instead of returning rows.
ALTER ROLE concierge_app NOBYPASSRLS;

-- Note what concierge_app is NOT granted: scheduler_tenant_ids(). The webhook role cannot
-- enumerate tenants. GATE 8 check 9 proves this by trying it and being refused.

-- ---------------------------------------------------------------- the worker role

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'concierge_worker') THEN
        CREATE ROLE concierge_worker LOGIN PASSWORD 'concierge_worker';
    END IF;
END
$$;

-- The whole grant list for this role, deliberately: it may call one function that returns opaque
-- uuids. No SELECT, no INSERT, no UPDATE, no DELETE, on any table. The scheduler uses this
-- connection ONLY to learn which tenants exist, then opens a normal RLS-scoped `tenant_session`
-- as concierge_app to do the actual per-tenant work. Stealing this credential yields a list of
-- uuids and no data whatsoever.
GRANT USAGE ON SCHEMA public TO concierge_worker;
GRANT EXECUTE ON FUNCTION scheduler_tenant_ids() TO concierge_worker;
ALTER ROLE concierge_worker NOBYPASSRLS;

REVOKE EXECUTE ON FUNCTION scheduler_tenant_ids() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION scheduler_tenant_ids() FROM concierge_app;
