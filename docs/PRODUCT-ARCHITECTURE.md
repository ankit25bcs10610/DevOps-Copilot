# Product Architecture — demo → multi-tenant SaaS

How to evolve **this** codebase from a single-tenant reference implementation into a
commercial, multi-tenant "AI on-call investigator." Every section maps the change
onto the existing `app/` modules and names the specific constructs to refactor, so
this is a build map, not a wishlist.

> Read alongside [`DEPLOY.md`](../DEPLOY.md) §6 (single-instance limits) and
> [`ARCHITECTURE.md`](ARCHITECTURE.md) (the current design).

---

## 0. Where we are, honestly

What carries forward unchanged (the good bones):

- The **LangGraph state machine** (`app/graph/`) — plan → agent → approval → tools →
  reflect, the bounded iteration loop, and the resumable `interrupt()` **human-approval
  gate**. This is the core IP and the enterprise trust story; it does not change.
- The **MCP tool abstraction** (`app/mcp/`) — the agent only sees tools, never servers.
  This is exactly the seam you extend with real integrations.
- The **provider factory** (`app/llm.py`) — multi-provider + adaptive thinking, so a
  tenant can bring their own model/key for data-residency and cost reasons.
- **Streaming** (`/chat/stream`, `/approve/stream`) and the **hardening** (auth,
  rate limiting, probes, structured logs + request-id, graceful shutdown).

What is single-tenant-by-design and must change (the work):

| Constraint today | Where it lives |
|---|---|
| One shared bearer token; no orgs/users/roles | `app/api/main.py` `require_auth` |
| **Process-global mutable config** (`_ov`, `_keys`) shared across all callers | `app/runtime.py` |
| In-process session pool, in-memory rate limiter | `app/api/main.py` `_SESSIONS`, `_RL`, `_ConfigGate` |
| SQLite checkpointer on local disk | `app/graph/builder.py` `make_checkpointer` |
| MCP servers read **sample fixtures** | `app/mcp/servers/*` |
| User must type a question; no auto-trigger | `app/api/main.py` chat endpoints |
| Secrets in `.env` / in-memory | `app/config.py`, `app/runtime.py` |

**The single biggest refactor is `app/runtime.py`.** It's a module-global store
(`_ov`, `_keys`) — fine for one user, fatal for many: tenant A's model/key/GitHub
config would leak to tenant B. Everything tenant-specific must become *request-scoped*,
resolved from the tenant's record. Plan for this first; it touches `llm.py`, `mcp/`,
and every node that calls `get_llm()` / `runtime.*`.

---

## 1. Target architecture

```
                         ┌──────────────────────────────────────────┐
   Slack / PagerDuty ───▶│  Ingress API (FastAPI)                   │
   Web console      ───▶│  authn/z · org scoping · rate/quota      │
   Alert webhooks   ───▶│  enqueue investigation jobs              │
                         └───────────────┬──────────────────────────┘
                                         │ (job: org_id, incident, source refs)
                                         ▼
                              ┌─────────────────────┐     ┌──────────────────┐
                              │  Job queue          │────▶│  Agent workers    │  (N, stateless)
                              │  (Redis/SQS)        │     │  LangGraph + MCP  │
                              └─────────────────────┘     └─────────┬────────┘
                                                                    │
         ┌──────────────────────┬───────────────────┬──────────────┴───────────────┐
         ▼                      ▼                   ▼                                ▼
  ┌─────────────┐      ┌─────────────────┐  ┌──────────────────┐         ┌────────────────────┐
  │ Postgres    │      │ Secret vault    │  │ Remote MCP        │         │ Tenant integrations│
  │ checkpointer│      │ (KMS-encrypted, │  │ connectors (HTTP) │────────▶│ Datadog · GitHub · │
  │ + app data  │      │  per-tenant)    │  │ per-tenant creds  │         │ Slack · PagerDuty… │
  └─────────────┘      └─────────────────┘  └──────────────────┘         └────────────────────┘
```

Split into a **control plane** (orgs, users, integrations, billing, audit — CRUD,
low rate) and a **data plane** (the agent workers running investigations — stateless,
horizontally scaled, all shared state in Postgres + the queue). The current single
process becomes: an **ingress/API** service + a **worker** pool, both stateless.

---

## 2. Multi-tenancy & isolation

**Identity & access.** Replace the single-token `require_auth` with real authn/z:

- `app/api/main.py` → introduce `Principal{org_id, user_id, roles}` resolved from a
  session cookie or JWT (add SSO/SAML/OIDC for enterprise). Make `org_id` a required
  dependency on every data-plane route; never trust a client-supplied org.
- Add **RBAC**: roles like `viewer` (read investigations), `responder` (approve/reject
  writes), `admin` (manage integrations/secrets). The approval gate (`approval_node`)
  should check `responder` before accepting a decision.
- Namespace every `thread_id` by org (`{org_id}:{thread}`) so checkpointer state and
  sessions can never collide across tenants.

**Kill the global config.** `app/runtime.py`'s `_ov`/`_keys` dicts become a
per-tenant record (Postgres row + a short-TTL cache). Concretely:

- Replace `runtime.provider()`, `provider_key()`, `model_override()`,
  `repo_path()`, `github_token()`, etc. with functions that take an explicit
  `tenant: TenantConfig` (or a context-var set per request/job) instead of reading
  module globals.
- `app/llm.py` `get_llm(...)` takes the tenant's provider/model/key.
- `app/graph/nodes.py` builds the graph per investigation with the tenant's tools and
  model — so `make_agent_node` / `make_plan_node` close over tenant-resolved `get_llm`.
- `app/api/main.py` `_evict_all()` / `_ConfigGate` logic (which exists to protect a
  *global* config swap) largely disappears — config is per-tenant data, not a process
  global, so a tenant updating their model no longer needs to drain everyone's turns.

This is the heavy lift but it's mechanical: thread a `tenant`/`Principal` through the
call sites that currently read `runtime.*`.

---

## 3. Per-tenant secret vault

Today keys live in `.env` (`app/config.py`) and in-memory (`runtime._keys`). Productize:

- A **secret vault** service: per-tenant secrets (LLM keys, Datadog API key, GitHub
  app token, Slack token) encrypted at rest with envelope encryption (cloud KMS /
  Vault). Store ciphertext in Postgres; decrypt only in the worker at use time.
- `app/runtime.py` secret getters → fetch from the vault by `(org_id, integration)`.
- MCP connector processes get credentials injected at init (env or a short-lived
  fetch), **never** written to logs. You already keep secrets out of logs and validate
  GitHub creds before storing (`/github/connect`) — generalize that pattern to every
  integration: validate on connect, store encrypted, surface metadata only.
- Offer **bring-your-own-LLM-key** (and a self-host mode) so customers who can't send
  logs to a third party can still buy.

---

## 4. State & scale (SQLite → Postgres, stateless workers)

- **Checkpointer:** swap `make_checkpointer()` (`app/graph/builder.py`) from
  `AsyncSqliteSaver` to `langgraph-checkpoint-postgres` (`AsyncPostgresSaver`) pointed
  at a managed Postgres. State is already keyed by `thread_id`, so this is the main
  change — and it makes the approval interrupt resumable from **any** worker, not just
  the process that started it.
- **Sessions:** the in-process `_SESSIONS` pool + `_ConfigGate` + per-thread locks
  (`app/api/main.py`) were the right call for one process; in the worker model they go
  away. Each investigation is a **job**: a stateless worker loads the graph, runs to the
  next interrupt or completion, persists via the checkpointer, and exits. An approval
  decision enqueues a "resume" job. This removes the LRU eviction / reconstruction
  machinery entirely.
- **Queue:** Redis/SQS between ingress and workers; gives backpressure, retries, and
  fair scheduling across tenants (per-tenant concurrency caps).
- **Rate limiting:** move the in-memory `_RL` limiter behind Redis (or the gateway) so
  it's shared across instances; key it by `org_id`, not just IP.
- **MCP:** run connectors as **remote HTTP MCP servers** (or a connector service)
  instead of per-worker stdio subprocesses — so workers stay light and connectors scale
  and deploy independently. `MultiServerMCPClient` already supports URL transports.

---

## 5. Demo MCP → real connectors (the moat)

The integration layer is 80% of the product and the main defensibility. Keep the MCP
seam; replace the fixtures:

| Today (fixtures) | Product connector(s) |
|---|---|
| `logs_metrics` (sample files) | Datadog, Grafana/Prometheus, CloudWatch, Splunk, Sentry |
| `repo` (local sandbox) | GitHub / GitLab / Bitbucket (read + PR) |
| `github` (live/offline) | GitHub/GitLab app (scoped install token per org) |
| — | Kubernetes (events, pod logs, describe), PagerDuty/Opsgenie, Slack |

Design notes:

- A **connector registry per tenant**: admin connects Datadog + GitHub + Slack; the
  worker builds the MCP tool set from *that tenant's enabled connectors* with *their*
  vaulted credentials. This generalizes today's `MCP_CATALOG` + `load_mcp_tools`.
- Keep tools **read-only by default**; only specific actions (open PR, post Slack,
  ack/resolve page) are writes — and every write routes through the existing approval
  gate (`WRITE_TOOLS` becomes per-connector). The path-sandbox discipline in the repo
  server (`_safe_path`, realpath boundary) is the template for safe connectors.
- Connectors are the unit of work you ship continuously; the agent core rarely changes.

---

## 6. Triggering & delivery (the actual product loop)

Today a human types a question. The product auto-triggers and delivers to where on-call
already lives:

- **Ingress webhooks**: `POST /webhooks/pagerduty`, `/webhooks/alertmanager`,
  `/webhooks/sentry` — verify the signature, resolve the org, enqueue an investigation
  job seeded with the alert context (service, time window, links).
- **Slack app**: post the streamed findings into the incident channel; render the
  approval as Slack interactive buttons (Approve / Reject) that call back into
  `/approve` for that `thread_id`. This is the existing `interrupt()` flow, just with
  Slack as the approval UI instead of the web card.
- The web console stays as the deep-dive/replay surface and for orgs without Slack.

This reuses everything you have — the graph, streaming events, and the approval
interrupt — and turns "ask the bot" into "the bot shows up when you're paged."

---

## 7. Security & compliance (enterprise table stakes)

- **Audit trail:** you already stamp every request with a `request_id` and log
  structured JSON. Promote that to an **immutable audit log** in Postgres: who triggered
  what, every tool call, every approval decision + approver + the evidence shown. This
  is both a compliance artifact and a trust feature.
- **Least privilege:** scoped, revocable per-integration tokens (GitHub *app* install
  tokens, read-only Datadog keys). Never a god-token.
- **Data handling:** tenant-scoped retention controls, PII redaction on ingested logs,
  region pinning, and a **self-host / VPC** deployment (your single-image Docker already
  makes this credible) — the #1 objection-remover for security-conscious buyers.
- **Compliance path:** SOC 2 Type II, SSO/SCIM for the enterprise tier.
- Keep `COPILOT_ENV=production` fail-closed; extend it to require vault + DB config too.

---

## 8. Cost control, metering & reliability

- **Per-tenant LLM budgets:** you already log per-call token usage (`_log_usage`) and
  rate-limit. Aggregate usage → enforce per-org monthly quotas and soft/hard caps so a
  runaway investigation can't blow a tenant's (or your) budget.
- **Metering → billing:** emit usage events (tokens, investigations, connected services)
  to a metering store → Stripe for invoicing.
- **Reliability:** queue retries with idempotency, per-tenant concurrency limits, SLOs
  on time-to-first-finding, and circuit-breakers around flaky integrations (one
  connector down must not fail the whole investigation — degrade gracefully).

---

## 9. Accuracy & the eval loop (what actually wins deals)

A demo wins the meeting; correct root-cause on *their* data wins the contract.

- Extend `evals/` into **per-tenant golden incidents**: capture resolved incidents and
  replay the agent against them to track recall/precision over time.
- Close the feedback loop: the approve/reject decision + a thumbs-up/down on the
  diagnosis becomes labeled data. Feed it into prompt/eval tuning. The `reflect` node's
  gap-feedback mechanism is the in-loop version of this; the eval harness is the
  offline version.

---

## 10. Phasing (maps to the MVP build order)

1. **Wedge + connectors** — pick one stack (e.g. Datadog + GitHub + Slack + PagerDuty);
   replace the 3 demo MCP servers with real ones; per-tenant creds from a minimal vault.
2. **Tenant-scope the core** — refactor `runtime.py` globals → per-tenant config;
   thread `Principal`/`tenant` through `llm.py` + nodes; org-namespaced `thread_id`.
3. **Stateless data plane** — Postgres checkpointer; queue + worker; Slack approval
   buttons; alert webhook → job. (Retire the in-process session pool.)
4. **Trust & scale** — audit log, RBAC/SSO, KMS vault, per-tenant quotas/metering,
   self-host/VPC option.
5. **Accuracy flywheel** — golden-incident evals + feedback loop; then turn on pricing
   with design partners.

The order matters: do **(1)** to prove value on real data, **(2)** before any real
customer touches it (isolation is non-negotiable), and **(3)** before you have more than
one busy tenant.

---

## Summary: what changes vs. what stays

- **Stays:** LangGraph graph + approval interrupt, MCP tool seam, provider factory,
  streaming, the hardening primitives (auth pattern, rate-limit pattern, request-id,
  fail-closed config).
- **Changes:** `runtime.py` globals → per-tenant config (biggest refactor); SQLite →
  Postgres; in-process sessions → queue + stateless workers; demo MCP → real
  connectors; add identity/RBAC/SSO, a KMS secret vault, audit log, webhooks + Slack,
  quotas/metering, and a self-host option.
