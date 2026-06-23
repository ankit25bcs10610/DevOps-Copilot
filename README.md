# 🛠️ DevOps Copilot

An **autonomous DevOps assistant** that investigates production incidents and
proposes fixes — built to showcase three technologies working together the way
they're meant to:

| Technology | Role |
|------------|------|
| **MCP** (Model Context Protocol) | The **tool layer** — three independent MCP servers (logs/metrics, repo, GitHub) the agent discovers at runtime. One is fully custom. |
| **LangGraph** | The **orchestration brain** — a stateful graph (`plan → reason/act → approve → reflect`) with cycles, checkpointing, and human-in-the-loop. |
| **LangChain** | The **building blocks** — Groq (Llama 3.3) model wrappers, prompts, and the MCP→LangChain tool adapter. |

> Give it a question like *"Why is the checkout API throwing 500s?"* and it
> pulls logs, reads the code, inspects git history, finds the root cause, and
> drafts a fix — pausing for your approval before touching anything.

---

## Architecture

```
        ┌─────────────┐   ┌─────────────────┐
        │   CLI       │   │  FastAPI /chat  │      Interfaces
        └──────┬──────┘   └────────┬────────┘
               └───────────┬───────┘
                           ▼
        ┌──────────────────────────────────────┐
        │      LangGraph state machine         │
        │                                      │
        │  plan ▶ agent ▶ (route)              │
        │           │  ├─ write? ▶ approval ───┤ ◀── human ✅/❌
        │           │  ├─ read?  ▶ tools ──────┤
        │           │  └─ done?  ▶ reflect ────┤
        │           └────────◀─────────────────┘
        │  checkpointer: SQLite (resumable)    │
        └───────────────────┬──────────────────┘
                            ▼  (MCP protocol, stdio)
   ┌─────────────┬──────────────────┬──────────────────┐
   │ logs-metrics│       repo        │      github      │  MCP servers
   │  (CUSTOM)   │ read_file / grep  │ commits / PRs    │
   │ search_logs │ git_log / list    │ create_pull_req  │
   └─────────────┴──────────────────┴──────────────────┘
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

---

## Quickstart

```bash
# 1. Install (uv recommended)
uv venv && uv pip install -e .

# 2. Configure — works fully offline; only a Groq key is required
cp .env.example .env
#   set GROQ_API_KEY=...   (GitHub token optional -> offline demo mode)

# 3. Ask it something
uv run copilot "Why is the checkout API throwing 500 errors?"
```

You'll watch it plan, call MCP tools across services, identify the
null-handling bug in `sample_repo/checkout.js`, and ask permission before
opening a PR.

### Run the API instead

```bash
uv run uvicorn app.api.main:app --reload
# POST /chat     {"thread_id": "t1", "message": "why are checkouts failing?"}
# POST /approve  {"thread_id": "t1", "approved": true}
```

---

## What each part demonstrates

- **A custom MCP server** (`app/mcp/servers/logs_metrics`) built with the
  official `mcp` Python SDK — not just *using* MCP, but *implementing* it.
- **Multi-server orchestration** — the agent reasons across three MCP servers
  it discovered dynamically; adding a fourth is one entry in `client.py`.
- **Real agent control flow** — LangGraph cycles, a reflection loop, an
  iteration guard, and a **human-in-the-loop `interrupt()`** before writes.
- **Production touches** — persistent checkpointing, an eval harness
  (`evals/`), Docker, and config via environment.

---

## Project layout

```
app/
  api/        FastAPI surface (/chat, /approve)
  graph/      LangGraph: state, nodes, edges, builder, prompts
  mcp/        client wiring + three MCP servers (one custom)
  cli.py      interactive terminal UI
  session.py  ties MCP + graph together, drives approvals
evals/        test cases + harness
sample_repo/  fixture repo with a planted bug
docs/         architecture write-up
```

---

## Tech stack

Groq / Llama 3.3 (`langchain-groq`) · LangGraph + SQLite checkpointer ·
`mcp` SDK + `langchain-mcp-adapters` · FastAPI · Rich CLI · Docker

---

## License

MIT — built as a portfolio / learning project.
