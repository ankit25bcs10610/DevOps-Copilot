# Deploying DevOps Copilot

This guide covers running DevOps Copilot as a **single deployable artifact**: one
container that builds the React SPA, serves it from FastAPI, and runs the agent +
its stdio MCP servers in-process.

> TL;DR for a quick prod-ish launch:
> ```bash
> export COPILOT_ENV=production
> export COPILOT_API_TOKEN="$(openssl rand -hex 24)"
> export ANTHROPIC_API_KEY=sk-ant-...
> docker compose up --build -d
> # open http://localhost:8000  (send Authorization: Bearer $COPILOT_API_TOKEN on API calls)
> ```

---

## 1. What's in the box

- **One image, whole app.** The Dockerfile builds the SPA (stage 1) and serves it
  from FastAPI at `/` (stage 2). API routes (`/chat`, `/config`, …) take
  precedence; everything else is the SPA. So `http://<host>:8000` is the product.
- **Non-root** runtime user (`uid 10001`); SQLite checkpoint state lives on a
  writable volume at `/data`.
- **Probes:** `/healthz` (liveness) and `/readyz` (readiness — 503 in production
  until an LLM key is configured). Both bypass auth. A Docker `HEALTHCHECK` hits
  `/healthz`.
- **Built-in guards:** bearer-token auth, per-IP rate limiting, request-body and
  message-length caps, a bounded session pool, and graceful shutdown (drains an
  in-flight turn for up to 30s before tearing down MCP subprocesses).

## 2. Required configuration

| Variable | Required? | Notes |
|---|---|---|
| `COPILOT_ENV` | yes for prod | `production` makes the app **fail closed** at startup unless `COPILOT_API_TOKEN` is set. |
| `COPILOT_API_TOKEN` | yes for prod | Shared bearer token. Every API call must send `Authorization: Bearer <token>`. Generate with `openssl rand -hex 24`. |
| `COPILOT_PROVIDER` | no | `anthropic` (default) \| `openai` \| `gemini` \| `groq` \| `deepseek`. |
| `<PROVIDER>_API_KEY` | yes (one) | e.g. `ANTHROPIC_API_KEY`. May instead be pasted at runtime via the UI, but then `/readyz` is 503 until it is. |
| `CORS_ORIGINS` | only if SPA is hosted elsewhere | Not needed when the SPA is served same-origin from this backend. |
| `COPILOT_SOURCES_ROOT` | recommended | Confines `/sources/*` (the agent's file tools) to a directory tree. Defaults to the project root. |
| `COPILOT_CHECKPOINT_DB` | no | Defaults to `/data/copilot_checkpoints.sqlite` in the image. |

Safety limits (sensible defaults; override only if needed):
`COPILOT_RATE_LIMIT_PER_MIN` (120), `COPILOT_MAX_BODY_BYTES` (1000000),
`COPILOT_MAX_MESSAGE_CHARS` (16000), `COPILOT_MAX_SESSIONS` (50),
`COPILOT_TRUST_PROXY` (false), `COPILOT_MAX_ITERATIONS` (8). See `.env.example`
for the full list.

## 3. Frontend ↔ backend auth (same-origin)

The SPA is served by the backend, so it calls the API with **relative URLs**
(`VITE_API_URL=""`, baked at image build). When `COPILOT_API_TOKEN` is set, the
browser must send it. Two options:

1. **Bake it into the build** — set `VITE_API_TOKEN` at build time (it ships in
   the JS bundle; only acceptable when the token is effectively a shared gate,
   not a per-user secret).
2. **Front the app with your own auth** (SSO / reverse proxy) and leave
   `COPILOT_API_TOKEN` empty *behind that proxy* — the proxy is then the gate.

For a genuine multi-user product, replace the single shared token with real
per-user auth at a gateway; the bearer check here is a single-tenant gate.

## 4. Run it

**Docker Compose (recommended):**
```bash
cp .env.example .env        # fill in COPILOT_ENV, COPILOT_API_TOKEN, a provider key
docker compose up --build -d
docker compose logs -f copilot
curl -fsS http://localhost:8000/healthz
```

**Plain Docker:**
```bash
docker build -t devops-copilot .
docker run -d -p 8000:8000 \
  -e COPILOT_ENV=production -e COPILOT_API_TOKEN=... -e ANTHROPIC_API_KEY=sk-ant-... \
  -v copilot_state:/data devops-copilot
```

**No Docker (uvicorn):**
```bash
uv sync
( cd frontend && npm ci && VITE_API_URL="" npm run build )   # produces frontend/dist
COPILOT_ENV=production COPILOT_API_TOKEN=... ANTHROPIC_API_KEY=sk-ant-... \
  uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```
For multiple workers put a process manager / `--workers N` in front — but note the
caveat in §6 (in-process state isn't shared across workers yet).

## 4b. Triggers — auto-investigate from PagerDuty, approve in Slack

Beyond the web console, the agent can be driven by your incident pipeline:

- **`POST /webhooks/pagerduty`** — point a PagerDuty v3 webhook here. It's
  HMAC-verified with `PAGERDUTY_WEBHOOK_SECRET`; an `incident.triggered` event
  auto-starts an investigation (keyed by the incident id). Needs an LLM key, or
  it accepts and logs `accepted_no_llm`.
- **Slack delivery** — set `SLACK_BOT_TOKEN` + `SLACK_CHANNEL` and findings post
  to the channel; a pending write renders as **Approve / Reject** buttons.
- **`POST /webhooks/slack/interactions`** — set this as your Slack app's
  Interactivity Request URL. It's verified with `SLACK_SIGNING_SECRET`, and a
  button click resumes the agent through the same approval gate.

Both webhook routes bypass the bearer token (they authenticate via their own
provider signatures) but are still body-size- and rate-limited. They need a
public HTTPS URL (i.e. a deployed instance).

## 5. Observability

Set `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` to ship traces to LangSmith
(`app/observability.py` wires the env on startup). Structured logs go to stdout.

## 6. Horizontal scaling (multi-instance)

The shared-state seams are now in place — set these and run `replicaCount > 1`
(the Helm chart in `deploy/helm/` wires an HPA + PDB):

- **Shared session state.** Set `COPILOT_CHECKPOINT_DB` to a `postgres://...` URL
  (install the `postgres` extra) — `make_checkpointer()` switches to the Postgres
  saver automatically, and because graph state is keyed by `thread_id`, an evicted
  session **rehydrates from the checkpointer on any replica** (`_get_session`).
- **Shared limiter / job queue / spend cap.** Set `COPILOT_REDIS_URL` to a
  `redis://...` URL — the rate limiter (`app/ratelimit.py`), the durable
  investigation queue (`app/jobqueue.py`), and the fleet-wide token cap
  (`app/spend.py`) all switch to the Redis backend so limits/jobs/budgets hold
  across replicas.
- **Secrets + observability.** Source secrets from a manager
  (`COPILOT_SECRETS_PROVIDER=aws|vault`, or External Secrets → the chart's Secret),
  export traces (`OTEL_EXPORTER_OTLP_ENDPOINT`), and scrape agent SLOs at
  `GET /metrics/slo` (Grafana dashboard + Prometheus alerts in `deploy/observability/`).

### Still single-instance-only
- **MCP servers run as in-container stdio subprocesses** — one per server, held
  open for each live session (so at most `COPILOT_MAX_SESSIONS` × 3 processes;
  the cap defaults to 50 and idle sessions are evicted LRU). For isolation/scale,
  run them as remote HTTP MCP servers (`app/mcp/client.py`).

These are deliberately out of scope for the single-container artifact this guide
deploys; the code is structured so each is a localized change.
