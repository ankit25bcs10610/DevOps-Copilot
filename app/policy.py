"""Action policy engine — graduated human-in-the-loop by consequence class.

The old gate was binary: a tool was either a "write" (always approve) or a read
(always auto-run). That doesn't scale as the agent gains more mutating tools with
very different blast radii (open a PR vs. scale a deployment to zero vs. ack a
page). This module maps every tool — and, where it matters, its *arguments* — to
one of three decisions:

    allow   — read-only / no external side effect; run immediately.
    notify  — low-consequence, reversible mutation; run, but record an audit event.
    approve — consequential or hard-to-reverse mutation; pause for a human.

It also attaches a risk tier and a human-readable impact PREVIEW so the approval
card shows *what will happen* (terraform-plan style), not just a tool name. This
is the structural piece behind graduated oversight (a compliance expectation for
agents that can act on infrastructure).

Pure and dependency-free so the routing logic stays unit-testable. The set of
approve-class tools is the single source of truth for the graph's approval gate
(re-exported as WRITE_TOOLS for backward compatibility).
"""

from __future__ import annotations

from typing import Literal

Decision = Literal["allow", "notify", "approve"]
Risk = Literal["low", "medium", "high"]

# tool name -> (decision, risk, why). Tools not listed default to allow/low
# (read-only). Add a mutating tool here the moment its server is wired up, so the
# gate is opt-out-safe: an unknown tool is treated as a read, but every WRITE we
# ship is explicitly classified.
_POLICY: dict[str, tuple[Decision, Risk, str]] = {
    # github
    "create_pull_request": ("approve", "medium", "Opens a real pull request against the repository"),
    # pagerduty (write actions added with the connector work)
    "add_incident_note": ("notify", "low", "Posts a note to the incident timeline"),
    "acknowledge_incident": ("notify", "low", "Acknowledges the incident (reversible)"),
    "resolve_incident": ("approve", "medium", "Marks the incident resolved"),
    # kubernetes (write actions added with the connector work)
    "scale_deployment": ("approve", "high", "Changes the replica count of a live deployment"),
    "rollback_deployment": ("approve", "high", "Rolls a deployment back to a previous revision"),
    "restart_deployment": ("approve", "medium", "Triggers a rolling restart of a deployment"),
}


def classify(tool_name: str, args: dict | None = None) -> dict:
    """Return the policy decision for a tool call: decision, risk, why, preview.

    Argument-aware escalation: e.g. scaling a deployment to zero replicas is a
    full outage, so it is always high-risk/approve regardless of base policy.
    """
    decision, risk, why = _POLICY.get(tool_name, ("allow", "low", "Read-only — no external side effects"))
    args = args or {}

    # Arg-aware escalations (consequence depends on the arguments, not just the tool).
    if tool_name == "scale_deployment":
        replicas = args.get("replicas")
        try:
            if replicas is not None and int(replicas) == 0:
                decision, risk = "approve", "high"
                why = "Scales the deployment to ZERO replicas (full outage of the service)"
        except (TypeError, ValueError):
            pass

    return {
        "tool": tool_name,
        "decision": decision,
        "risk": risk,
        "why": why,
        "write": decision in ("notify", "approve"),
        "preview": describe_action(tool_name, args),
    }


def requires_approval(tool_name: str, args: dict | None = None) -> bool:
    """True when a tool call must pause for a human (approve-class)."""
    return classify(tool_name, args)["decision"] == "approve"


def describe_action(tool_name: str, args: dict | None = None) -> str:
    """A short, human-readable preview of what a tool call will do — shown on the
    approval card so the reviewer sees the impact, not just the tool name."""
    args = args or {}

    def _g(*keys: str) -> str:
        for k in keys:
            v = args.get(k)
            if v not in (None, ""):
                return str(v)
        return "?"

    if tool_name == "create_pull_request":
        return f"Open PR “{_g('title')}” ({_g('head')} → {_g('base')})"
    if tool_name == "scale_deployment":
        return f"Scale {_g('deployment', 'name')} to {_g('replicas')} replica(s)"
    if tool_name == "rollback_deployment":
        return f"Roll back {_g('deployment', 'name')} to a previous revision"
    if tool_name == "restart_deployment":
        return f"Restart {_g('deployment', 'name')}"
    if tool_name == "resolve_incident":
        return f"Resolve incident {_g('incident_id', 'id')}"
    if tool_name == "acknowledge_incident":
        return f"Acknowledge incident {_g('incident_id', 'id')}"
    if tool_name == "add_incident_note":
        return f"Add note to incident {_g('incident_id', 'id')}"
    return tool_name


# The approve-class tools — the canonical set the graph's approval gate keys on.
# Re-exported as WRITE_TOOLS so existing imports keep working unchanged.
APPROVE_TOOLS: set[str] = {t for t, (d, _, _) in _POLICY.items() if d == "approve"}
