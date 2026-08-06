"""Redaction for anything that reaches a log line.

Debug logging of tool arguments and responses (CLAUDE.md §6: "never logged")
is only safe if every value passes through here first. Two independent layers,
because either alone leaks:

1. **Key-name matching** — ``{"github_token": "ghp_x"}`` is redacted because of
   the *key*, whatever the value looks like.
2. **Value-pattern matching** — a credential embedded in free text
   (``"401 calling https://api?apikey=abc"``) has no key to match on, so
   token-shaped substrings are scrubbed from every string we emit.

Structures are walked recursively with depth/size caps: logs are a
denial-of-service surface as much as a disclosure one, and an upstream can
return an arbitrarily large body.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Final

REDACTED: Final = "***REDACTED***"

MAX_DEPTH: Final = 6
MAX_STRING_LEN: Final = 512
MAX_COLLECTION_ITEMS: Final = 50

# Substring match: `github_token`, `x-api-key`, and `clientSecret` should all hit
# without enumerating every provider's spelling.
_SECRET_KEY_PARTS: Final = frozenset(
    {
        "apikey",
        "api_key",
        "authorization",
        "auth",
        "bearer",
        "certificate",
        "credential",
        "cookie",
        "passwd",
        "password",
        "private_key",
        "secret",
        "session",
        "signature",
        "token",
    }
)

# Names that contain a secret-ish substring but are never secrets. Without this,
# `max_tokens` and friends redact into uselessness.
_SAFE_KEYS: Final = frozenset(
    {
        "max_tokens",
        "n_tokens",
        "token_count",
        "tokens",
        "tokens_used",
        "authenticated",
        "auth_type",
        "session_count",
        # Deliberately-safe derivations. `fingerprint()` output is a one-way
        # hash whose whole purpose is to be logged; redacting it by name would
        # make caller correlation impossible.
        "api_key_fp",
        "key_fingerprint",
    }
)

# Credential shapes that must be scrubbed even when they appear mid-sentence.
_VALUE_PATTERNS: Final = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"),
    # Credentials smuggled through query strings — the common accidental leak,
    # since upstream errors love to echo the full request URL back at you.
    re.compile(r"(?i)([?&](?:api_?key|access_token|token|key|secret|password|sig)=)[^&\s\"']+"),
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_KEYS:
        return False
    # Match against both the raw name and a separator-stripped form, so
    # `x-api-key`, `X_API_KEY`, and `apiKey` all reduce to the same needle.
    squashed = re.sub(r"[^a-z0-9]", "", lowered)
    return any(part in lowered or part.replace("_", "") in squashed for part in _SECRET_KEY_PARTS)


def scrub_text(value: str) -> str:
    """Strip credential-shaped substrings out of free text, then bound its length."""
    for pattern in _VALUE_PATTERNS:
        # Patterns with a capture group keep the `?apikey=` prefix so the log still
        # shows *which* parameter was carrying a secret.
        value = pattern.sub(
            (lambda m: m.group(1) + REDACTED) if pattern.groups else REDACTED,
            value,
        )
    if len(value) > MAX_STRING_LEN:
        value = value[:MAX_STRING_LEN] + f"…[truncated {len(value) - MAX_STRING_LEN} chars]"
    return value


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets and bound the size of an arbitrary value.

    Safe to call on tool arguments, upstream responses, and exception messages.
    """
    if _depth >= MAX_DEPTH:
        return "…[max depth]"

    if isinstance(value, str):
        return scrub_text(value)

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                out["…"] = f"[{len(value) - MAX_COLLECTION_ITEMS} more keys]"
                break
            name = str(key)
            out[name] = REDACTED if _is_secret_key(name) else redact(item, _depth=_depth + 1)
        return out

    if isinstance(value, list | tuple | set):
        items = list(value)
        out_list = [redact(item, _depth=_depth + 1) for item in items[:MAX_COLLECTION_ITEMS]]
        if len(items) > MAX_COLLECTION_ITEMS:
            out_list.append(f"…[{len(items) - MAX_COLLECTION_ITEMS} more items]")
        return out_list

    if isinstance(value, bool | int | float) or value is None:
        return value

    return scrub_text(str(value))


def fingerprint(secret: str | None) -> str:
    """Stable, non-reversible id for a credential.

    Lets you correlate "which key made these calls" across log lines without the
    key itself ever reaching disk.
    """
    if not secret:
        return "anonymous"
    return "key_" + hashlib.sha256(secret.encode()).hexdigest()[:12]
