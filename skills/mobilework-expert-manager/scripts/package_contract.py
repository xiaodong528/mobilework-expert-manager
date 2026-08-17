#!/usr/bin/env python3
"""Shared package contract helpers for MobileWork expert packages."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse


PACKAGE_RUNTIME_DIR = ".opencode"
WORKSPACE_RUNTIME_DIR = ".opencode"
WORKSPACE_CONFIG = "opencode.jsonc"
INSTALL_RECEIPT_DIR = ".expert-installs"
OPENCODE_SCHEMA = "https://opencode.ai/config.json"
OPENCODE_PACKAGE_ROOT_KEYS = frozenset(
    {"$schema", "agent", "mcp", "plugin", "references", "instructions", "lsp"}
)
CUSTOM_TOOL_ENTRY_KEYS = frozenset({"path", "content", "purpose"})
AGENT_STEP_KEYS = ("steps", "max_turns", "maxTurns")
AGENT_OPTIONAL_RUNTIME_KEYS = (
    "model",
    "variant",
    "hidden",
    "options",
)
LEGACY_AGENT_SAMPLING_KEYS = ("temperature", "top_p")
AGENT_READ_RUNTIME_KEYS = (
    *AGENT_OPTIONAL_RUNTIME_KEYS,
    *LEGACY_AGENT_SAMPLING_KEYS,
)
AGENT_MANIFEST_KEYS = frozenset(
    {
        "id",
        "mode",
        # MobileWork role contract; projected only through generated permission.
        "autonomy",
        "name",
        # MobileWork legacy input fallback only; never projected as an Agent key.
        "title",
        "display_name",
        "profession",
        "description",
        "avatar_url",
        "color",
        *AGENT_STEP_KEYS,
        *AGENT_READ_RUNTIME_KEYS,
        "responsibilities",
        "workflow",
        "quality_gates",
        "route_triggers",
        "handoff_contract",
        "skills",
        "mcp",
        "permission",
        "permission_reason",
        "custom_tools",
        "references",
        "instructions",
        "tools",
    }
)
FORBIDDEN_AGENT_MANIFEST_KEYS = frozenset({"prompt", "disable", "maxSteps"})
OPENCODE_PACKAGE_AGENT_KEYS = frozenset(
    {
        "mode",
        "description",
        "steps",
        *AGENT_READ_RUNTIME_KEYS,
        "permission",
    }
)
AGENT_MARKDOWN_KEYS = frozenset(
    {
        "name",
        "description",
        "displayName",
        "profession",
        "mode",
        "color",
        "avatar_url",
        "steps",
        *AGENT_READ_RUNTIME_KEYS,
        "permission",
    }
)

AGENTS_SUBDIR = "agents"
SKILLS_SUBDIR = "skills"
COMMANDS_SUBDIR = "commands"
TOOLS_SUBDIR = "tools"
PLUGINS_SUBDIR = "plugins"
REFERENCES_SUBDIR = "references"
INSTRUCTIONS_SUBDIR = "instructions"

ROOT_FILES = {"expert.json", "opencode.json", "README.md", ".env.example", ".gitignore"}
ROOT_DIRS = {"avatars", PACKAGE_RUNTIME_DIR}
RUNTIME_FILES = {"package.json"}
RUNTIME_DIRS = {
    AGENTS_SUBDIR,
    SKILLS_SUBDIR,
    COMMANDS_SUBDIR,
    TOOLS_SUBDIR,
    PLUGINS_SUBDIR,
    REFERENCES_SUBDIR,
    INSTRUCTIONS_SUBDIR,
}

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENV_REFERENCE_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
GLOB_CHARS = frozenset("*?[")
AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MCP_BASE_KEYS = frozenset({"name", "type", "enabled", "timeout"})
MCP_LOCAL_KEYS = MCP_BASE_KEYS | frozenset({"command", "environment"})
MCP_REMOTE_KEYS = MCP_BASE_KEYS | frozenset({"url", "headers", "oauth"})
MCP_OAUTH_KEYS = frozenset(
    {"clientId", "clientSecret", "scope", "callbackPort", "redirectUri"}
)
REFERENCE_ENTRY_KEYS = frozenset(
    {"path", "repository", "branch", "description", "hidden"}
)
ROLE_INSTRUCTION_ENTRY_KEYS = frozenset({"path", "description"})
EMBEDDED_GIT_CREDENTIAL_RE = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{16,}|glpat-[A-Za-z0-9_-]{10,}|"
    r"x-access-token|oauth2(?=[:@]))"
)
SECRET_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "credential",
        "key",
        "oauth_token",
        "password",
        "private_token",
        "secret",
        "token",
    }
)
GIT_REPOSITORY_SCHEMES = frozenset({"git", "http", "https", "ssh"})
GIT_SCP_RE = re.compile(
    r"^(?:(?P<user>[A-Za-z0-9._-]+)@)?"
    r"(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\\\s?#]+)$"
)
LSP_SERVER_KEYS = frozenset(
    {"disabled", "command", "extensions", "env", "initialization"}
)
PACKAGE_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies")


class ContractError(ValueError):
    """Raised when a package violates the shared contract."""


def first_duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def normalize_provider_model(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or any(char.isspace() for char in value)
    ):
        raise ContractError(f"{field}: must be a non-empty provider/model string")
    provider, separator, model_id = value.partition("/")
    if not separator or not provider or not model_id:
        raise ContractError(f"{field}: must be a non-empty provider/model string")
    return value


def _validate_json_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{field}: numbers must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ContractError(f"{field}: object keys must be non-empty strings")
            _validate_json_value(item, f"{field}.{key}")
        return
    raise ContractError(f"{field}: must contain only JSON values")


@lru_cache(maxsize=1)
def expert_runtime_projection_policy() -> dict[str, Any]:
    """Read the strict Agent/command projection policy from its machine SSOT."""

    import manager_contract

    return manager_contract.load_policy()["expertRuntimeProjection"]


def agent_sampling_option_paths(
    value: Any,
    field: str,
) -> list[str]:
    """Return nested options paths that try to reintroduce sampling controls."""

    forbidden = set(
        expert_runtime_projection_policy()["agent"]["forbiddenSamplingFields"]
    )
    paths: list[str] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(agent_sampling_option_paths(item, f"{field}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            path = f"{field}.{key}"
            if key in forbidden:
                paths.append(path)
            paths.extend(agent_sampling_option_paths(item, path))
    return paths


def normalize_agent_runtime_options(
    role: dict[str, Any],
    field: str,
    *,
    expected_mode: str,
    default_steps: int,
    allow_legacy_sampling: bool = False,
) -> dict[str, Any]:
    """Validate and normalize package-owned OpenCode Agent runtime options."""

    agent_policy = expert_runtime_projection_policy()["agent"]
    step_keys = (
        agent_policy["canonicalStepField"],
        *agent_policy["legacyStepInputFields"],
    )
    sampling_keys = tuple(agent_policy["forbiddenSamplingFields"])
    forbidden = sorted(
        set(role)
        & (
            set(FORBIDDEN_AGENT_MANIFEST_KEYS)
            | set(agent_policy["deprecatedStepFields"])
        )
    )
    if forbidden:
        raise ContractError(
            f"{field}: unsupported Agent fields {', '.join(forbidden)}; "
            "MobileWork owns prompt and enablement through generated package content"
        )
    sampling_fields = sorted(set(role) & set(sampling_keys))
    if sampling_fields and not allow_legacy_sampling:
        raise ContractError(
            f"{field}: unsupported Agent fields {', '.join(sampling_fields)}; "
            "MobileWork expert packages inherit sampling behavior from the model/provider"
        )
    unexpected = sorted(set(role) - AGENT_MANIFEST_KEYS)
    if unexpected:
        raise ContractError(
            f"{field}: unknown Agent fields {', '.join(unexpected)}; "
            "put provider-specific parameters under options"
        )

    declared_steps: list[tuple[str, int]] = []
    for key in step_keys:
        if key not in role:
            continue
        value = role[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{field}.{key}: must be a positive integer")
        declared_steps.append((key, value))
    if declared_steps and len({value for _, value in declared_steps}) != 1:
        rendered = ", ".join(f"{key}={value}" for key, value in declared_steps)
        raise ContractError(f"{field}: conflicting step aliases: {rendered}")

    result: dict[str, Any] = {
        "steps": declared_steps[0][1] if declared_steps else default_steps,
    }
    if "model" in role:
        result["model"] = normalize_provider_model(role["model"], f"{field}.model")

    if "variant" in role:
        variant = role["variant"]
        if not isinstance(variant, str) or not variant.strip() or variant.strip() != variant:
            raise ContractError(f"{field}.variant: must be a non-empty string")
        if "model" not in result:
            raise ContractError(f"{field}.variant: requires model")
        result["variant"] = variant

    for key in sampling_keys:
        if key not in role:
            continue
        value = role[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractError(f"{field}.{key}: must be a finite number from 0.0 to 1.0")
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError(f"{field}.{key}: must be a finite number from 0.0 to 1.0")
        if value < 0 or value > 1:
            raise ContractError(f"{field}.{key}: must be from 0.0 to 1.0")
        result[key] = value

    if "hidden" in role:
        hidden = role["hidden"]
        if expected_mode != "subagent":
            raise ContractError(f"{field}.hidden: is only allowed for subagents")
        if not isinstance(hidden, bool):
            raise ContractError(f"{field}.hidden: must be a boolean")
        result["hidden"] = hidden

    if "options" in role:
        options = role["options"]
        if not isinstance(options, dict) or not options:
            raise ContractError(f"{field}.options: must be a non-empty JSON object")
        _validate_json_value(options, f"{field}.options")
        sampling_paths = agent_sampling_option_paths(options, f"{field}.options")
        if sampling_paths and not allow_legacy_sampling:
            raise ContractError(
                f"{field}.options: sampling fields are unsupported at "
                + ", ".join(sampling_paths)
                + "; MobileWork expert packages inherit sampling behavior from the model/provider"
            )
        result["options"] = options
    return result


def _string_mapping(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and bool(key.strip())
        and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ContractError(f"{field} must map strings to strings with non-empty keys")
    return dict(value)


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractError(f"{field}: must be a non-empty string without surrounding whitespace")
    return value


def _http_url(value: Any, field: str) -> str:
    url = _non_empty_string(value, field)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{field}: must use http:// or https://")
    return url


def repository_contains_credentials(value: str) -> bool:
    """Return whether a Git reference embeds credentials instead of using host auth."""

    if "{env:" in value or EMBEDDED_GIT_CREDENTIAL_RE.search(value):
        return True
    parsed = urlparse(value)
    try:
        password = parsed.password
        username = parsed.username
    except ValueError:
        return True
    if password is not None:
        return True
    if parsed.scheme.lower() in {"http", "https"} and username is not None:
        return True
    return any(key.lower() in SECRET_QUERY_KEYS for key, _item in parse_qsl(parsed.query))


def normalize_git_repository(value: Any, field: str) -> str:
    """Accept remote Git locations while rejecting credentials and local paths."""

    repository = _non_empty_string(value, field)
    if repository_contains_credentials(repository):
        raise ContractError(
            f"{field}: must not embed credentials; "
            "use the host Git credential or SSH configuration"
        )
    if "\\" in repository or repository.startswith(("/", "~/", "./", "../")):
        raise ContractError(f"{field}: must be a remote Git URL, host/path, or owner/repo")
    if re.match(r"^[A-Za-z]:", repository):
        raise ContractError(f"{field}: must not use a local filesystem path")

    scp_match = None if "://" in repository else GIT_SCP_RE.fullmatch(repository)
    if scp_match:
        user = scp_match.group("user")
        host = scp_match.group("host")
        remote_path = scp_match.group("path")
        if remote_path.startswith(":") or (user is None and "." not in host):
            raise ContractError(
                f"{field}: ambiguous SCP-like location; use user@host:path, "
                "a qualified host, or host/path"
            )
        parts = PurePosixPath(remote_path).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ContractError(f"{field}: remote Git path contains a dot or traversal segment")
        return repository

    parsed = urlparse(repository)
    if parsed.scheme:
        if parsed.scheme.lower() not in GIT_REPOSITORY_SCHEMES:
            raise ContractError(
                f"{field}: unsupported Git URL scheme {parsed.scheme}; "
                "use git, http, https, or ssh"
            )
        try:
            hostname = parsed.hostname
            password = parsed.password
            username = parsed.username
            parsed.port
        except ValueError as exc:
            raise ContractError(f"{field}: invalid remote Git URL: {exc}") from exc
        if not hostname or not parsed.path.strip("/"):
            raise ContractError(f"{field}: remote Git URL must include a host and repository path")
        if parsed.query or parsed.fragment:
            raise ContractError(f"{field}: query strings and fragments are not allowed")
        if password is not None:
            raise ContractError(
                f"{field}: must not embed credentials; "
                "use the host Git credential or SSH configuration"
            )
        if parsed.scheme.lower() in {"http", "https"} and username is not None:
            raise ContractError(
                f"{field}: must not embed credentials; "
                "use the host Git credential or SSH configuration"
            )
        path_parts = PurePosixPath(parsed.path.lstrip("/")).parts
        if not path_parts or any(part in {"", ".", ".."} for part in path_parts):
            raise ContractError(f"{field}: remote Git path contains a dot or traversal segment")
        return repository

    if any(char in repository for char in "?#@:"):
        raise ContractError(f"{field}: must be a remote Git URL, host/path, or owner/repo")
    parts = PurePosixPath(repository).parts
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise ContractError(f"{field}: must be a remote Git host/path or owner/repo")
    if not all(re.fullmatch(r"[A-Za-z0-9._~-]+", part) for part in parts):
        raise ContractError(f"{field}: remote Git path contains unsupported characters")
    return repository


def normalize_mcp_servers(value: Any) -> dict[str, dict[str, Any]]:
    """Validate and normalize the package-owned OpenCode MCP subset."""

    servers = [] if value is None else value
    if not isinstance(servers, list):
        raise ContractError("mcp_servers: must be a list")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(servers):
        field = f"mcp_servers[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{field}: must be a mapping")
        name = item.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ContractError(f"{field}.name: must be lowercase-hyphen")
        if name in result:
            raise ContractError(f"{field}.name: duplicates {name}")
        server_type = item.get("type", "local")
        if server_type not in {"local", "remote"}:
            raise ContractError(f"{field}.type: must be local or remote")
        allowed = MCP_LOCAL_KEYS if server_type == "local" else MCP_REMOTE_KEYS
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise ContractError(f"{field}: unsupported fields {', '.join(unknown)}")

        enabled = item.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ContractError(f"{field}.enabled: must be a boolean")
        entry: dict[str, Any] = {"type": server_type, "enabled": enabled}

        if "timeout" in item:
            timeout = item["timeout"]
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout < 1
                or timeout > MAX_SAFE_INTEGER
            ):
                raise ContractError(f"{field}.timeout: must be a positive integer")
            entry["timeout"] = timeout

        if server_type == "local":
            command = item.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and bool(part.strip()) for part in command)
            ):
                raise ContractError(f"{field}.command: must be a non-empty list of non-empty strings")
            entry["command"] = list(command)
            if "environment" in item:
                entry["environment"] = _string_mapping(item["environment"], f"{field}.environment")
        else:
            entry["url"] = _http_url(item.get("url"), f"{field}.url")
            if "headers" in item:
                entry["headers"] = _string_mapping(item["headers"], f"{field}.headers")
            if "oauth" in item:
                oauth = item["oauth"]
                if oauth is False:
                    entry["oauth"] = False
                elif isinstance(oauth, dict):
                    unknown_oauth = sorted(set(oauth) - MCP_OAUTH_KEYS)
                    if unknown_oauth:
                        raise ContractError(
                            f"{field}.oauth: unsupported fields {', '.join(unknown_oauth)}"
                        )
                    normalized_oauth: dict[str, Any] = {}
                    for key in ("clientId", "clientSecret", "scope"):
                        if key in oauth:
                            normalized_oauth[key] = _non_empty_string(
                                oauth[key], f"{field}.oauth.{key}"
                            )
                    if "callbackPort" in oauth:
                        callback_port = oauth["callbackPort"]
                        if (
                            isinstance(callback_port, bool)
                            or not isinstance(callback_port, int)
                            or callback_port < 1
                            or callback_port > 65535
                        ):
                            raise ContractError(
                                f"{field}.oauth.callbackPort: must be an integer from 1 to 65535"
                            )
                        normalized_oauth["callbackPort"] = callback_port
                    if "redirectUri" in oauth:
                        normalized_oauth["redirectUri"] = _http_url(
                            oauth["redirectUri"], f"{field}.oauth.redirectUri"
                        )
                    entry["oauth"] = normalized_oauth
                else:
                    raise ContractError(f"{field}.oauth: must be false or a mapping")
        result[name] = entry
    return result


def normalize_reference_entries(
    value: Any,
    field: str,
    *,
    slug: str,
    reference_file_paths: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Validate package-owned local and Git references under the manager contract."""

    references = {} if value is None else value
    if not isinstance(references, dict):
        raise ContractError(f"{field}: must be a mapping")
    backing_files = set(reference_file_paths)
    result: dict[str, dict[str, Any]] = {}
    local_prefixes: list[str] = []
    for alias, raw_entry in references.items():
        entry_field = f"{field}.{alias}"
        if (
            not isinstance(alias, str)
            or not NAME_RE.fullmatch(alias)
            or len(alias) > 64
        ):
            raise ContractError(f"{field} key: must be lowercase-hyphen and 64 characters or fewer")
        if not isinstance(raw_entry, dict):
            raise ContractError(f"{entry_field}: must be a mapping; string shorthand is not allowed")
        unknown = sorted(set(raw_entry) - REFERENCE_ENTRY_KEYS)
        if unknown:
            raise ContractError(f"{entry_field}: unsupported fields {', '.join(unknown)}")

        has_path = "path" in raw_entry
        has_repository = "repository" in raw_entry
        if has_path == has_repository:
            raise ContractError(f"{entry_field}: must define exactly one of path or repository")

        normalized: dict[str, Any] = {}
        if has_path:
            path = posix_relative_path(raw_entry["path"], f"{entry_field}.path")
            expected = local_reference_prefix(slug, alias)
            if path != expected:
                raise ContractError(f"{entry_field}.path: must equal {expected}")
            if not any(item.startswith(path + "/") for item in backing_files):
                raise ContractError(f"{entry_field}.path: has no matching reference_files entry")
            if "branch" in raw_entry:
                raise ContractError(f"{entry_field}.branch: is only valid with repository")
            normalized["path"] = path
            local_prefixes.append(path)
        else:
            repository = normalize_git_repository(
                raw_entry["repository"],
                f"{entry_field}.repository",
            )
            normalized["repository"] = repository
            if "branch" in raw_entry:
                normalized["branch"] = _non_empty_string(
                    raw_entry["branch"],
                    f"{entry_field}.branch",
                )

        if "description" in raw_entry:
            description = raw_entry["description"]
            if not isinstance(description, str):
                raise ContractError(f"{entry_field}.description: must be a string")
            normalized["description"] = description
        if "hidden" in raw_entry:
            hidden = raw_entry["hidden"]
            if not isinstance(hidden, bool):
                raise ContractError(f"{entry_field}.hidden: must be a boolean")
            normalized["hidden"] = hidden
        result[alias] = normalized

    for path in sorted(backing_files):
        if not any(path.startswith(prefix + "/") for prefix in local_prefixes):
            raise ContractError(
                f"reference_files path {path}: is not owned by a local references entry"
            )
    return result


def normalize_role_instruction_entries(
    value: Any,
    field: str,
    *,
    slug: str,
    instruction_file_paths: Iterable[str],
) -> dict[str, dict[str, str]]:
    """Validate role-scoped instruction declarations backed by package Markdown."""

    entries = {} if value is None else value
    if not isinstance(entries, dict):
        raise ContractError(f"{field}: must be a mapping")
    backing_files = set(instruction_file_paths)
    result: dict[str, dict[str, str]] = {}
    for alias, raw_entry in entries.items():
        entry_field = f"{field}.{alias}"
        if not isinstance(alias, str) or not NAME_RE.fullmatch(alias) or len(alias) > 64:
            raise ContractError(f"{field} key: must be lowercase-hyphen and 64 characters or fewer")
        if not isinstance(raw_entry, dict):
            raise ContractError(f"{entry_field}: must be a mapping")
        unknown = sorted(set(raw_entry) - ROLE_INSTRUCTION_ENTRY_KEYS)
        if unknown:
            raise ContractError(f"{entry_field}: unsupported fields {', '.join(unknown)}")
        expected = f"{instruction_prefix(slug)}/roles/{alias}.md"
        path = posix_relative_path(raw_entry.get("path"), f"{entry_field}.path")
        if path != expected:
            raise ContractError(f"{entry_field}.path: must equal {expected}")
        if path not in backing_files:
            raise ContractError(f"{entry_field}.path: has no matching instruction_files entry")
        normalized = {"path": path}
        if "description" in raw_entry:
            description = raw_entry["description"]
            if not isinstance(description, str):
                raise ContractError(f"{entry_field}.description: must be a string")
            normalized["description"] = description
        result[alias] = normalized
    return result


def normalize_role_aliases(value: Any, field: str) -> list[str]:
    """Validate explicit role-to-resource alias bindings."""

    if not isinstance(value, list):
        raise ContractError(f"{field}: must be a list")
    values = value
    result: list[str] = []
    for index, item in enumerate(values):
        if item == "*":
            raise ContractError(f"{field}[{index}]: wildcard bindings are not allowed")
        if not isinstance(item, str) or not NAME_RE.fullmatch(item) or len(item) > 64:
            raise ContractError(
                f"{field}[{index}]: must be lowercase-hyphen and 64 characters or fewer"
            )
        result.append(item)
    duplicate = first_duplicate(result)
    if duplicate is not None:
        raise ContractError(f"{field}: duplicates {duplicate}")
    return result


def normalize_lsp_config(value: Any, field: str = "runtime_extensions.lsp") -> bool | dict[str, Any] | None:
    """Validate the package-owned LSP subset under the manager contract."""

    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ContractError(f"{field}: must be false, true, or a mapping")
    if not value:
        raise ContractError(f"{field}: mapping must not be empty; omit lsp instead")

    result: dict[str, Any] = {}
    for name, raw_entry in value.items():
        entry_field = f"{field}.{name}"
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or len(name) > 64:
            raise ContractError(f"{field} key: must be lowercase-hyphen and 64 characters or fewer")
        if not isinstance(raw_entry, dict):
            raise ContractError(f"{entry_field}: must be a mapping")
        unknown = sorted(set(raw_entry) - LSP_SERVER_KEYS)
        if unknown:
            raise ContractError(
                f"{entry_field} contains unsupported fields: {', '.join(unknown)}"
            )

        disabled = raw_entry.get("disabled")
        if "disabled" in raw_entry and not isinstance(disabled, bool):
            raise ContractError(f"{entry_field}.disabled: must be a boolean")

        if "command" not in raw_entry:
            if disabled is not True:
                raise ContractError(f"{entry_field}: must declare command or disabled true")
            if set(raw_entry) != {"disabled"}:
                raise ContractError(
                    f"{entry_field}: disabled-only server must contain only disabled"
                )
            result[name] = {"disabled": True}
            continue

        command = raw_entry["command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and bool(part.strip()) for part in command)
        ):
            raise ContractError(
                f"{entry_field}.command: must be a non-empty list of non-empty strings"
            )
        if "extensions" not in raw_entry:
            raise ContractError(f"{entry_field}.extensions: is required when command is declared")
        extensions = raw_entry["extensions"]
        if (
            not isinstance(extensions, list)
            or not extensions
            or not all(isinstance(part, str) and bool(part.strip()) for part in extensions)
        ):
            raise ContractError(
                f"{entry_field}.extensions: must be a non-empty list of non-empty strings"
            )

        entry: dict[str, Any] = {
            "command": list(command),
            "extensions": list(extensions),
        }
        if "disabled" in raw_entry:
            entry["disabled"] = disabled
        if "env" in raw_entry:
            entry["env"] = _string_mapping(raw_entry["env"], f"{entry_field}.env")
        if "initialization" in raw_entry:
            initialization = raw_entry["initialization"]
            if not isinstance(initialization, dict):
                raise ContractError(f"{entry_field}.initialization: must be an object")
            _validate_json_value(initialization, f"{entry_field}.initialization")
            entry["initialization"] = dict(initialization)
        result[name] = entry
    return result


def normalize_package_dependencies(value: Any, field: str) -> dict[str, dict[str, str]]:
    """Validate the dependency-only ``.opencode/package.json`` contract."""

    package_json = {} if value is None else value
    if not isinstance(package_json, dict):
        raise ContractError(f"{field}: must be a mapping")
    unknown = sorted(set(package_json) - set(PACKAGE_DEPENDENCY_SECTIONS))
    if unknown:
        raise ContractError(
            f"{field}: only dependencies and devDependencies are supported; "
            f"got {', '.join(unknown)}"
        )

    normalized: dict[str, dict[str, str]] = {}
    declared_sections: dict[str, str] = {}
    for section in PACKAGE_DEPENDENCY_SECTIONS:
        if section not in package_json:
            continue
        dependencies = package_json[section]
        if not isinstance(dependencies, dict):
            raise ContractError(f"{field}.{section}: must map package names to versions")
        normalized_section: dict[str, str] = {}
        for package_name, version in dependencies.items():
            if (
                not isinstance(package_name, str)
                or not package_name.strip()
                or package_name != package_name.strip()
                or not isinstance(version, str)
                or not version.strip()
                or version != version.strip()
            ):
                raise ContractError(
                    f"{field}.{section}: must map non-empty package names to non-empty versions"
                )
            previous_section = declared_sections.get(package_name)
            if previous_section is not None:
                raise ContractError(
                    f"{field}: package {package_name} cannot appear in both "
                    f"{previous_section} and {section}"
                )
            declared_sections[package_name] = section
            normalized_section[package_name] = version
        normalized[section] = normalized_section
    return normalized


def skill_purposes(value: Any, field: str) -> list[str]:
    """Validate strict ``[{"purpose": "..."}]`` skill declarations."""

    if not isinstance(value, list) or not value:
        raise ContractError(f"{field}: must be a non-empty list of purpose mappings")
    purposes: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{item_field}: must be a mapping with only purpose")
        if set(item) != {"purpose"}:
            raise ContractError(f"{item_field}: must contain only purpose")
        purpose = item.get("purpose")
        if not isinstance(purpose, str) or not NAME_RE.fullmatch(purpose):
            raise ContractError(f"{item_field}.purpose: must be lowercase-hyphen")
        if purpose in seen:
            raise ContractError(f"{item_field}.purpose: duplicates {purpose}")
        seen.add(purpose)
        purposes.append(purpose)
    return purposes


def common_skill_names(slug: str, value: Any) -> list[str]:
    purposes = skill_purposes(value, "common_skills")
    for index, purpose in enumerate(purposes):
        if purpose.startswith(f"{slug}-"):
            raise ContractError(
                f"common_skills[{index}].purpose: must be a purpose, not a complete skill name"
            )
    return [f"{slug}-common-{purpose}" for purpose in purposes]


def role_skill_names(slug: str, role: dict[str, Any], field: str) -> list[str]:
    agent_id = role.get("id")
    if not isinstance(agent_id, str) or not NAME_RE.fullmatch(agent_id):
        raise ContractError(f"{field}.id: must be lowercase-hyphen")
    purposes = skill_purposes(role.get("skills"), f"{field}.skills")
    for index, purpose in enumerate(purposes):
        if purpose.startswith(f"{slug}-"):
            raise ContractError(
                f"{field}.skills[{index}].purpose: must be a purpose, not a complete skill name"
            )
    return [f"{slug}-{agent_id}-{purpose}" for purpose in purposes]


def extract_env_references(value: Any) -> list[str]:
    """Return sorted OpenCode {env:NAME} references from a JSON-like value."""

    names: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            names.update(ENV_REFERENCE_RE.findall(item))
        elif isinstance(item, dict):
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(names)


def render_env_example(names: Iterable[str]) -> str:
    """Render a deterministic placeholder-only environment example."""

    unique = sorted(set(names))
    return "".join(f"{name}=<required>\n" for name in unique)


def posix_relative_path(value: Any, field: str, *, allow_glob: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field}: must be a non-empty package-relative path")
    if "\\" in value:
        raise ContractError(f"{field}: must use forward slashes")
    if value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:/", value):
        raise ContractError(f"{field}: absolute paths are not allowed")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{field}: path traversal and dot segments are not allowed")
    if not allow_glob and any(char in value for char in GLOB_CHARS):
        raise ContractError(f"{field}: glob characters are not allowed")
    return path.as_posix()


def require_prefix(path: str, prefix: str, field: str) -> str:
    normalized = posix_relative_path(path, field)
    expected = prefix.rstrip("/") + "/"
    if not normalized.startswith(expected):
        raise ContractError(f"{field}: must be under {prefix}/")
    return normalized


def local_reference_prefix(slug: str, alias: str) -> str:
    return f"{PACKAGE_RUNTIME_DIR}/{REFERENCES_SUBDIR}/{slug}/{alias}"


def instruction_prefix(slug: str) -> str:
    return f"{PACKAGE_RUNTIME_DIR}/{INSTRUCTIONS_SUBDIR}/{slug}"


def namespaced_reference_alias(slug: str, alias: str) -> str:
    return f"{slug}-{alias}"


def reference_fallback_skill_name(slug: str, alias: str) -> str:
    base = f"{slug}-reference-{alias}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
    prefix = base[: 64 - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"


def has_glob(value: str) -> bool:
    return any(char in value for char in GLOB_CHARS)


def package_glob_matches(pattern: str, declared_files: Iterable[str]) -> list[str]:
    candidate = PurePosixPath(pattern)
    return sorted(path for path in declared_files if PurePosixPath(path).match(candidate.as_posix()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_avatar_bytes(data: bytes, suffix: str, field: str) -> None:
    normalized = suffix.lower()
    if normalized not in AVATAR_SUFFIXES:
        raise ContractError(f"{field}: unsupported avatar suffix {suffix}")
    if not data:
        raise ContractError(f"{field}: avatar file is empty")
    if len(data) > MAX_AVATAR_BYTES:
        raise ContractError(f"{field}: avatar exceeds the 2 MiB limit")
    if normalized == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContractError(f"{field}: file content is not PNG")
    if normalized in {".jpg", ".jpeg"} and not (data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")):
        raise ContractError(f"{field}: file content is not JPEG")
    if normalized == ".gif" and not data.startswith((b"GIF87a", b"GIF89a")):
        raise ContractError(f"{field}: file content is not GIF")
    if normalized == ".webp" and not (
        len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    ):
        raise ContractError(f"{field}: file content is not WebP")
    if normalized != ".svg":
        return
    lowered_source = data.lower()
    if b"<!doctype" in lowered_source or b"<!entity" in lowered_source:
        raise ContractError(f"{field}: unsafe SVG document declaration")
    try:
        root = ET.fromstring(data.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise ContractError(f"{field}: invalid SVG: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ContractError(f"{field}: SVG root element must be <svg>")
    forbidden_elements = {"script", "foreignobject"}
    external_value = re.compile(r"(?:https?:|file:|javascript:|(?<!:)//)", re.IGNORECASE)
    external_css = re.compile(r"(?:@import\s|url\s*\(\s*['\"]?(?:https?:|file:|javascript:|//))", re.IGNORECASE)
    safe_data_image = re.compile(r"^data:image/(?:png|jpe?g|gif|webp);base64,", re.IGNORECASE)
    for element in root.iter():
        local_tag = element.tag.rsplit("}", 1)[-1].lower()
        if local_tag in forbidden_elements:
            raise ContractError(f"{field}: unsafe SVG element <{local_tag}>")
        if element.text and external_css.search(element.text):
            raise ContractError(f"{field}: unsafe external SVG reference")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            value = raw_value.strip().lower()
            if name.startswith("on"):
                raise ContractError(f"{field}: unsafe SVG event attribute {name}")
            if name in {"href", "src"} and value:
                if not value.startswith("#") and not safe_data_image.match(value):
                    raise ContractError(f"{field}: unsafe external SVG reference")
            elif external_value.search(value) or external_css.search(value):
                raise ContractError(f"{field}: unsafe external SVG reference")


def assert_no_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ContractError(f"symlink is not allowed: {root}")
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(directories):
            if name == ".git":
                directories.remove(name)
        for name in [*directories, *files]:
            path = base / name
            if path.is_symlink():
                raise ContractError(f"symlink is not allowed: {path.relative_to(root).as_posix()}")


def is_allowed_package_path(relative: PurePosixPath) -> bool:
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES or relative.name in ROOT_DIRS
    first = relative.parts[0]
    if first == "avatars":
        return True
    if first != PACKAGE_RUNTIME_DIR:
        return False
    if len(relative.parts) == 2:
        return relative.parts[1] in RUNTIME_DIRS or relative.parts[1] in RUNTIME_FILES
    return relative.parts[1] in RUNTIME_DIRS


def declared_package_files(manifest: dict[str, Any]) -> set[str]:
    """Return the exact file allowlist derived from a validated manifest."""

    import skill_contract

    slug = manifest.get("slug")
    if not isinstance(slug, str) or not NAME_RE.fullmatch(slug):
        raise ContractError("expert.json slug is invalid")
    allowed = {"expert.json", "opencode.json", "README.md", ".env.example", ".gitignore"}

    expert_type = manifest.get("type")
    if expert_type == "expert":
        raw_roles = [manifest.get("agent")]
    elif expert_type == "team":
        subagents = manifest.get("subagents")
        raw_roles = [manifest.get("primary_agent"), *(subagents if isinstance(subagents, list) else [])]
    else:
        raise ContractError("expert.json type must be expert or team")
    roles = [role for role in raw_roles if isinstance(role, dict)]

    skill_names = set(skill_contract.catalog_names(manifest))
    for role_index, role in enumerate(roles):
        agent_id = role.get("id")
        if isinstance(agent_id, str):
            allowed.add(f"{PACKAGE_RUNTIME_DIR}/{AGENTS_SUBDIR}/{agent_id}.md")
    for skill_name in skill_names:
        if skill_contract.schema_mode(manifest) == "legacy":
            allowed.add(f"{PACKAGE_RUNTIME_DIR}/{SKILLS_SUBDIR}/{skill_name}/SKILL.md")

    workflows = manifest.get("workflows", [])
    if isinstance(workflows, list):
        for index, workflow in enumerate(workflows):
            if not isinstance(workflow, dict):
                continue
            command = workflow.get("command")
            if isinstance(command, dict) and isinstance(command.get("name"), str):
                name = posix_relative_path(
                    command["name"] + ".md",
                    f"workflows[{index}].command.name",
                )
                allowed.add(f"{PACKAGE_RUNTIME_DIR}/{COMMANDS_SUBDIR}/{name}")

    for role_or_manifest in [manifest, *roles]:
        avatar = role_or_manifest.get("avatar_url")
        if isinstance(avatar, str) and avatar and not avatar.lower().startswith("https://"):
            allowed.add(posix_relative_path(avatar, "avatar_url"))

    runtime = manifest.get("runtime_extensions", {})
    if runtime is None:
        runtime = {}
    if not isinstance(runtime, dict):
        raise ContractError("runtime_extensions must be an object")
    commands = runtime.get("commands", [])
    if isinstance(commands, list):
        for index, item in enumerate(commands):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                name = posix_relative_path(item["name"] + ".md", f"runtime_extensions.commands[{index}].name")
                allowed.add(f"{PACKAGE_RUNTIME_DIR}/{COMMANDS_SUBDIR}/{name}")
    custom_tools = runtime.get("custom_tools", [])
    if isinstance(custom_tools, list):
        for index, item in enumerate(custom_tools):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                path = posix_relative_path(item["path"], f"runtime_extensions.custom_tools[{index}].path")
                allowed.add(f"{PACKAGE_RUNTIME_DIR}/{TOOLS_SUBDIR}/{path}")
    plugins = runtime.get("plugins", {})
    if isinstance(plugins, dict):
        local_plugins = plugins.get("local", [])
        if isinstance(local_plugins, list):
            for index, item in enumerate(local_plugins):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    path = posix_relative_path(
                        item["path"], f"runtime_extensions.plugins.local[{index}].path"
                    )
                    allowed.add(f"{PACKAGE_RUNTIME_DIR}/{PLUGINS_SUBDIR}/{path}")
        if isinstance(plugins.get("package_json"), dict) and plugins["package_json"]:
            allowed.add(f"{PACKAGE_RUNTIME_DIR}/package.json")
    for section in ["reference_files", "instruction_files"]:
        entries = runtime.get(section, [])
        if isinstance(entries, list):
            for index, item in enumerate(entries):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    path = posix_relative_path(item["path"], f"runtime_extensions.{section}[{index}].path")
                    allowed.add(path)
    resources = manifest.get("package_resources", [])
    if isinstance(resources, list):
        for index, item in enumerate(resources):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                allowed.add(posix_relative_path(item["path"], f"package_resources[{index}].path"))
    return allowed


def parse_jsonc(text: str, source: str = "JSONC") -> dict[str, Any]:
    """Parse JSONC without corrupting comment-like text inside strings."""

    stripped: list[str] = []
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
                stripped.append(char)
            else:
                stripped.append(" ")
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                stripped.extend((" ", " "))
                block_comment = False
                index += 2
                continue
            stripped.append(char if char in "\r\n" else " ")
            index += 1
            continue
        if in_string:
            stripped.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            stripped.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            stripped.extend((" ", " "))
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            stripped.extend((" ", " "))
            index += 2
            continue
        stripped.append(char)
        index += 1
    if block_comment:
        raise ContractError(f"{source}: unterminated block comment")

    without_comments = "".join(stripped)
    no_trailing: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(without_comments):
        char = without_comments[index]
        if in_string:
            no_trailing.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            no_trailing.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(without_comments) and without_comments[lookahead].isspace():
                lookahead += 1
            if lookahead < len(without_comments) and without_comments[lookahead] in "}]":
                index += 1
                continue
        no_trailing.append(char)
        index += 1

    try:
        data = json.loads("".join(no_trailing))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{source}: invalid JSONC: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{source}: root must be an object")
    return data


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def rebase_reference_entry(entry: Any) -> Any:
    if not isinstance(entry, dict) or "path" not in entry:
        return entry
    source = posix_relative_path(entry["path"], "references.path")
    prefix = f"{PACKAGE_RUNTIME_DIR}/{REFERENCES_SUBDIR}/"
    if not source.startswith(prefix):
        raise ContractError("references.path: package-local path is outside .opencode/references/")
    result = dict(entry)
    result["path"] = source[len(f"{PACKAGE_RUNTIME_DIR}/") :]
    return result


def rebase_instruction_entry(entry: str) -> str:
    if entry.startswith("https://"):
        return entry
    source = posix_relative_path(entry, "instructions entry", allow_glob=True)
    prefix = f"{PACKAGE_RUNTIME_DIR}/{INSTRUCTIONS_SUBDIR}/"
    if not source.startswith(prefix):
        raise ContractError("instructions entry is outside .opencode/instructions/")
    return f"{WORKSPACE_RUNTIME_DIR}/{source[len(f'{PACKAGE_RUNTIME_DIR}/') :]}"
