# Golden cassettes

Recorded LLM responses for **deterministic, offline** golden-trajectory evals
(see `evals/run_golden.py` and `app/replay.py`).

The MCP servers already run deterministic offline fixtures, so only the LLM is
non-deterministic. A cassette records each LLM response keyed by a normalized hash
of its input messages, so the same investigation replays bit-for-bit with **no API
key and no network**.

## Record (once, with a live LLM key)

```bash
COPILOT_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... \
  uv run python -m evals.run_golden --record
```

This writes `golden.json` here. Commit it — it's the regression baseline.

## Replay (CI / anyone, no key)

```bash
uv run python -m evals.run_golden
```

Fails (exit 1) if any case's tool use or RCA verdict regresses. If no cassette is
present it exits 0 (nothing to gate yet).

Re-record whenever you intentionally change prompts, the model, the ranker, or tool
behavior — and review the cassette diff like any other golden file.
