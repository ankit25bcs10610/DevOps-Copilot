"""Audit trail for security-relevant actions.

Emits structured, append-only audit events (who/what/when) on a dedicated
`devcopilot.audit` logger. Records flow through the same handler as the rest of
the app, so they're JSON in production and carry the request-id (via the logging
filter) for end-to-end correlation. In a multi-tenant deployment these events are
the input to a persisted, queryable audit log.
"""

from __future__ import annotations

import json
import logging

_audit = logging.getLogger("devcopilot.audit")


def record(event: str, **fields) -> None:
    """Record one audit event, e.g. record("approval.decided", thread="t", approved=True)."""
    _audit.info("audit event=%s %s", event, json.dumps(fields, default=str, sort_keys=True))
