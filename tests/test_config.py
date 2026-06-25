"""Settings validators: provider/env normalization and production fail-closed."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_provider_normalized_and_validated():
    assert Settings(copilot_provider="ANTHROPIC").copilot_provider == "anthropic"
    with pytest.raises(ValidationError):
        Settings(copilot_provider="bogus")


def test_env_normalization():
    # A production env requires a token (fail-closed), so supply one here.
    assert Settings(copilot_env="PROD", copilot_api_token="x").is_production is True
    assert Settings(copilot_env="production", copilot_api_token="x").copilot_env == "production"
    assert Settings(copilot_env="").is_production is False
    assert Settings(copilot_env="development").is_production is False


def test_production_requires_api_token():
    # Fail closed: no unauthenticated API in production.
    with pytest.raises(ValidationError):
        Settings(copilot_env="production", copilot_api_token="")
    s = Settings(copilot_env="production", copilot_api_token="secret")
    assert s.is_production and s.copilot_api_token == "secret"


def test_safety_limit_defaults():
    s = Settings(copilot_env="development")
    assert s.copilot_rate_limit_per_min > 0
    assert s.copilot_max_message_chars > 0
    assert s.copilot_max_body_bytes > 0
    assert s.copilot_max_sessions >= 0
    assert s.copilot_trust_proxy is False
