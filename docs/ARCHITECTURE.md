# Architecture

DevOps Copilot is split into three layers that map cleanly onto the three
technologies it showcases.

```
Interfaces  ─────────────  CLI  +  FastAPI
                              │
Orchestration  ───────────  LangGraph state machine  (+ LangChain glue)
                              │  MCP protocol (stdio)
Tools  ───────────────────  7 MCP servers: datadog · pagerduty · kubernetes ·
                            sentry · github · repo · incident-memory
```

---

## 1. Tool layer — MCP

Each capability lives behind an **MCP server**, a separate process that speaks
the Model Context Protocol. The agent discovers tools at runtime; it never
imports a server. Every server has a **live-API mode** (when credentials are
configured) and an **offline-fixture mode**, so the whole agent runs end-to-end
with no external accounts — the fixtures are all tied to one coherent demo
incident (a bad `checkout-svc` discount deploy).

| Server | Type | Tools |
|--------|------|-------|
| `datadog` | observability (live API / offline) | `search_logs`, `get_error_summary`, `get_metric`, `list_services`, `detect_anomaly` |
| `pagerduty` | alerting (live API / offline) | `list_incidents`, `get_incident`, `get_incident_alerts`, `add_incident_note` *(w)*, `acknowledge_incident` *(w)*, `resolve_incident` *(write)* |
| `kubernetes` | orchestration (kubeconfig / offline) | `list_pods`, `describe_pod`, `get_events`, `get_deployment_status`, `rollout_history`, `scale_deployment`/`rollback_deployment`/`restart_deployment` *(write)* |
| `sentry` | error tracking (live API / offline) | `list_issues`, `get_issue`, `get_latest_event` |
| `github` | repo host (real API / offline) | `list_recent_commits`, `get_commit_diff`, `correlate_changes`, `list_workflow_runs`, `get_failed_job_logs`, `create_pull_request` *(write)* |
| `repo` | sandboxed FS + git | `list_dir`, `read_file`, `grep`, `git_log` |
| `memory` | incident memory (BM25 over a corpus) | `search_incidents`, `get_incident_record` |

`app/mcp/client.py` registers all seven with `MultiServerMCPClient` and converts
their tools to LangChain tools via `langchain-mcp-adapters`. **Adding a server is
one dict entry — zero agent changes.** That decoupling is the whole point of MCP.

Mutating actions are classified by the **action policy engine** (`app/policy.py`),
which maps each tool — and, where it matters, its arguments — to *allow / notify /
approve* with a risk tier, so the graph knows which calls need human approval.
A **guarded tool node** provenance-boxes and prompt-injection-scans every tool
result (`app/guardrails.py`) before it re-enters the model's context.

---

## 2. Orchestration layer — LangGraph

State that flows through every node (`app/graph/state.py`):

```python
messages         # full convo incl. tool calls/results (reducer: add_messages)
plan             # the planner's ordered steps
pending_action   # a write awaiting approval
iteration        # loop guard
tokens_used      # running LLM token total (additive reducer) — cost kill-switch
feedback         # reflect's targeted gap note for the next agent pass
report           # the structured RCA deliverable (set by the report node)
status           # investigating | awaiting_approval | done | failed
```

### Nodes

| Node | Responsibility |
|------|----------------|
| `plan` | Decompose the request into 2–5 investigation steps. |
| `agent` | Bind tools, let Claude decide the next tool call or final answer. Forced to summarize at the iteration cap **or** the token budget. |
| `tools` | Guarded `ToolNode` — executes read tools and approved writes, then provenance-boxes + injection-scans every result. |
| `approval` | `interrupt()` — pause for a human ✅/❌ before any approve-class action, with risk tier + impact preview. |
| `reflect` | DONE or CONTINUE? Enforces the iteration cap and the token budget. |
| `report` | Compile the structured RCA (ranked hypotheses + verdicts + evidence, severity, confidence) and render a postmortem. |

### Control flow

```
START → plan → agent ─┬─ approve call? → approval ─┬─ approved → tools → agent
                      │                             └─ rejected → agent
                      ├─ read call?    → tools → agent
                      └─ no call?      → reflect ─┬─ continue → agent
                                                  └─ done     → report → END
```

The cycle (`agent → tools → agent`) is what makes this an agent rather than a
chain. `reflect` + `iteration` + the token budget prevent infinite loops and
runaway cost.

### Human-in-the-loop

`approval_node` calls `interrupt()`, which suspends the graph and persists state
via the **checkpointer** (SQLite). The API returns an `approval_request`; a later
`POST /approve` resumes with `Command(resume={"approved": ...})`. Because state
is checkpointed per `thread_id`, the pause can span separate HTTP requests or
even a process restart.

On rejection the node writes `ToolMessage`s answering the open tool-call ids
(the Anthropic API requires every `tool_call` to be answered) and routes back to
the agent to choose another path.

---

## 3. Interface layer

- **CLI** (`app/cli.py`) — Rich REPL; shows the live node trace and prompts
  inline for approvals.
- **API** (`app/api/main.py`) — `POST /chat`, `POST /approve`, `GET /healthz`.
  Sessions keyed by `thread_id`.

Both go through `CopilotSession` (`app/session.py`), which owns the MCP
lifecycle, drives the graph with `astream`, and surfaces interrupts.

---

## Production path

Everything below is a config change, not a rewrite:

| Demo | Production |
|------|------------|
| SQLite checkpointer | Postgres (`langgraph-checkpoint-postgres`) |
| stdio MCP subprocesses | remote MCP servers over HTTP |
| GitHub offline fixtures | real GitHub API (set `GITHUB_TOKEN`) |
| in-process sessions | reconstruct from checkpointer by `thread_id` |
| tracing off | set `LANGCHAIN_TRACING_V2=true` for LangSmith |

---

## Evaluation

`evals/run_evals.py` runs cases from `testcases.yaml` against a real session and
scores keyword recall, tool-usage correctness, the structured RCA verdict (root
cause named + valid severity), and latency. Write actions are auto-approved so
runs are non-interactive. Thumbs-down feedback captured at runtime (`/feedback`,
`app/feedback.py`) is the natural source of new regression cases.
