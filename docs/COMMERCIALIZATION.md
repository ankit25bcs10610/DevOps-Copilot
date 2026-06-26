# Commercialization — multi-tenant SaaS layer

DevOps-Copilot ships an **additive, opt-in** commercial layer that turns the
single-tenant demo into a multi-tenant B2B SaaS. Everything below is gated by
`COPILOT_MULTI_TENANT` (default **off**) — when off, the app runs exactly as the
offline single-artifact demo, byte-for-byte. It was built in the research-prescribed
order: establish the isolation seam first, prove it leak-free, then layer features
behind it.

## Architecture

```
request ─► require_auth ─► resolve dcp_ API key (TenantStore)
                         └► TenantConfig on a contextvar  (app/tenant_context.py)
                                │
   runtime.py accessors ◄───────┤  per-tenant provider/keys/github/sources
   mcp/client.py env   ◄────────┤  per-tenant decrypted integration secrets
   _scoped(thread_id)  ◄────────┘  org-namespaced investigations (no cross-tenant resume)
```

- **Tenant context seam** (`app/tenant_context.py`) — a frozen `TenantConfig` on a
  `ContextVar`; deny-by-default (no context ⇒ no tenant). `run_with_tenant()`
  re-establishes it inside background tasks / SSE generators. Proven by a
  build-blocking `tests/test_tenant_isolation.py`.
- **Per-tenant config** (`app/runtime.py`) — every accessor reads the tenant's
  config when set (never leaking the host's `.env` keys to a tenant), else the
  global/`.env` path verbatim.
- **Tenant store** (`app/tenancy/`) — async (aiosqlite; Postgres-swappable) store
  for orgs, members, API keys, encrypted integration secrets, and usage. API keys
  are stored as `dcp_<prefix>_<sha256>` (constant-time resolve, revocation).

## What ships (offline-buildable, tested)

| Capability | Where |
|---|---|
| **Orgs + RBAC** (viewer/responder/admin/owner permission matrix) | `app/tenancy/models.py` |
| **Tenant-scoped API keys** (issue/resolve/revoke, hashed at rest) | `app/tenancy/store.py` |
| **Per-tenant config + encrypted integration secrets** | `runtime.py`, `mcp/client.py`, Fernet vault |
| **Investigation isolation** (org-namespaced threads) | `_scoped()` in `app/api/main.py` |
| **Tenant-resolving auth + RBAC route gates** | `require_auth`, `require_perm` |
| **Usage metering + plan quotas** (free/team/enterprise, 402 hard-cap, `/usage`) | `app/metering.py` |
| **Admin API** (org, members, API keys, integrations, plan) | `/admin/*` |
| **CLI org provisioning** | `python -m app.cli provision-org` |
| **PII/secret redaction** of telemetry before the LLM/checkpoint | `app/redaction.py` |
| **Tamper-evident audit** (hash-chained, `/audit/verify`) | `app/audit.py` |

## Quickstart (multi-tenant)

```bash
# 1. bootstrap a tenant (writes to the tenant store)
COPILOT_TENANT_DB=./tenants.sqlite \
  python -m app.cli provision-org --name "Acme" --email owner@acme.com --plan team
# -> prints the owner API key (dcp_…)  [shown once]

# 2. run the API in multi-tenant mode
COPILOT_MULTI_TENANT=true COPILOT_TENANT_DB=./tenants.sqlite \
  COPILOT_SECRET_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
  uvicorn app.api.main:app

# 3. tenants authenticate with their key
curl -H "Authorization: Bearer dcp_…" localhost:8000/config
curl -H "Authorization: Bearer dcp_…" -X POST localhost:8000/admin/integrations \
     -d '{"name":"DD_API_KEY","value":"<datadog key>"}'
```

## Plans & quotas (`app/tenancy/models.py`)

| Plan | Investigations/mo | Seats | Integrations | API keys |
|---|---|---|---|---|
| free | 50 | 3 | 2 | 2 |
| team | 1000 | 25 | 10 | 20 |
| enterprise | unlimited | unlimited | unlimited | unlimited |

## Deliberately deferred (need live infra; mapped, not done)

- **Phase 3 — per-tenant envelope encryption (DEK/KEK + rotation).** The vault
  already encrypts secrets at rest with a single Fernet key; per-tenant DEKs add
  blast-radius isolation + crypto-shredding. (offline-buildable; not yet done.)
- **Phase 7 — Postgres + Row-Level Security.** The SQLite store is app-level
  isolated (WHERE org_id), not RLS-hard-isolated; production wants asyncpg + RLS
  under a non-owner role. (needs a live Postgres.)
- **Phase 8 — Stripe metered billing + SSO/SAML/OIDC + SCIM.** The local usage
  ledger is the system of record a Stripe sync worker reads; SSO/SCIM go through a
  broker (WorkOS/Auth0). (needs live keys / an IdP.)

These are sequenced in the deep-research plan; the offline foundation is complete
so each is a localized addition behind the existing seam.
