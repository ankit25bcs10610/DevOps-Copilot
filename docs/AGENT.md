# The agent

How a one-line question becomes an evidence-backed root-cause analysis.

## The investigation loop (7 nodes)

A [LangGraph](https://langchain-ai.github.io/langgraph/) state machine drives the
LLM through a cyclic graph. State flows through every node and is checkpointed per
`thread_id`, which is what makes the human-approval pause resumable across requests.

```
START → plan → agent ─┬─ approve call? → approval ─┬─ approved → tools → agent
                      │                             └─ rejected → agent
                      ├─ read call?    → tools → agent
                      └─ no call?      → reflect ─┬─ continue → agent
                                                  └─ done → report → verify ─┬─ unverified fix → agent (revise once)
                                                                             └─ verified / done → END
```

| Stage | What happens |
|-------|--------------|
| **1 · Plan** | Decompose the incident into a short plan (cheap *fast* model), **warm-started with similar prior incidents** from the memory corpus (verify-don't-assume) plus prior-turn context on follow-ups. |
| **2 · Investigate** | Call read-only MCP tools — search prior incidents, read logs/metrics/traces, inspect Kubernetes + Sentry, correlate deploys + commits, grep code. Every tool result is **redacted + injection-scanned** before the agent sees it. |
| **3 · Approve** | A consequential action (open a PR, scale/rollback a deployment, resolve an incident) **pauses** the graph for human approval — with a risk tier, impact preview, and an evidence-count hint. |
| **4 · Diagnose** | Pinpoint the root cause and propose a fix, grounded in observed tool output. |
| **5 · Reflect** | Judge completeness (fast model). On *continue*, hand the agent a **targeted gap note** so the next pass makes progress instead of repeating itself. |
| **6 · Report** | Compile a **structured RCA** + render a blameless postmortem. When 2+ hypotheses still compete, a **parallel probe** scores each concurrently against the evidence and re-ranks (best-supported leads). Two deterministic critics (evidence-density calibration + grounding) run, then an **adversarial Prosecutor/Defender critique** (`COPILOT_ADVERSARIAL_CRITIQUE`): the Prosecutor tries to refute the root cause, the Defender rebuts using only observed evidence, and a deterministic judge **abstains** the RCA on a standing high-severity objection or **downgrades** confidence on a medium one — cutting confident-but-wrong conclusions. |
| **7 · Verify** | Assess whether the **proposed fix actually addresses the root cause** — deterministically grounding the fix against the implicated files/services, plus a fast-model check that emits **resolution criteria**. Optionally runs a **sandbox counterfactual** (`COPILOT_SANDBOX_VERIFY`): applies the PR's `patch` to a throwaway repo copy and runs a reproducer — a FAIL→PASS transition *proves* the fix and overrides the model's opinion. An unverified fix bounces back to the agent **once** to revise (bounded by `COPILOT_VERIFY_MAX_ATTEMPTS`); informational runs with no fix pass straight through. Toggle with `COPILOT_VERIFY_FIX`. |

The loop is bounded twice over: at the **iteration cap** *or* the **per-investigation
token budget**, the agent is invoked without tools and forced to summarize — so a run
never ends on an unexecuted tool call and runaway cost is hard-stopped. The graph's
`recursion_limit` is derived from the cap, with a `GraphRecursionError` safety net.

## Human-in-the-loop, by design

The defining safety property: **the agent asks permission before it changes anything.**

- An **action policy engine** (`app/policy.py`) classifies every tool call
  **allow / notify / approve** by consequence + risk tier, and is **argument-aware**
  (e.g. `scale_deployment` to zero replicas escalates to high-risk/approve).
- Approve-class calls route through `approval_node` → LangGraph `interrupt()`, which
  surfaces **every** call in the batch with its risk tier, a terraform-plan-style
  **impact preview**, and how much the agent investigated first — so a reviewer never
  approves a hidden write bundled with reads.
- The routing (`app/graph/edges.py`) **cannot** reach the tool executor for an
  approve-class action without passing approval first (asserted in `tests/test_edges.py`).
- A **confidence gate** (`policy.confidence_gate`, `COPILOT_CONFIDENCE_GATE`) refuses a
  *programmatic* auto-approval of a consequential write that rests on thin evidence — a
  high-risk write needs a high-confidence (well-evidenced) investigation to be
  auto-approved. Evals, bots, and the auto-remediation loop are held to this;
  a human reviewer still sees the warning and can approve explicitly.
- **Progressive autonomy** (opt-in, `POST /remediate`, `app/autonomy.py`) closes the
  loop past *propose*: apply a **reversible** fix (rollback/restart only — never
  scale-to-zero or a PR merge), watch the incident signal for a window, and
  **auto-revert + escalate** if it doesn't recover. Doubly gated — **off by default**
  and **dry-run even when enabled** (`COPILOT_AUTONOMY` + `COPILOT_AUTONOMY_DRYRUN`) —
  and only fires on a high-confidence investigation.
- On rejection, each `tool_call_id` is answered with a `ToolMessage` (keeping history
  valid) and control returns to the agent to find another path.
- The pause is **resumable across separate HTTP requests** — state is checkpointed, so
  it survives even after the in-memory session is evicted.

## The structured RCA report

Every finished investigation is compiled into a typed object (not a freeform blob):

- **Ranked hypotheses**, each marked *validated / invalidated / inconclusive* with
  **cited evidence**.
- **Severity**, affected services, recommended actions, and a one-line root cause.
- A **calibrated confidence** computed deterministically from evidence density — it
  **abstains** ("insufficient evidence — here's what I'd need") instead of bluffing on
  thin investigations.
- A **deterministic grounding verifier** that downgrades/abstains when cited evidence
  isn't actually present in the tool output (anti-fabrication — no extra LLM call).
- A rendered **blameless postmortem** (Markdown, one-click download).

It flows through `TurnResult → ChatResponse/SSE → the RcaReportCard` UI and the Slack
delivery (verdict-at-a-glance blocks).

## Trust & safety layers

- **Prompt-injection guardrails** (`app/guardrails.py`) — every tool output is
  provenance-boxed ("untrusted data, not instructions") and scanned for injection
  patterns before re-entering the model's context; hits are audited.
- **PII/secret redaction** (`app/redaction.py`) — emails, IPs, tokens, JWTs, and
  Luhn-valid cards are scrubbed to consistent placeholders **before** the LLM sees them
  or state is persisted.
- **Token budget** (`COPILOT_MAX_TOKENS_PER_RUN`) — a per-investigation cost ceiling.

## Multi-provider LLMs

Provider/model/key are resolved in one place (`app/llm.py`) and switchable live from
the UI: Anthropic (Claude Opus 4.8, adaptive thinking), OpenAI, Gemini, Groq/Llama,
DeepSeek. Adaptive thinking runs only on the main reasoning model; the plan/reflect/
report nodes use the cheaper fast model.

## Concurrency & state

The FastAPI layer serves more than one user at a time:

- A **writer-preferring reader/writer gate** lets agent turns run concurrently across
  threads while a config change drains in-flight turns and runs exclusively — a config
  swap can never tear a session down mid-investigation.
- **Per-thread locks** serialize one thread's turns without blocking others.
- A **bounded, LRU-evicted session pool** never drops a running or awaiting-approval
  thread; an evicted thread is transparently **reconstructed from the checkpointer**.

See also: [Architecture](ARCHITECTURE.md) · [Connectors](CONNECTORS.md) · [Evaluation](EVALUATION.md).
