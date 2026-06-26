"""PII / secret redaction of ingested telemetry."""

from app import redaction


def test_redacts_email_ip_and_tokens_consistently():
    text = (
        "user alice@acme.com from 10.0.1.23 used ghp_" + "a" * 30 + " and again alice@acme.com"
    )
    clean, entities = redaction.scrub(text)
    assert "alice@acme.com" not in clean
    assert "10.0.1.23" not in clean
    assert "<EMAIL_1>" in clean
    assert "<IP_1>" in clean
    assert "<GITHUB_TOKEN_1>" in clean
    # same email -> same token (consistent), so the agent can still correlate
    assert clean.count("<EMAIL_1>") == 2
    types = {e["type"] for e in entities}
    assert {"EMAIL", "IP", "GITHUB_TOKEN"} <= types
    # entities never carry the raw value
    assert all("token" in e and "@" not in e["token"] for e in entities)


def test_redacts_jwt_and_ssn():
    jwt = "eyJhbGc.eyJzdWIiOiIxIn0.sig123"
    clean, _ = redaction.scrub(f"token {jwt} ssn 123-45-6789")
    assert "<JWT_1>" in clean
    assert "<SSN_1>" in clean
    assert "123-45-6789" not in clean


def test_card_redacted_only_when_luhn_valid():
    valid = "4242 4242 4242 4242"  # passes Luhn
    clean, _ = redaction.scrub(f"card {valid}")
    assert valid not in clean and "<CARD_1>" in clean
    # a long non-card numeric id (fails Luhn) is left alone
    clean2, _ = redaction.scrub("order 1234567890123456 shipped")
    assert "1234567890123456" in clean2


def test_clean_text_unchanged():
    text = "checkout-svc returned 500 at applyDiscount (checkout.js:42)"
    clean, entities = redaction.scrub(text)
    assert clean == text
    assert entities == []
