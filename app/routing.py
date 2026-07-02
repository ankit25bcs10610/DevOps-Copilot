"""Model routing — send each request to the right-sized model.

The agent loop runs on the main reasoning model (Opus, with adaptive thinking) by
default, which is correct for incident investigation but overkill for a simple
informational lookup ("which services are emitting logs?"). This classifies the
request and lets the agent node triage clearly-informational queries onto the cheap
fast model, reserving the expensive model for the hard, consequential work.

Pure + deterministic (no LLM) so it's free and unit-testable. Biased toward
'complex': when in doubt we do NOT under-power a real incident.
"""

from __future__ import annotations

import re
from typing import Literal

Difficulty = Literal["simple", "complex"]

# Incident / debugging language — anything that smells like a real investigation
# stays on the main model. Checked first: if both fire, complex wins.
_COMPLEX = re.compile(
    r"\b(why|root[- ]?cause|caus(e|ed|ing)|failing|fail(ed|ure)?|error|errors|"
    r"5\d\d|5xx|4\d\d|crash(ing|loop)?|down|outage|degrad|incident|debug|"
    r"investigat|latency|slow|timeout|throwing|broken|regress|leak|stuck|"
    r"rollback|roll ?back|deploy|fix|patch|exception|traceback|stack ?trace)\b",
    re.I,
)

# Read-only, informational asks — safe to triage onto the fast model.
_SIMPLE = re.compile(
    r"\b(list|show|which|what (is|are|services)|how many|display|"
    r"summar(y|ize|ise)|status|describe|tell me about|overview)\b",
    re.I,
)


def classify_difficulty(request: str) -> Difficulty:
    """Classify a user request as 'simple' (informational) or 'complex' (incident).
    Complex wins ties; unknown → complex (never under-power a real incident)."""
    text = request or ""
    if _COMPLEX.search(text):
        return "complex"
    if _SIMPLE.search(text):
        return "simple"
    return "complex"


def use_fast_model(request: str, routing_enabled: bool) -> bool:
    """Whether the agent loop should triage this request onto the fast model."""
    return routing_enabled and classify_difficulty(request) == "simple"
