#!/usr/bin/env python3
"""Validate a generated MobileWork expert or expert-team package."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised in a subprocess fallback test
    yaml = None

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import package_contract as contract
import gitignore_contract
import manifest_contract
import manager_contract
import permission_policy
import renderers
import skill_contract
import supply_chain_audit
from validation_result import ValidationResult
import workflow_autonomy


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CODE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*Q$")
AVATAR_RE = re.compile(r"^(https://[^\s]+|[A-Za-z0-9._/-]+\.(?:png|jpg|jpeg|webp|gif|svg))$", re.IGNORECASE)
HTTP_AVATAR_RE = re.compile(r"^https://", re.IGNORECASE)
EXPERT_DIR = contract.PACKAGE_RUNTIME_DIR
AGENTS_SUBDIR = "agents"
SKILLS_SUBDIR = "skills"
COMMANDS_SUBDIR = "commands"
TOOLS_SUBDIR = "tools"
PLUGINS_SUBDIR = "plugins"
REFERENCES_DIR = contract.REFERENCES_SUBDIR
INSTRUCTIONS_DIR = contract.INSTRUCTIONS_SUBDIR
MANIFEST_FILE = "expert.json"
UNOWNED_AGENTS_FILE = "AGENTS.md"
RUNTIME_CONFIG = "opencode.json"
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"']?"
        r"(?!\{env:|\[TODO|<|your-|YOUR_|example|xxx)[A-Za-z0-9_./+=:-]{12,}"
    ),
]
TEXT_SCAN_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonc",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_SCAN_NAMES = {".env.example"}
FORBIDDEN_DISTRIBUTION_DIRS = {
    ".cache",
    ".git",
    ".serena",
    ".venv",
    "__" + "pycache__",
    "node_modules",
    "venv",
}
FORBIDDEN_DISTRIBUTION_FILES = {
    ".DS_Store",
    ".env",
}
FORBIDDEN_DISTRIBUTION_SUFFIXES = {
    ".log",
    ".py" + "c",
    ".pyo",
}
NON_PORTABLE_TEXT_PATTERNS = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "developer home absolute path"),
    (re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"'`]+\\\\"), "Windows user absolute path"),
    (re.compile(r"~/(?:\.agents|\.codex|work|Downloads|Library|MobileWork)\b"), "user-home anchored path"),
    (re.compile(r"(?:^|[\s\"'`=])\.(?:agents|codex)/skills/"), "global agent skill path"),
    (re.compile(r"BrowserRecordReplay/Browser-BC"), "local Browser-BC checkout path"),
    (re.compile(r"/Applications/Microsoft Edge\.app"), "machine-specific Edge executable path"),
    (re.compile(r"(?:^|[\s\"'`=])\.\.[/\\]"), "path escape outside package"),
]


Result = ValidationResult


def iter_package_paths(package_dir: Path):
    """Yield package paths while treating only the root `.git` as source metadata."""

    for current_root, directory_names, file_names in os.walk(package_dir, followlinks=False):
        current = Path(current_root)
        is_root = current == package_dir
        for name in sorted(list(directory_names)):
            path = current / name
            if name == ".git":
                directory_names.remove(name)
                if not is_root:
                    yield path
                continue
            yield path
        for name in sorted(file_names):
            yield current / name


def read_json(path: Path, result: Result) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.error(f"{path.name}: cannot parse JSON: {exc}")
        return None
    if not isinstance(data, dict):
        result.error(f"{path.name}: root must be an object")
        return None
    return data


def validate_name(value: Any, field: str, result: Result) -> str | None:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        result.error(f"{field}: must match ^[a-z0-9]+(-[a-z0-9]+)*$")
        return None
    if len(value) > 64:
        result.error(f"{field}: must be 64 characters or fewer")
        return None
    return value


def validate_text(value: Any, field: str, result: Result, *, required: bool = False) -> str:
    if value is None:
        if required:
            result.error(f"{field}: is required")
        return ""
    if not isinstance(value, str):
        result.error(f"{field}: must be a string")
        return ""
    return value


def validate_string_list(value: Any, field: str, result: Result, *, recommended_count: int | None = None) -> list[str]:
    if value is None:
        if recommended_count is not None:
            result.warn(f"{field}: recommended count is {recommended_count}, got 0")
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        result.error(f"{field}: must be a list of strings")
        return []
    if recommended_count is not None and len(value) != recommended_count:
        result.warn(f"{field}: recommended count is {recommended_count}, got {len(value)}")
    return value


def check_single_expert_public_name(
    public_name: str,
    *,
    slug: str | None,
    agent_id: str | None,
    result: Result,
) -> None:
    if not public_name:
        return
    if public_name in {slug, agent_id}:
        result.error("name: type expert public name must be an expert title, not the slug or agent id")
        return
    if NAME_RE.fullmatch(public_name):
        result.warn("name: type expert public name looks slug-like; use the user-visible expert title")
    if CODE_NAME_RE.fullmatch(public_name):
        result.warn("name: type expert public name looks like an internal code name; use the expert title")


def validate_avatar(value: Any, field: str, result: Result) -> None:
    avatar = validate_text(value, field, result)
    if avatar and not AVATAR_RE.fullmatch(avatar):
        result.error(f"{field}: must be an https URL or a supported relative image path")
    elif avatar and not is_remote_avatar(avatar):
        validate_local_avatar_path(avatar, field, result)


def is_remote_avatar(avatar: str) -> bool:
    return bool(HTTP_AVATAR_RE.match(avatar))


def validate_local_avatar_path(avatar: str, field: str, result: Result) -> Path | None:
    try:
        normalized = contract.posix_relative_path(avatar, field)
    except contract.ContractError as exc:
        result.error(str(exc))
        return None
    path = Path(normalized)
    if not path.name:
        result.error(f"{field}: must point to an image file")
        return None
    if path.suffix.lower() not in contract.AVATAR_SUFFIXES:
        result.error(f"{field}: unsupported avatar suffix")
        return None
    return path


def validate_package_file_path(
    value: Any,
    field: str,
    result: Result,
    *,
    allowed_suffixes: set[str] | None = None,
    required_prefix: str | None = None,
    allow_glob: bool = False,
) -> str:
    try:
        normalized = contract.posix_relative_path(value, field, allow_glob=allow_glob)
    except contract.ContractError as exc:
        result.error(str(exc))
        return ""
    path = Path(normalized)
    if required_prefix and not normalized.startswith(required_prefix.rstrip("/") + "/"):
        result.error(f"{field}: must be under {required_prefix}/")
    if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
        result.error(f"{field}: must use one of these suffixes: {', '.join(sorted(allowed_suffixes))}")
    return normalized


def validate_text_resource_list(
    raw: Any,
    field: str,
    result: Result,
    *,
    required_prefix: str,
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        result.error(f"{field}: must be a list")
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            result.error(f"{field}[{index}]: must be a mapping")
            continue
        unknown = sorted(set(item) - {"path", "content"})
        if unknown:
            result.error(f"{field}[{index}]: unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(
            item.get("path"),
            f"{field}[{index}].path",
            result,
            required_prefix=required_prefix,
        )
        if path:
            if path in seen:
                result.error(f"{field}[{index}].path: duplicates {path}")
            seen.add(path)
            paths.append(path)
        content = validate_text(item.get("content"), f"{field}[{index}].content", result, required=True)
        if not content.strip():
            result.error(f"{field}[{index}].content: must be non-empty")
    return paths


def validate_embedded_files(
    raw: Any,
    field: str,
    result: Result,
    *,
    allowed_suffixes: set[str],
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        result.error(f"{field}: must be a list")
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            result.error(f"{field}[{index}]: must be a mapping")
            continue
        unknown = sorted(set(item) - {"path", "content"})
        if unknown:
            result.error(f"{field}[{index}]: unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(
            item.get("path"),
            f"{field}[{index}].path",
            result,
            allowed_suffixes=allowed_suffixes,
        )
        if path:
            if path in seen:
                result.error(f"{field}[{index}].path: duplicates {path}")
            seen.add(path)
            paths.append(path)
        content = validate_text(item.get("content"), f"{field}[{index}].content", result, required=True)
        if not content.strip():
            result.error(f"{field}[{index}].content: must be non-empty")
    return paths


def check_runtime_extensions_manifest(manifest: dict[str, Any], result: Result) -> dict[str, Any]:
    raw = manifest.get("runtime_extensions", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        result.error("runtime_extensions: must be a mapping")
        return {}
    unknown_runtime = sorted(
        set(raw)
        - {
            "commands", "custom_tools", "plugins", "reference_files",
            "instruction_files", "references", "instructions", "lsp",
        }
    )
    if unknown_runtime:
        result.error(f"runtime_extensions: unsupported fields: {', '.join(unknown_runtime)}")

    commands = raw.get("commands", [])
    command_names: list[str] = []
    declared_agent_ids = {
        role.get("id")
        for role in roles_from_manifest(manifest)
        if isinstance(role.get("id"), str)
    }
    if commands is not None:
        if not isinstance(commands, list):
            result.error("runtime_extensions.commands: must be a list")
        else:
            seen_commands: set[str] = set()
            for index, item in enumerate(commands):
                if not isinstance(item, dict):
                    result.error(f"runtime_extensions.commands[{index}]: must be a mapping")
                    continue
                unknown = sorted(
                    set(item) - {"name", "template", "description", "agent", "subtask", "model"}
                )
                if unknown:
                    result.error(
                        f"runtime_extensions.commands[{index}]: unsupported fields: {', '.join(unknown)}"
                    )
                name = validate_name(item.get("name"), f"runtime_extensions.commands[{index}].name", result)
                if name:
                    if name in seen_commands:
                        result.error(f"runtime_extensions.commands[{index}].name: duplicates {name}")
                    seen_commands.add(name)
                    command_names.append(name)
                template = validate_text(item.get("template"), f"runtime_extensions.commands[{index}].template", result, required=True)
                if not template.strip():
                    result.error(f"runtime_extensions.commands[{index}].template: must be non-empty")
                if item.get("agent") is not None:
                    command_agent = validate_name(
                        item.get("agent"),
                        f"runtime_extensions.commands[{index}].agent",
                        result,
                    )
                    if command_agent and command_agent not in declared_agent_ids:
                        result.error(
                            f"runtime_extensions.commands[{index}].agent: references "
                            f"undeclared agent {command_agent}"
                        )
                if item.get("subtask") is not None and not isinstance(item.get("subtask"), bool):
                    result.error(f"runtime_extensions.commands[{index}].subtask: must be a boolean")
                if item.get("model") is not None:
                    try:
                        contract.normalize_provider_model(
                            item.get("model"),
                            f"runtime_extensions.commands[{index}].model",
                        )
                    except contract.ContractError as exc:
                        result.error(str(exc))

    custom_tools = validate_embedded_files(
        raw.get("custom_tools"),
        "runtime_extensions.custom_tools",
        result,
        allowed_suffixes={".js", ".ts"},
    )
    plugins = raw.get("plugins", {})
    plugin_files: list[str] = []
    npm_plugins: list[str] = []
    if plugins is None:
        plugins = {}
    if not isinstance(plugins, dict):
        result.error("runtime_extensions.plugins: must be a mapping")
    else:
        unknown_plugins = sorted(set(plugins) - {"npm", "local", "package_json"})
        if unknown_plugins:
            result.error(
                f"runtime_extensions.plugins: unsupported fields: {', '.join(unknown_plugins)}"
            )
        npm_plugins = validate_string_list(plugins.get("npm"), "runtime_extensions.plugins.npm", result)
        duplicate_npm = contract.first_duplicate(npm_plugins)
        if duplicate_npm is not None:
            result.error(f"runtime_extensions.plugins.npm: duplicates {duplicate_npm}")
        for index, item in enumerate(npm_plugins):
            if not item.strip() or any(char.isspace() for char in item):
                result.error(f"runtime_extensions.plugins.npm[{index}]: must be a non-empty package name")
        plugin_files = validate_embedded_files(
            plugins.get("local"),
            "runtime_extensions.plugins.local",
            result,
            allowed_suffixes={".js", ".ts"},
        )
        try:
            package_json = contract.normalize_package_dependencies(
                plugins.get("package_json"),
                "runtime_extensions.plugins.package_json",
            )
        except contract.ContractError as exc:
            result.error(str(exc))
            package_json = {}

    slug = manifest.get("slug") if isinstance(manifest.get("slug"), str) else ""
    reference_files = validate_text_resource_list(
        raw.get("reference_files"),
        "runtime_extensions.reference_files",
        result,
        required_prefix=f"{EXPERT_DIR}/{REFERENCES_DIR}/{slug}",
    )
    instruction_files = validate_text_resource_list(
        raw.get("instruction_files"),
        "runtime_extensions.instruction_files",
        result,
        required_prefix=f"{EXPERT_DIR}/{INSTRUCTIONS_DIR}/{slug}",
    )
    reference_file_paths = set(reference_files)
    instruction_file_paths = set(instruction_files)

    try:
        references = contract.normalize_reference_entries(
            raw.get("references"),
            "runtime_extensions.references",
            slug=slug,
            reference_file_paths=reference_file_paths,
        )
    except contract.ContractError as exc:
        result.error(str(exc))
        references = {}

    instructions = validate_string_list(raw.get("instructions"), "runtime_extensions.instructions", result)
    duplicate_instruction = contract.first_duplicate(instructions)
    if duplicate_instruction is not None:
        result.error(
            f"runtime_extensions.instructions: duplicates {duplicate_instruction}"
        )
    for index, item in enumerate(instructions):
        if item.lower().startswith("https://"):
            result.warn(f"runtime_extensions.instructions[{index}]: remote instruction is not reproducible")
            continue
        if item.lower().startswith("http://"):
            result.error(f"runtime_extensions.instructions[{index}]: must use https, not http")
            continue
        path = validate_package_file_path(
            item,
            f"runtime_extensions.instructions[{index}]",
            result,
            allow_glob=True,
        )
        expected = contract.instruction_prefix(slug) + "/"
        if path and not path.startswith(expected):
            result.error(f"runtime_extensions.instructions[{index}]: must be under {expected}")
        if path and not contract.package_glob_matches(path, instruction_file_paths):
            result.error(f"runtime_extensions.instructions[{index}]: no matching instruction_files entry")

    try:
        lsp = contract.normalize_lsp_config(raw.get("lsp"))
    except contract.ContractError as exc:
        result.error(str(exc))
        lsp = None

    return {
        "command_names": command_names,
        "custom_tools": custom_tools,
        "plugin_files": plugin_files,
        "npm_plugins": npm_plugins,
        "package_json": package_json if isinstance(plugins, dict) else {},
        "reference_files": reference_files,
        "instruction_files": instruction_files,
        "references": references,
        "instructions": instructions,
        "lsp": lsp,
    }


def check_package_resources_manifest(
    package_dir: Path,
    manifest: dict[str, Any],
    result: Result,
) -> dict[str, dict[str, str]]:
    raw = manifest.get("package_resources", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        result.error("package_resources: must be a list")
        return {}
    declared_skills = set(expected_skill_names(manifest))
    declared: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw):
        field = f"package_resources[{index}]"
        if not isinstance(item, dict):
            result.error(f"{field}: must be a mapping")
            continue
        unknown = sorted(set(item) - {"path", "kind", "sha256"})
        if unknown:
            result.error(f"{field}: unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(
            item.get("path"),
            f"{field}.path",
            result,
            required_prefix=f"{EXPERT_DIR}/{SKILLS_SUBDIR}",
        )
        if not path:
            continue
        parts = Path(path).parts
        if len(parts) < 4 or parts[2] not in declared_skills:
            result.error(f"{field}.path: must be inside a declared supplemental skill")
        if (
            Path(path).name == "SKILL.md"
            and skill_contract.schema_mode(manifest) == "legacy"
        ):
            result.error(f"{field}.path: generated SKILL.md must not be declared")
        if path in declared:
            result.error(f"{field}.path: duplicates {path}")
        kind = item.get("kind")
        if kind not in {"text", "binary"}:
            result.error(f"{field}.kind: must be text or binary")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not contract.SHA256_RE.fullmatch(digest):
            result.error(f"{field}.sha256: generated packages require a lowercase SHA-256 digest")
            digest = ""
        target = package_dir / path
        if target.is_symlink():
            result.error(f"{field}.path: symlinks are not allowed")
        elif not target.is_file():
            result.error(f"{field}.path: declared resource file is missing")
        else:
            actual = contract.sha256_file(target)
            if digest and actual != digest:
                result.error(f"{field}.sha256: expected {digest}, got {actual}")
            if kind == "text":
                try:
                    target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    result.error(f"{field}.path: kind text requires UTF-8 content")
        declared[path] = {"path": path, "kind": str(kind), "sha256": str(digest)}

    if skill_contract.schema_mode(manifest) == "unified":
        for skill_name in sorted(declared_skills):
            required = f"{EXPERT_DIR}/{SKILLS_SUBDIR}/{skill_name}/SKILL.md"
            if required not in declared:
                result.error(
                    f"skills.{skill_name}: package_resources must declare {required}"
                )

    skills_root = package_dir / EXPERT_DIR / SKILLS_SUBDIR
    if skills_root.is_dir():
        for path in sorted(skills_root.rglob("*")):
            if not path.is_file():
                continue
            if (
                path.name == "SKILL.md"
                and skill_contract.schema_mode(manifest) == "legacy"
            ):
                continue
            relative = path.relative_to(package_dir).as_posix()
            if relative not in declared:
                result.error(f"undeclared supplemental skill resource: {relative}")
    return declared


def parse_frontmatter(path: Path, result: Result) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        result.error(f"{path}: cannot read file: {exc}")
        return None
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        result.error(f"{path}: missing YAML frontmatter")
        return None
    try:
        data = yaml.safe_load(match.group(1)) if yaml is not None else json.loads(match.group(1))
    except Exception as exc:
        result.error(f"{path}: cannot parse frontmatter: {exc}")
        return None
    if not isinstance(data, dict):
        result.error(f"{path}: frontmatter must be a mapping")
        return None
    return data


def validate_role(role: Any, field: str, result: Result, *, expected_mode: str) -> str | None:
    if not isinstance(role, dict):
        result.error(f"{field}: must be a mapping")
        return None
    role_id = validate_name(role.get("id"), f"{field}.id", result)
    if role.get("mode", expected_mode) != expected_mode:
        result.error(f"{field}.mode: must be {expected_mode}")
    validate_text(role.get("name", role.get("title")), f"{field}.name", result, required=True)
    validate_text(role.get("description"), f"{field}.description", result, required=True)
    validate_avatar(role.get("avatar_url"), f"{field}.avatar_url", result)
    default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
    try:
        contract.normalize_agent_runtime_options(
            role,
            field,
            expected_mode=expected_mode,
            default_steps=default_steps,
        )
    except contract.ContractError as exc:
        result.error(str(exc))
    role_mcp = validate_string_list(role.get("mcp"), f"{field}.mcp", result)
    duplicate_mcp = contract.first_duplicate(role_mcp)
    if duplicate_mcp is not None:
        result.error(f"{field}.mcp: duplicates {duplicate_mcp}")
    validate_string_list(role.get("route_triggers"), f"{field}.route_triggers", result)
    validate_string_list(role.get("handoff_contract"), f"{field}.handoff_contract", result)
    for mcp_name in role.get("mcp", []) if isinstance(role.get("mcp"), list) else []:
        validate_name(mcp_name, f"{field}.mcp[]", result)
    return role_id


def list_role_ids(manifest: dict[str, Any], result: Result) -> tuple[str | None, list[str]]:
    expert_type = manifest.get("type")
    if expert_type not in {"expert", "team"}:
        result.error("expert.json: type must be expert or team")
        return None, []

    if expert_type == "expert":
        if "primary_agent" in manifest or "subagents" in manifest:
            result.error("expert.json: type expert must use agent and must not define primary_agent or subagents")
        primary_id = validate_role(manifest.get("agent"), "agent", result, expected_mode="primary")
        return primary_id, []

    if "agent" in manifest:
        result.error("expert.json: type team must use primary_agent and subagents, not agent")
    primary_id = validate_role(manifest.get("primary_agent"), "primary_agent", result, expected_mode="primary")
    subagents_raw = manifest.get("subagents")
    subagent_ids: list[str] = []
    if not isinstance(subagents_raw, list) or not subagents_raw:
        result.error("expert.json: subagents must contain at least one role for type team")
        return primary_id, subagent_ids

    for index, item in enumerate(subagents_raw):
        agent_id = validate_role(item, f"subagents[{index}]", result, expected_mode="subagent")
        if agent_id:
            subagent_ids.append(agent_id)

    ids = [item for item in [primary_id, *subagent_ids] if item]
    if len(ids) != len(set(ids)):
        result.error("expert.json: agent ids must be unique")
    return primary_id, subagent_ids


def roles_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [role for _, role in manifest_contract.manifest_roles(manifest)]


def expected_skill_names(manifest: dict[str, Any]) -> list[str]:
    try:
        skills = skill_contract.catalog_names(manifest)
    except contract.ContractError:
        return []
    return sorted(skills)


def check_skill_contract(manifest: dict[str, Any], result: Result) -> None:
    try:
        skill_contract.validate_manifest_skills(manifest)
    except contract.ContractError as exc:
        result.error(str(exc))
        return
    for field, role in manifest_contract.manifest_roles(manifest):
        permission = role.get("permission")
        skill_permission = permission.get("skill") if isinstance(permission, dict) else None
        if skill_permission is None:
            continue
        if skill_contract.schema_mode(manifest) == "unified":
            result.error(
                f"{field}.permission.skill: is derived from role skills and must be omitted"
            )
            continue
        if not isinstance(skill_permission, dict):
            result.error(f"{field}.permission.skill: must be a mapping")
            continue
        try:
            allowed = set(skill_contract.role_skill_names(manifest, role, field))
        except contract.ContractError:
            continue
        for skill_name in skill_permission:
            if skill_name != "*" and skill_name not in allowed:
                result.error(
                    f"{field}.permission.skill: references undeclared skill {skill_name}"
                )


def manifest_issue_already_reported(
    issue: manifest_contract.ManifestIssue,
    errors: list[str],
) -> bool:
    rendered = str(issue)
    if rendered in errors:
        return True
    field = issue.field
    if field == "expert.json":
        return False
    if any(error.startswith(f"{field}:") for error in errors):
        return True
    if field == "type":
        return any(error.startswith("expert.json: type") for error in errors)
    if field in {"agent", "primary_agent", "subagents"}:
        return any(
            error.startswith("expert.json: type")
            or error.startswith(f"expert.json: {field}")
            for error in errors
        )
    if field == "roles":
        return any(error.startswith("expert.json: agent ids") for error in errors)
    return False


def check_manifest_shape(manifest: dict[str, Any], result: Result) -> tuple[str | None, list[str]]:
    first_manifest_error = len(result.errors)
    validate_name(manifest.get("slug"), "slug", result)
    public_name = validate_text(manifest.get("name"), "name", result, required=True)
    validate_text(manifest.get("description"), "description", result, required=True)
    validate_avatar(manifest.get("avatar_url"), "avatar_url", result)
    validate_string_list(manifest.get("tags"), "tags", result, recommended_count=3)
    quick_prompts = validate_string_list(manifest.get("quick_prompts"), "quick_prompts", result, recommended_count=3)
    default_prompt = validate_text(manifest.get("default_prompt"), "default_prompt", result)
    if default_prompt and quick_prompts and default_prompt != quick_prompts[0]:
        result.error("default_prompt: must match quick_prompts[0]")

    try:
        contract.normalize_mcp_servers(manifest.get("mcp_servers"))
    except contract.ContractError as exc:
        result.error(str(exc))

    check_runtime_extensions_manifest(manifest, result)

    check_skill_contract(manifest, result)
    primary_id, subagent_ids = list_role_ids(manifest, result)
    if manifest.get("type") == "expert" and isinstance(manifest.get("agent"), dict) and public_name:
        slug = manifest.get("slug") if isinstance(manifest.get("slug"), str) else None
        check_single_expert_public_name(public_name, slug=slug, agent_id=primary_id, result=result)
        agent = manifest["agent"]
        for field in ["name", "display_name"]:
            value = agent.get(field)
            if isinstance(value, str) and value and value != public_name:
                result.error(f"agent.{field}: type expert must match top-level name")
    check_workflows(manifest, {item for item in [primary_id, *subagent_ids] if item}, result)
    existing_manifest_errors = result.errors[first_manifest_error:]
    for issue in manifest_contract.collect_manifest_issues(manifest):
        if not manifest_issue_already_reported(issue, existing_manifest_errors):
            result.error(str(issue))
    return primary_id, subagent_ids


def check_workflows(manifest: dict[str, Any], role_ids: set[str], result: Result) -> None:
    roles = roles_from_manifest(manifest)
    primary_id = next(
        (
            role.get("id")
            for role in roles
            if role.get("mode", "primary") == "primary"
            and isinstance(role.get("id"), str)
        ),
        "",
    )
    try:
        workflow_autonomy.normalize_workflows(
            manifest,
            role_ids=role_ids,
            primary_id=primary_id,
        )
    except workflow_autonomy.WorkflowContractError as exc:
        result.error(str(exc))


def check_files(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    primary_id, subagent_ids = check_manifest_shape(manifest, result)
    agents_dir = package_dir / EXPERT_DIR / AGENTS_SUBDIR
    skills_dir = package_dir / EXPERT_DIR / SKILLS_SUBDIR

    if not (package_dir / "README.md").exists():
        result.error("missing README.md")
    if not agents_dir.exists():
        result.error("missing agent definitions directory")
    if not skills_dir.exists():
        result.error("missing skill directory")

    if primary_id and not (agents_dir / f"{primary_id}.md").exists():
        result.error(f"missing primary agent file: {primary_id}.md")
    for agent_id in subagent_ids:
        if not (agents_dir / f"{agent_id}.md").exists():
            result.error(f"missing subagent file: {agent_id}.md")

    for skill_name in expected_skill_names(manifest):
        validate_name(skill_name, f"skill {skill_name}", result)
        if not (skills_dir / skill_name / "SKILL.md").exists():
            result.error(f"missing skill file: {skill_name}/SKILL.md")

    ext = check_runtime_extensions_manifest(manifest, result)
    commands_dir = package_dir / EXPERT_DIR / COMMANDS_SUBDIR
    workflows = manifest.get("workflows", [])
    if isinstance(workflows, list):
        for workflow in workflows:
            if not isinstance(workflow, dict):
                continue
            command = workflow.get("command")
            if not isinstance(command, dict) or not isinstance(command.get("name"), str):
                continue
            if not (commands_dir / f"{command['name']}.md").exists():
                result.error(
                    f"missing workflow command file: {COMMANDS_SUBDIR}/{command['name']}.md"
                )
    for command_name in ext.get("command_names", []):
        if isinstance(command_name, str) and not (commands_dir / f"{command_name}.md").exists():
            result.error(f"missing command file: {COMMANDS_SUBDIR}/{command_name}.md")
    runtime_raw = manifest.get("runtime_extensions", {})
    runtime_commands = runtime_raw.get("commands", []) if isinstance(runtime_raw, dict) else []
    if isinstance(runtime_commands, list):
        for item in runtime_commands:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or item["name"] not in ext.get("command_names", [])
                or not isinstance(item.get("template"), str)
            ):
                continue
            frontmatter: dict[str, Any] = {}
            if isinstance(item.get("description"), str) and item["description"]:
                frontmatter["description"] = item["description"]
            for key in ["agent", "subtask", "model"]:
                if key in item:
                    frontmatter[key] = item[key]
            expected = renderers.render_frontmatter(frontmatter, item["template"])
            if not expected.endswith("\n"):
                expected += "\n"
            command_path = commands_dir / f"{item['name']}.md"
            if command_path.is_file() and read_markdown_body(command_path, result) != expected:
                result.error(
                    f"{command_path}: runtime command projection differs from expert.json"
                )
    tools_dir = package_dir / EXPERT_DIR / TOOLS_SUBDIR
    for path in ext.get("custom_tools", []):
        if isinstance(path, str) and not (tools_dir / path).is_file():
            result.error(f"missing custom tool file: {TOOLS_SUBDIR}/{path}")
    custom_tools = runtime_raw.get("custom_tools", []) if isinstance(runtime_raw, dict) else []
    custom_tool_contents = {
        item["path"]: item["content"]
        for item in custom_tools
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("content"), str)
    } if isinstance(custom_tools, list) else {}
    for path in ext.get("custom_tools", []):
        expected = custom_tool_contents.get(path)
        target = tools_dir / path
        if expected is None or not target.is_file():
            continue
        if not expected.endswith("\n"):
            expected += "\n"
        if read_markdown_body(target, result) != expected:
            result.error(f"{target}: custom tool content differs from expert.json")
    plugins_dir = package_dir / EXPERT_DIR / PLUGINS_SUBDIR
    for path in ext.get("plugin_files", []):
        if isinstance(path, str) and not (plugins_dir / path).is_file():
            result.error(f"missing local plugin file: {PLUGINS_SUBDIR}/{path}")
    plugins = runtime_raw.get("plugins", {}) if isinstance(runtime_raw, dict) else {}
    local_plugins = plugins.get("local", []) if isinstance(plugins, dict) else []
    local_plugin_contents = {
        item["path"]: item["content"]
        for item in local_plugins
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("content"), str)
    } if isinstance(local_plugins, list) else {}
    for path in ext.get("plugin_files", []):
        expected = local_plugin_contents.get(path)
        target = plugins_dir / path
        if expected is None or not target.is_file():
            continue
        if not expected.endswith("\n"):
            expected += "\n"
        if read_markdown_body(target, result) != expected:
            result.error(f"{target}: local plugin content differs from expert.json")
    expected_package_json = ext.get("package_json", {})
    package_json_path = package_dir / EXPERT_DIR / "package.json"
    if expected_package_json:
        if not package_json_path.is_file():
            result.error("missing .opencode/package.json for runtime_extensions.plugins.package_json")
        else:
            actual_package_json = read_json(package_json_path, result)
            if actual_package_json != expected_package_json:
                result.error(
                    ".opencode/package.json must exactly match "
                    "expert.json runtime_extensions.plugins.package_json"
                )
    for path in [*ext.get("reference_files", []), *ext.get("instruction_files", [])]:
        if isinstance(path, str) and not (package_dir / path).is_file():
            result.error(f"missing runtime extension resource file: {path}")
    if isinstance(runtime_raw, dict):
        for section in ["reference_files", "instruction_files"]:
            entries = runtime_raw.get(section, [])
            if not isinstance(entries, list):
                continue
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                content = item.get("content")
                if not isinstance(path, str) or not isinstance(content, str):
                    continue
                target = package_dir / path
                if not target.is_file():
                    continue
                try:
                    actual = target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    result.error(f"runtime_extensions.{section}[{index}]: generated file is not UTF-8")
                    continue
                if actual != content:
                    result.error(
                        f"runtime_extensions.{section}[{index}]: generated file content differs from expert.json"
                    )
    check_package_resources_manifest(package_dir, manifest, result)


def avatar_entries(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    top_avatar = manifest.get("avatar_url")
    if isinstance(top_avatar, str) and top_avatar:
        entries.append(("avatar_url", top_avatar))
    for role in roles_from_manifest(manifest):
        role_id = role.get("id", "unknown")
        avatar = role.get("avatar_url")
        if isinstance(role_id, str) and isinstance(avatar, str) and avatar:
            entries.append((f"{role_id}.avatar_url", avatar))
    return entries


def check_avatar_assets(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    package_root = package_dir.resolve()
    referenced: set[str] = set()
    for field, avatar in avatar_entries(manifest):
        if is_remote_avatar(avatar):
            continue
        relative_path = validate_local_avatar_path(avatar, field, result)
        if relative_path is None:
            continue
        if not relative_path.parts or relative_path.parts[0] != "avatars":
            result.error(f"{field}: local avatar must be under avatars/")
            continue
        referenced.add(relative_path.as_posix())
        lexical_target = package_dir / relative_path
        if lexical_target.is_symlink():
            result.error(f"{field}: avatar symlinks are not allowed")
            continue
        target = (package_dir / relative_path).resolve()
        try:
            target.relative_to(package_root)
        except ValueError:
            result.error(f"{field}: avatar path escapes package root")
            continue
        if not target.is_file():
            result.error(f"{field}: avatar file does not exist: {relative_path.as_posix()}")
            continue
        try:
            contract.validate_avatar_bytes(target.read_bytes(), target.suffix, field)
        except contract.ContractError as exc:
            result.error(str(exc))
    avatars_dir = package_dir / "avatars"
    if not avatars_dir.is_dir():
        result.error("missing avatars directory")
        return
    for path in sorted(avatars_dir.rglob("*")):
        if path.is_file() and path.relative_to(package_dir).as_posix() not in referenced:
            result.error(f"unreferenced avatar file: {path.relative_to(package_dir).as_posix()}")


def read_markdown_body(path: Path, result: Result) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        result.error(f"{path}: cannot read file: {exc}")
        return ""


def check_skill_markdown_shape(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    skills_dir = package_dir / EXPERT_DIR / SKILLS_SUBDIR
    resources_by_skill: dict[str, list[str]] = {}
    prefix = f"{EXPERT_DIR}/{SKILLS_SUBDIR}/"
    raw_resources = manifest.get("package_resources", [])
    if isinstance(raw_resources, list):
        for item in raw_resources:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            path = item["path"]
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            parts = remainder.split("/", 1)
            if len(parts) == 2 and parts[1] != "SKILL.md":
                resources_by_skill.setdefault(parts[0], []).append(parts[1])

    for skill_name in expected_skill_names(manifest):
        path = skills_dir / skill_name / "SKILL.md"
        if not path.exists():
            continue
        frontmatter = parse_frontmatter(path, result)
        body = read_markdown_body(path, result)
        if not frontmatter:
            continue
        if frontmatter.get("name") != skill_name:
            result.error(f"{path}: frontmatter name must equal skill directory {skill_name}")
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            result.error(f"{path}: frontmatter description must be non-empty")
        elif len(description) > 1024:
            result.error(f"{path}: frontmatter description must be 1024 characters or fewer")
        compatibility = frontmatter.get("compatibility")
        if compatibility is not None:
            if skill_contract.schema_mode(manifest) == "legacy":
                if compatibility != "opencode":
                    result.error(
                        f"{path}: optional frontmatter compatibility must equal opencode"
                    )
            elif not isinstance(compatibility, str) or not compatibility.strip():
                result.error(
                    f"{path}: optional frontmatter compatibility must be a non-empty string"
                )
        metadata = frontmatter.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                if skill_contract.schema_mode(manifest) == "legacy":
                    result.error(
                        f"{path}: optional frontmatter metadata must map strings to strings"
                    )
                else:
                    result.error(
                        f"{path}: optional frontmatter metadata must be a mapping"
                    )
            elif skill_contract.schema_mode(manifest) == "legacy" and not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in metadata.items()
            ):
                result.error(
                    f"{path}: optional frontmatter metadata must map strings to strings"
                )
        resources = sorted(resources_by_skill.get(skill_name, []))
        if (
            resources
            and skill_contract.schema_mode(manifest) == "legacy"
            and "资源导航" not in body
        ):
            result.error(f"{path}: generated skill with package_resources must include 资源导航")
        for resource in resources if skill_contract.schema_mode(manifest) == "legacy" else []:
            if resource not in body:
                result.error(f"{path}: resource navigation missing declared path {resource}")


def expected_role_runtime_options(
    role: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    role_id = role.get("id")
    for field, candidate in manifest_contract.manifest_roles(manifest):
        if candidate is not role and candidate.get("id") != role_id:
            continue
        expected_mode = "primary" if field in {"agent", "primary_agent"} else "subagent"
        default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
        try:
            return contract.normalize_agent_runtime_options(
                role,
                field,
                expected_mode=expected_mode,
                default_steps=default_steps,
            )
        except contract.ContractError:
            return None
    return None


def check_agent_markdown_shape(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    agents_dir = package_dir / EXPERT_DIR / AGENTS_SUBDIR
    roles = roles_from_manifest(manifest)
    primary_id = None
    if manifest.get("type") == "expert" and isinstance(manifest.get("agent"), dict):
        primary_id = manifest["agent"].get("id")
    elif manifest.get("type") == "team" and isinstance(manifest.get("primary_agent"), dict):
        primary_id = manifest["primary_agent"].get("id")

    subagent_ids = [
        role.get("id") for role in roles if isinstance(role.get("id"), str) and role.get("id") != primary_id
    ]

    for role in roles:
        agent_id = role.get("id")
        if not isinstance(agent_id, str):
            continue
        md_path = agents_dir / f"{agent_id}.md"
        if not md_path.exists():
            continue
        fm = parse_frontmatter(md_path, result)
        body = read_markdown_body(md_path, result)
        if not fm:
            continue
        unexpected_frontmatter = sorted(set(fm) - contract.AGENT_MARKDOWN_KEYS)
        if unexpected_frontmatter:
            result.error(
                f"{md_path}: unsupported frontmatter fields {', '.join(unexpected_frontmatter)}"
            )
        if fm.get("name") != agent_id:
            result.error(f"{md_path}: frontmatter name must equal agent id {agent_id}")
        description = fm.get("description")
        if not isinstance(description, str) or not description.strip():
            result.error(f"{md_path}: frontmatter description must be non-empty")
        elif role.get("description") and role.get("description") not in description:
            result.error(f"{md_path}: frontmatter description must include the role capability description")
        for field in ["displayName", "profession", "steps"]:
            if field not in fm:
                result.error(f"{md_path}: missing frontmatter {field}")
        if fm.get("avatar_url") != role.get("avatar_url"):
            result.error(f"{md_path}: frontmatter avatar_url must match expert.json")
        expected_runtime = expected_role_runtime_options(role, manifest)
        if expected_runtime is not None:
            for field in ("steps", *contract.AGENT_OPTIONAL_RUNTIME_KEYS):
                if field in expected_runtime and fm.get(field) != expected_runtime[field]:
                    result.error(f"{md_path}: {field} must match expert.json")
                elif field not in expected_runtime and field in fm:
                    result.error(f"{md_path}: {field} must be omitted when absent from expert.json")

        expected_task = {"*": "deny"}
        if agent_id == primary_id and manifest.get("type") == "team":
            expected_task.update({subagent_id: "allow" for subagent_id in subagent_ids})
        permission = fm.get("permission")
        if not isinstance(permission, dict) or permission.get("task") != expected_task:
            result.error(f"{md_path}: permission.task must equal {expected_task}")

        if manifest.get("type") == "expert":
            for text in ["触发与不适用场景", "核心能力", "工作流程", "输出规范", "质量门控", "异常处理"]:
                if text not in body:
                    result.error(f"{md_path}: single expert Markdown missing {text}")
            for forbidden in ["TeamCreate", "subagent_type", "SendMessage", "task_id"]:
                if forbidden in body:
                    result.error(f"{md_path}: single expert Markdown must not contain {forbidden}")
        elif agent_id == primary_id:
            for text in ["触发与不适用场景", "团队角色", "预设 Workflow", "task", "subagent_type", "task_id", "子任务命名", "异常处理"]:
                if text not in body:
                    result.error(f"{md_path}: primary Markdown missing {text}")
            for forbidden in ["TeamCreate", "SendMessage"]:
                if forbidden in body:
                    result.error(f"{md_path}: primary Markdown must not contain {forbidden}")
            for subagent_id in subagent_ids:
                expected = f'subagent_type: "{subagent_id}"'
                if expected not in body:
                    result.error(f"{md_path}: primary Markdown missing Agent ID dispatch rule for {subagent_id}")
        else:
            for text in ["触发与不适用场景", "核心能力", "工作流程", "输出规范", "Task 结果返回要求", "task_id", "不得绕过团长", "异常处理"]:
                if text not in body:
                    result.error(f"{md_path}: subagent Markdown missing {text}")
            for forbidden in ["TeamCreate", "SendMessage"]:
                if forbidden in body:
                    result.error(f"{md_path}: subagent Markdown must not contain {forbidden}")


def check_readme_shape(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    readme_path = package_dir / "README.md"
    if not readme_path.exists():
        return
    text = read_markdown_body(readme_path, result)
    if not text:
        return

    title = f"# {manifest.get('name')}"
    if title not in text.splitlines()[:3]:
        result.error("README.md: title must use the MobileWork expert or expert-team name")

    required_sections = [
        "## 类型",
        "## 功能",
        "## 工作流程",
        "## 内置技能",
        "## 使用示例",
        "## 包结构",
        "## 运行时扩展",
        "## 配置与环境变量",
        "## 注意事项",
    ]
    for section in required_sections:
        if section not in text:
            result.error(f"README.md: missing Chinese section {section}")

    if "### Agent 运行参数" not in text:
        result.error("README.md: missing Agent runtime options summary")
    if "### Agent 权限基线" not in text:
        message = "README.md: missing Agent permission baseline summary"
        workflows = normalized_autonomy_workflows(manifest)
        if workflow_autonomy.has_autonomy_contract(workflows):
            result.error(message)
        else:
            result.warn(
                message,
                code="LEGACY_README_PERMISSION_SECTION_MISSING",
                phase="documentation",
                path="README.md",
                location="documentation",
                root_cause="legacy-permission-contract",
                remediation=(
                    "Preserve compatibility now; add workflow autonomy and regenerate "
                    "README.md during the next structural modification."
                ),
            )

    type_specific = "## 团队角色" if manifest.get("type") == "team" else "## 专家能力"
    if type_specific not in text:
        result.error(f"README.md: missing Chinese section {type_specific}")

    forbidden_fragments = [
        "Generated MobileWork",
        "## Description",
        "## Public Shape",
        "## Structure",
        "## Usage",
        "No MCP entries were configured",
    ]
    for fragment in forbidden_fragments:
        if fragment in text:
            result.error(f"README.md: contains old non-MobileWork README skeleton fragment {fragment!r}")


def normalized_autonomy_workflows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    roles = roles_from_manifest(manifest)
    role_ids = {
        role["id"]
        for role in roles
        if isinstance(role.get("id"), str)
    }
    primary_id = next(
        (
            role["id"]
            for role in roles
            if isinstance(role.get("id"), str)
            and role.get("mode", "primary") == "primary"
        ),
        "",
    )
    try:
        return workflow_autonomy.normalize_workflows(
            manifest,
            role_ids=role_ids,
            primary_id=primary_id,
        )
    except workflow_autonomy.WorkflowContractError:
        return []


def check_workflow_projection_parity(
    package_dir: Path,
    manifest: dict[str, Any],
    result: Result,
) -> None:
    workflows = normalized_autonomy_workflows(manifest)
    if not workflow_autonomy.has_autonomy_contract(workflows):
        return
    roles = roles_from_manifest(manifest)
    primary = next(
        (
            role
            for role in roles
            if isinstance(role.get("id"), str)
            and role.get("mode", "primary") == "primary"
        ),
        None,
    )
    if primary is None:
        return

    all_projection = workflow_autonomy.render_all_workflows(workflows)
    readme = read_markdown_body(package_dir / "README.md", result)
    if all_projection not in readme:
        result.error("README.md: workflow autonomy projection differs from expert.json")

    primary_path = package_dir / EXPERT_DIR / AGENTS_SUBDIR / f"{primary['id']}.md"
    if primary_path.exists() and all_projection not in read_markdown_body(primary_path, result):
        result.error(f"{primary_path}: workflow autonomy projection differs from expert.json")

    if skill_contract.schema_mode(manifest) == "legacy":
        try:
            common_skills = skill_contract.legacy_common_names(manifest)
        except contract.ContractError:
            common_skills = []
        for skill_name in common_skills:
            skill_path = (
                package_dir / EXPERT_DIR / SKILLS_SUBDIR / skill_name / "SKILL.md"
            )
            if skill_path.exists() and all_projection not in read_markdown_body(
                skill_path, result
            ):
                result.error(
                    f"{skill_path}: workflow autonomy projection differs from expert.json"
                )

    for role_index, role in enumerate(roles):
        role_id = role.get("id")
        if not isinstance(role_id, str):
            continue
        role_projection = workflow_autonomy.render_role_workflows(workflows, role_id)
        if not role_projection:
            continue
        if role_id != primary["id"]:
            agent_path = package_dir / EXPERT_DIR / AGENTS_SUBDIR / f"{role_id}.md"
            if agent_path.exists() and role_projection not in read_markdown_body(agent_path, result):
                result.error(
                    f"{agent_path}: Agent workflow autonomy projection differs from expert.json"
                )
        if skill_contract.schema_mode(manifest) == "legacy":
            try:
                role_skills = contract.role_skill_names(
                    str(manifest.get("slug", "")),
                    role,
                    f"roles[{role_index}]",
                )
            except contract.ContractError:
                role_skills = []
            for skill_name in role_skills:
                skill_path = (
                    package_dir
                    / EXPERT_DIR
                    / SKILLS_SUBDIR
                    / skill_name
                    / "SKILL.md"
                )
                if skill_path.exists() and role_projection not in read_markdown_body(
                    skill_path, result
                ):
                    result.error(
                        f"{skill_path}: Agent workflow autonomy projection differs from expert.json"
                    )

    for workflow in workflows:
        command = workflow["command"]
        if not workflow["contract_enabled"] or command is None:
            continue
        expected = renderers.render_frontmatter(
            {
                "description": workflow_autonomy.workflow_command_description(workflow),
                "agent": primary["id"],
            },
            workflow_autonomy.render_workflow_command(workflow),
        )
        command_path = package_dir / EXPERT_DIR / COMMANDS_SUBDIR / f"{command['name']}.md"
        if command_path.exists() and read_markdown_body(command_path, result) != expected:
            result.error(
                f"{command_path}: workflow command projection differs from expert.json"
            )


def check_package_owned_config_keys(
    value: dict[str, Any],
    allowed: frozenset[str],
    field: str,
    result: Result,
) -> None:
    unexpected = sorted(set(value) - allowed)
    if not unexpected:
        return
    result.error(
        f"{RUNTIME_CONFIG}: {field} keys {', '.join(unexpected)} are not owned by MobileWork "
        "expert packages; official OpenCode schema support does not make a field package-owned. "
        f"Declare support through expert.json before adding it to this derived file; allowed keys: "
        f"{', '.join(sorted(allowed))}"
    )


def check_permission_policy(
    package_dir: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
    result: Result,
) -> None:
    workflows = normalized_autonomy_workflows(manifest)
    roles = roles_from_manifest(manifest)
    if not roles:
        return
    try:
        mcp_names = list(contract.normalize_mcp_servers(manifest.get("mcp_servers")))
    except contract.ContractError:
        return
    runtime_raw = manifest.get("runtime_extensions", {})
    custom_tool_paths = [
        item["path"]
        for item in runtime_raw.get("custom_tools", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ] if isinstance(runtime_raw, dict) else []
    subagent_ids = [
        str(role["id"])
        for role in roles[1:]
        if isinstance(role.get("id"), str)
    ] if manifest.get("type") == "team" else []
    config_agents = config.get("agent")
    if not isinstance(config_agents, dict):
        return
    readme = read_markdown_body(package_dir / "README.md", result)
    for index, role in enumerate(roles):
        role_id = role.get("id")
        if not isinstance(role_id, str):
            continue
        try:
            role_skills = skill_contract.role_skill_names(
                manifest,
                role,
                f"roles[{index}]",
            )
            normalized_role = dict(role)
            normalized_role["allowed_skills"] = role_skills
            normalized_role["mcp"] = role.get("mcp", [])
            normalized_role["custom_tools"] = role.get("custom_tools", [])
            source_permission = role.get("permission", {})
            if isinstance(source_permission, dict):
                source_permission = dict(source_permission)
                if skill_contract.schema_mode(manifest) == "unified":
                    source_permission.pop("skill", None)
            normalized_role["permission"] = source_permission
            normalized_role["permission_reason"] = role.get("permission_reason", "")
            tools = role.get("tools", {})
            if not isinstance(tools, dict):
                continue
            expected, audit = permission_policy.build_role_permission(
                normalized_role,
                workflows=workflows,
                mcp_names=mcp_names,
                custom_tool_paths=custom_tool_paths,
                subagent_ids=subagent_ids,
                is_primary=index == 0,
                legacy_tools_permission=permission_policy.tools_to_permission(
                    tools, f"{role_id}.tools"
                ),
            )
        except (contract.ContractError, permission_policy.PermissionPolicyError) as exc:
            result.error(f"{role_id}: {exc}")
            continue
        config_agent = config_agents.get(role_id)
        if isinstance(config_agent, dict) and config_agent.get("permission") != expected:
            result.error(
                f"{RUNTIME_CONFIG}: agent.{role_id}.permission must match the autonomy-derived policy"
            )
        legacy_permission_baseline = audit["warning"] == "legacy-permission-baseline"
        if legacy_permission_baseline:
            result.warn(
                f"{role_id}: HIGH RISK legacy-permission-baseline preserves historical permissions, including possible unconditional Bash wildcard allow; add workflow autonomy during the next structural modification"
            )
        elif audit["warning"] == "unused-role-bounded-fallback":
            result.warn(
                f"{role_id}: unused-role-bounded-fallback; role is not assigned to an autonomy-enabled workflow"
            )
        for expected_text in (
            audit["source"],
            audit["effective"],
            audit["permission_reason"] or "无",
        ):
            if expected_text not in readme:
                message = (
                    f"README.md: Agent permission baseline for {role_id} "
                    "differs from expert.json"
                )
                if legacy_permission_baseline:
                    result.warn(
                        message,
                        code="LEGACY_README_PERMISSION_PROJECTION_MISMATCH",
                        phase="documentation",
                        path="README.md",
                        location="documentation",
                        root_cause="legacy-permission-contract",
                        remediation=(
                            "Preserve compatibility now; add workflow autonomy and "
                            "regenerate README.md during the next structural modification."
                        ),
                    )
                else:
                    result.error(message)
                break


def check_runtime_config(package_dir: Path, config: dict[str, Any], manifest: dict[str, Any], result: Result) -> None:
    check_package_owned_config_keys(
        config,
        contract.OPENCODE_PACKAGE_ROOT_KEYS,
        "root",
        result,
    )
    schema = config.get("$schema")
    if schema is None:
        result.warn(f"{RUNTIME_CONFIG}: missing OpenCode $schema; regenerate the package to add it")
    elif schema != contract.OPENCODE_SCHEMA:
        result.error(f"{RUNTIME_CONFIG}: $schema must equal {contract.OPENCODE_SCHEMA}")
    agents = config.get("agent")
    if not isinstance(agents, dict):
        result.error(f"{RUNTIME_CONFIG}: agent must be an object")
        return

    expected_agent_ids = {
        role.get("id")
        for role in roles_from_manifest(manifest)
        if isinstance(role.get("id"), str)
    }
    if set(agents) != expected_agent_ids:
        result.error(
            f"{RUNTIME_CONFIG}: agent ids must exactly match expert.json: "
            f"{', '.join(sorted(expected_agent_ids))}"
        )

    for agent_id, data in agents.items():
        if isinstance(data, dict):
            check_package_owned_config_keys(
                data,
                contract.OPENCODE_PACKAGE_AGENT_KEYS,
                f"agent.{agent_id}",
                result,
            )

    primary_count = sum(1 for data in agents.values() if isinstance(data, dict) and data.get("mode") == "primary")
    subagent_count = sum(1 for data in agents.values() if isinstance(data, dict) and data.get("mode") == "subagent")
    if primary_count != 1:
        result.error(f"{RUNTIME_CONFIG}: expected exactly 1 primary agent, got {primary_count}")
    if manifest.get("type") == "expert" and subagent_count != 0:
        result.error(f"{RUNTIME_CONFIG}: type expert expected 0 subagents, got {subagent_count}")
    if manifest.get("type") == "team" and subagent_count < 1:
        result.error(f"{RUNTIME_CONFIG}: type team expected at least 1 subagent")

    manifest_mcp_raw = manifest.get("mcp_servers", [])
    manifest_has_mcp = isinstance(manifest_mcp_raw, list) and bool(manifest_mcp_raw)
    if not manifest_has_mcp and "mcp" in config:
        result.error(f"{RUNTIME_CONFIG}: mcp must be omitted when expert.json has no mcp_servers")
    if manifest_has_mcp and "mcp" not in config:
        result.error(f"{RUNTIME_CONFIG}: mcp is required when expert.json defines mcp_servers")

    try:
        expected_mcp = contract.normalize_mcp_servers(manifest_mcp_raw)
    except contract.ContractError:
        expected_mcp = {}

    mcp = config.get("mcp", {})
    if not isinstance(mcp, dict):
        result.error(f"{RUNTIME_CONFIG}: mcp must be an object")
    elif mcp != expected_mcp:
        result.error(f"{RUNTIME_CONFIG}: mcp must exactly match expert.json mcp_servers")

    ext = check_runtime_extensions_manifest(manifest, result)
    runtime_ext = manifest.get("runtime_extensions", {})
    plugins = runtime_ext.get("plugins", {}) if isinstance(runtime_ext, dict) else {}
    npm_plugins = plugins.get("npm", []) if isinstance(plugins, dict) else []
    if npm_plugins:
        if config.get("plugin") != npm_plugins:
            result.error(f"{RUNTIME_CONFIG}: plugin must match expert.json runtime_extensions.plugins.npm")
    elif "plugin" in config:
        result.error(f"{RUNTIME_CONFIG}: plugin must be omitted when runtime_extensions.plugins.npm is empty")

    references = ext.get("references", {})
    if references:
        expected_references = {
            contract.namespaced_reference_alias(str(manifest.get("slug")), alias): entry
            for alias, entry in references.items()
        }
        if config.get("references") != expected_references:
            result.error(
                f"{RUNTIME_CONFIG}: references must use <slug>-<alias> names and exactly match expert.json"
            )
    elif "references" in config:
        result.error(
            f"{RUNTIME_CONFIG}: references must be omitted when runtime_extensions.references is empty"
        )

    instructions = list(ext.get("instructions", []))
    if instructions:
        if config.get("instructions") != instructions:
            result.error(f"{RUNTIME_CONFIG}: instructions must exactly match expert.json")
    elif "instructions" in config:
        result.error(f"{RUNTIME_CONFIG}: instructions must be omitted when runtime_extensions.instructions is empty")

    lsp = ext.get("lsp")
    if lsp is not None:
        if config.get("lsp") != lsp:
            result.error(f"{RUNTIME_CONFIG}: lsp must match expert.json runtime_extensions.lsp")
    elif "lsp" in config:
        result.error(f"{RUNTIME_CONFIG}: lsp must be omitted when runtime_extensions.lsp is omitted")

    agents_dir = package_dir / EXPERT_DIR / AGENTS_SUBDIR
    role_ids = [role.get("id") for role in roles_from_manifest(manifest)]
    for agent_id in [item for item in role_ids if isinstance(item, str)]:
        md_path = agents_dir / f"{agent_id}.md"
        if not md_path.exists():
            continue
        fm = parse_frontmatter(md_path, result)
        config_agent = agents.get(agent_id)
        if not isinstance(config_agent, dict):
            result.error(f"{RUNTIME_CONFIG}: missing agent entry for {agent_id}")
            continue
        if fm and fm.get("permission") != config_agent.get("permission"):
            result.error(f"permission mismatch for agent {agent_id}: Markdown frontmatter != {RUNTIME_CONFIG}")
        if fm and fm.get("description") != config_agent.get("description"):
            result.error(f"description mismatch for agent {agent_id}: Markdown frontmatter != {RUNTIME_CONFIG}")
        role = next((item for item in roles_from_manifest(manifest) if item.get("id") == agent_id), {})
        expected_runtime = expected_role_runtime_options(role, manifest)
        if expected_runtime is None:
            continue
        primary_id = (
            manifest.get("agent", {}).get("id")
            if manifest.get("type") == "expert" and isinstance(manifest.get("agent"), dict)
            else manifest.get("primary_agent", {}).get("id")
            if manifest.get("type") == "team" and isinstance(manifest.get("primary_agent"), dict)
            else None
        )
        expected_mode = "primary" if agent_id == primary_id else "subagent"
        if config_agent.get("mode") != expected_mode:
            result.error(f"{RUNTIME_CONFIG}: agent.{agent_id}.mode must be {expected_mode}")
        if fm and fm.get("mode") != expected_mode:
            result.error(f"{md_path}: mode must be {expected_mode}")
        for field in ("steps", *contract.AGENT_OPTIONAL_RUNTIME_KEYS):
            if field in expected_runtime:
                if config_agent.get(field) != expected_runtime[field]:
                    result.error(
                        f"{RUNTIME_CONFIG}: agent.{agent_id}.{field} must match expert.json"
                    )
                if fm and fm.get(field) != expected_runtime[field]:
                    result.error(f"{md_path}: {field} must match expert.json")
            else:
                if field in config_agent:
                    result.error(
                        f"{RUNTIME_CONFIG}: agent.{agent_id}.{field} must be omitted when absent "
                        "from expert.json"
                    )
                if fm and field in fm:
                    result.error(
                        f"{md_path}: {field} must be omitted when absent from expert.json"
                    )


def scan_secrets(package_dir: Path, result: Result) -> None:
    for path in iter_package_paths(package_dir):
        if not path.is_file() or (path.suffix not in TEXT_SCAN_SUFFIXES and path.name not in TEXT_SCAN_NAMES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                line = text[line_start:line_end]
                allowed_source_terms = [
                    "TOKEN_ENV",
                    "authToken",
                    "newToken",
                    "token_set",
                    "token_value_redacted",
                    "os.environ.get",
                    "localStorage.getItem",
                    "generateAuthToken",
                    "<paste",
                    "<copy-from",
                    "<redacted>",
                    "{env:",
                ]
                if any(term in line for term in allowed_source_terms):
                    continue
                result.error(f"possible secret-like value in {path.relative_to(package_dir)}")
                break
            else:
                continue
            break


def check_env_example(package_dir: Path, config: dict[str, Any], result: Result) -> None:
    expected_names = contract.extract_env_references(config)
    path = package_dir / ".env.example"
    if not expected_names:
        if path.exists():
            result.error(".env.example must be omitted when opencode.json has no {env:VARIABLE} references")
        return
    if not path.exists():
        result.warn(
            ".env.example is missing for referenced environment variables; regenerate the package to add it"
        )
        return
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        result.error(f".env.example cannot be read: {exc}")
        return
    expected = contract.render_env_example(expected_names)
    if content != expected:
        result.error(
            ".env.example must exactly list sorted referenced variables as VARIABLE=<required>"
        )


def check_forbidden_files(package_dir: Path, result: Result) -> None:
    for path in iter_package_paths(package_dir):
        rel = path.relative_to(package_dir)
        if path == package_dir:
            continue
        if rel.as_posix() == UNOWNED_AGENTS_FILE:
            result.error(
                "AGENTS.md is supported by official OpenCode but is not owned by a MobileWork "
                "expert package; move workspace-wide rules to "
                "runtime_extensions.instruction_files and runtime_extensions.instructions"
            )
            continue
        if not contract.is_allowed_package_path(Path(rel.as_posix())):
            result.error(f"path is outside the package allowlist: {rel.as_posix()}")
        for part in rel.parts:
            if part in FORBIDDEN_DISTRIBUTION_DIRS:
                result.error(f"non-distributable directory in package: {rel.as_posix()}")
                break
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_DISTRIBUTION_FILES:
            result.error(f"non-distributable file in package: {rel.as_posix()}")
        if path.suffix in FORBIDDEN_DISTRIBUTION_SUFFIXES:
            result.error(f"non-distributable file suffix in package: {rel.as_posix()}")


def check_declared_file_allowlist(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    try:
        allowed = contract.declared_package_files(manifest)
    except contract.ContractError as exc:
        result.error(str(exc))
        return
    for path in iter_package_paths(package_dir):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir).as_posix()
        if relative == UNOWNED_AGENTS_FILE:
            continue
        if relative not in allowed:
            result.error(f"undeclared package file: {relative}")


def check_gitignore(package_dir: Path, manifest: dict[str, Any], result: Result) -> None:
    path = package_dir / ".gitignore"
    if not path.exists():
        result.warn(
            "root .gitignore is missing; legacy package remains installable but source VCS hygiene is pending",
            code="GITIGNORE_MISSING",
            phase="version-control",
            path=".gitignore",
            root_cause="legacy-source-without-gitignore",
            remediation="Regenerate the trusted source package to add the managed .gitignore block.",
        )
        return
    try:
        content = path.read_text(encoding="utf-8")
        declared = contract.declared_package_files(manifest)
    except (OSError, UnicodeDecodeError, contract.ContractError) as exc:
        result.error(
            f"root .gitignore cannot be validated: {exc}",
            code="GITIGNORE_INVALID",
            phase="version-control",
            path=".gitignore",
            root_cause="invalid-gitignore",
        )
        return
    for code, message in gitignore_contract.validate_content(content, declared):
        result.error(
            message,
            code=code,
            phase="version-control",
            path=".gitignore",
            root_cause="invalid-gitignore",
            remediation="Restore the managed block and unignore every package-owned file.",
        )


def scan_portability(package_dir: Path, result: Result) -> None:
    for path in iter_package_paths(package_dir):
        if not path.is_file() or (path.suffix not in TEXT_SCAN_SUFFIXES and path.name not in TEXT_SCAN_NAMES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(package_dir).as_posix()
        for pattern, label in NON_PORTABLE_TEXT_PATTERNS:
            match = pattern.search(text)
            if match:
                result.error(f"non-portable {label} in {rel}: {match.group(0).strip()!r}")
                break


def check_static_syntax(package_dir: Path, result: Result) -> None:
    """Parse package Python as source text without importing or executing it."""

    for path in iter_package_paths(package_dir):
        if not path.is_file() or path.suffix != ".py":
            continue
        relative = path.relative_to(package_dir).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            result.error(
                f"Python syntax check failed in {relative}: {exc}",
                code="PYTHON_STATIC_SYNTAX_INVALID",
                phase="static-syntax",
                path=relative,
                root_cause="invalid-python-syntax",
                evidence=str(exc),
            )


def validate_package(
    package_dir: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> Result:
    result = Result(input_path=package_dir, target=target)
    if not package_dir.exists() or not package_dir.is_dir():
        result.error(f"package directory does not exist: {package_dir}")
        result.finalize_contract()
        return result

    try:
        contract.assert_no_symlinks(package_dir)
    except contract.ContractError as exc:
        result.error(str(exc))

    manifest_path = package_dir / MANIFEST_FILE
    config_path = package_dir / RUNTIME_CONFIG
    check_forbidden_files(package_dir, result)
    if not manifest_path.exists():
        result.error(f"missing {MANIFEST_FILE}")
        result.finalize_contract()
        return result
    if not config_path.exists():
        result.error(f"missing {RUNTIME_CONFIG}")
        result.finalize_contract()
        return result

    manifest = read_json(manifest_path, result)
    config = read_json(config_path, result)
    if not manifest or not config:
        result.finalize_contract()
        return result
    if "version" not in manifest:
        result.warn(
            "expert.json version is absent; package is unreleased and remains valid for local trusted-source iteration",
            code="EXPERT_VERSION_UNRELEASED",
            phase="version-control",
            path="expert.json",
            location="/version",
            root_cause="unreleased-expert-source",
            remediation="After a successful trusted-source modification, ask the user whether to publish a SemVer release.",
        )

    check_declared_file_allowlist(package_dir, manifest, result)
    check_gitignore(package_dir, manifest, result)
    check_files(package_dir, manifest, result)
    check_avatar_assets(package_dir, manifest, result)
    check_agent_markdown_shape(package_dir, manifest, result)
    check_skill_markdown_shape(package_dir, manifest, result)
    check_readme_shape(package_dir, manifest, result)
    check_workflow_projection_parity(package_dir, manifest, result)
    check_runtime_config(package_dir, config, manifest, result)
    check_permission_policy(package_dir, manifest, config, result)
    check_env_example(package_dir, config, result)
    check_static_syntax(package_dir, result)
    supply_chain_audit.add_to_result(result, package_dir, manifest, config)
    scan_secrets(package_dir, result)
    scan_portability(package_dir, result)
    if result.gates["portability"] == "not-run":
        result.set_gate("portability", "passed")
    result.finalize_contract()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", type=Path, help="Generated expert package directory")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--schema-version", choices=(1, 2), type=int, default=2)
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Request runtime verification (blocked by this static validator)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package_dir = args.package_dir.expanduser().absolute()
    try:
        target = manager_contract.resolve_target(
            cli_version=args.target_opencode_version,
            host_contract=args.host_contract,
        )
        result = validate_package(package_dir, target=target)
    except manager_contract.ManagerContractError as exc:
        result = Result(
            execution_reason="version-contract-error",
            input_path=package_dir,
            target=manager_contract.TargetContract(
                version="unknown",
                source="version-contract-error",
                capabilities={},
                capability_verified=False,
            ),
        )
        result.error(
            f"version contract error: {exc}",
            code="MANAGER_VERSION_CONTRACT_ERROR",
            phase="manager",
            root_cause="invalid-version-contract",
            evidence="",
        )
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 2
    except Exception as exc:
        result = Result(
            execution_reason="manager-internal-error",
            target=manager_contract.TargetContract(
                version="unknown",
                source="manager-internal-error",
                capabilities={},
                capability_verified=False,
            ),
        )
        result.error(
            f"internal manager failure: {exc}",
            code="MANAGER_INTERNAL_ERROR",
            phase="manager",
            root_cause="manager-internal-error",
            evidence="",
        )
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 3
    if args.runtime:
        result.execution["reason"] = "runtime-request-blocked-use-sandboxed-trusted-flow"
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 4
    result.print_summary(output_format=args.format, schema_version=args.schema_version)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
