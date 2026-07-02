<div align="center">

# DevOps Copilot

**An autonomous incident-investigation agent.** It pulls logs, metrics, traces, Kubernetes state and recent deploys, reads the code and git history, finds the root cause, and drafts the fix as a pull request — **pausing for human approval before it ever writes.**

[![CI](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-stateful%20agent-1C3C3C)
![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-7C5CFF)

LangGraph 7-node graph · 9 MCP servers / 47 tools · structured RCA + postmortem · fix verification · human-in-the-loop · opt-in multi-tenant SaaS · runs fully offline

</div>

<p align="center">
  <img src="docs/screenshots/hero.png" alt="DevOps Copilot — incident command center" width="100%">
</p>

---

## What it does

Ask *"Why is the checkout API throwing 500s?"* and a **LangGraph** state machine drives an
LLM across nine **MCP** tool servers: it checks whether the incident has happened before,
gathers logs · metrics · traces · K8s · Sentry, correlates the failure to a recent deploy,
finds the bug, and proposes a pull request — **stopping for your approval before any write.**
Every run ends in a **structured root-cause report** (ranked hypotheses with cited evidence,
severity, calibrated confidence) and a downloadable **blameless postmortem**. Progress streams
live to a React console.

It runs **fully offline** out of the box (bundled fixtures; only an LLM key needed), and ships
the production concerns — auth, rate limiting, health probes, structured logging, a token-cost
kill-switch, prompt-injection guardrails, a risk-tiered approval policy, a tamper-evident audit
trail, deterministic replay evals, tests, CI, and a single Docker image — **actually built.**

## Highlights

- **Structured RCA + postmortem** — typed, ranked, *validated/invalidated/inconclusive*
  hypotheses with cited evidence; abstains on thin evidence instead of bluffing.
- **Human-in-the-loop, by design** — a risk-tiered policy gates every consequential action
  behind a resumable approval the routing can't bypass.
- **9 MCP servers / 47 tools** — datadog · pagerduty · kubernetes · sentry · traces · deploys ·
  github · repo · incident-memory, each **live-API or offline-fixture**, plus deterministic
  analysis tools (blast-radius, critical-path, SLO burn-rate, anomaly→trace, deploy-bisect).
- **Trust & safety** — PII redaction + prompt-injection guardrails on all telemetry; a
  per-investigation token budget; a hash-chained audit trail.
- **Triggered + proactive** — a signed PagerDuty webhook auto-investigates and posts findings to
  Slack with Approve/Reject buttons; an opt-in SLO-burn poller opens investigations *before* a page.
- **5 LLM providers**, switchable live from the UI (Anthropic · OpenAI · Gemini · Groq · DeepSeek).
- **Opt-in multi-tenant SaaS** — orgs, RBAC, tenant-scoped API keys, usage metering + quotas,
  admin endpoints — additive, so the offline demo is unchanged when off.

<table>
  <tr>
    <td><img src="docs/screenshots/dashboard.png" alt="Console welcome" width="100%"></td>
    <td><img src="docs/screenshots/console.png" alt="Live investigation + approval card" width="100%"></td>
  </tr>
</table>

## Quickstart

```bash
uv venv && uv pip install -e .
cp .env.example .env          # set ANTHROPIC_API_KEY (or COPILOT_PROVIDER + its key)
uv run copilot "Why is the checkout API throwing 500 errors?"
```

Or the whole product (console + API) in one container:

```bash
docker compose up --build     # http://localhost:8000
```

Full instructions (CLI · API · web dev server · Docker · triggered mode) → **[docs/USAGE.md](docs/USAGE.md)**.

## Architecture

```
   CLI / React console ─► FastAPI ─► LangGraph state machine
                                       plan → agent → (policy route)
                                         ├─ approve? → approval  ◄── human ✅/❌
                                         ├─ read?    → tools (redacted + injection-scanned)
                                         └─ done?    → reflect → report → verify (fix?) → RCA + postmortem
                                       checkpointer: SQLite / Postgres (resumable)
                                              │  MCP (stdio)
        datadog · pagerduty · kubernetes · sentry · traces · deploys · github · repo · incident-memory
```

The agent never imports a server — it only sees the tools each MCP server advertises. Deep dive
→ **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** · **[docs/AGENT.md](docs/AGENT.md)**.

## Documentation

| Doc | What's inside |
|-----|---------------|
| **[Usage](docs/USAGE.md)** | Install & run it (CLI · API · web · Docker), and how a session works |
| **[Configuration](docs/CONFIGURATION.md)** | Every environment variable + the full HTTP API surface |
| **[Architecture](docs/ARCHITECTURE.md)** | The three layers and the LangGraph design |
| **[The agent](docs/AGENT.md)** | The 7-node loop, approval gate, RCA report, fix verification, guardrails, token budget |
| **[Connectors](docs/CONNECTORS.md)** | The 9 MCP servers / 47 tools, live/offline modes, how to add one |
| **[Hardening](docs/HARDENING.md)** | Reliability, security, cost, observability, reproducibility, testing/CI |
| **[Evaluation](docs/EVALUATION.md)** | Eval harness, trajectory/path-safety scorers, golden-replay CI gate |
| **[Commercialization](docs/COMMERCIALIZATION.md)** | The opt-in multi-tenant SaaS layer (and what's deferred) |
| **[Deployment](DEPLOY.md)** | The single Docker image and production setup |

Index: **[docs/README.md](docs/README.md)**.

## Tech stack

**Agent:** LangGraph · `mcp` (FastMCP) · `langchain-mcp-adapters` · LangChain
**Models:** Claude Opus 4.8 (adaptive thinking) · OpenAI · Gemini · Groq/Llama · DeepSeek
**API:** FastAPI · SQLite / Postgres checkpointer · SSE · bearer auth · rate limiting
**Frontend:** React 18 · TypeScript · Vite · React Three Fiber (lazy-loaded)
**Tooling:** uv · ruff · mypy · pytest · ESLint · Vitest · GitHub Actions · multi-stage Docker

---

## Author

**Ankit Pandey** — creator & maintainer · [@ankit25bcs10610](https://github.com/ankit25bcs10610)

Contributions welcome — open an issue or PR.

<div align="center">

[MIT](LICENSE) — built as a portfolio / learning project.

</div>
