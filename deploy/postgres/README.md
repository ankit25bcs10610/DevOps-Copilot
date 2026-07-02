# Postgres tenant store + Row-Level Security

The SQLite `TenantStore` isolates tenants at the application layer (`WHERE org_id = ?`).
For production, `deploy/postgres/rls.sql` provisions the same schema in Postgres with
**Row-Level Security** so isolation is enforced by the database — a missing/incorrect
`WHERE` can't leak across tenants.

## Model
- The app connects as a **non-owner** role (`copilot_app`) that is subject to RLS.
- Per request/transaction, the app sets the active org:
  ```sql
  SET LOCAL app.current_org = '<org_id>';
  ```
- Every policy constrains rows to `org_id = current_setting('app.current_org')`
  (`orgs` by its own `id`). `FORCE ROW LEVEL SECURITY` applies it even to the owner.

## Apply
```bash
psql "$DATABASE_URL" -f deploy/postgres/rls.sql        # as a superuser/owner
```

## Wiring the asyncpg store (the remaining live step)
`app/tenancy/store.py` currently rejects `postgres://` URLs with a clear error. An
asyncpg-backed `TenantStore` implementing the same async interface — and issuing
`SET LOCAL app.current_org` from the request's `tenant_context` at the start of each
transaction — is the drop-in that makes RLS active. That step needs a live Postgres
to validate (connection pooling, `SET LOCAL` semantics, and a cross-tenant isolation
test that asserts a query under org A returns zero of org B's rows), so it's left for
deployment. The schema + policies here are the security artifact it targets.
