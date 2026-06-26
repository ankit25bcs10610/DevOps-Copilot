"""PII / secret redaction for ingested telemetry.

Logs, traces, and incident text routinely contain emails, IPs, tokens, and card/
SSN-like numbers. Before any of that reaches the LLM — or gets persisted to the
LangGraph checkpoint DB — it is scrubbed here and replaced with consistent,
label-preserving placeholders (<EMAIL_1>) so the agent can still reason about
"the same email" without ever seeing the value. This is a hard requirement for
selling to enterprises (and keeps secrets out of model providers / traces).

Pure + deterministic (regex + a Luhn check for cards), so it's unit-testable; an
optional NER pass (Presidio/spaCy) can be layered later as an install-time extra.
"""

from __future__ import annotations

import re

# (label, pattern). Order matters: more specific/structured patterns first so a
# token isn't partially eaten by a broader rule.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),  # Luhn-validated below
]


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total, parity = 0, len(nums) % 2
    for i, d in enumerate(nums):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def scrub(text: str) -> tuple[str, list[dict]]:
    """Replace PII/secrets with consistent <LABEL_n> tokens.

    Returns (clean_text, entities) where entities lists {type, token} — never the
    raw value, so the audit of a redaction can't itself leak the secret.
    """
    if not text:
        return text, []
    counters: dict[str, int] = {}
    mapping: dict[tuple[str, str], str] = {}  # (label, value) -> token
    entities: list[dict] = []

    def _token_for(label: str, value: str) -> str:
        key = (label, value)
        if key not in mapping:
            counters[label] = counters.get(label, 0) + 1
            tok = f"<{label}_{counters[label]}>"
            mapping[key] = tok
            entities.append({"type": label, "token": tok})
        return mapping[key]

    out = text
    for label, pat in _PATTERNS:
        def _repl(m: re.Match, _label: str = label) -> str:
            value = m.group(0)
            if _label == "CARD" and not _luhn_ok(value):
                return value  # not a real card number — leave it (e.g. a long id)
            return _token_for(_label, value)

        out = pat.sub(_repl, out)
    return out, entities
