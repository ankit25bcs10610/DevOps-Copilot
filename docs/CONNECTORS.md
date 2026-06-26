# Connectors (MCP servers)

Every capability lives behind a **Model Context Protocol** server — a separate
subprocess speaking MCP over stdio. The agent discovers tools at runtime via
`langchain-mcp-adapters` and **never imports a server directly**, exactly like a real
MCP deployment. Adding a capability is one entry in `app/mcp/client.py` — no agent
code changes.

Every server has two modes:
- **Live** — when its credentials/URL are configured, it calls the real API.
- **Offline** — otherwise it serves bundled fixtures, all tied to one coherent demo
  incident (a bad `checkout-svc` discount deploy), so the whole agent runs end-to-end
  with **no external accounts**.

Mutating tools are marked **(W)** — they're classified by the
[action policy](AGENT.md#human-in-the-loop-by-design) and gated behind human approval.

## The 9 servers / 47 tools

| Server | Mode toggle | Tools |
|--------|-------------|-------|
| **datadog** (observability) | `DD_API_KEY` + `DD_APP_KEY` | `search_logs`, `get_error_summary`, `cluster_logs`, `get_metric`, `list_services`, `detect_anomaly`, `compute_burn_rate`, `onset_timeline` |
| **pagerduty** (alerting) | `PAGERDUTY_API_TOKEN` | `list_incidents`, `get_incident`, `get_incident_alerts`, `add_incident_note` (W), `acknowledge_incident` (W), `resolve_incident` (W) |
| **kubernetes** (orchestration) | `KUBE_CONFIG_PATH` | `list_pods`, `describe_pod`, `get_events`, `get_deployment_status`, `rollout_history`, `scale_deployment` (W), `rollback_deployment` (W), `restart_deployment` (W) |
| **sentry** (errors) | `SENTRY_API_TOKEN` | `list_issues`, `get_issue`, `get_latest_event` |
| **traces** (distributed tracing) | `TRACES_API_URL` | `search_traces`, `get_trace`, `service_dependencies`, `analyze_blast_radius`, `analyze_critical_path`, `get_exemplars` |
| **deploys** (change events) | `DEPLOYS_API_URL` | `list_deploys`, `get_deploy`, `deploys_in_window` |
| **github** (repo host) | `GITHUB_TOKEN` + `GITHUB_REPO` | `list_recent_commits`, `get_commit_diff`, `correlate_changes`, `first_bad_deploy`, `list_workflow_runs`, `get_failed_job_logs`, `create_pull_request` (W) |
| **repo** (sandboxed FS + git) | `TARGET_REPO_PATH` | `list_dir`, `read_file`, `grep`, `git_log` |
| **incident-memory** (institutional memory) | `CORPUS_PATH` | `search_incidents`, `get_incident_record` |

## What the non-obvious tools do

**Analysis tools** turn raw signals into causal artifacts (they were the explicit
direction from the research: *stop adding raw-signal connectors, add analysis*):

- `detect_anomaly` — z-score + change-point over a metric series (spike vs noise).
- `onset_timeline` — orders each series' change-point to answer *who moved first*
  (a chain implies a cascade; simultaneous implies a shared cause).
- `compute_burn_rate` — multi-window SLO error-budget burn → page / ticket / ok.
- `cluster_logs` — Drain-style template mining (masks ids/numbers) so a flood of
  near-identical lines collapses to a few ranked patterns.
- `analyze_blast_radius` — from the trace dependency graph, who is *affected*
  (upstream) vs. a service's *downstream dependencies* (candidate causes) — cause vs.
  symptom.
- `analyze_critical_path` — self-time latency attribution + the deepest **fault span**,
  so the agent blames the span actually responsible, not its slow parent.
- `get_exemplars` — joins a metric-anomaly window to representative failing traces.
- `correlate_changes` — ranks recent commits by overlap with the incident signal.
- `first_bad_deploy` — time-anchored deploy bisect: the last change before onset.
- `search_incidents` — BM25 over prior RCAs/runbooks ("have we seen this before?").

## How the agent reaches them

```
session → load_mcp_tools(stack) → MultiServerMCPClient(_server_config())
        → one long-lived stdio subprocess per server (reused for the session)
        → tools converted to LangChain tools → bound to the agent node
```

Tool results pass through a **guarded tool node** that redacts PII and injection-scans
every output before it re-enters the model's context.

## Adding a connector

1. Create `app/mcp/servers/<name>/server.py` with a `FastMCP("<name>")` instance and
   `@mcp.tool()` functions. Follow the live/offline pattern: read creds from env; serve
   fixtures when absent.
2. Register it in `app/mcp/client.py` — add `"<name>"` to `_SERVERS` and an entry in
   `_server_config()` (use `_isecret(...)` so it's tenant-aware).
3. Add it to `MCP_CATALOG` in `app/api/main.py` (for the UI sidebar) and to the tool
   list in `app/graph/prompts.py`.
4. Classify any mutating tools in `app/policy.py`.
5. Add offline tests in `tests/test_connectors.py`.

See also: [The agent](AGENT.md) · [Configuration](CONFIGURATION.md).
