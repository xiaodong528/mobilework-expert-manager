#!/usr/bin/env python3
"""Validate a generated MobileWork expert or expert-team package."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
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
import cli_contract
import gitignore_contract
import manifest_contract
import manager_contract
import permission_policy
import plugin_contract
import renderers
import safe_input
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


def _has_finding(result: Result, *, code: str, location: str) -> bool:
    return any(
        finding.code == code and finding.location == location
        for finding in result.findings
    )


def _parse_runtime_config_npm_plugins(
    config: dict[str, Any],
    result: Result,
) -> tuple[list[tuple[int, plugin_contract.NpmPluginSpec]], bool]:
    if "plugin" not in config:
        return [], True
    raw_plugins = config.get("plugin")
    if not isinstance(raw_plugins, list):
        result.error(f"{RUNTIME_CONFIG}: plugin must be a list")
        return [], False

    parsed_plugins: list[tuple[int, plugin_contract.NpmPluginSpec]] = []
    valid = True
    for index, item in enumerate(raw_plugins):
        location = f"/plugin/{index}"
        if not isinstance(item, str):
            result.error(
                f"{RUNTIME_CONFIG}: plugin[{index}] must be a string",
                code=plugin_contract.ERROR_CODE,
                phase="runtime-config",
                path=RUNTIME_CONFIG,
                location=location,
                root_cause="invalid-npm-plugin-spec",
                remediation="Project a valid registry npm Plugin spec from expert.json.",
                evidence="",
            )
            valid = False
            continue
        try:
            parsed = plugin_contract.parse_npm_plugin_spec(item)
        except plugin_contract.PluginContractError as exc:
            result.error(
                f"{RUNTIME_CONFIG}: plugin[{index}] is invalid: {exc}",
                code=plugin_contract.ERROR_CODE,
                phase="runtime-config",
                path=RUNTIME_CONFIG,
                location=location,
                root_cause="invalid-npm-plugin-spec",
                remediation="Project a valid registry npm Plugin spec from expert.json.",
                evidence="",
            )
            valid = False
            continue
        parsed_plugins.append((index, parsed))
    return parsed_plugins, valid


def load_unique_yaml_mapping(raw_frontmatter: str) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required")

    class UniqueKeySafeLoader(yaml.SafeLoader):
        pass

    def construct_unique_mapping(
        loader: Any,
        node: Any,
        deep: bool = False,
    ) -> dict[Any, Any]:
        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeySafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )
    return yaml.load(raw_frontmatter, Loader=UniqueKeySafeLoader)


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
    custom_tool_purpose: bool = False,
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
        allowed_keys = (
            contract.CUSTOM_TOOL_ENTRY_KEYS
            if custom_tool_purpose
            else frozenset({"path", "content"})
        )
        unknown = sorted(set(item) - allowed_keys)
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
        if custom_tool_purpose:
            if "purpose" not in item:
                result.warn(
                    f"{field}[{index}].purpose: legacy package omits the Custom Tool invocation purpose",
                    code="LEGACY_CUSTOM_TOOL_PURPOSE_MISSING",
                    phase="manifest",
                    path=MANIFEST_FILE,
                    location=f"/runtime_extensions/custom_tools/{index}/purpose",
                    root_cause="legacy-custom-tool-purpose-missing",
                    remediation=(
                        "Add the confirmed non-empty invocation purpose before the next "
                        "structural modification."
                    ),
                    evidence="missing",
                )
            else:
                purpose = validate_text(
                    item.get("purpose"),
                    f"{field}[{index}].purpose",
                    result,
                    required=True,
                )
                if not purpose.strip():
                    result.error(f"{field}[{index}].purpose: must be non-empty")
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
            "instruction_files", "references", "instructions", "role_instructions", "lsp",
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
    main_agent = (
        manifest.get("agent")
        if manifest.get("type") == "expert"
        else manifest.get("primary_agent")
        if manifest.get("type") == "team"
        else None
    )
    main_agent_id = main_agent.get("id") if isinstance(main_agent, dict) else None
    command_policy = contract.expert_runtime_projection_policy()["command"]
    expected_subtask = command_policy["subtask"]
    expected_subtask_text = str(expected_subtask).lower()
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
                command_agent: str | None = None
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
                routing_location = f"/runtime_extensions/commands/{index}"
                routing_is_legacy = (
                    command_agent != main_agent_id
                    or item.get("subtask") is not expected_subtask
                )
                if (
                    routing_is_legacy
                    and not _has_finding(
                        result,
                        code="LEGACY_COMMAND_ROUTING",
                        location=routing_location,
                    )
                ):
                    result.warn(
                        f"runtime_extensions.commands[{index}]: legacy command routing "
                        "remains readable; structural modification must set agent to "
                        f"the mode all Agent {main_agent_id} and subtask to "
                        f"{expected_subtask_text}",
                        code="LEGACY_COMMAND_ROUTING",
                        phase="manifest",
                        path=MANIFEST_FILE,
                        location=routing_location,
                        root_cause="legacy-command-routing",
                        remediation=(
                            "Regenerate the command so agent references the package's "
                            f"mode all Agent and subtask is {expected_subtask_text}."
                        ),
                        evidence=(
                            f"agent={item.get('agent')!r}, "
                            f"subtask={item.get('subtask')!r}"
                        ),
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
        custom_tool_purpose=True,
    )
    plugins = raw.get("plugins", {})
    plugin_files: list[str] = []
    npm_plugin_specs: list[plugin_contract.NpmPluginSpec] = []
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
        raw_npm_plugins = validate_string_list(
            plugins.get("npm"),
            "runtime_extensions.plugins.npm",
            result,
        )
        canonical_indexes: dict[str, int] = {}
        for index, item in enumerate(raw_npm_plugins):
            location = f"/runtime_extensions/plugins/npm/{index}"
            try:
                parsed = plugin_contract.parse_npm_plugin_spec(item)
            except plugin_contract.PluginContractError as exc:
                if not _has_finding(
                    result,
                    code=plugin_contract.ERROR_CODE,
                    location=location,
                ):
                    result.error(
                        f"runtime_extensions.plugins.npm[{index}]: {exc}",
                        code=plugin_contract.ERROR_CODE,
                        phase="manifest",
                        path=MANIFEST_FILE,
                        location=location,
                        root_cause="invalid-npm-plugin-spec",
                        remediation="Use a registry package with a valid npm selector.",
                        evidence="",
                    )
                continue
            previous_index = canonical_indexes.get(parsed["canonicalKey"])
            if previous_index is not None:
                if not _has_finding(
                    result,
                    code=plugin_contract.DUPLICATE_CODE,
                    location=location,
                ):
                    result.error(
                        f"runtime_extensions.plugins.npm[{index}]: duplicates the canonical "
                        f"npm Plugin declared at index {previous_index}",
                        code=plugin_contract.DUPLICATE_CODE,
                        phase="manifest",
                        path=MANIFEST_FILE,
                        location=location,
                        root_cause="duplicate-npm-plugin-spec",
                        remediation="Declare each canonical npm Plugin selector only once.",
                        evidence="",
                    )
            else:
                canonical_indexes[parsed["canonicalKey"]] = index
            if not parsed["isPinned"] and not _has_finding(
                result,
                code=plugin_contract.UNPINNED_CODE,
                location=location,
            ):
                result.warn(
                    f"runtime_extensions.plugins.npm[{index}]: legacy npm Plugin spec "
                    "is not pinned to an exact SemVer",
                    code=plugin_contract.UNPINNED_CODE,
                    phase="supply-chain",
                    path=MANIFEST_FILE,
                    location=location,
                    root_cause="unlocked-plugin",
                    remediation="Pin the npm Plugin to an exact SemVer in newly generated packages.",
                    evidence="",
                )
            npm_plugin_specs.append(parsed)
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

    try:
        role_instructions = contract.normalize_role_instruction_entries(
            raw.get("role_instructions"),
            "runtime_extensions.role_instructions",
            slug=slug,
            instruction_file_paths=instruction_file_paths,
        )
    except contract.ContractError as exc:
        result.error(str(exc))
        role_instructions = {}

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
        if path:
            for alias, entry in role_instructions.items():
                if contract.package_glob_matches(path, [entry["path"]]):
                    result.error(
                        f"runtime_extensions.instructions[{index}]: overlaps role rule {alias}"
                    )

    try:
        lsp = contract.normalize_lsp_config(raw.get("lsp"))
    except contract.ContractError as exc:
        result.error(str(exc))
        lsp = None

    return {
        "command_names": command_names,
        "custom_tools": custom_tools,
        "plugin_files": plugin_files,
        "npm_plugin_specs": npm_plugin_specs,
        "package_json": package_json if isinstance(plugins, dict) else {},
        "reference_files": reference_files,
        "instruction_files": instruction_files,
        "references": references,
        "instructions": instructions,
        "role_instructions": role_instructions,
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


def parse_frontmatter(
    path: Path,
    result: Result,
    *,
    require_block_yaml: bool = False,
) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        result.error(f"{path}: cannot read file: {exc}")
        return None
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        result.error(f"{path}: missing YAML frontmatter")
        return None
    raw_frontmatter = match.group(1)
    if require_block_yaml and raw_frontmatter.lstrip().startswith("{"):
        result.error(
            f"{path}: frontmatter must use block-style YAML, not a JSON flow mapping"
        )
        return None
    if yaml is None and require_block_yaml:
        result.error(f"{path}: cannot parse frontmatter: PyYAML is required")
        return None
    try:
        data = (
            load_unique_yaml_mapping(raw_frontmatter)
            if yaml is not None
            else json.loads(raw_frontmatter)
        )
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
    autonomy_missing = "autonomy" not in role or role.get("autonomy") is None
    autonomy = role.get("autonomy")
    if autonomy_missing:
        result.warn(
            f"{field}.autonomy: missing legacy role autonomy; temporary projection defaults to bounded (中) without modifying the source package",
            code="LEGACY_ROLE_AUTONOMY_DEFAULTED",
            phase="permission",
            path="expert.json",
            location=f"{field}.autonomy",
            root_cause="legacy-role-autonomy-missing",
            remediation=(
                "Choose and persist an explicit autonomy for every role before any "
                "structural modification."
            ),
            evidence="bounded temporary projection",
        )
    elif autonomy not in workflow_autonomy.AUTONOMY_LEVELS:
        result.error(
            f"{field}.autonomy: must be one of {', '.join(workflow_autonomy.AUTONOMY_LEVELS)}",
            code="ROLE_AUTONOMY_INVALID",
            phase="permission",
            path="expert.json",
            location=f"{field}.autonomy",
            root_cause="invalid-role-autonomy",
            remediation="Choose one supported role autonomy value.",
            evidence=str(autonomy),
        )
    runtime_expected_mode = expected_mode
    if expected_mode == "all":
        legacy_mode = role.get("mode", "primary" if autonomy_missing else "all")
        if legacy_mode not in {"primary", "all"}:
            result.error(f"{field}.mode: must be primary or all")
        else:
            runtime_expected_mode = legacy_mode
            if legacy_mode == "primary":
                result.warn(
                    f"{field}.mode: legacy primary remains compatible for read-only validation and install; structural modification must migrate it to all",
                    code="LEGACY_PRIMARY_AGENT_MODE",
                    phase="manifest",
                    path="expert.json",
                    location=f"{field}.mode",
                    root_cause="legacy-primary-agent-mode",
                    remediation="Set the main Agent mode to all during the next structural modification.",
                    evidence="primary",
                )
    elif role.get("mode", expected_mode) != expected_mode:
        result.error(f"{field}.mode: must be {expected_mode}")
    validate_text(role.get("name", role.get("title")), f"{field}.name", result, required=True)
    validate_text(role.get("description"), f"{field}.description", result, required=True)
    validate_avatar(role.get("avatar_url"), f"{field}.avatar_url", result)
    default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
    try:
        contract.normalize_agent_runtime_options(
            role,
            field,
            expected_mode=runtime_expected_mode,
            default_steps=default_steps,
            allow_legacy_sampling=True,
        )
    except contract.ContractError as exc:
        result.error(str(exc))
    for sampling_field in contract.LEGACY_AGENT_SAMPLING_KEYS:
        if sampling_field not in role:
            continue
        location = f"{field}.{sampling_field}"
        if _has_finding(
            result,
            code="LEGACY_AGENT_SAMPLING_FIELD",
            location=location,
        ):
            continue
        result.warn(
            f"{location}: legacy sampling field remains readable; structural "
            "modification must remove it",
            code="LEGACY_AGENT_SAMPLING_FIELD",
            phase="manifest",
            path=MANIFEST_FILE,
            location=location,
            root_cause="legacy-agent-sampling-field",
            remediation=(
                "Remove temperature/top_p and inherit sampling behavior from the "
                "selected model/provider before structural modification."
            ),
            evidence=sampling_field,
        )
    for sampling_path in contract.agent_sampling_option_paths(
        role.get("options"),
        f"{field}.options",
    ):
        if _has_finding(
            result,
            code="LEGACY_AGENT_SAMPLING_FIELD",
            location=sampling_path,
        ):
            continue
        result.warn(
            f"{sampling_path}: legacy nested sampling field remains readable; "
            "structural modification must remove it",
            code="LEGACY_AGENT_SAMPLING_FIELD",
            phase="manifest",
            path=MANIFEST_FILE,
            location=sampling_path,
            root_cause="legacy-agent-sampling-field",
            remediation=(
                "Remove temperature/top_p from options and inherit sampling "
                "behavior from the selected model/provider."
            ),
            evidence=sampling_path,
        )
    role_mcp = validate_string_list(role.get("mcp"), f"{field}.mcp", result)
    duplicate_mcp = contract.first_duplicate(role_mcp)
    if duplicate_mcp is not None:
        result.error(f"{field}.mcp: duplicates {duplicate_mcp}")
    validate_string_list(role.get("route_triggers"), f"{field}.route_triggers", result)
    validate_string_list(role.get("handoff_contract"), f"{field}.handoff_contract", result)
    for resource_field in ("references", "instructions"):
        if resource_field not in role:
            continue
        try:
            contract.normalize_role_aliases(
                role.get(resource_field), f"{field}.{resource_field}"
            )
        except contract.ContractError as exc:
            result.error(str(exc))
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
        primary_id = validate_role(manifest.get("agent"), "agent", result, expected_mode="all")
        return primary_id, []

    if "agent" in manifest:
        result.error("expert.json: type team must use primary_agent and subagents, not agent")
    primary_id = validate_role(manifest.get("primary_agent"), "primary_agent", result, expected_mode="all")
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


def check_role_resource_bindings(
    manifest: dict[str, Any],
    extensions: dict[str, Any],
    result: Result,
) -> None:
    roles = manifest_contract.manifest_roles(manifest)
    references = extensions.get("references", {})
    role_instructions = extensions.get("role_instructions", {})

    def warn_missing_reference_description(alias: str) -> None:
        result.warn(
            f"runtime_extensions.references.{alias}.description: add when assigned roles should use this Reference",
            code="REFERENCE_DESCRIPTION_MISSING",
            phase="manifest",
            path="expert.json",
            location=f"/runtime_extensions/references/{alias}/description",
            root_cause="reference-guidance-missing",
            remediation="Add a short, specific usage description.",
        )

    for alias, entry in references.items():
        if "repository" in entry and "branch" not in entry:
            result.warn(
                f"runtime_extensions.references.{alias}.branch: omitted; OpenCode will use the repository default branch",
                code="REFERENCE_GIT_DEFAULT_BRANCH",
                phase="manifest",
                path="expert.json",
                location=f"/runtime_extensions/references/{alias}/branch",
                root_cause="mutable-git-reference",
                remediation="Confirm a branch or ref when repeatable materialization is required.",
            )

    if references:
        explicit = [field for field, role in roles if "references" in role]
        if not explicit:
            result.warn(
                "role Reference consumers are absent; legacy package-wide behavior remains valid until the next structural migration",
                code="LEGACY_REFERENCE_BINDINGS_IMPLICIT",
                phase="manifest",
                path="expert.json",
                location="/runtime_extensions/references",
                root_cause="legacy-reference-bindings",
                remediation="Preview a migration and assign every Reference alias to explicit role ids before structural changes.",
            )
            for alias, entry in references.items():
                if not entry.get("description", "").strip():
                    warn_missing_reference_description(alias)
        else:
            consumers = {alias: [] for alias in references}
            for field, role in roles:
                if "references" not in role:
                    result.error(f"{field}.references: is required in explicit binding mode")
                    continue
                try:
                    aliases = contract.normalize_role_aliases(
                        role.get("references"), f"{field}.references"
                    )
                except contract.ContractError:
                    continue
                for alias in aliases:
                    if alias not in references:
                        result.error(f"{field}.references: references unknown Reference {alias}")
                    else:
                        consumers[alias].append(role.get("id"))
            for alias, role_ids in consumers.items():
                if not role_ids:
                    result.error(
                        f"runtime_extensions.references.{alias}: must be assigned to at least one role"
                    )
                if role_ids and not references[alias].get("description", "").strip():
                    warn_missing_reference_description(alias)

    if role_instructions:
        consumers = {alias: [] for alias in role_instructions}
        for field, role in roles:
            if "instructions" not in role:
                result.error(f"{field}.instructions: is required when role rules exist")
                continue
            try:
                aliases = contract.normalize_role_aliases(
                    role.get("instructions"), f"{field}.instructions"
                )
            except contract.ContractError:
                continue
            for alias in aliases:
                if alias not in role_instructions:
                    result.error(f"{field}.instructions: references unknown role rule {alias}")
                else:
                    consumers[alias].append(role.get("id"))
        for alias, role_ids in consumers.items():
            if not role_ids:
                result.error(
                    f"runtime_extensions.role_instructions.{alias}: must be assigned to at least one role"
                )


def check_reference_target_capability(
    manifest: dict[str, Any],
    target: manager_contract.TargetContract | None,
    result: Result,
) -> None:
    if target is None:
        return
    runtime_extensions = manifest.get("runtime_extensions", {})
    references = runtime_extensions.get("references", {}) if isinstance(runtime_extensions, dict) else {}
    if not isinstance(references, dict) or not references:
        return
    if target.capability_verified and target.capabilities.get("references") is True:
        return
    git_aliases = sorted(
        alias
        for alias, entry in references.items()
        if isinstance(entry, dict) and "repository" in entry
    )
    if git_aliases:
        result.warn(
            "Git Reference install requires verified Runtime Reference support; blocked aliases: "
            + ", ".join(git_aliases),
            code="REFERENCE_CAPABILITY_MISSING",
            phase="config-load",
            path="opencode.json",
            location="/references",
            root_cause="reference-capability-missing",
            remediation="Provide a host contract with references=true or import a trusted local checkout.",
        )
    local_aliases = sorted(
        alias
        for alias, entry in references.items()
        if isinstance(entry, dict) and "path" in entry
    )
    if local_aliases:
        result.warn(
            "Local References will use role-assigned compatibility Skills because Runtime Reference support is not verified: "
            + ", ".join(local_aliases),
            code="REFERENCE_LOCAL_FALLBACK",
            phase="config-load",
            path="opencode.json",
            location="/references",
            root_cause="reference-capability-unverified",
            remediation="Provide a host contract with references=true to use native Reference projection.",
        )


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


def check_manifest_shape(
    manifest: dict[str, Any],
    result: Result,
    *,
    extensions: dict[str, Any] | None = None,
) -> tuple[str | None, list[str]]:
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

    active_extensions = (
        extensions
        if extensions is not None
        else check_runtime_extensions_manifest(manifest, result)
    )
    check_role_resource_bindings(manifest, active_extensions, result)

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
    primary_role = (
        manifest.get("agent")
        if manifest.get("type") == "expert"
        else manifest.get("primary_agent")
    )
    primary_id = (
        primary_role.get("id")
        if isinstance(primary_role, dict) and isinstance(primary_role.get("id"), str)
        else ""
    )
    try:
        workflow_autonomy.normalize_workflows(
            manifest,
            role_ids=role_ids,
            primary_id=primary_id,
        )
    except workflow_autonomy.WorkflowContractError as exc:
        result.error(str(exc))


def check_files(
    package_dir: Path,
    manifest: dict[str, Any],
    result: Result,
    *,
    extensions: dict[str, Any] | None = None,
) -> None:
    active_extensions = (
        extensions
        if extensions is not None
        else check_runtime_extensions_manifest(manifest, result)
    )
    primary_id, subagent_ids = check_manifest_shape(
        manifest,
        result,
        extensions=active_extensions,
    )
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

    ext = active_extensions
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
        frontmatter = parse_frontmatter(
            path,
            result,
            require_block_yaml=True,
        )
        body = read_markdown_body(path, result)
        if frontmatter is None:
            continue
        mode = skill_contract.schema_mode(manifest)
        relative_path = path.relative_to(package_dir).as_posix()
        skill_contract.add_skill_markdown_issues(
            result,
            [
                *skill_contract.validate_skill_frontmatter(
                    frontmatter,
                    directory_name=skill_name,
                    expected_compatibility="opencode" if mode == "legacy" else None,
                ),
                *skill_contract.skill_markdown_recommendations(
                    len(body.splitlines()),
                    body,
                ),
            ],
            path=relative_path,
        )
        resources = sorted(resources_by_skill.get(skill_name, []))
        if (
            resources
            and mode == "legacy"
            and "资源导航" not in body
        ):
            result.error(f"{path}: generated skill with package_resources must include 资源导航")
        for resource in resources if mode == "legacy" else []:
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
        expected_mode = (
            role.get("mode", "primary" if role.get("autonomy") is None else "all")
            if field in {"agent", "primary_agent"}
            else "all" if field in {"agent", "primary_agent"}
            else "subagent"
        )
        default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
        try:
            return contract.normalize_agent_runtime_options(
                role,
                field,
                expected_mode=expected_mode,
                default_steps=default_steps,
                allow_legacy_sampling=True,
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
            for field in ("steps", *contract.AGENT_READ_RUNTIME_KEYS):
                if field in expected_runtime and fm.get(field) != expected_runtime[field]:
                    result.error(f"{md_path}: {field} must match expert.json")
                elif field not in expected_runtime and field in fm:
                    result.error(f"{md_path}: {field} must be omitted when absent from expert.json")

        runtime_extensions = manifest.get("runtime_extensions", {})
        runtime_extensions = runtime_extensions if isinstance(runtime_extensions, dict) else {}
        references = runtime_extensions.get("references", {})
        references = references if isinstance(references, dict) else {}
        role_instructions = runtime_extensions.get("role_instructions", {})
        role_instructions = role_instructions if isinstance(role_instructions, dict) else {}
        explicit_reference_bindings = any(
            "references" in candidate
            for _field, candidate in manifest_contract.manifest_roles(manifest)
        )
        explicit_bindings = explicit_reference_bindings or bool(role_instructions)
        if explicit_bindings:
            if "分配资料与规则" not in body:
                result.error(f"{md_path}: missing role resource section")
            if explicit_reference_bindings:
                try:
                    assigned_references = set(
                        contract.normalize_role_aliases(
                            role.get("references", []),
                            f"{agent_id}.references",
                        )
                    )
                except contract.ContractError:
                    assigned_references = set()
            else:
                assigned_references = set(references)
            for alias, entry in references.items():
                namespaced = contract.namespaced_reference_alias(str(manifest.get("slug")), alias)
                if alias in assigned_references:
                    if namespaced not in body:
                        result.error(f"{md_path}: missing assigned Reference {alias}")
                    if isinstance(entry, dict) and "path" in entry:
                        fallback_name = contract.reference_fallback_skill_name(
                            str(manifest.get("slug")), alias
                        )
                        if fallback_name not in body:
                            result.error(
                                f"{md_path}: missing local Reference fallback skill {fallback_name}"
                            )
                elif namespaced in body:
                    result.error(f"{md_path}: contains unassigned Reference {alias}")

            try:
                assigned_instructions = set(
                    contract.normalize_role_aliases(
                        role.get("instructions", []),
                        f"{agent_id}.instructions",
                    )
                )
            except contract.ContractError:
                assigned_instructions = set()
            raw_instruction_files = runtime_extensions.get("instruction_files", [])
            instruction_files = (
                raw_instruction_files
                if isinstance(raw_instruction_files, list)
                else []
            )
            instruction_contents: dict[str, str] = {}
            for alias, entry in role_instructions.items():
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    continue
                matching = [
                    item["content"].strip()
                    for item in instruction_files
                    if isinstance(item, dict)
                    and item.get("path") == entry["path"]
                    and isinstance(item.get("content"), str)
                ]
                if matching and matching[0]:
                    instruction_contents[alias] = matching[0]
            assigned_contents = {
                instruction_contents[alias]
                for alias in assigned_instructions
                if alias in instruction_contents
            }
            for alias, entry in role_instructions.items():
                if not isinstance(entry, dict):
                    continue
                path = entry.get("path")
                content = instruction_contents.get(alias)
                if alias in assigned_instructions:
                    if f"`{alias}`" not in body:
                        result.error(f"{md_path}: missing assigned role rule {alias}")
                    if content and content not in body:
                        result.error(f"{md_path}: missing role rule content for {alias}")
                    continue
                if f"`{alias}`" in body:
                    result.error(f"{md_path}: contains unassigned role rule alias {alias}")
                if isinstance(path, str) and path in body:
                    result.error(f"{md_path}: contains unassigned role rule path {alias}")
                if content and content not in assigned_contents and content in body:
                    result.error(f"{md_path}: contains unassigned role rule content {alias}")

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
    primary_role = (
        manifest.get("agent")
        if manifest.get("type") == "expert"
        else manifest.get("primary_agent")
    )
    primary_id = (
        primary_role.get("id")
        if isinstance(primary_role, dict) and isinstance(primary_role.get("id"), str)
        else ""
    )
    try:
        return workflow_autonomy.normalize_workflows(
            manifest,
            role_ids=role_ids,
            primary_id=primary_id,
        )
    except workflow_autonomy.WorkflowContractError:
        return []


LEGACY_V2_AUTONOMY_LABELS = {
    "scripted": "极低：全程照脚本执行，不能自行换方法",
    "fixed": "低：按固定步骤执行，只能处理预设分支",
    "bounded": "中：可在明确边界内选择方法",
    "guided": "高：可根据目标灵活安排，但关键决定需确认",
    "adaptive": "极高：可自主规划、调整和返工，仍受安全与验收标准约束",
}
LEGACY_V2_AUTONOMY_PREFIXES = {
    "scripted": "【自主度：极低】",
    "fixed": "【自主度：低】",
    "bounded": "【自主度：中】",
    "guided": "【自主度：高】",
    "adaptive": "【自主度：极高】",
}
LEGACY_V2_MAX_AUTONOMY_PREFIXES = {
    "scripted": "【最高生效自主度：极低】",
    "fixed": "【最高生效自主度：低】",
    "bounded": "【最高生效自主度：中】",
    "guided": "【最高生效自主度：高】",
    "adaptive": "【最高生效自主度：极高】",
}


def uses_full_legacy_role_contract(manifest: dict[str, Any]) -> bool:
    """Return whether the manifest uses Manager 2.0's complete role contract."""

    roles = roles_from_manifest(manifest)
    if not roles or any("autonomy" in role for role in roles):
        return False
    primary = (
        manifest.get("agent")
        if manifest.get("type") == "expert"
        else manifest.get("primary_agent")
    )
    return isinstance(primary, dict) and primary.get("mode", "primary") == "primary"


def render_legacy_v2_workflow_projection(text: str) -> str:
    """Convert a modern deterministic Workflow projection to Manager 2.0 labels."""

    replacements: dict[str, str] = {}
    for level in workflow_autonomy.AUTONOMY_LEVELS:
        replacements[workflow_autonomy.AUTONOMY_LABELS[level]] = (
            LEGACY_V2_AUTONOMY_LABELS[level]
        )
        replacements[workflow_autonomy.AUTONOMY_PREFIXES[level]] = (
            LEGACY_V2_AUTONOMY_PREFIXES[level]
        )
        replacements[workflow_autonomy.MAX_AUTONOMY_PREFIXES[level]] = (
            LEGACY_V2_MAX_AUTONOMY_PREFIXES[level]
        )
    pattern = re.compile(
        "|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True))
    )
    return pattern.sub(lambda match: replacements[match.group(0)], text)


def check_workflow_projection_parity(
    package_dir: Path,
    manifest: dict[str, Any],
    result: Result,
) -> None:
    workflows = normalized_autonomy_workflows(manifest)
    if not workflow_autonomy.has_autonomy_contract(workflows):
        return
    roles = roles_from_manifest(manifest)
    primary = (
        manifest.get("agent")
        if manifest.get("type") == "expert"
        else manifest.get("primary_agent")
    )
    if primary is None:
        return

    legacy_v2 = uses_full_legacy_role_contract(manifest)
    all_projection = workflow_autonomy.render_all_workflows(workflows)
    if legacy_v2:
        all_projection = render_legacy_v2_workflow_projection(all_projection)
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
        if legacy_v2:
            role_projection = render_legacy_v2_workflow_projection(role_projection)
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
        expected_subtask = contract.expert_runtime_projection_policy()["command"][
            "subtask"
        ]
        expected_subtask_text = str(expected_subtask).lower()
        expected = renderers.render_frontmatter(
            {
                "description": workflow_autonomy.workflow_command_description(workflow),
                "agent": primary["id"],
                "subtask": expected_subtask,
            },
            workflow_autonomy.render_workflow_command(workflow),
        )
        legacy_expected_variants = [
            (
                renderers.render_frontmatter(
                    {
                        "description": workflow_autonomy.workflow_command_description(
                            workflow
                        ),
                        "agent": primary["id"],
                    },
                    workflow_autonomy.render_workflow_command(workflow),
                ),
                "subtask missing",
            ),
            (
                renderers.render_frontmatter(
                    {
                        "description": workflow_autonomy.workflow_command_description(
                            workflow
                        ),
                        "agent": primary["id"],
                        "subtask": False,
                    },
                    workflow_autonomy.render_workflow_command(workflow),
                ),
                "subtask=false",
            ),
        ]
        if legacy_v2:
            expected = render_legacy_v2_workflow_projection(expected)
            legacy_expected_variants = [
                (render_legacy_v2_workflow_projection(value), evidence)
                for value, evidence in legacy_expected_variants
            ]
        command_path = package_dir / EXPERT_DIR / COMMANDS_SUBDIR / f"{command['name']}.md"
        if command_path.exists():
            actual = read_markdown_body(command_path, result)
            legacy_evidence = next(
                (
                    evidence
                    for legacy_value, evidence in legacy_expected_variants
                    if actual == legacy_value and actual != expected
                ),
                None,
            )
            if legacy_evidence is not None:
                result.warn(
                    f"{command_path}: legacy workflow command does not use subtask "
                    f"{expected_subtask_text}; "
                    "structural modification must regenerate it",
                    code="LEGACY_COMMAND_ROUTING",
                    phase="workflow",
                    path=str(command_path),
                    location="frontmatter.subtask",
                    root_cause="legacy-command-routing",
                    remediation=(
                        "Regenerate the workflow command with subtask "
                        f"{expected_subtask_text}."
                    ),
                    evidence=legacy_evidence,
                )
            elif actual != expected:
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
            normalized_role["autonomy"] = role.get("autonomy")
            tools = role.get("tools", {})
            if not isinstance(tools, dict):
                continue
            expected, audit = permission_policy.build_role_permission(
                normalized_role,
                workflows=workflows,
                manifest_mode=skill_contract.schema_mode(manifest),
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
        legacy_role_autonomy = role.get("autonomy") is None
        config_agent = config_agents.get(role_id)
        if (
            not legacy_role_autonomy
            and isinstance(config_agent, dict)
            and config_agent.get("permission") != expected
        ):
            result.error(
                f"{RUNTIME_CONFIG}: agent.{role_id}.permission must match the autonomy-derived policy"
            )
        if legacy_role_autonomy:
            continue
        for expected_text in (
            audit["source"],
            audit["effective"],
            audit["label"],
        ):
            if expected_text not in readme:
                message = (
                    f"README.md: Agent permission baseline for {role_id} "
                    "differs from expert.json"
                )
                result.error(message)
                break


def check_runtime_config(
    package_dir: Path,
    config: dict[str, Any],
    manifest: dict[str, Any],
    result: Result,
    *,
    extensions: dict[str, Any] | None = None,
) -> list[tuple[int, plugin_contract.NpmPluginSpec]]:
    active_extensions = (
        extensions
        if extensions is not None
        else check_runtime_extensions_manifest(manifest, result)
    )
    config_npm_plugins, config_npm_plugins_valid = (
        _parse_runtime_config_npm_plugins(config, result)
    )
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
        return config_npm_plugins

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

    subagent_count = sum(1 for data in agents.values() if isinstance(data, dict) and data.get("mode") == "subagent")
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

    ext = active_extensions
    manifest_npm_plugins = ext.get("npm_plugin_specs", [])
    if manifest_npm_plugins:
        manifest_keys = [item["canonicalKey"] for item in manifest_npm_plugins]
        config_keys = [item["canonicalKey"] for _index, item in config_npm_plugins]
        if config_npm_plugins_valid and config_keys != manifest_keys:
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
        expected_mode = (
            role.get("mode", "primary" if role.get("autonomy") is None else "all")
            if agent_id == primary_id
            else "subagent"
        )
        if config_agent.get("mode") != expected_mode:
            result.error(f"{RUNTIME_CONFIG}: agent.{agent_id}.mode must be {expected_mode}")
        if fm and fm.get("mode") != expected_mode:
            result.error(f"{md_path}: mode must be {expected_mode}")
        for field in ("steps", *contract.AGENT_READ_RUNTIME_KEYS):
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
    return config_npm_plugins


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
    input_snapshot: safe_input.InputSnapshot | None = None,
) -> Result:
    if input_snapshot is None:
        try:
            input_snapshot = safe_input.inspect_package(package_dir)
        except safe_input.InputInspectionError as error:
            result = Result(input_error=error, target=target)
        else:
            if input_snapshot.kind == "directory":
                with tempfile.TemporaryDirectory(
                    prefix="mobilework-package-validation-snapshot-"
                ) as temp:
                    staged_package = input_snapshot.materialize(
                        Path(temp) / (package_dir.name or "package")
                    )
                    return validate_package(
                        staged_package,
                        target=target,
                        input_snapshot=input_snapshot,
                    )
            result = Result(input_snapshot=input_snapshot, target=target)
    else:
        result = Result(input_snapshot=input_snapshot, target=target)
    if result.input_inspection_error is not None:
        if result.input_inspection_error.code != "INPUT_NOT_FOUND":
            return result.block_input_preflight()
        result.error(f"package directory does not exist: {package_dir}")
        result.finalize_contract()
        return result
    if result.input_snapshot is None or result.input_snapshot.kind != "directory":
        result.error(
            f"package input must be a directory: {package_dir.name}",
            code="PACKAGE_INPUT_NOT_DIRECTORY",
            phase="input-preflight",
            path=package_dir.name,
            root_cause="invalid-package-input-kind",
            remediation="Provide the extracted expert package directory.",
            evidence=package_dir.name,
        )
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

    check_reference_target_capability(manifest, target, result)

    extensions = check_runtime_extensions_manifest(manifest, result)
    check_declared_file_allowlist(package_dir, manifest, result)
    check_gitignore(package_dir, manifest, result)
    check_files(package_dir, manifest, result, extensions=extensions)
    check_avatar_assets(package_dir, manifest, result)
    check_agent_markdown_shape(package_dir, manifest, result)
    check_skill_markdown_shape(package_dir, manifest, result)
    check_readme_shape(package_dir, manifest, result)
    check_workflow_projection_parity(package_dir, manifest, result)
    config_npm_plugins = check_runtime_config(
        package_dir,
        config,
        manifest,
        result,
        extensions=extensions,
    )
    check_permission_policy(package_dir, manifest, config, result)
    check_env_example(package_dir, config, result)
    check_static_syntax(package_dir, result)
    supply_chain_audit.add_to_result(
        result,
        package_dir,
        manifest,
        config,
        parsed_plugins=config_npm_plugins,
    )
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


def _legacy_main() -> int:
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


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "validate-expert",
        _legacy_main,
        argv=argv,
        default_format="human",
        delegated_output_flags=("format", "schema-version"),
    )


if __name__ == "__main__":
    sys.exit(main())
