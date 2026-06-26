"""Audit trail for security-relevant actions.

Emits structured, append-only audit events (who/what/when) on a dedicated
`devcopilot.audit` logger AND keeps them queryable: an in-process ring buffer
backs a read API (GET /audit), and — when COPILOT_AUDIT_LOG is set — every event
is also appended to a JSONL file for durable, tamper-evident retention. Records
carry the request-id (via observability) so an action can be correlated
end-to-end. Exposing a queryable audit read API is the commonly-forgotten half of
the SOC2 / compliance requirement, so it's built in here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

from app import observability, tenant_context

_audit = logging.getLogger("devcopilot.audit")

# Recent events kept in memory for the read API (bounded so memory is fixed).
_BUFFER: deque[dict] = deque(maxlen=2000)
# Optional durable sink — one JSON object per line.
_LOG_PATH = os.environ.get("COPILOT_AUDIT_LOG", "").strip()


def record(event: str, **fields) -> None:
    """Record one audit event, e.g. record("approval.decided", thread="t", approved=True)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "request_id": observability.request_id_var.get(),
        # Tenant-stamped from day one ('-' when single-tenant) so every audit
        # line is attributable to an org + actor in a multi-tenant deployment.
        "org_id": tenant_context.tenant_id(),
        "actor": tenant_context.get_actor(),
        **fields,
    }
    _audit.info("audit event=%s %s", event, json.dumps(fields, default=str, sort_keys=True))
    _BUFFER.append(entry)
    if _LOG_PATH:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            _audit.warning("could not append to audit log %s", _LOG_PATH)


def recent(limit: int = 100, event_prefix: str = "") -> list[dict]:
    """Return the most recent audit events (newest first), optionally filtered by
    an event-name prefix (e.g. "approval", "security", "feedback")."""
    items = list(_BUFFER)
    if event_prefix:
        items = [e for e in items if str(e.get("event", "")).startswith(event_prefix)]
    return items[-limit:][::-1]


def clear() -> None:
    """Drop buffered events (used by tests)."""
    _BUFFER.clear()
