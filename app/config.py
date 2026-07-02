"""Central configuration, loaded from environment / .env file.

Everything the agent and MCP servers need is resolved here so the rest of the
code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
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
    # Which provider backs the agent: anthropic | openai | gemini | groq | deepseek.
    copilot_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
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
    # Datadog observability connector. With both keys set it queries the real
    # Datadog API; otherwise the connector serves the bundled offline fixtures.
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site: str = "datadoghq.com"  # e.g. datadoghq.eu, us3.datadoghq.com
    # PagerDuty alerting connector. With a token it queries the real API;
    # otherwise the connector serves offline incident fixtures.
    pagerduty_api_token: str = ""
    # Email identifying the actor for PagerDuty write actions (note/ack/resolve).
    pagerduty_from_email: str = ""
    # Kubernetes connector. With a kubeconfig path set it queries the real cluster
    # (needs the `kubernetes` client); otherwise it serves offline pod/event fixtures.
    kube_config_path: str = ""
    kube_namespace: str = "default"
    # Sentry connector. With an auth token it queries the real API; otherwise it
    # serves offline issue/event fixtures. SENTRY_ORG/SENTRY_PROJECT scope live calls.
    sentry_api_token: str = ""
    sentry_org: str = ""
    sentry_project: str = ""
    # Incident-memory corpus (prior RCAs/runbooks for similarity search). Blank =
    # the bundled demo corpus; point at your own JSON to search real postmortems.
    incident_corpus_path: str = ""
    # Distributed-traces connector. With a Jaeger-compatible query URL it queries
    # real traces; otherwise it serves offline OpenTelemetry-style span fixtures.
    traces_api_url: str = ""
    # Deploys / change-events connector. With a deploy-tracker URL it queries real
    # deploys; otherwise it serves offline fixtures (the checkout-svc rollout).
    deploys_api_url: str = ""
    deploys_api_token: str = ""

    # --- Trigger / delivery (webhooks → investigate → Slack) ---
    # PagerDuty webhook → auto-start an investigation (HMAC-verified).
    pagerduty_webhook_secret: str = ""
    # Slack app: post investigations + approval buttons, verify interaction callbacks.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel: str = ""  # channel id/name investigations post into

    # --- API ---
    # Deployment environment: "development" | "production". In production the app
    # fails closed at startup unless COPILOT_API_TOKEN is set (see validator below).
    copilot_env: str = "development"
    # Comma-separated browser origins allowed by CORS (the dev server is always
    # allowed). Set this to your deployed frontend origin in production.
    cors_origins: str = ""
    # Shared bearer token guarding the API. Empty = auth disabled (local dev).
    # Set it in production; the frontend sends it via VITE_API_TOKEN.
    copilot_api_token: str = ""
    # Filesystem root that /sources/* may point the repo/logs MCP servers at.
    # Empty = the project root, which keeps the bundled sample data working while
    # preventing the agent from being aimed at arbitrary host paths.
    copilot_sources_root: str = ""

    # --- Production safety limits (enforced by the API layer) ---
    # Per-client (IP) POST cap per minute; 0 disables rate limiting.
    copilot_rate_limit_per_min: int = 120
    # Max accepted request body size in bytes (guards against huge payloads).
    copilot_max_body_bytes: int = 1_000_000
    # Max characters accepted in a single chat message.
    copilot_max_message_chars: int = 16_000
    # Max concurrent in-memory sessions; the LRU-idle one is evicted past this.
    # Each live session holds one stdio subprocess per MCP server (3), so keep
    # this modest. 0 = unlimited.
    copilot_max_sessions: int = 50
    # Trust the X-Forwarded-For header for client IP (rate limiting). Only enable
    # when behind a trusted reverse proxy that sets it — otherwise clients can
    # spoof it to evade the limiter. Off by default = use the socket peer address.
    copilot_trust_proxy: bool = False
    # Fernet key for the per-tenant secret vault (app/secrets_vault.py). Generate:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty = an ephemeral key (encrypted secrets won't survive a restart).
    copilot_secret_key: str = ""

    # --- Observability ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "devops-copilot"
    # Optional Sentry error tracking (needs `pip install sentry-sdk`). Empty = off.
    sentry_dsn: str = ""
    # Datadog APM self-instrumentation of the copilot (needs `ddtrace` + a DD agent).
    # Honors DD_SERVICE / DD_ENV / DD_AGENT_HOST. Off by default.
    dd_trace_enabled: bool = False

    # --- Multi-tenancy (commercial; opt-in) ---
    # When true, the API requires a per-tenant API key (dcp_…) and resolves
    # per-tenant config/integrations/quotas. When false (default), the app runs
    # single-tenant exactly as the offline demo does — multi-tenancy is additive.
    copilot_multi_tenant: bool = False
    # Tenant store DB: SQLite path (default) or a postgres://… URL in production.
    copilot_tenant_db: str = "./copilot_tenants.sqlite"
    # Stripe metered billing (optional). With a key set, the usage ledger syncs to
    # Stripe meter events; otherwise billing is local-ledger-only.
    stripe_api_key: str = ""
    stripe_meter_event: str = "investigation"
    # Supabase Auth SSO (optional). When SUPABASE_JWKS_URL is set, a Supabase-issued
    # JWT (Bearer) authenticates to the multi-tenant API, mapped to the user's org/
    # role membership. Verification uses only the PUBLIC JWKS — no Supabase secret.
    supabase_jwks_url: str = ""
    supabase_jwt_aud: str = "authenticated"
    # Self-serve onboarding: when true (and multi-tenant is on), POST /signup lets a
    # new user create an org + owner API key without an existing credential. Always
    # provisions the free plan (upgrades go through billing). Disable in locked-down
    # deployments to require admin-side provisioning (CLI / /admin).
    copilot_signup_enabled: bool = True

    # --- Agent behavior ---
    # Max agent (LLM) calls per turn — bounds the agent<->tools loop.
    copilot_max_iterations: int = 8
    # Token budget per investigation (sum of all LLM calls in a turn). When the
    # running total crosses this, the agent is forced to conclude on its next step
    # — a hard cost kill-switch on top of the iteration cap. 0 = unlimited.
    copilot_max_tokens_per_run: int = 0
    copilot_checkpoint_db: str = "./copilot_checkpoints.sqlite"
    # Prompt caching (Anthropic): cache the stable system prompt + bound tool
    # schemas so the agent's many loop iterations re-read them at ~0.1x cost
    # instead of re-paying full input price each turn. No-op for other providers.
    copilot_prompt_cache: bool = True
    # Fix verification: after the RCA report is compiled, a verify node judges
    # whether a proposed fix actually addresses the root cause and — bounded by
    # copilot_verify_max_attempts — bounces the run back to the agent to revise a
    # fix that misses. When off, the run ends at the report node (prior behavior).
    copilot_verify_fix: bool = True
    # How many times the verify node may send the run back to the agent to revise a
    # fix that doesn't address the root cause. Bounds the loop; 0 disables the
    # bounce (verification still annotates the report). Default 1 (single revision).
    copilot_verify_max_attempts: int = 1
    # Sandbox counterfactual: when the agent attaches a machine-applicable `patch`
    # to its PR, apply it to a throwaway copy of the target repo and run a reproducer
    # to PROVE the fix resolves the incident. Executes the model's patch, so OFF by
    # default. See app/graph/sandbox.py.
    copilot_sandbox_verify: bool = False
    # Reproducer command the sandbox runs (in the temp repo copy) before and after
    # applying the patch — a FAIL→PASS transition proves the fix. Operator-set, never
    # model-supplied. Defaults to the bundled sample_repo's node reproducer.
    copilot_sandbox_cmd: str = "node checkout.test.js"
    # Wall-clock timeout (seconds) for each sandbox subprocess run.
    copilot_sandbox_timeout_s: int = 30
    # Confidence gate: refuse to AUTO-approve a consequential write that rests on
    # thin evidence (a human can still approve it explicitly). Protects programmatic
    # approvers — evals, bots, a future auto-remediation loop — from rubber-stamping
    # a high-risk action on a low-confidence investigation. true (default) | false.
    copilot_confidence_gate: bool = True
    # Continual learning: after a confidently-resolved investigation, append a
    # structured runbook record to the learned incident corpus so future runs
    # warm-start from it ("have we seen this before?" improves over time). Only
    # well-evidenced, non-abstained runs are recorded. true (default) | false.
    copilot_learn_incidents: bool = True
    # Where learned incidents accumulate (JSON). Kept separate from the bundled
    # demo corpus so the shipped fixture is never mutated. Defaults to project root.
    copilot_learned_corpus: str = ""
    # Adversarial RCA critique: after the report is compiled, a Prosecutor tries to
    # refute the root cause and a Defender rebuts using only observed evidence; a
    # deterministic judge downgrades/abstains when a serious objection stands. Cuts
    # confident-but-wrong RCAs. Two fast-model calls; true (default) | false.
    copilot_adversarial_critique: bool = True
    # Model routing: triage clearly-informational requests ("which services…?") onto
    # the cheap fast model in the agent loop, reserving the main reasoning model for
    # real incident investigation. Biased toward the main model on any doubt.
    copilot_model_routing: bool = True
    # Proactive SLO poller: periodically evaluate each service's error-budget burn
    # rate and auto-open an investigation when it's page-worthy — before a human is
    # paged. Spends tokens autonomously, so OFF by default. true | false (default).
    copilot_slo_poller: bool = False
    # How often the poller sweeps services (seconds) and the per-service cooldown
    # before it will re-open an investigation for a still-burning service.
    copilot_slo_poll_interval_s: int = 300
    copilot_slo_cooldown_s: int = 3600
    # Parallel multi-hypothesis probe: when the RCA has 2+ competing hypotheses,
    # score each concurrently against the observed evidence and re-rank them, so a
    # weakly-supported top hypothesis can't win by ordering alone. true (default).
    copilot_parallel_hypotheses: bool = True
    # Resilience: bounded retries with exponential backoff + a circuit breaker around
    # each LLM call, so a transient 429/5xx/timeout doesn't fail an investigation and
    # a dead provider is stopped being hammered. 1 disables retries.
    copilot_llm_retries: int = 3
    # Optional cross-provider failover: when the primary provider stays unavailable,
    # retry the call on this provider (one of anthropic|openai|gemini|groq|deepseek).
    # Empty = no failover. Needs the fallback provider's key configured.
    copilot_fallback_provider: str = ""
    # Progressive-autonomy remediation: apply a REVERSIBLE fix, watch the signal, and
    # auto-revert on regression. Mutates real infra, so DOUBLY gated: OFF by default,
    # and DRY-RUN by default even when enabled — an operator must set both to let it
    # act. Only reversible actions, only on a high-confidence investigation.
    copilot_autonomy: bool = False
    copilot_autonomy_dryrun: bool = True
    # How long to watch the signal after applying a remediation before judging recovery.
    copilot_autonomy_watch_s: int = 60

    @field_validator("copilot_provider")
    @classmethod
    def _normalize_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        allowed = {"anthropic", "openai", "gemini", "groq", "deepseek"}
        if v not in allowed:
            raise ValueError(f"COPILOT_PROVIDER must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("copilot_env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return "production" if (v or "").strip().lower().startswith("prod") else "development"

    @model_validator(mode="after")
    def _fail_closed_in_production(self) -> "Settings":
        # Refuse to start an unauthenticated API in production: a public, open
        # endpoint that drives an LLM is a cost + security liability.
        if self.copilot_env == "production" and not self.copilot_api_token.strip():
            raise ValueError(
                "COPILOT_API_TOKEN must be set when COPILOT_ENV=production "
                "(refusing to start an unauthenticated, internet-exposed API)."
            )
        # Multi-tenant stores per-tenant credentials encrypted at rest; in
        # production refuse to boot with an ephemeral vault key (it would lose
        # every tenant's secrets on restart and weaken isolation).
        if (
            self.copilot_multi_tenant
            and self.copilot_env == "production"
            and not self.copilot_secret_key.strip()
        ):
            raise ValueError(
                "COPILOT_SECRET_KEY must be set when COPILOT_MULTI_TENANT=true in "
                "production (per-tenant secrets are encrypted with it)."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.copilot_env == "production"

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
    def sources_root(self) -> Path:
        """The directory tree /sources/* is confined to (defaults to project root)."""
        base = self.copilot_sources_root.strip()
        return Path(base).expanduser().resolve() if base else ROOT.resolve()

    @property
    def offline_mode(self) -> bool:
        """True when no GitHub token is set — the GitHub MCP server runs against
        local demo fixtures instead of the real API."""
        return not self.github_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
