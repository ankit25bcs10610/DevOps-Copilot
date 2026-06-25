"""Feedback loop — turn real thumbs-up/down into labeled cases.

Every rating an operator gives a completed investigation is captured as a
structured record (and an audit event). Thumbs-down on a real failure is exactly
the seed for a regression eval case, so this closes the loop: production feedback
-> labeled dataset -> the offline eval harness (evals/) -> a CI gate that blocks
prompt/model regressions.

Records append to COPILOT_FEEDBACK_LOG (JSONL; default ./feedback.jsonl) so they
survive restarts and can be replayed into the eval set.
"""

from __future__ import annotations

import json
import logging
import os
import time

from app import audit

log = logging.getLogger("devcopilot.feedback")

_LOG_PATH = os.environ.get("COPILOT_FEEDBACK_LOG", "feedback.jsonl").strip()

RATINGS = {"up", "down"}


def record_feedback(thread_id: str, rating: str, comment: str = "", question: str = "") -> dict:
    """Persist one feedback record and emit an audit event. Returns the record.

    Raises ValueError on an invalid rating so the API can return a clean 400.
    """
    rating = (rating or "").strip().lower()
    if rating not in RATINGS:
        raise ValueError("rating must be 'up' or 'down'")
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "thread_id": thread_id,
        "rating": rating,
        "comment": (comment or "").strip()[:2000],
        "question": (question or "").strip()[:2000],
    }
    if _LOG_PATH:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            log.warning("could not append feedback to %s", _LOG_PATH)
    audit.record("feedback.submitted", thread=thread_id, rating=rating)
    return entry
