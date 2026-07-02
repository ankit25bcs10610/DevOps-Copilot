# DevOps Copilot — Helm chart

Production Kubernetes deployment of the single-image app (SPA served by FastAPI).

```bash
helm install copilot ./deploy/helm/copilot \
  --set image.tag=1.0.0 \
  --set-string config.COPILOT_REDIS_URL=redis://redis:6379/0 \
  --set-string config.COPILOT_CHECKPOINT_DB=postgresql://user@pg/copilot \
  --set secret.existingSecret=copilot-secrets
```

## What's in the chart
- **Deployment** with liveness (`/healthz`) + readiness (`/readyz`) probes, resource
  requests/limits, a non-root hardened `securityContext`, and a config checksum so a
  ConfigMap change triggers a rolling restart.
- **HorizontalPodAutoscaler** (CPU target) and **PodDisruptionBudget**.
- **Service**, **ServiceAccount**, **ConfigMap** (non-secret env), and a **Secret**
  (use `secret.existingSecret` to source from your secrets manager / External Secrets
  Operator instead of inlining values).
- Optional **PVC** for the SQLite checkpointer (single-instance only).

## Scaling to >1 replica
The default SQLite checkpointer + per-process limiter/queue are single-instance. For
`replicaCount > 1` set:
- `config.COPILOT_CHECKPOINT_DB` → a `postgresql://…` URL (shared session/graph state),
- `config.COPILOT_REDIS_URL` → a `redis://…` URL (shared rate limiter, job queue, spend cap).

Then the HPA can scale horizontally safely.

## Secrets
Prefer `secret.existingSecret` populated by your secrets manager (see
`app/secretsmgr.py` / External Secrets Operator) rather than the inline `secret.data`
map, which is for local/dev only.
