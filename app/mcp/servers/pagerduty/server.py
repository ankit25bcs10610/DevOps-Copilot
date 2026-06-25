"""PagerDuty alerting MCP server.

Gives the agent the *alerting* context of an incident: what's currently paging,
the incident details, and the underlying alerts/signals that triggered it. When
`PAGERDUTY_API_TOKEN` is set it calls the real PagerDuty REST API; otherwise it
runs in OFFLINE DEMO mode with fixtures that tie into the bundled checkout-svc
incident — the same live/offline pattern as the datadog and github servers.

This is the "alerting" leg of the product stack (Datadog · GitHub · PagerDuty ·
Slack). In production a PagerDuty webhook is also what *triggers* an investigation
(see the deployment/trigger work); these tools let the agent pull incident context
mid-investigation.

Live API (per https://developer.pagerduty.com/api-reference/):
  - GET https://api.pagerduty.com/incidents
  - GET https://api.pagerduty.com/incidents/{id}
  - GET https://api.pagerduty.com/incidents/{id}/alerts
  - auth:  Authorization: Token token=<API_TOKEN>
           Accept: application/vnd.pagerduty+json;version=2
Verify scopes against your own PagerDuty account.

Tools:
  - list_incidents:        current incidents (filter by status)
  - get_incident:          one incident's details
  - get_incident_alerts:   the alerts/signals that triggered an incident
  - add_incident_note:     (WRITE/notify) post the copilot's findings to the timeline
  - acknowledge_incident:  (WRITE/notify) acknowledge an incident
  - resolve_incident:      (WRITE/approve) mark an incident resolved

Run standalone for debugging:
    PAGERDUTY_API_TOKEN=... python -m app.mcp.servers.pagerduty.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

PAGERDUTY_API_TOKEN = os.environ.get("PAGERDUTY_API_TOKEN", "").strip()
# PagerDuty write actions require a `From` header identifying the acting user.
PAGERDUTY_FROM_EMAIL = os.environ.get("PAGERDUTY_FROM_EMAIL", "").strip()
OFFLINE = not PAGERDUTY_API_TOKEN
BASE_URL = "https://api.pagerduty.com"

mcp = FastMCP("pagerduty")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    return {
        "Authorization": f"Token token={PAGERDUTY_API_TOKEN}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
    }


# --- Offline fixtures (tie into the bundled checkout-svc incident) ---------- #
_DEMO_INCIDENT = {
    "id": "PINC4242",
    "number": 4242,
    "title": "Elevated 5xx error rate on checkout-svc",
    "status": "triggered",
    "urgency": "high",
    "service": "checkout-svc",
    "created_at": "2026-06-23T14:05:00Z",
    "html_url": "https://acme.pagerduty.com/incidents/PINC4242",
    "description": "checkout-svc 5xx error rate crossed the alert threshold.",
}

_DEMO_ALERTS = [
    {
        "id": "PALERT1",
        "status": "triggered",
        "summary": "checkout-svc 5xx error rate > 5% (Datadog monitor)",
        "created_at": "2026-06-23T14:05:00Z",
        "body": "error_rate_5xx=0.71 exceeded threshold 0.05 for service:checkout-svc "
        "over the last 5m. Source monitor: 'checkout-svc 5xx'.",
    }
]


@mcp.tool()
def list_incidents(statuses: str = "triggered,acknowledged", limit: int | str = 20) -> list[dict]:
    """List current incidents.

    Args:
        statuses: comma-separated subset of triggered/acknowledged/resolved.
        limit: max incidents to return (newest first).
    """
    limit = _as_int(limit, 20)
    wanted = [s.strip() for s in statuses.split(",") if s.strip()]
    if OFFLINE:
        items = [_DEMO_INCIDENT] if (not wanted or _DEMO_INCIDENT["status"] in wanted) else []
        return items[:limit]

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/incidents",
        headers=_headers(),
        params={"statuses[]": wanted, "limit": limit, "sort_by": "created_at:desc"},
        timeout=20,
    )
    resp.raise_for_status()
    return [
        {
            "id": inc.get("id"),
            "number": inc.get("incident_number"),
            "title": inc.get("title"),
            "status": inc.get("status"),
            "urgency": inc.get("urgency"),
            "service": (inc.get("service") or {}).get("summary"),
            "created_at": inc.get("created_at"),
            "html_url": inc.get("html_url"),
        }
        for inc in resp.json().get("incidents", [])
    ]


@mcp.tool()
def get_incident(incident_id: str) -> dict:
    """Return one incident's details by id."""
    if OFFLINE:
        return dict(_DEMO_INCIDENT) if incident_id in ("", _DEMO_INCIDENT["id"]) else {
            "error": f"unknown incident '{incident_id}' (offline demo has {_DEMO_INCIDENT['id']})"
        }

    import httpx

    resp = httpx.get(f"{BASE_URL}/incidents/{incident_id}", headers=_headers(), timeout=20)
    resp.raise_for_status()
    inc = resp.json().get("incident", {})
    return {
        "id": inc.get("id"),
        "number": inc.get("incident_number"),
        "title": inc.get("title"),
        "status": inc.get("status"),
        "urgency": inc.get("urgency"),
        "service": (inc.get("service") or {}).get("summary"),
        "created_at": inc.get("created_at"),
        "html_url": inc.get("html_url"),
        "description": inc.get("description"),
    }


@mcp.tool()
def get_incident_alerts(incident_id: str) -> list[dict]:
    """Return the alerts/signals that triggered an incident — the raw symptom the
    investigation should start from (service, metric, threshold, source monitor)."""
    if OFFLINE:
        return _DEMO_ALERTS if incident_id in ("", _DEMO_INCIDENT["id"]) else []

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/incidents/{incident_id}/alerts", headers=_headers(), timeout=20
    )
    resp.raise_for_status()
    out = []
    for al in resp.json().get("alerts", []):
        body = al.get("body") or {}
        out.append(
            {
                "id": al.get("id"),
                "status": al.get("status"),
                "summary": al.get("summary"),
                "created_at": al.get("created_at"),
                "body": body.get("details") or body.get("contexts") or "",
            }
        )
    return out


# --- Write tools (gated via app/policy.py → human approval/notify) --------- #
def _write_headers() -> dict:
    h = _headers()
    if PAGERDUTY_FROM_EMAIL:
        h["From"] = PAGERDUTY_FROM_EMAIL
    return h


@mcp.tool()
def add_incident_note(incident_id: str, note: str) -> dict:
    """(WRITE) Post a note to an incident's timeline — e.g. the copilot's RCA
    summary, so responders see the findings where they're already looking."""
    if OFFLINE:
        return {"status": "note added (simulated — offline demo)", "incident_id": incident_id,
                "note": note[:500]}
    import httpx

    resp = httpx.post(
        f"{BASE_URL}/incidents/{incident_id}/notes",
        headers=_write_headers(), json={"note": {"content": note}}, timeout=20,
    )
    resp.raise_for_status()
    return {"status": "note added", "incident_id": incident_id}


@mcp.tool()
def acknowledge_incident(incident_id: str) -> dict:
    """(WRITE) Acknowledge an incident (reversible)."""
    return _set_status(incident_id, "acknowledged")


@mcp.tool()
def resolve_incident(incident_id: str) -> dict:
    """(WRITE) Mark an incident resolved."""
    return _set_status(incident_id, "resolved")


def _set_status(incident_id: str, status: str) -> dict:
    if OFFLINE:
        return {"status": f"incident {status} (simulated — offline demo)", "incident_id": incident_id}
    import httpx

    resp = httpx.put(
        f"{BASE_URL}/incidents/{incident_id}",
        headers=_write_headers(),
        json={"incident": {"type": "incident_reference", "status": status}},
        timeout=20,
    )
    resp.raise_for_status()
    return {"status": status, "incident_id": incident_id}


if __name__ == "__main__":
    mcp.run(transport="stdio")
