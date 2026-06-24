"""Central configuration, loaded from environment / .env file.

Everything the agent and MCP servers need is resolved here so the rest of the
code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (app/config.py -> app -> root)
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    # Which provider backs the agent: "anthropic" (Claude) or "groq" (Llama).
    copilot_provider: str = "anthropic"
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    # Optional model overrides. Leave blank to use the provider's defaults
    # (see app/llm.py: Opus 4.8 + Haiku 4.5 for Anthropic, Llama 3.3/3.1 for Groq).
    copilot_model: str = ""       # main reasoning / tool-calling model
    copilot_fast_model: str = ""  # cheaper model for lightweight nodes (plan, reflect)

    # --- MCP servers ---
    target_repo_path: str = "./sample_repo"
    logs_data_path: str = "./app/mcp/servers/logs_metrics/sample_data"
    github_token: str = ""
    # "owner/repo" — required only when github_token is set (real GitHub mode).
    github_repo: str = ""

    # --- API ---
    # Comma-separated browser origins allowed by CORS (the dev server is always
    # allowed). Set this to your deployed frontend origin in production.
    cors_origins: str = ""

    # --- Observability ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "devops-copilot"

    # --- Agent behavior ---
    # Max agent (LLM) calls per turn — bounds the agent<->tools loop.
    copilot_max_iterations: int = 8
    copilot_checkpoint_db: str = "./copilot_checkpoints.sqlite"

    @field_validator("copilot_provider")
    @classmethod
    def _normalize_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in {"anthropic", "groq"}:
            raise ValueError("COPILOT_PROVIDER must be 'anthropic' or 'groq'")
        return v

    @property
    def allowed_origins(self) -> list[str]:
        base = ["http://localhost:5173", "http://127.0.0.1:5173"]
        extra = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return base + extra

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
