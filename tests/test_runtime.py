"""Runtime override store: per-provider keys, model resolution, snapshot/restore."""

from app import runtime


def test_set_model_records_provider_key_and_models():
    runtime.reset()
    runtime.set_model("openai", "sk-test", "gpt-4o", "gpt-4o-mini")
    assert runtime.provider() == "openai"
    assert runtime.provider_key("openai") == "sk-test"
    assert runtime.model_override() == "gpt-4o"
    assert runtime.fast_model_override() == "gpt-4o-mini"
    runtime.reset()


def test_keys_are_isolated_per_provider():
    runtime.reset()
    runtime.set_model("openai", "o-key", "", "")
    runtime.set_model("anthropic", "a-key", "", "")
    assert runtime.provider_key("openai") == "o-key"
    assert runtime.provider_key("anthropic") == "a-key"
    runtime.reset()


def test_snapshot_restore_roundtrip():
    runtime.reset()
    runtime.set_model("groq", "gk", "", "")
    snap = runtime.model_snapshot()
    runtime.set_model("anthropic", "ak", "", "")
    runtime.restore_model(snap)
    assert runtime.provider() == "groq"
    assert runtime.provider_key("groq") == "gk"
    runtime.reset()


def test_reset_clears_everything():
    runtime.set_model("deepseek", "dk", "deepseek-chat", "")
    runtime.reset()
    assert runtime.provider() == "anthropic"  # back to .env / default
    assert runtime.provider_key("deepseek") == ""
