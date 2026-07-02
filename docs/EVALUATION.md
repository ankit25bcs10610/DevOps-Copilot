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

```bash
# 1) record once, with a live key
COPILOT_REPLAY_MODE=record uv run python -m evals.run_golden --record
# 2) replay forever, offline — fails (exit 1) on any tool-use / verdict / path-safety regression
uv run python -m evals.run_golden
```

CI runs the replay gate automatically once a cassette is committed under
`evals/cassettes/`. See [`evals/cassettes/README.md`](../evals/cassettes/README.md).

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

## The learning loop

Thumbs-down feedback captured in production (`POST /feedback` → `feedback.jsonl`) is the
natural source of new regression cases: convert a real failure into a `testcases.yaml`
entry, record a cassette, and it's gated forever after.
