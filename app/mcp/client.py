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

_SERVERS = ("datadog", "repo", "github", "pagerduty")


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
            "env": {"PAGERDUTY_API_TOKEN": s.pagerduty_api_token},  # blank = offline fixtures
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
