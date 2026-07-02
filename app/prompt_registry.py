"""Prompt version registry + a benchmark-gated A/B comparison.

Prompt changes are the highest-leverage, riskiest edits to an agent. This gives them
a version registry (so you can pin/roll back a prompt) and, crucially, an A/B GATE
that compares two benchmark scorecards and blocks a candidate that regresses quality
— using the deterministic scorers in evals/benchmark.py.

The registry seeds from app/graph/prompts.py and can be overridden from a JSON file
(COPILOT_PROMPT_OVERRIDES) without a code change. The A/B gate is pure/testable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("devcopilot.prompts")


def _seed() -> dict[str, dict[str, str]]:
    """Baseline prompt versions from the shipped constants (version 'v1')."""
    from app.graph import prompts as p

    names = ["PLANNER_SYSTEM", "AGENT_SYSTEM", "REFLECT_SYSTEM", "REPORT_SYSTEM",
             "VERIFY_SYSTEM", "PROSECUTOR_SYSTEM", "DEFENDER_SYSTEM", "HYPOTHESIS_PROBE_SYSTEM"]
    return {n: {"v1": getattr(p, n)} for n in names if hasattr(p, n)}


class PromptRegistry:
    """Versioned prompts with an active pointer per name."""

    def __init__(self, seed: dict[str, dict[str, str]] | None = None):
        self._versions: dict[str, dict[str, str]] = seed if seed is not None else _seed()
        self._active: dict[str, str] = {n: "v1" for n in self._versions}

    def register(self, name: str, version: str, text: str) -> None:
        self._versions.setdefault(name, {})[version] = text

    def versions(self, name: str) -> list[str]:
        return sorted(self._versions.get(name, {}))

    def set_active(self, name: str, version: str) -> bool:
        if version in self._versions.get(name, {}):
            self._active[name] = version
            return True
        return False

    def active_version(self, name: str) -> str:
        return self._active.get(name, "v1")

    def get(self, name: str) -> str:
        """The active prompt text for `name` (raises KeyError if unknown)."""
        return self._versions[name][self._active[name]]

    def load_overrides(self, path: str) -> int:
        """Merge {name: {version: text}} overrides from a JSON file. Returns the count
        of registered variants. Best-effort — a missing/invalid file is a no-op."""
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return 0
        n = 0
        for name, versions in (data or {}).items():
            for version, text in (versions or {}).items():
                self.register(name, version, str(text))
                n += 1
        return n


# --------------------------------------------------------------------------- #
# A/B gate: compare two benchmark Scorecard summaries and decide pass/regress.
# --------------------------------------------------------------------------- #
# The metrics that must not drop (higher-is-better), and how much slack to allow.
_GATE_METRICS = ("a1", "pcw", "loc_top1", "groundedness")


def ab_gate(baseline: dict, candidate: dict, tolerance: float = 0.02) -> dict:
    """Compare two benchmark scorecards (their `overall` dicts, or full summaries) and
    decide whether the candidate is safe to ship. A candidate REGRESSES if any gate
    metric drops by more than `tolerance`. Pure over its inputs.

    Returns {regressed, verdict, deltas, regressions}."""
    b = baseline.get("overall", baseline)
    c = candidate.get("overall", candidate)
    deltas: dict[str, float] = {}
    regressions: list[str] = []
    for m in _GATE_METRICS:
        bv, cv = float(b.get(m, 0.0)), float(c.get(m, 0.0))
        deltas[m] = round(cv - bv, 4)
        if cv < bv - tolerance:
            regressions.append(m)
    regressed = bool(regressions)
    verdict = "regressed" if regressed else ("improved" if any(d > tolerance for d in deltas.values()) else "neutral")
    return {"regressed": regressed, "verdict": verdict, "deltas": deltas, "regressions": regressions}


_REGISTRY: PromptRegistry | None = None


def registry() -> PromptRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PromptRegistry()
        override = os.environ.get("COPILOT_PROMPT_OVERRIDES", "").strip()
        if override:
            n = _REGISTRY.load_overrides(override)
            log.info("loaded %d prompt override variant(s) from %s", n, override)
    return _REGISTRY
