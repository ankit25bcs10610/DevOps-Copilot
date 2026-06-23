# Architecture

DevOps Copilot is split into three layers that map cleanly onto the three
technologies it showcases.

```
Interfaces  ─────────────  CLI  +  FastAPI
                              │
Orchestration  ───────────  LangGraph state machine  (+ LangChain glue)
                              │  MCP protocol (stdio)
Tools  ───────────────────  3 MCP servers: logs-metrics · repo · github
```

---

## 1. Tool layer — MCP

Each capability lives behind an **MCP server**, a separate process that speaks
the Model Context Protocol. The agent discovers tools at runtime; it never
imports a server.

| Server | Type | Tools |
|--------|------|-------|
| `logs-metrics` | **custom** (built with the `mcp` SDK) | `search_logs`, `get_error_summary`, `get_metric`, `list_services` |
| `repo` | custom, sandboxed FS + git | `list_dir`, `read_file`, `grep`, `git_log` |
| `github` | real API or offline fixtures | `list_recent_commits`, `get_commit_diff`, `create_pull_request` *(write)* |

`app/mcp/client.py` registers all three with `MultiServerMCPClient` and converts
their tools to LangChain tools via `langchain-mcp-adapters`. **Adding a fourth
server is one dict entry — zero agent changes.** That decoupling is the whole
point of MCP.

Write actions are tagged in one place (`WRITE_TOOLS`) so the graph knows which
calls need human approval.

---

## 2. Orchestration layer — LangGraph

State that flows through every node (`app/graph/state.py`):

```python
messages         # full convo incl. tool calls/results (reducer: add_messages)
plan             # the planner's ordered steps
pending_action   # a write awaiting approval
iteration        # loop guard
status           # investigating | awaiting_approval | done | failed
```

### Nodes

| Node | Responsibility |
|------|----------------|
| `plan` | Decompose the request into 2–5 investigation steps. |
| `agent` | Bind tools, let Claude decide the next tool call or final answer. |
| `tools` | Prebuilt `ToolNode` — executes read tools and approved writes. |
| `approval` | `interrupt()` — pause for a human ✅/❌ before any write. |
| `reflect` | DONE or CONTINUE? Enforces the iteration cap. |

### Control flow

```
START → plan → agent ─┬─ write call?  → approval ─┬─ approved → tools → agent
                      │                            └─ rejected → agent
                      ├─ read call?   → tools → agent
                      └─ no call?     → reflect ─┬─ continue → agent
                                                 └─ done     → END
```

The cycle (`agent → tools → agent`) is what makes this an agent rather than a
chain. `reflect` + `iteration` prevent infinite loops.

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
scores keyword recall, tool-usage correctness, and latency. Write actions are
auto-approved so runs are non-interactive.
