# Configuration & API reference

Set configuration in `.env` (see [`.env.example`](../.env.example) for the complete,
commented list). Most model/source settings are also changeable **live from the console
UI** — those overrides are in-memory only, not persisted across restarts.

## Core

| Variable | Default | Description |
|----------|---------|-------------|
| `COPILOT_PROVIDER` | `anthropic` | `anthropic` · `openai` · `gemini` · `groq` · `deepseek` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` / `DEEPSEEK_API_KEY` | — | Key for the active provider |
| `COPILOT_MODEL` / `COPILOT_FAST_MODEL` | per-provider | Override the main / fast model (defaults in `app/llm.py`) |
| `COPILOT_ENV` | `development` | `production` fails closed unless `COPILOT_API_TOKEN` is set |
| `COPILOT_API_TOKEN` | — | Bearer token guarding the API (empty = open, dev only) |
| `COPILOT_MAX_ITERATIONS` | `8` | Max agent steps per turn |
| `COPILOT_MAX_TOKENS_PER_RUN` | `0` | Per-investigation token budget (0 = unlimited) |
| `COPILOT_VERIFY_FIX` | `true` | Verify the proposed fix addresses the root cause (adds the verify node) |
| `COPILOT_VERIFY_MAX_ATTEMPTS` | `1` | Times an unverified fix bounces back to the agent to revise (0 = annotate only) |
| `COPILOT_SANDBOX_VERIFY` | `false` | Prove a fix by applying its `patch` to a throwaway repo copy and running a reproducer (executes model patch — off by default) |
| `COPILOT_SANDBOX_CMD` | `node checkout.test.js` | Reproducer command run before/after the patch (operator-set) |
| `COPILOT_SANDBOX_TIMEOUT_S` | `30` | Wall-clock timeout per sandbox subprocess run |
| `COPILOT_CONFIDENCE_GATE` | `true` | Refuse programmatic auto-approval of a high-risk write on thin evidence (humans can still approve) |
| `COPILOT_LLM_RETRIES` | `3` | Bounded retries + circuit breaker around each LLM call (1 disables) |
| `COPILOT_FALLBACK_PROVIDER` | — | Cross-provider failover when the primary is unavailable (needs that provider's key) |
| `COPILOT_REDIS_URL` | — | Redis URL for the shared cross-replica rate limiter (empty = in-memory, single-instance) |
| `COPILOT_LEARN_INCIDENTS` | `true` | After a confident RCA, append a runbook to the learned corpus so future runs warm-start from it |
| `COPILOT_LEARNED_CORPUS` | *(root)* | Path to the learned-incidents JSON (empty = `learned_incidents.json` in the project root) |
| `COPILOT_ADVERSARIAL_CRITIQUE` | `true` | Prosecutor/Defender panel stress-tests the root cause; abstains/downgrades on a standing objection |
| `COPILOT_MODEL_ROUTING` | `true` | Triage informational requests onto the fast model; incidents stay on the main model |
| `COPILOT_SLO_POLLER` | `false` | Proactively poll error-budget burn and auto-open an investigation before a page (spends tokens) |
| `COPILOT_SLO_POLL_INTERVAL_S` | `300` | SLO poller sweep cadence (seconds) |
| `COPILOT_SLO_COOLDOWN_S` | `3600` | Per-service cooldown before re-opening an SLO investigation |
| `COPILOT_PARALLEL_HYPOTHESES` | `true` | Concurrently score 2+ competing RCA hypotheses against evidence and re-rank |
| `COPILOT_AUTONOMY` | `false` | Enable `POST /remediate` (apply reversible fix → watch → auto-revert). Off = 403 |
| `COPILOT_AUTONOMY_DRYRUN` | `true` | Even when enabled, only simulate remediation unless set false |
| `COPILOT_AUTONOMY_WATCH_S` | `60` | Seconds to watch the signal after a remediation before judging recovery |
| `COPILOT_CHECKPOINT_DB` | `./copilot_checkpoints.sqlite` | SQLite path or a `postgres://…` URL |

## Data sources & connectors
`TARGET_REPO_PATH`, `LOGS_DATA_PATH`, `COPILOT_SOURCES_ROOT`; and per-connector creds
(`DD_*`, `PAGERDUTY_*`, `KUBE_*`, `SENTRY_*`, `TRACES_API_URL`, `DEPLOYS_*`,
`GITHUB_TOKEN`/`GITHUB_REPO`, `CORPUS_PATH`). Each is optional — blank ⇒ that connector
runs offline fixtures. See [Connectors](CONNECTORS.md).

## Triggers / delivery
`PAGERDUTY_WEBHOOK_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL`.

## Production limits & observability
`COPILOT_RATE_LIMIT_PER_MIN`, `COPILOT_MAX_BODY_BYTES`, `COPILOT_MAX_MESSAGE_CHARS`,
`COPILOT_MAX_SESSIONS`, `COPILOT_TRUST_PROXY`, `CORS_ORIGINS`; `LOG_FORMAT`, `SENTRY_DSN`,
`LANGCHAIN_TRACING_V2` + `LANGCHAIN_API_KEY`; `COPILOT_AUDIT_LOG`, `COPILOT_FEEDBACK_LOG`.

## Multi-tenant (opt-in) — see [Commercialization](COMMERCIALIZATION.md)
`COPILOT_MULTI_TENANT`, `COPILOT_TENANT_DB`, `COPILOT_SECRET_KEY`.

## Replay / evals — see [Evaluation](EVALUATION.md)
`COPILOT_REPLAY_MODE` (`off`|`record`|`replay`), `COPILOT_CASSETTE_PATH`.

---

## HTTP API surface

| Endpoint | Purpose |
|----------|---------|
| `POST /chat` · `POST /chat/stream` | Start an investigation (JSON, or live SSE) |
| `POST /approve` · `POST /approve/stream` | Resume a paused approval with a decision |
| `GET /config` · `POST /model/configure` | Inspect / switch provider, model, key |
| `POST /github/connect` · `/github/disconnect` · `GET /github/status` | Live GitHub mode |
| `POST /sources/repo` · `/sources/logs` · `POST /reset` | Point tools at your data / revert |
| `GET /metrics` | Real metric series + error summary |
| `POST /feedback` | Thumbs up/down (feeds the eval loop) |
| `GET /audit` · `GET /audit/verify` | Queryable + hash-chain-verified audit trail |
| `GET /usage` | Per-tenant usage + plan quota (multi-tenant) |
| `/admin/*` | Tenant self-management: org, members, API keys, integrations, plan (RBAC-gated) |
| `POST /webhooks/pagerduty` | PagerDuty trigger → auto-investigate (HMAC-verified) |
| `POST /webhooks/slack/interactions` | Slack Approve/Reject callback (signature-verified) |
| `GET /healthz` · `GET /readyz` | Liveness / readiness probes (auth-exempt) |
