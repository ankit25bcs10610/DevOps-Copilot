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

import hashlib
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
# Running hash of the chain (tamper-evidence): each entry's hash folds in the
# previous one, so editing/deleting any entry breaks every hash after it.
_LAST_HASH = ""


def _canonical(entry: dict) -> str:
    """Stable serialization of an entry EXCLUDING the chain fields, for hashing."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
    return json.dumps(body, default=str, sort_keys=True)


def _chain_hash(entry: dict, prev_hash: str) -> str:
    return hashlib.sha256((_canonical(entry) + prev_hash).encode()).hexdigest()


def record(event: str, **fields) -> None:
    """Record one audit event, e.g. record("approval.decided", thread="t", approved=True).
    Each entry is hash-chained to the previous, making the trail tamper-evident."""
    global _LAST_HASH
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
    entry["prev_hash"] = _LAST_HASH
    entry["hash"] = _chain_hash(entry, _LAST_HASH)
    _LAST_HASH = entry["hash"]
    _audit.info("audit event=%s %s", event, json.dumps(fields, default=str, sort_keys=True))
    _BUFFER.append(entry)
    if _LOG_PATH:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            _audit.warning("could not append to audit log %s", _LOG_PATH)


def verify_chain(events: list[dict] | None = None) -> dict:
    """Recompute the hash chain (oldest→newest) and report the first broken link —
    the artifact auditors ask for. Defaults to the in-memory buffer."""
    items = events if events is not None else list(_BUFFER)
    prev = ""
    for i, e in enumerate(items):
        expected = _chain_hash(e, prev)
        if e.get("hash") != expected or e.get("prev_hash") != prev:
            return {"valid": False, "checked": i, "broken_at": i,
                    "event": e.get("event"), "count": len(items)}
        prev = e["hash"]
    return {"valid": True, "count": len(items)}


def recent(limit: int = 100, event_prefix: str = "") -> list[dict]:
    """Return the most recent audit events (newest first), optionally filtered by
    an event-name prefix (e.g. "approval", "security", "feedback")."""
    items = list(_BUFFER)
    if event_prefix:
        items = [e for e in items if str(e.get("event", "")).startswith(event_prefix)]
    return items[-limit:][::-1]


def clear() -> None:
    """Drop buffered events + reset the chain (used by tests)."""
    global _LAST_HASH
    _BUFFER.clear()
    _LAST_HASH = ""
