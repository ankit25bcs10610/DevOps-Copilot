# Golden cassettes

Recorded LLM responses for **deterministic, offline** golden-trajectory evals
(see `evals/run_golden.py` and `app/replay.py`).

The MCP servers already run deterministic offline fixtures, so only the LLM is
non-deterministic. A cassette records each LLM response keyed by a normalized hash
of its input messages, so the same investigation replays bit-for-bit with **no API
key and no network**. The replay set lives in `evals/golden_cases.yaml` (a stable
subset of `testcases.yaml`); recording and replay use the same file so they never drift.

## The committed `golden.json` is a seeded baseline

`golden.json` here was seeded **offline with a deterministic scripted agent**
(`evals/record_golden_offline.py`) so the CI gate is live without a paid key:

```bash
uv run python -m evals.record_golden_offline
```

Re-record from a **live LLM** anytime to capture real model trajectories — the
cassette format is identical, and you can expand `golden_cases.yaml` first:

## Record from a live LLM (optional upgrade)

```bash
COPILOT_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... \
  uv run python -m evals.run_golden --record
```

This writes `golden.json` here. Commit it — it's the regression baseline.

## Replay (CI / anyone, no key)

```bash
uv run python -m evals.run_golden
```

Fails (exit 1) if any case's tool use or RCA verdict regresses. Runs in CI on every
push (`.github/workflows/ci.yml`).

Re-record whenever you intentionally change prompts, the model, the ranker, or tool
behavior — and review the cassette diff like any other golden file.
