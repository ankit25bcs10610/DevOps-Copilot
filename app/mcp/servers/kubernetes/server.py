"""Kubernetes MCP server — read-only cluster inspection + bounded remediation.

Most production incidents surface first as orchestration symptoms — CrashLoopBackOff,
OOMKilled, a stuck rollout, pods not becoming Ready — and the highest-value
remediation (rollback) lives here too. This server gives the agent that context.

When `KUBE_CONFIG_PATH` is set it talks to the real cluster via the official
`kubernetes` client; otherwise it runs in OFFLINE DEMO mode with fixtures that tie
into the bundled checkout-svc incident (a bad discount deploy → CrashLoopBackOff),
so the whole agent stays runnable with no cluster — the same live/offline pattern
as the datadog/github/pagerduty servers.

Read tools (allow): list_pods, describe_pod, get_events, get_deployment_status,
rollout_history.
Write tools (gated via app/policy.py → human approval): scale_deployment,
rollback_deployment, restart_deployment.

Run standalone for debugging:
    python -m app.mcp.servers.kubernetes.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

KUBE_CONFIG_PATH = os.environ.get("KUBE_CONFIG_PATH", "").strip()
KUBE_NAMESPACE = os.environ.get("KUBE_NAMESPACE", "default").strip() or "default"
OFFLINE = not KUBE_CONFIG_PATH

mcp = FastMCP("kubernetes")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Offline fixtures — a checkout-svc deploy gone wrong (ties to the discount bug).
# --------------------------------------------------------------------------- #
_DEMO_PODS = [
    {
        "name": "checkout-svc-7d9f4c8b6-q2m4x", "namespace": "default", "status": "CrashLoopBackOff",
        "ready": "0/1", "restarts": 8, "age": "12m", "node": "ip-10-0-1-23",
        "reason": "Back-off restarting failed container (exit code 1)",
    },
    {
        "name": "checkout-svc-7d9f4c8b6-h7k9p", "namespace": "default", "status": "Running",
        "ready": "1/1", "restarts": 5, "age": "12m", "node": "ip-10-0-1-44",
        "reason": "",
    },
    {
        "name": "checkout-svc-6b1a2c3d4-zz000", "namespace": "default", "status": "Running",
        "ready": "1/1", "restarts": 0, "age": "6d", "node": "ip-10-0-1-7",
        "reason": "previous stable revision",
    },
    {
        "name": "inventory-svc-5c4b3a2-abc12", "namespace": "default", "status": "Running",
        "ready": "1/1", "restarts": 0, "age": "9d", "node": "ip-10-0-1-9",
        "reason": "",
    },
]

_DEMO_EVENTS = [
    {"type": "Normal", "reason": "ScalingReplicaSet", "object": "deployment/checkout-svc",
     "age": "13m", "message": "Scaled up replica set checkout-svc-7d9f4c8b6 to 3"},
    {"type": "Normal", "reason": "Pulled", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
     "age": "12m", "message": "Successfully pulled image checkout-svc:1.8.0"},
    {"type": "Warning", "reason": "BackOff", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
     "age": "11m", "message": "Back-off restarting failed container checkout in pod"},
    {"type": "Warning", "reason": "Unhealthy", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
     "age": "11m", "message": "Readiness probe failed: HTTP probe returned 500"},
    {"type": "Normal", "reason": "Pulled", "object": "pod/inventory-svc-5c4b3a2-abc12",
     "age": "9d", "message": "Container image already present on machine"},
]

_DEMO_DEPLOYMENT = {
    "name": "checkout-svc", "namespace": "default",
    "desired": 3, "ready": 1, "available": 1, "unavailable": 2, "updated": 3,
    "image": "checkout-svc:1.8.0",
    "conditions": [
        {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
        {"type": "Progressing", "status": "False",
         "reason": "ProgressDeadlineExceeded", "message": "ReplicaSet checkout-svc-7d9f4c8b6 has timed out progressing"},
    ],
    "rollout": "stuck — 2/3 replicas unavailable since the 1.8.0 rollout",
}

_DEMO_ROLLOUT_HISTORY = [
    {"revision": 1, "image": "checkout-svc:1.6.2", "change_cause": "Initial deploy"},
    {"revision": 2, "image": "checkout-svc:1.7.0", "change_cause": "Refactor cart total calculation"},
    {"revision": 3, "image": "checkout-svc:1.8.0",
     "change_cause": "Add percentage discount support to checkout (commit abc1234)"},
]


def _client():
    """Build (CoreV1Api, AppsV1Api) from the configured kubeconfig (live mode)."""
    from kubernetes import client, config  # lazy: optional dependency

    config.load_kube_config(config_file=KUBE_CONFIG_PATH)
    return client.CoreV1Api(), client.AppsV1Api()


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_pods(namespace: str = "", status: str | None = None) -> list[dict]:
    """List pods with status, readiness, restart count, age, and node.

    Args:
        namespace: kube namespace (defaults to the configured one).
        status: optionally filter by phase/state (e.g. "CrashLoopBackOff", "Running").
    """
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        return [p for p in _DEMO_PODS if not status or str(p["status"]).lower() == status.lower()]
    core, _ = _client()
    out = []
    for p in core.list_namespaced_pod(ns).items:
        cs = p.status.container_statuses or []
        restarts = sum(c.restart_count for c in cs)
        ready = f"{sum(1 for c in cs if c.ready)}/{len(cs)}" if cs else "0/0"
        phase = p.status.phase or "Unknown"
        out.append({
            "name": p.metadata.name, "namespace": ns, "status": phase,
            "ready": ready, "restarts": restarts, "node": p.spec.node_name or "",
        })
    if status:
        out = [p for p in out if p["status"].lower() == status.lower()]
    return out


@mcp.tool()
def describe_pod(name: str, namespace: str = "") -> dict:
    """Describe a pod: container statuses (CrashLoopBackOff / OOMKilled / exit
    codes), restart count, and the pod's recent events — the fastest path from
    'pod is unhealthy' to *why*."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        pod = next((p for p in _DEMO_PODS if p["name"] == name), None)
        if not pod:
            return {"error": f"pod '{name}' not found (offline demo)",
                    "available": [p["name"] for p in _DEMO_PODS]}
        events = [e for e in _DEMO_EVENTS if name in e["object"]]
        return {**pod, "events": events,
                "containers": [{"name": "checkout", "state": pod["status"],
                                "last_exit_code": 1 if pod["restarts"] else 0,
                                "reason": pod["reason"]}]}
    core, _ = _client()
    p = core.read_namespaced_pod(name, ns)
    containers = [{
        "name": c.name,
        "ready": c.ready,
        "restarts": c.restart_count,
        "state": next(iter((c.state.to_dict() or {}).keys()), "unknown") if c.state else "unknown",
    } for c in (p.status.container_statuses or [])]
    return {"name": name, "namespace": ns, "status": p.status.phase, "containers": containers}


@mcp.tool()
def get_events(namespace: str = "", only_warnings: bool = False) -> list[dict]:
    """Recent cluster events (Normal/Warning) — surfaces BackOff, Unhealthy probes,
    image pull errors, and rollout activity. Set only_warnings to focus on problems."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        return [e for e in _DEMO_EVENTS if not only_warnings or e["type"] == "Warning"]
    core, _ = _client()
    out = []
    for e in core.list_namespaced_event(ns).items:
        if only_warnings and e.type != "Warning":
            continue
        out.append({"type": e.type, "reason": e.reason,
                    "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "message": e.message})
    return out


@mcp.tool()
def get_deployment_status(name: str, namespace: str = "") -> dict:
    """Deployment health: desired/ready/available/unavailable replicas, rollout
    conditions, and image — i.e. is the rollout healthy or stuck?"""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        if name != _DEMO_DEPLOYMENT["name"]:
            return {"error": f"deployment '{name}' not found (offline demo has '{_DEMO_DEPLOYMENT['name']}')"}
        return dict(_DEMO_DEPLOYMENT)
    _, apps = _client()
    d = apps.read_namespaced_deployment(name, ns)
    s = d.status
    return {
        "name": name, "namespace": ns,
        "desired": d.spec.replicas, "ready": s.ready_replicas or 0,
        "available": s.available_replicas or 0,
        "unavailable": s.unavailable_replicas or 0,
        "conditions": [{"type": c.type, "status": c.status, "reason": c.reason}
                       for c in (s.conditions or [])],
    }


@mcp.tool()
def rollout_history(deployment: str, namespace: str = "") -> list[dict]:
    """Revision history of a deployment with change-causes — the 'what changed'
    needed to correlate a bad rollout with the offending release/commit."""
    if OFFLINE:
        if deployment != _DEMO_DEPLOYMENT["name"]:
            return [{"error": f"deployment '{deployment}' not found (offline demo)"}]
        return list(_DEMO_ROLLOUT_HISTORY)
    # Live revision history lives on the deployment's ReplicaSets; summarize them.
    _, apps = _client()
    ns = namespace or KUBE_NAMESPACE
    out = []
    for rs in apps.list_namespaced_replica_set(ns).items:
        ann = rs.metadata.annotations or {}
        rev = ann.get("deployment.kubernetes.io/revision")
        if rev:
            out.append({"revision": _as_int(rev, 0),
                        "change_cause": ann.get("kubernetes.io/change-cause", ""),
                        "image": rs.spec.template.spec.containers[0].image})
    return sorted(out, key=lambda r: r["revision"])


# --------------------------------------------------------------------------- #
# Write tools — gated via app/policy.py (human approval; scale-to-zero = high risk)
# --------------------------------------------------------------------------- #
@mcp.tool()
def scale_deployment(deployment: str, replicas: int | str, namespace: str = "") -> dict:
    """(WRITE) Scale a deployment's replica count. Requires human approval."""
    replicas = _as_int(replicas, -1)
    if replicas < 0:
        return {"error": "replicas must be a non-negative integer"}
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        return {"status": "scaled (simulated — offline demo, no cluster touched)",
                "deployment": deployment, "namespace": ns, "replicas": replicas}
    _, apps = _client()
    apps.patch_namespaced_deployment_scale(deployment, ns, {"spec": {"replicas": replicas}})
    return {"status": "scaled", "deployment": deployment, "namespace": ns, "replicas": replicas}


@mcp.tool()
def rollback_deployment(deployment: str, namespace: str = "") -> dict:
    """(WRITE) Roll a deployment back to its previous revision — the highest-value
    remediation for a change-induced incident. Requires human approval."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        prev = _DEMO_ROLLOUT_HISTORY[-2] if len(_DEMO_ROLLOUT_HISTORY) >= 2 else None
        return {"status": "rolled back (simulated — offline demo, no cluster touched)",
                "deployment": deployment, "namespace": ns,
                "to_revision": prev["revision"] if prev else None,
                "to_image": prev["image"] if prev else None}
    # Live rollback = restore the previous ReplicaSet's pod template (kubectl
    # rollout undo semantics). Left to the operator's GitOps in this build.
    return {"status": "not_implemented_live",
            "detail": "Live rollback should go through your GitOps/CD; offline demo simulates it."}


@mcp.tool()
def restart_deployment(deployment: str, namespace: str = "") -> dict:
    """(WRITE) Trigger a rolling restart of a deployment. Requires human approval."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        return {"status": "restart triggered (simulated — offline demo, no cluster touched)",
                "deployment": deployment, "namespace": ns}
    import datetime

    _, apps = _client()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    patch = {"spec": {"template": {"metadata": {"annotations":
            {"kubectl.kubernetes.io/restartedAt": now}}}}}
    apps.patch_namespaced_deployment(deployment, ns, patch)
    return {"status": "restart triggered", "deployment": deployment, "namespace": ns}


if __name__ == "__main__":
    mcp.run(transport="stdio")
