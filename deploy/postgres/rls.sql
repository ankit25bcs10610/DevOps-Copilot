-- DevOps Copilot — Postgres schema + Row-Level Security for hard tenant isolation.
--
-- The SQLite store enforces isolation at the application layer (every query filters
-- WHERE org_id = ?). Production wants defense-in-depth: RLS in the database so a
-- missing WHERE clause (or a compromised query) still cannot cross tenants. The app
-- connects as a NON-OWNER role and sets the current org per transaction:
--
--     SET LOCAL app.current_org = '<org_id>';
--
-- Every RLS policy below constrains rows to that org. The owner/migration role
-- bypasses RLS to run this file; the runtime role (copilot_app) is subject to it.

-- 1) Roles ------------------------------------------------------------------
--    (Run as a superuser/owner; the app then connects as copilot_app.)
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_app') THEN
    CREATE ROLE copilot_app LOGIN;
  END IF;
END $$;

-- 2) Tables (mirror app/tenancy/store.py) -----------------------------------
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL,
    created_at TEXT NOT NULL, stripe_customer_id TEXT DEFAULT '', wrapped_dek TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, hash TEXT NOT NULL, name TEXT DEFAULT '',
    role TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT DEFAULT '',
    revoked_at TEXT DEFAULT '', expires_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS integration_secrets (
    org_id TEXT NOT NULL, name TEXT NOT NULL, value_encrypted TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (org_id, name)
);
CREATE TABLE IF NOT EXISTS usage (
    org_id TEXT NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL, ts TEXT NOT NULL,
    meta TEXT DEFAULT '{}', event_key TEXT DEFAULT ''
);

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO copilot_app;

-- 3) Row-Level Security: one policy per org-scoped table --------------------
--    Rows are visible/writable only when org_id matches the session's current org.
--    `orgs` is scoped by its own id.
ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;
CREATE POLICY orgs_isolation ON orgs
    USING (id = current_setting('app.current_org', true))
    WITH CHECK (id = current_setting('app.current_org', true));

ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY memberships_isolation ON memberships
    USING (org_id = current_setting('app.current_org', true))
    WITH CHECK (org_id = current_setting('app.current_org', true));

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY api_keys_isolation ON api_keys
    USING (org_id = current_setting('app.current_org', true))
    WITH CHECK (org_id = current_setting('app.current_org', true));

ALTER TABLE integration_secrets ENABLE ROW LEVEL SECURITY;
CREATE POLICY integration_secrets_isolation ON integration_secrets
    USING (org_id = current_setting('app.current_org', true))
    WITH CHECK (org_id = current_setting('app.current_org', true));

ALTER TABLE usage ENABLE ROW LEVEL SECURITY;
CREATE POLICY usage_isolation ON usage
    USING (org_id = current_setting('app.current_org', true))
    WITH CHECK (org_id = current_setting('app.current_org', true));

-- Force RLS even for the table owner in case the app ever connects as owner.
ALTER TABLE orgs FORCE ROW LEVEL SECURITY;
ALTER TABLE memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE integration_secrets FORCE ROW LEVEL SECURITY;
ALTER TABLE usage FORCE ROW LEVEL SECURITY;
