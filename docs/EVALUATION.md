# Evaluation

The agent is non-deterministic, so quality is measured — and regressions are gated in CI.

## Eval harness

```bash
uv run python -m evals.run_evals
```

Runs each case in `evals/testcases.yaml` against a real agent session and scores:

- **keyword recall** — did the final answer name the expected root-cause signals?
- **tool-usage correctness** — did it call the right categories of tools?
- **structured RCA verdict** — does the report name the root cause with a valid severity?
- **path-safety** — did every write pass through an approval pause? (a hard gate)
- **latency** — wall-clock per case.

Write actions are auto-approved so runs are non-interactive. The deterministic scorers
live in `evals/scorers.py` and are unit-tested independently.

## Deterministic golden gate (offline, no key)

Because the MCP servers run deterministic offline fixtures, **only the LLM is
non-deterministic**. A VCR-style **cassette layer** (`app/replay.py`) records each LLM
response keyed by a normalized message hash (ids/timestamps excluded), so a whole
investigation replays bit-for-bit with **no API key and no network**.

The replay set is `evals/golden_cases.yaml` (a stable subset of `testcases.yaml`);
recording and replay share it so the cassette and the gate never drift.

```bash
# Replay forever, offline — fails (exit 1) on any tool-use / verdict / path-safety regression
uv run python -m evals.run_golden
```

The committed cassette (`evals/cassettes/golden.json`) is **seeded offline with a
deterministic scripted agent** (`evals/record_golden_offline.py`), so the gate is
**live in CI without a paid key** — it runs on every push. This scripted seed locks
in graph routing, tool wiring, redaction/guardrails, the scorers, and RCA parsing;
re-record from a live LLM to also lock in real model trajectories (identical format):

```bash
COPILOT_REPLAY_MODE=record uv run python -m evals.run_golden --record   # needs a key
```

Fixing this exposed a real determinism bug — LangChain stamps random `lc_<uuid>` ids
onto tool-result content blocks, which broke the cassette key; `app/replay.py` now
scrubs them (regression-tested in `tests/test_replay.py`).
See [`evals/cassettes/README.md`](../evals/cassettes/README.md).

## Prompt-injection red-team gate (offline, no key)

The guardrails (`app/guardrails.py`) are deterministic regex, so they're scored
directly against an adversarial corpus — no LLM needed. `evals/redteam_corpus.yaml`
holds indirect-injection attacks hidden in telemetry (log lines, commit messages,
stack traces, incident text, config) **plus benign look-alikes** as false-positive
controls. The harness reports the **detection rate** and **false-positive rate** and
gates on both:

```bash
uv run python -m evals.run_redteam        # score + gate (exit 1 on regression)
uv run python -m evals.run_redteam --json # machine-readable summary
```

This runs in CI on every push and is also asserted in `tests/test_redteam.py`, so a
weakened guardrail (or a new attack the corpus captures) fails the build. Adding a
case is the natural response to any injection attempt seen in the wild — it becomes a
permanent regression test, and often hardens a pattern (this suite already caught a
too-narrow exfiltration matcher).

## RCA benchmark scorecard

Beyond the pass/fail golden gate, `evals/benchmark.py` scores the structured RCA the
way the 2025-26 agent-eval literature recommends (AIOpsLab, OpenRCA, CUJBench, RCAgent,
the Microsoft FSE'24 RCA study, and the Agentic Benchmark Checklist). Cases carry a
ground-truth **closed answer space** (`evals/benchmark_cases.yaml`): `component`,
`layer`, `fault_type`, cited `artifacts`, `should_abstain`, and a `difficulty` tier.

Metrics (all deterministic, offline — no LLM judge, which is unreliable for citation
faithfulness):

- **RCA correctness** — exact-match after canonicalization of the root-cause *elements*,
  reported as **A@1** (all elements) and **PCW** (partial credit, 0.5 component / 0.2
  layer / 0.3 fault). Matching is **committed** — naming every layer/fault/service earns
  nothing — so restatement can't inflate the score.
- **Localization** — top-1 / top-3 of the faulty component among ranked hypotheses.
- **Evidence** — recall of ground-truth artifacts + **groundedness** (cited evidence must
  fuzzy-match the source telemetry).
- **Calibration** — **ECE** + **Brier** over stated-confidence-vs-correctness; abstention is
  a first-class outcome (correct abstention counts as correct; a confident-wrong answer is
  the worst case), classified with the Microsoft failure-mode taxonomy.
- **Reliability** — `pass@k` / `pass^k` estimators (meaningful only with live multi-sample
  runs; deterministic replay is `pass^1`).
- **Efficiency / safety** — steps, tokens, path-safety — the outcome is graded, not the exact path.

The scorecard is **stratified by difficulty tier** (accuracy collapses on multi-element
cases). Crucially, a **null (do-nothing) and adversarial (evidence-spam) baseline
self-check** runs on every invocation and in CI (`tests/test_benchmark.py`) — both must
score **zero** on answer cases, proving the scorer can't be gamed (this check caught, and
drove the fix for, an early gameable version).

```bash
uv run python -m evals.run_benchmark              # scorecard (replay) + baseline self-check
uv run python -m evals.run_benchmark --baselines  # non-gameability self-check only (fully offline)
uv run python -m evals.run_benchmark --live        # run the agent against a real LLM
```

## The learning loop

Thumbs-down feedback captured in production (`POST /feedback` → `feedback.jsonl`) is the
natural source of new regression cases: convert a real failure into a `testcases.yaml`
entry, record a cassette, and it's gated forever after.

**Continual incident memory.** Beyond regression cases, every *confidently-resolved*
investigation is automatically distilled into a runbook record and appended to the
learned incident corpus (`COPILOT_LEARN_INCIDENTS`, deduped by root cause, abstained
runs skipped). The planner already warm-starts from the corpus via BM25 ("have we seen
this before?"), so institutional memory **compounds** — the second occurrence of a
failure mode starts from the first one's runbook instead of a blank page. Learned
incidents are stored separately from the bundled demo corpus so the fixture is never
mutated (`app/incident_memory.py`).
