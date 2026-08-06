#!/usr/bin/env python3
"""Central redaction helpers for manager findings and serialized output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "<redacted>"

_ENV_REFERENCE_RE = re.compile(r"^\{env:[A-Za-z_][A-Za-z0-9_]*\}$")
_EXPLICIT_ENV_REFERENCE_RE = re.compile(
    r"\{env:[A-Za-z_][A-Za-z0-9_]*\}"
)
_AUTH_ENV_REFERENCE_RE = re.compile(
    r"^(?:Bearer|Basic|Token)\s+"
    r"\{env:[A-Za-z_][A-Za-z0-9_]*\}$",
    re.IGNORECASE,
)
_URL_ENV_USERINFO_RE = re.compile(
    r"^\{env:[A-Za-z_][A-Za-z0-9_]*\}"
    r"(?::\{env:[A-Za-z_][A-Za-z0-9_]*\})?$"
)

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "consumer_secret",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "password",
        "passwd",
        "private_key",
        "proxy_authorization",
        "pwd",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
        "x_api_key",
    }
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"']+")
_SCHEME_RELATIVE_URL_RE = re.compile(
    r"(?i)(?<!:)//[a-z0-9.-]+(?::[0-9]+)?"
    r"(?:/[^\s?\"']*)?\?[^\s\"']+"
)
_ROOT_RELATIVE_URL_RE = re.compile(
    r"(?i)(?<![\w:/.-])/[^\s?\"']*\?[^\s\"']+"
)
_BARE_HOST_URL_RE = re.compile(
    r"(?i)(?<![@/])\b(?:localhost|(?:[a-z0-9-]+\.)+[a-z]{2,})"
    r"(?::[0-9]+)?(?:/[^\s?\"']*)?\?[^\s\"']+"
)
_HEADER_RE = re.compile(
    r"(?im)(?P<prefix>\b(?:authorization|proxy-authorization|cookie|set-cookie)"
    r"\s*[:=]\s*)(?P<value>[^\r\n]+)"
)
_KEY_VALUE_RE = re.compile(
    r"(?i)(?P<prefix>\b(?:access[ _-]?token|api[ _-]?key|apikey|auth|"
    r"authorization|proxy[ _-]?authorization|cookie|set[ _-]?cookie|"
    r"client[ _-]?secret|consumer[ _-]?secret|credential(?:s)?|id[ _-]?token|"
    r"password|passwd|private[ _-]?key|refresh[ _-]?token|secret|token|pwd)"
    r"\b[\"']?\s*[:=]\s*)"
    r"(?P<value>\{env:[A-Za-z_][A-Za-z0-9_]*\}|"
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}\]\r\n]+)"
)
_AUTH_SCHEME_RE = re.compile(
    r"(?i)(?P<prefix>\b(?:Bearer|Basic)\s+)(?P<value>[^\s,;}\]\r\n]+)"
)
_CLI_FLAG_RE = re.compile(
    r"(?i)(?P<prefix>--(?:access[ _-]?token|api[ _-]?key|apikey|auth|"
    r"authorization|cookie|client[ _-]?secret|credential(?:s)?|password|"
    r"private[ _-]?key|refresh[ _-]?token|secret|token)\s+)"
    r"(?P<value>\{env:[A-Za-z_][A-Za-z0-9_]*\}|"
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}\]\r\n]+)"
)
_COMMON_SECRET_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-(?:proj|svcacct)-[A-Za-z0-9_-]{20,}|"
    r"sk-(?=[A-Za-z0-9_-]{32,}(?:[^A-Za-z0-9_-]|$))"
    r"(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[0-9])"
    r"[A-Za-z0-9_-]{32,}|"
    r"gh[pousr]_[A-Za-z0-9]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}|"
    r"npm_[A-Za-z0-9]{16,}|"
    r"AKIA[0-9A-Z]{16}"
    r")(?![A-Za-z0-9_-])"
)


def _normalize_key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalize_key(value)
    if normalized in _SENSITIVE_KEYS:
        return True
    ordered_parts = tuple(part for part in normalized.split("_") if part)
    parts = frozenset(ordered_parts)
    if ordered_parts and ordered_parts[-1] in {
            "authorization",
            "cookie",
            "credential",
            "credentials",
            "passwd",
            "password",
            "pwd",
            "secret",
            "token",
    }:
        return True
    return bool(
        {"api", "key"}.issubset(parts)
        or {"private", "key"}.issubset(parts)
        or {"secret", "key"}.issubset(parts)
    )


def _is_environment_reference(value: str) -> bool:
    candidate = value.strip()
    return bool(
        _ENV_REFERENCE_RE.fullmatch(candidate)
        or _AUTH_ENV_REFERENCE_RE.fullmatch(candidate)
    )


def _redact_or_preserve_environment(value: str) -> str:
    if _is_environment_reference(value):
        return value
    references = list(dict.fromkeys(_EXPLICIT_ENV_REFERENCE_RE.findall(value)))
    return " ".join(references) if references else REDACTED


def _sanitize_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    trailing = ""
    while raw_url and raw_url[-1] in ".,;)]":
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]

    try:
        parsed = urlsplit(raw_url)
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, host = netloc.rsplit("@", 1)
            sanitized_userinfo = (
                userinfo if _URL_ENV_USERINFO_RE.fullmatch(userinfo) else REDACTED
            )
            netloc = f"{sanitized_userinfo}@{host}"

        query = urlencode(
            [
                (
                    key,
                    _redact_or_preserve_environment(value),
                )
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
            safe="{}:$<>",
        )
        fragment = (
            _redact_or_preserve_environment(parsed.fragment)
            if parsed.fragment
            else ""
        )
        sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except ValueError:
        return REDACTED + trailing
    return sanitized + trailing


def _sanitize_header(match: re.Match[str]) -> str:
    value = match.group("value").strip()
    return match.group("prefix") + _redact_or_preserve_environment(value)


def _sanitize_key_value(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value[:1] in {"\"", "'"} and value[-1:] == value[:1] else ""
    unquoted = value[1:-1] if quote else value
    sanitized = _redact_or_preserve_environment(unquoted)
    return f"{match.group('prefix')}{quote}{sanitized}{quote}"


def _sanitize_auth_scheme(match: re.Match[str]) -> str:
    value = match.group("value")
    if _is_environment_reference(f"{match.group('prefix').strip()} {value}"):
        return match.group(0)
    return match.group("prefix") + REDACTED


def sanitize_text(value: str) -> str:
    """Redact secrets from free-form text while retaining environment references."""

    sanitized = _URL_RE.sub(_sanitize_url, value)
    sanitized = _SCHEME_RELATIVE_URL_RE.sub(_sanitize_url, sanitized)
    sanitized = _ROOT_RELATIVE_URL_RE.sub(_sanitize_url, sanitized)
    sanitized = _BARE_HOST_URL_RE.sub(_sanitize_url, sanitized)
    sanitized = _HEADER_RE.sub(_sanitize_header, sanitized)
    sanitized = _CLI_FLAG_RE.sub(_sanitize_key_value, sanitized)
    sanitized = _KEY_VALUE_RE.sub(_sanitize_key_value, sanitized)
    sanitized = _AUTH_SCHEME_RE.sub(_sanitize_auth_scheme, sanitized)
    return _COMMON_SECRET_TOKEN_RE.sub(REDACTED, sanitized)


def _is_sensitive_cli_flag(value: str) -> bool:
    if not value.startswith("--") or "=" in value:
        return False
    return _is_sensitive_key(value[2:])


def _sanitize_sequence(value: list[Any] | tuple[Any, ...]) -> list[Any] | tuple[Any, ...]:
    sanitized: list[Any] = []
    redact_next = False
    for item in value:
        if redact_next:
            sanitized.append(_sanitize_sensitive_value(item))
            redact_next = False
            continue
        sanitized.append(sanitize_value(item))
        if isinstance(item, str) and _is_sensitive_cli_flag(item):
            redact_next = True
    return tuple(sanitized) if isinstance(value, tuple) else sanitized


def _sanitized_mapping(
    value: Mapping[Any, Any],
    *,
    sensitive_values: bool,
) -> dict[Any, Any]:
    sanitized: dict[Any, Any] = {}
    for key, item in value.items():
        safe_key = sanitize_text(key) if isinstance(key, str) else key
        if safe_key in sanitized:
            base = str(safe_key)
            index = 2
            candidate = f"{base}#{index}"
            while candidate in sanitized:
                index += 1
                candidate = f"{base}#{index}"
            safe_key = candidate
        if sensitive_values or _is_sensitive_key(str(key)):
            sanitized[safe_key] = _sanitize_sensitive_value(item)
        else:
            sanitized[safe_key] = sanitize_value(item)
    return sanitized


def _sanitize_sensitive_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sanitized_mapping(value, sensitive_values=True)
    if isinstance(value, list):
        return [_sanitize_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_sensitive_value(item) for item in value)
    if isinstance(value, str):
        return _redact_or_preserve_environment(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return REDACTED
    return value


def sanitize_value(value: Any) -> Any:
    """Return a recursively redacted copy of dict, list, tuple, or scalar output."""

    if isinstance(value, Mapping):
        return _sanitized_mapping(value, sensitive_values=False)
    if isinstance(value, list):
        return _sanitize_sequence(value)
    if isinstance(value, tuple):
        return _sanitize_sequence(value)
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_mapping(value: Mapping[Any, Any]) -> dict[Any, Any]:
    """Return a redacted dictionary without mutating the supplied mapping."""

    return _sanitized_mapping(value, sensitive_values=False)


def sanitize_exception(error: BaseException) -> str:
    """Render an exception without exposing secret-bearing values."""

    return sanitize_text(str(error))


def json_dumps(value: Any, *, indent: int | None = None) -> str:
    """Serialize one recursively sanitized JSON value."""

    return json.dumps(
        sanitize_value(value),
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
    )
