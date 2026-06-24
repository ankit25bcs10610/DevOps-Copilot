"""MCP client wiring.

Registers every MCP server the agent can use and loads their tools as LangChain
tools via `langchain-mcp-adapters`. The agent never imports a server directly —
it only sees the discovered tools, exactly like a real MCP deployment.

Adding a new capability = adding one entry here. No agent code changes.
"""

from __future__ import annotations

import sys

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import get_settings


def _server_config() -> dict:
    """Build the MultiServerMCPClient config.

    Each server is launched as a subprocess speaking MCP over stdio. We pass the
    current Python interpreter and forward the relevant paths via env so the
    servers read the same data the app is configured with.
    """
    settings = get_settings()
    py = sys.executable
    return {
        "logs-metrics": {
            "command": py,
            "args": ["-m", "app.mcp.servers.logs_metrics.server"],
            "transport": "stdio",
            "env": {"LOGS_DATA_PATH": str(settings.logs_path)},
        },
        "repo": {
            "command": py,
            "args": ["-m", "app.mcp.servers.repo.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(settings.repo_path)},
        },
        "github": {
            "command": py,
            "args": ["-m", "app.mcp.servers.github.server"],
            "transport": "stdio",
            "env": {
                "GITHUB_TOKEN": settings.github_token,
                "GITHUB_REPO": settings.github_repo,
            },
        },
    }


# Tool names that mutate external state — the agent must get human approval
# before invoking any of these.
WRITE_TOOLS: set[str] = {"create_pull_request"}


async def load_mcp_tools() -> tuple[list[BaseTool], MultiServerMCPClient]:
    """Start all MCP servers and return their tools as LangChain tools.

    Returns the tool list and the client (keep the client alive for the
    duration of the session so the server subprocesses stay up).
    """
    client = MultiServerMCPClient(_server_config())
    tools = await client.get_tools()
    return tools, client
