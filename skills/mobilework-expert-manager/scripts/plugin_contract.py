#!/usr/bin/env python3
"""Strict registry-only npm Plugin spec parsing for expert packages."""

from __future__ import annotations

import re
from typing import TypedDict


ERROR_CODE = "PLUGIN_NPM_SPEC_INVALID"
UNPINNED_CODE = "PLUGIN_NPM_SPEC_UNPINNED"
DUPLICATE_CODE = "PLUGIN_NPM_SPEC_DUPLICATE"
CATEGORY_CLEAN = "clean"
CATEGORY_UNPINNED_LEGACY = "unpinned-legacy"

_PACKAGE_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._~-]*$")
_EXACT_VERSION_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_VERSIONISH_RE = re.compile(
    r"^(?:0|[1-9][0-9]*|[xX*])"
    r"(?:\.(?:0|[1-9][0-9]*|[xX*])){0,2}"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_COMPARATOR_RE = re.compile(r"^(?:\^|~|<=|>=|<|>|=)?(.+)$")
_DIST_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCP_RE = re.compile(r"^(?:[^/@:\s]+@)?[^/:\s]+:.+")
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")


class NpmPluginSpec(TypedDict):
    name: str
    selector: str
    canonicalKey: str
    isPinned: bool
    category: str
    normalized: str


class PluginContractError(ValueError):
    """Raised when an npm Plugin spec escapes the registry-only contract."""

    code = ERROR_CODE

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reject(reason: str) -> None:
    raise PluginContractError(reason)


def _check_percent_encoding(spec: str) -> None:
    if "%" not in spec:
        return
    position = 0
    while True:
        position = spec.find("%", position)
        if position < 0:
            break
        if _PERCENT_ESCAPE_RE.match(spec, position) is None:
            _reject("npm Plugin spec contains invalid percent encoding")
        position += 3
    _reject("npm Plugin registry names and selectors must not be percent-encoded")


def _split_spec(spec: str) -> tuple[str, str | None]:
    if spec.startswith("@"):
        slash = spec.find("/")
        if slash <= 1:
            _reject("scoped npm Plugin spec must use @scope/package")
        selector_marker = spec.find("@", slash + 1)
        if selector_marker < 0:
            return spec, None
        return spec[:selector_marker], spec[selector_marker + 1 :]
    package, marker, selector = spec.partition("@")
    return package, selector if marker else None


def _validate_package_name(name: str) -> None:
    if len(name) > 214:
        _reject("npm Plugin package name exceeds 214 characters")
    if name.startswith("@"):
        scope, separator, package = name[1:].partition("/")
        if not separator or not scope or not package or "/" in package:
            _reject("scoped npm Plugin spec must use @scope/package")
        if not _PACKAGE_PART_RE.fullmatch(scope) or not _PACKAGE_PART_RE.fullmatch(package):
            _reject("scoped npm Plugin package name is invalid")
        return
    if "/" in name:
        _reject("unscoped npm Plugin package name must not contain a slash")
    if not _PACKAGE_PART_RE.fullmatch(name):
        _reject("npm Plugin package name is invalid")


def _valid_versionish(value: str) -> bool:
    if not _VERSIONISH_RE.fullmatch(value):
        return False
    if _has_numeric_prerelease_leading_zero(value):
        return False
    core = value.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    wildcard_seen = False
    for part in parts:
        wildcard = part in {"x", "X", "*"}
        if wildcard_seen and not wildcard:
            return False
        wildcard_seen = wildcard_seen or wildcard
    return True


def _has_numeric_prerelease_leading_zero(value: str) -> bool:
    version_without_build = value.split("+", 1)[0]
    _core, separator, prerelease = version_without_build.partition("-")
    if not separator:
        return False
    return any(
        len(identifier) > 1
        and identifier.startswith("0")
        and re.fullmatch(r"[0-9]+", identifier) is not None
        for identifier in prerelease.split(".")
    )


def _is_exact_version(value: str) -> bool:
    if _EXACT_VERSION_RE.fullmatch(value) is None:
        return False
    if _has_numeric_prerelease_leading_zero(value):
        _reject(
            "npm Plugin exact version has a numeric prerelease "
            "identifier with a leading zero"
        )
    return True


def _valid_range_clause(clause: str) -> bool:
    hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", clause)
    if hyphen is not None:
        return _valid_versionish(hyphen.group(1)) and _valid_versionish(hyphen.group(2))
    tokens = clause.split()
    if not tokens:
        return False
    for token in tokens:
        match = _COMPARATOR_RE.fullmatch(token)
        if match is None or not _valid_versionish(match.group(1)):
            return False
    return True


def _normalize_range(selector: str) -> str | None:
    clauses = selector.split("||")
    if any(not clause.strip() for clause in clauses):
        return None
    normalized: list[str] = []
    for clause in clauses:
        compact = " ".join(clause.strip().split())
        if not _valid_range_clause(compact):
            return None
        normalized.append(compact)
    return " || ".join(normalized)


def _looks_like_range(selector: str) -> bool:
    return bool(selector) and (
        selector[0].isdigit()
        or selector[0] in "xX*~^<>="
        or any(char.isspace() for char in selector)
        or "||" in selector
    )


def parse_npm_plugin_spec(value: str) -> NpmPluginSpec:
    """Parse a safe npm registry Plugin spec without resolving or installing it."""

    if not isinstance(value, str) or not value:
        _reject("npm Plugin spec must be a non-empty string")
    if value != value.strip():
        _reject("npm Plugin spec must not contain surrounding whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _reject("npm Plugin spec must not contain control characters")
    if "?" in value or "#" in value:
        _reject("npm Plugin spec must not contain a query or fragment")
    _check_percent_encoding(value)

    lowered = value.lower()
    if lowered.startswith("npm:") or "@npm:" in lowered:
        _reject("npm aliases are not allowed for expert Plugins")
    if _SCHEME_RE.match(value) or lowered.startswith(("git+", "git@")):
        _reject("npm Plugin spec must use the npm registry, not a URL or protocol")
    if value.startswith((".", "/", "~", "\\")) or _WINDOWS_PATH_RE.match(value):
        _reject("npm Plugin spec must not be a local path")
    if "\\" in value or value.startswith("//"):
        _reject("npm Plugin spec must not be a local or UNC path")
    if _SCP_RE.match(value):
        _reject("npm Plugin spec must not use Git or SCP syntax")
    if re.match(r"^[^/@:\s]+:[^/@\s]+@", value):
        _reject("npm Plugin spec must not embed credentials")

    name, raw_selector = _split_spec(value)
    _validate_package_name(name)
    if raw_selector is not None and (not raw_selector or "@" in raw_selector):
        _reject("npm Plugin selector is invalid")

    if raw_selector is None:
        selector = "latest"
        category = CATEGORY_UNPINNED_LEGACY
        normalized = name
    elif _is_exact_version(raw_selector):
        selector = raw_selector
        category = CATEGORY_CLEAN
        normalized = f"{name}@{selector}"
    else:
        normalized_range = _normalize_range(raw_selector)
        if normalized_range is not None:
            selector = normalized_range
        elif _looks_like_range(raw_selector):
            _reject("npm Plugin semver range is invalid")
        elif _DIST_TAG_RE.fullmatch(raw_selector):
            selector = raw_selector
        else:
            _reject("npm Plugin selector must be an exact version, semver range, or dist-tag")
        category = CATEGORY_UNPINNED_LEGACY
        normalized = f"{name}@{selector}"

    return {
        "name": name,
        "selector": selector,
        "canonicalKey": f"{name}@{selector}",
        "isPinned": category == CATEGORY_CLEAN,
        "category": category,
        "normalized": normalized,
    }
