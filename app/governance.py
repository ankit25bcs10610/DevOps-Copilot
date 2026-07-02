"""Data governance — retention, GDPR erasure, and audit export.

Three compliance primitives on top of the tenant store + tamper-evident audit log:

  - retention_cutoff / apply_retention: prune usage/metering rows past a window.
  - gdpr_delete_org: crypto-shred the tenant's keys AND delete all its rows —
    right-to-erasure that leaves nothing recoverable.
  - export_audit: verify the hash chain and write the events to a durable JSONL
    sink (append-only / WORM target), returning the chain-integrity result.

The date math and export are pure/testable; the store operations are thin.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app import audit

log = logging.getLogger("devcopilot.governance")


def retention_cutoff(days: int, now_epoch: float | None = None) -> str:
    """ISO-8601 timestamp `days` before now — rows with `ts` < this are expired.
    `now_epoch` is injectable for deterministic tests."""
    now = time.time() if now_epoch is None else now_epoch
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - max(0, days) * 86_400))


async def apply_retention(store: Any, days: int, now_epoch: float | None = None) -> int:
    """Purge usage/metering older than the retention window. No-op when days<=0."""
    if days <= 0:
        return 0
    removed = await store.purge_usage_before(retention_cutoff(days, now_epoch))
    if removed:
        log.info("retention: purged %d usage rows older than %d days", removed, days)
        audit.record("governance.retention_purge", rows=removed, days=days)
    return removed


async def gdpr_delete_org(store: Any, org_id: str) -> bool:
    """Right-to-erasure: crypto-shred the org's key material, then delete every row it
    owns. Returns True if the org existed. Audited."""
    await store.crypto_shred_org(org_id)  # destroy DEK + encrypted secrets first
    deleted = await store.delete_org(org_id)
    if deleted:
        audit.record("governance.gdpr_delete", org_id=org_id)
        log.info("GDPR erasure complete for org %s", org_id)
    return deleted


def export_audit(events: list[dict], path: str) -> dict:
    """Write audit events to a durable JSONL sink and report chain integrity. Point
    `path` at a WORM/object-lock-backed volume for a compliant immutable trail."""
    events = events if events is not None else []
    verdict = audit.verify_chain(events)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, default=str) + "\n")
    return {"exported": len(events), "path": str(out), "chain_ok": bool(verdict.get("valid"))}
