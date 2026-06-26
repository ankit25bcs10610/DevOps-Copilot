# DevOps Copilot — Documentation

Everything beyond the project [README](../README.md) lives here.

## Start here
- **[Usage](USAGE.md)** — install, run (CLI · API · web · Docker), and use it end-to-end.
- **[Configuration](CONFIGURATION.md)** — every environment variable + the full HTTP API surface.

## How it works
- **[Architecture](ARCHITECTURE.md)** — the three layers (interfaces · LangGraph orchestration · MCP tools) and the graph design.
- **[The agent](AGENT.md)** — the 6-node investigation loop, human-in-the-loop approval, the structured RCA report, guardrails, and the token budget.
- **[Connectors](CONNECTORS.md)** — the 9 MCP servers / 47 tools, their live/offline modes, and how to add one.

## Operating it
- **[Hardening](HARDENING.md)** — reliability, security, cost control, observability, reproducibility, testing/CI, accessibility.
- **[Evaluation](EVALUATION.md)** — the eval harness, trajectory/path-safety scorers, and the deterministic golden-replay CI gate.
- **[Deployment](../DEPLOY.md)** — the single Docker image and production setup.

## Commercial
- **[Commercialization](COMMERCIALIZATION.md)** — the opt-in multi-tenant SaaS layer (orgs, RBAC, API keys, usage/quotas, admin, redaction, tamper-evident audit) and what's deferred.
- **[Product architecture](PRODUCT-ARCHITECTURE.md)** — the longer-range commercial product design.
