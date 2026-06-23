# DevOps Copilot — Frontend

React + TypeScript + Vite UI for the DevOps Copilot agent. It renders the live
agent activity timeline and, crucially, surfaces the **human-in-the-loop
approval gate** as an interactive card before any write action runs.

## Run

```bash
# 1. Start the backend (from the repo root)
uv run uvicorn app.api.main:app --reload      # http://localhost:8000

# 2. Start the frontend
cd frontend
npm install
cp .env.example .env        # VITE_API_URL=http://localhost:8000
npm run dev                 # http://localhost:5173
```

## Architecture

```
src/
  api.ts                 typed client for /chat, /approve, /healthz
  types.ts               mirrors the FastAPI response contract
  hooks/useCopilot.ts    conversation state + approval flow (one thread_id)
  components/
    Header.tsx           brand + live backend health dot
    Message.tsx          user / assistant turn rendering
    ActivityTimeline.tsx node-by-node agent trace
    ApprovalCard.tsx     approve / reject the proposed write action
    Composer.tsx         input + suggested prompts
  App.tsx                layout + autoscroll
  styles.css             dark theme
```

### Design notes
- **Single `thread_id` per session** (`useCopilot`) so the backend checkpointer
  can pause at an approval interrupt and resume on the next `/approve` call.
- **No client-side secrets** — the browser only talks to our backend; the LLM
  and MCP servers stay server-side.
- **Stateless rendering** — every turn is derived from the backend response
  (`status`, `trace`, `approval_request`), so the UI can't drift from the agent.
