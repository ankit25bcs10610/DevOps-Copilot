# Production hardening

Every item is in the code today, with file references so it's verifiable.

## Reliability
`/healthz` (liveness) and `/readyz` (readiness — 503 in production until an LLM key is
configured); a 30s-bounded graceful-shutdown drain that closes MCP subprocesses
cleanly; the [concurrency model](AGENT.md#concurrency--state) (reader/writer config
gate, per-thread locks, LRU session pool). — `app/api/main.py`, `app/config.py`

## Security
- Bearer-token auth with constant-time comparison (`hmac.compare_digest`).
- Per-IP rate limiter (memory-bounded, trusted-proxy guard for `X-Forwarded-For`).
- Request-body + message-length caps returning `413`/`429` **inside** CORS so the
  browser can read them.
- A `/sources` path allowlist (the agent's file tools can't be aimed at arbitrary host paths).
- **Fail-closed startup**: refuses to boot `COPILOT_ENV=production` without
  `COPILOT_API_TOKEN` (and, in multi-tenant, without `COPILOT_SECRET_KEY`).
- A **risk-tiered action policy** (`app/policy.py`) gating consequential tools behind approval.
- **Structural prompt-injection defenses** (`app/guardrails.py`) — every tool output is
  provenance-boxed + pattern-scanned before the model sees it; detections audited.
- **PII/secret redaction** (`app/redaction.py`) before the LLM and before state persists.

— `app/api/main.py`, `app/policy.py`, `app/guardrails.py`, `app/redaction.py`

## Cost control
Per-LLM-call token-usage logging (input / output / cache-read) aggregated into a
**per-investigation token budget** that hard-stops the loop (`COPILOT_MAX_TOKENS_PER_RUN`),
surfaced per-turn in the UI. — `app/graph/nodes.py`, `app/graph/state.py`

## Observability
- Structured **JSON logs** in production (text in dev), each record carrying a
  request-id propagated end-to-end via a contextvar.
- An append-only, **queryable audit trail** (`GET /audit`) of approvals, model changes,
  injection detections, redactions, and feedback — and **tamper-evident** (hash-chained,
  `GET /audit/verify`). — `app/audit.py`
- A **feedback loop** (`/feedback`) capturing labeled cases. — `app/feedback.py`
- Optional **LangSmith** tracing, **Sentry** error tracking (`SENTRY_DSN`), and **Datadog
  APM** self-instrumentation of the copilot (`DD_TRACE_ENABLED` + the `apm` extra; honors
  `DD_SERVICE`/`DD_ENV`/`DD_AGENT_HOST`). — `app/observability.py`

## Reproducibility
A VCR-style **record/replay cassette layer** (`app/replay.py`) makes a non-deterministic
agent run bit-for-bit reproducible: it records each LLM response keyed by a normalized
message hash (ids/timestamps excluded), so a whole investigation replays offline with no
key. This powers the [golden-trajectory CI gate](EVALUATION.md). Production is untouched
(replay is off by default).

## Testing & CI
A **pytest suite (175+ tests)** covering the approval policy + routing, RCA report
parsing/rendering + grounding, the token-budget kill-switch, prompt-injection guardrails,
PII redaction, the record/replay cassette layer, the tamper-evident audit chain, the
fail-closed config validator, the repo path-traversal/symlink sandbox, per-provider key
isolation, the auth / rate-limit / body-cap middleware, webhook signature + idempotency,
multi-tenant auth + RBAC + quota isolation, and every connector's offline path — **all
without an LLM key**. CI (`.github/workflows/ci.yml`) runs **ruff + mypy + pytest + the
golden replay gate** on the backend and **ESLint + tsc + Vitest + Vite build** on the
frontend, every push and PR.

## Accessibility
`prefers-reduced-motion` (pauses the 3D loop, static fallback), ARIA roles/labels + a
screen-reader live region for the streaming trace, a skip-to-content link, WCAG-AA
contrast, keyboard-navigable menus, and a cancellable Stop control with conversation
persistence across reloads. — `frontend/src/`
