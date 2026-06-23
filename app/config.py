"""Central configuration, loaded from environment / .env file.

Everything the agent and MCP servers need is resolved here so the rest of the
code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (app/config.py -> app -> root)
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (Groq) ---
    groq_api_key: str = ""
    # Main reasoning/tool-calling model.
    copilot_model: str = "llama-3.3-70b-versatile"
    # Cheaper, faster model for lightweight nodes (plan, reflect) to save tokens.
    copilot_fast_model: str = "llama-3.1-8b-instant"

    # --- MCP servers ---
    target_repo_path: str = "./sample_repo"
    logs_data_path: str = "./app/mcp/servers/logs_metrics/sample_data"
    github_token: str = ""

    # --- Observability ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "devops-copilot"

    # --- Agent behavior ---
    copilot_max_iterations: int = 6
    copilot_checkpoint_db: str = "./copilot_checkpoints.sqlite"

    @property
    def repo_path(self) -> Path:
        return (ROOT / self.target_repo_path).resolve()

    @property
    def logs_path(self) -> Path:
        return (ROOT / self.logs_data_path).resolve()

    @property
    def offline_mode(self) -> bool:
        """True when no GitHub token is set — the GitHub MCP server runs against
        local demo fixtures instead of the real API."""
        return not self.github_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
