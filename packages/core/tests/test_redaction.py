"""Redaction is a security control, so it gets adversarial tests, not happy-path ones."""

from __future__ import annotations

from mcp_platform_core.observability.redaction import (
    MAX_COLLECTION_ITEMS,
    MAX_STRING_LEN,
    REDACTED,
    fingerprint,
    redact,
    scrub_text,
)


def test_redacts_by_key_name_across_spellings() -> None:
    out = redact(
        {
            "github_token": "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "clientSecret": "abc",
            "X-API-Key": "abc",
            "password": "hunter2",
            "Authorization": "Bearer abc",
            "owner": "riteshpatani",
        }
    )
    assert out["github_token"] == REDACTED
    assert out["clientSecret"] == REDACTED
    assert out["X-API-Key"] == REDACTED
    assert out["password"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["owner"] == "riteshpatani"  # non-secrets survive intact


def test_safe_keys_are_not_redacted() -> None:
    assert redact({"max_tokens": 100, "token_count": 5})["max_tokens"] == 100


def test_fingerprint_survives_the_redactor() -> None:
    """Regression: `api_key_fp` matched the `api_key` needle and self-redacted,
    which silently removed caller correlation from every log line."""
    out = redact({"api_key_fp": fingerprint("premium-demo-key")})
    assert out["api_key_fp"].startswith("key_")


def test_scrubs_credential_shapes_from_free_text() -> None:
    # The real leak vector: an upstream error echoing the request URL back.
    text = "401 from https://api.example.com/v1?apikey=SUPERSECRET123&symbol=IBM"
    out = scrub_text(text)
    assert "SUPERSECRET123" not in out
    assert "?apikey=" in out  # which param carried it is still visible
    assert "symbol=IBM" in out


def test_scrubs_bare_tokens_with_no_key_to_match_on() -> None:
    for secret in (
        "github_pat_11EXAMPLE0000000000000_aaaaaaaaaaaaaaaaaaaa",
        "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123",
    ):
        assert secret not in scrub_text(f"failed with {secret} oops")


def test_redacts_nested_structures() -> None:
    out = redact({"outer": {"items": [{"api_key": "leak", "name": "ok"}]}})
    assert out["outer"]["items"][0]["api_key"] == REDACTED
    assert out["outer"]["items"][0]["name"] == "ok"


def test_bounds_depth_strings_and_collections() -> None:
    deep: dict = {"a": {}}
    node = deep["a"]
    for _ in range(20):
        node["a"] = {}
        node = node["a"]
    assert "max depth" in str(redact(deep))

    long = redact("x" * (MAX_STRING_LEN + 500))
    assert len(long) < MAX_STRING_LEN + 100
    assert "truncated" in long

    big = redact(list(range(MAX_COLLECTION_ITEMS + 25)))
    assert len(big) == MAX_COLLECTION_ITEMS + 1
    assert "more items" in big[-1]


def test_fingerprint_is_stable_non_reversible_and_handles_anonymous() -> None:
    secret = "premium-demo-key"
    fp = fingerprint(secret)
    assert fp == fingerprint(secret)
    assert secret not in fp
    assert fp.startswith("key_")
    assert fingerprint(None) == "anonymous"
    assert fingerprint(secret) != fingerprint("free-demo-key")
