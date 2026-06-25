"""PagerDuty webhook (v3) verification + parsing.

A signed webhook is what *triggers* an investigation in production. Verification
follows PagerDuty's HMAC-SHA256 signing scheme
(https://developer.pagerduty.com/docs/webhooks/webhooks-overview/), where the
`X-PagerDuty-Signature` header is a comma-separated list of `v1=<hex>` digests.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Constant-time verify; true if any `v1=` digest in the header matches."""
    if not (secret and signature_header):
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    expected = f"v1={digest}"
    return any(hmac.compare_digest(expected, part.strip()) for part in signature_header.split(","))


def parse_incident(payload: dict) -> dict | None:
    """Pull the incident fields we seed an investigation with. Returns None for
    non-incident events."""
    event = payload.get("event") or {}
    etype = event.get("event_type", "")
    if not etype.startswith("incident."):
        return None
    data = event.get("data") or {}
    return {
        "id": data.get("id"),
        "type": etype,  # e.g. "incident.triggered"
        "title": data.get("title") or data.get("summary") or "(untitled incident)",
        "service": (data.get("service") or {}).get("summary"),
        "url": data.get("html_url"),
    }
