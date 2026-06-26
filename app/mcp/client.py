"""MCP client wiring.

Registers every MCP server the agent can use and loads their tools as LangChain
tools via `langchain-mcp-adapters`. The agent never imports a server directly —
it only sees the discovered tools, exactly like a real MCP deployment.

Adding a new capability = adding one entry here. No agent code changes.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools as _load_session_tools

from app import policy, runtime
from app.config import get_settings

_SERVERS = (
    "datadog", "repo", "github", "pagerduty", "kubernetes", "sentry", "memory", "traces",
)


def _server_config() -> dict:
    """Build the MultiServerMCPClient config.

    Each server is launched as a subprocess speaking MCP over stdio. We pass the
    current Python interpreter and forward the relevant paths via env so the
    servers read the same data the app is configured with.
    """
    py = sys.executable
    s = get_settings()
    return {
        "datadog": {
            "command": py,
            "args": ["-m", "app.mcp.servers.datadog.server"],
            "transport": "stdio",
            # LOGS_DATA_PATH backs offline-demo mode; DD_* enable the live API.
            "env": {
                "LOGS_DATA_PATH": str(runtime.logs_path()),
                "DD_API_KEY": s.dd_api_key,
                "DD_APP_KEY": s.dd_app_key,
                "DD_SITE": s.dd_site,
            },
        },
        "repo": {
            "command": py,
            "args": ["-m", "app.mcp.servers.repo.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "github": {
            "command": py,
            "args": ["-m", "app.mcp.servers.github.server"],
            "transport": "stdio",
            # Runtime creds (set via the UI "Connect GitHub" flow) take
            # precedence over .env, so live mode can be toggled without restart.
            "env": {
                "GITHUB_TOKEN": runtime.github_token(),
                "GITHUB_REPO": runtime.github_repo(),
            },
        },
        "pagerduty": {
            "command": py,
            "args": ["-m", "app.mcp.servers.pagerduty.server"],
            "transport": "stdio",
            "env": {  # blank token = offline fixtures
                "PAGERDUTY_API_TOKEN": s.pagerduty_api_token,
                "PAGERDUTY_FROM_EMAIL": s.pagerduty_from_email,
            },
        },
        "kubernetes": {
            "command": py,
            "args": ["-m", "app.mcp.servers.kubernetes.server"],
            "transport": "stdio",
            # KUBE_CONFIG_PATH enables live cluster mode; blank = offline fixtures.
            "env": {"KUBE_CONFIG_PATH": s.kube_config_path, "KUBE_NAMESPACE": s.kube_namespace},
        },
        "sentry": {
            "command": py,
            "args": ["-m", "app.mcp.servers.sentry.server"],
            "transport": "stdio",
            # SENTRY_API_TOKEN enables live mode; blank = offline fixtures.
            "env": {
                "SENTRY_API_TOKEN": s.sentry_api_token,
                "SENTRY_ORG": s.sentry_org,
                "SENTRY_PROJECT": s.sentry_project,
            },
        },
        "memory": {
            "command": py,
            "args": ["-m", "app.mcp.servers.memory.server"],
            "transport": "stdio",
            # CORPUS_PATH overrides the bundled prior-incident corpus; blank = bundled.
            "env": {"CORPUS_PATH": s.incident_corpus_path},
        },
        "traces": {
            "command": py,
            "args": ["-m", "app.mcp.servers.traces.server"],
            "transport": "stdio",
            # TRACES_API_URL enables live (Jaeger-compatible) mode; blank = fixtures.
            "env": {"TRACES_API_URL": s.traces_api_url},
        },
    }


# Tool names that mutate external state and require human approval. The action
# policy engine (app/policy.py) is the single source of truth; this alias keeps
# existing imports working while approve/notify/allow classification lives there.
WRITE_TOOLS: set[str] = policy.APPROVE_TOOLS


async def load_mcp_tools(stack: AsyncExitStack) -> tuple[list[BaseTool], MultiServerMCPClient]:
    """Open ONE long-lived MCP session per server and load each server's tools
    bound to that session.

    The sessions are entered into the caller's AsyncExitStack, so they (and their
    stdio subprocesses) close when the CopilotSession does. This reuses a single
    subprocess per server for the whole session, instead of `get_tools()`, whose
    tools spawn and tear down a fresh subprocess on *every* tool call — a stdio
    handshake per call that dominated latency in a multi-tool investigation.
    """
    client = MultiServerMCPClient(_server_config())
    tools: list[BaseTool] = []
    for name in _SERVERS:
        session = await stack.enter_async_context(client.session(name))
        tools.extend(await _load_session_tools(session))
    return tools, client
