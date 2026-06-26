# Usage

Runs **fully offline** out of the box (no GitHub or cloud accounts) — only an LLM API
key is required. All five providers ship in the base install.

## 1 · Backend

```bash
uv venv && uv pip install -e .
cp .env.example .env
#   anthropic (default):  ANTHROPIC_API_KEY=sk-ant-...        (Claude Opus 4.8)
#   or e.g.:              COPILOT_PROVIDER=groq  GROQ_API_KEY=gsk_...
#   (openai · gemini · deepseek also supported — set COPILOT_PROVIDER + its key)
```

### Try it from the CLI
```bash
uv run copilot "Why is the checkout API throwing 500 errors?"
```
Watch it plan, call MCP tools across services, find the bug in
`sample_repo/checkout.js`, and **ask permission before opening a PR**.

### Run the API
```bash
uv run uvicorn app.api.main:app --reload      # http://localhost:8000
```
It also serves the built console once you've run the frontend build.

## 2 · Frontend (dev server with hot reload)

```bash
cd frontend
npm install
cp .env.example .env          # VITE_API_URL=http://localhost:8000
npm run dev                   # http://localhost:5173
```

## 3 · One Docker image (the whole product)

```bash
docker compose up --build     # http://localhost:8000  (SPA + API)
```
A multi-stage build compiles the React + WebGL console and serves it from FastAPI. It
runs as non-root, persists the SQLite checkpoint DB to a volume, and ships a
`HEALTHCHECK`. Production setup (auth token, `COPILOT_ENV=production`, limits, same-origin
auth) is in [`DEPLOY.md`](../DEPLOY.md).

## How a session works

1. Ask a question (CLI, web console, or `POST /chat[/stream]`).
2. The agent investigates, streaming a live activity trace over SSE.
3. If it wants to **write** (open a PR, scale a deployment, …), it pauses; you approve or
   reject on the approval card (or via Slack buttons if triggered by a PagerDuty webhook).
4. On completion you get a **structured RCA report** + a downloadable **postmortem**, and
   can thumbs-up/down to feed the eval loop.

## Triggered mode (the product loop)

Point a signed **PagerDuty webhook** at `POST /webhooks/pagerduty`: the agent auto-starts
an investigation and posts findings to **Slack** with Approve/Reject buttons that resume
it through the same approval gate — the agent shows up when you're paged. See
[Configuration](CONFIGURATION.md) for the secrets involved.

## Multi-tenant mode

For the commercial SaaS layer (orgs, RBAC, API keys, quotas), see
[Commercialization](COMMERCIALIZATION.md).
