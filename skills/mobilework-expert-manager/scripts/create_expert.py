#!/usr/bin/env python3
"""Generate a MobileWork expert or expert-team package from expert.json."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, cast

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import package_contract as contract
import execution_context
import gitignore_contract
import manifest_contract
import permission_policy
import renderers
import skill_contract
import workflow_autonomy


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$|^(primary|secondary|accent|success|warning|error|info)$")
AVATAR_RE = re.compile(r"^(https://[^\s]+|[A-Za-z0-9._/-]+\.(?:png|jpg|jpeg|webp|gif|svg))$", re.IGNORECASE)
HTTP_AVATAR_RE = re.compile(r"^https://", re.IGNORECASE)
DEFAULT_COLOR = "primary"
ACTION_VALUES = {"allow", "ask", "deny"}
EXPERT_DIR = contract.PACKAGE_RUNTIME_DIR
AGENTS_SUBDIR = "agents"
SKILLS_SUBDIR = "skills"
COMMANDS_SUBDIR = "commands"
TOOLS_SUBDIR = "tools"
PLUGINS_SUBDIR = "plugins"
AVATARS_DIR = "avatars"
REFERENCES_DIR = contract.REFERENCES_SUBDIR
INSTRUCTIONS_DIR = contract.INSTRUCTIONS_SUBDIR
MANIFEST_FILE = "expert.json"
RUNTIME_CONFIG = "opencode.json"
REQUIRED_GENERATED_FILES = (MANIFEST_FILE, "README.md", RUNTIME_CONFIG)
CONTROLLED_TARGET_ENV = "MOBILEWORK_EXPERT_MANAGER_TARGET"
PACKAGE_LOCK_SUFFIX = ".mobilework.lock"
PACKAGE_LOCK_OWNER = "owner.json"
PACKAGE_LOCK_TIMEOUT_SECONDS = 30.0
AVATAR_PALETTE = [
    "#2563eb",
    "#0f766e",
    "#c2410c",
    "#7c3aed",
    "#be123c",
    "#047857",
    "#b45309",
    "#4338ca",
]


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def normalized_output_dir(output_dir: Path | None) -> Path:
    """Resolve the only output root allowed by the current host contract."""
    try:
        return execution_context.resolve_execution_context(
            requested_output_dir=output_dir,
        ).output_root
    except execution_context.ExecutionContextError as error:
        fail(f"{error.code}: {error}")


def package_lock_path(output_root: Path, slug: str) -> Path:
    return output_root / f".{slug}{PACKAGE_LOCK_SUFFIX}"


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_lock_owner(lock_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((lock_path / PACKAGE_LOCK_OWNER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


@contextlib.contextmanager
def package_lock(output_root: Path, slug: str):
    """Share a sibling package lock with MobileWork's projection synchronizer."""
    lock_path = package_lock_path(output_root, slug)
    started_at = time.monotonic()
    owner_token = f"{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex}"
    while True:
        try:
            lock_path.mkdir()
        except FileExistsError:
            owner = read_lock_owner(lock_path)
            owner_pid = owner.get("pid") if owner else None
            if isinstance(owner_pid, int) and owner_pid > 0 and not process_is_alive(owner_pid):
                shutil.rmtree(lock_path, ignore_errors=True)
                continue
            if time.monotonic() - started_at >= PACKAGE_LOCK_TIMEOUT_SECONDS:
                fail(f"timed out waiting for expert package lock: {slug}")
            time.sleep(0.05)
            continue
        try:
            (lock_path / PACKAGE_LOCK_OWNER).write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "token": owner_token,
                        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(lock_path, ignore_errors=True)
            raise
        break
    try:
        yield
    finally:
        owner = read_lock_owner(lock_path)
        if owner and owner.get("token") == owner_token:
            shutil.rmtree(lock_path, ignore_errors=True)


def calculate_package_revision(package_dir: Path) -> str:
    """Match the desktop synchronizer's deterministic package revision hash."""
    digest = hashlib.sha256()
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(package_dir, followlinks=False):
        current = Path(current_root)
        for directory in list(dir_names):
            candidate = current / directory
            if directory in {"__pycache__", ".git"}:
                dir_names.remove(directory)
            elif candidate.is_symlink():
                fail(f"expert package cannot contain symlink: {candidate.relative_to(package_dir)}")
        for file_name in file_names:
            if file_name == ".DS_Store" or file_name.endswith(".pyc"):
                continue
            candidate = current / file_name
            if candidate.is_symlink():
                fail(f"expert package cannot contain symlink: {candidate.relative_to(package_dir)}")
            files.append(candidate)
    for file_path in sorted(files, key=lambda item: item.relative_to(package_dir).as_posix()):
        relative = file_path.relative_to(package_dir).as_posix().encode("utf-8")
        data = file_path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(len(data)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def validate_generated_project(project_dir: Path, output_dir: Path, slug: str) -> None:
    expected = execution_context.canonical_path(output_dir / slug)
    actual = execution_context.canonical_path(project_dir)
    if actual != expected:
        fail(f"generated project directory mismatch: expected {expected}, got {actual}")
    for relative_path in REQUIRED_GENERATED_FILES:
        if not (actual / relative_path).is_file():
            fail(f"missing required generated file: {relative_path}")
    generated_json: dict[str, Any] = {}
    for relative_path in (MANIFEST_FILE, RUNTIME_CONFIG):
        try:
            generated_json[relative_path] = json.loads(
                (actual / relative_path).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            fail(f"invalid generated JSON: {relative_path}")
    generated_manifest = generated_json[MANIFEST_FILE]
    if not isinstance(generated_manifest, dict) or generated_manifest.get("slug") != slug:
        fail(f"generated expert.json slug mismatch: expected {slug}")


def validate_slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        fail(f"{field} must match ^[a-z0-9]+(-[a-z0-9]+)*$")
    if len(value) > 64:
        fail(f"{field} must be 64 characters or fewer")
    return value


def text_list(values: Any, field: str, *, default: list[str] | None = None) -> list[str]:
    if values is None:
        return list(default or [])
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        fail(f"{field} must be a list of strings")
    return values


def optional_text(value: Any, field: str, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        fail(f"{field} must be a string")
    return value


def validate_avatar_url(value: Any, field: str) -> str:
    avatar_url = optional_text(value, field)
    if avatar_url and not AVATAR_RE.fullmatch(avatar_url):
        fail(f"{field} must be an https URL or a supported relative image path")
    if avatar_url and not is_remote_avatar(avatar_url):
        validate_local_avatar_path(avatar_url, field)
    return avatar_url


def is_remote_avatar(avatar_url: str) -> bool:
    return bool(HTTP_AVATAR_RE.match(avatar_url))


def validate_local_avatar_path(avatar_url: str, field: str) -> Path:
    try:
        normalized = contract.posix_relative_path(avatar_url, field)
    except contract.ContractError as exc:
        fail(str(exc))
    path = Path(normalized)
    if not path.name:
        fail(f"{field} must point to an image file")
    if path.suffix.lower() not in contract.AVATAR_SUFFIXES:
        fail(f"{field} must use a supported image suffix")
    return path


def default_avatar_path(identifier: str) -> str:
    return f"{AVATARS_DIR}/{identifier}.svg"


def copied_avatar_path(identifier: str, avatar_url: str) -> str:
    path = validate_local_avatar_path(avatar_url, "avatar_url")
    if path.parts and path.parts[0] == AVATARS_DIR:
        return path.as_posix()
    return f"{AVATARS_DIR}/{identifier}{path.suffix.lower()}"


def avatar_label(display_name: str, fallback: str) -> str:
    compact = "".join(char for char in display_name.strip() if char.isalnum())
    if compact:
        return compact[:2].upper()
    return fallback[:2].upper()


def avatar_color(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return AVATAR_PALETTE[digest[0] % len(AVATAR_PALETTE)]


def render_placeholder_avatar(identifier: str, display_name: str) -> bytes:
    label = html.escape(avatar_label(display_name, identifier))
    title = html.escape(display_name or identifier)
    color = avatar_color(identifier)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="{title}">
  <rect width="256" height="256" rx="48" fill="{color}"/>
  <circle cx="196" cy="48" r="56" fill="#ffffff" opacity="0.18"/>
  <circle cx="64" cy="208" r="72" fill="#000000" opacity="0.12"/>
  <text x="128" y="148" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="72" font-weight="700" fill="#ffffff">{label}</text>
</svg>
"""
    return svg.encode("utf-8")


def read_local_avatar(manifest_dir: Path, avatar_url: str) -> bytes | None:
    source_path = manifest_dir / validate_local_avatar_path(avatar_url, "avatar_url")
    if source_path.is_symlink():
        fail(f"avatar_url must not reference a symlink: {avatar_url}")
    if not source_path.is_file():
        return None
    content = source_path.read_bytes()
    try:
        contract.validate_avatar_bytes(content, source_path.suffix, "avatar_url")
    except contract.ContractError as exc:
        fail(str(exc))
    return content


def add_avatar_asset(
    assets: dict[str, bytes],
    target: str,
    content: bytes,
    *,
    conflict_suffix: str,
) -> str:
    existing = assets.get(target)
    if existing is None or existing == content:
        assets[target] = content
        return target

    path = Path(target)
    stem = (path.parent / path.stem).as_posix()
    suffix = path.suffix
    candidate = f"{stem}-{conflict_suffix}{suffix}"
    counter = 2
    while candidate in assets and assets[candidate] != content:
        candidate = f"{stem}-{conflict_suffix}-{counter}{suffix}"
        counter += 1
    assets[candidate] = content
    return candidate


def prepare_avatar_assets(manifest: dict[str, Any], manifest_dir: Path) -> None:
    assets: dict[str, bytes] = {}
    source_manifest = manifest["source_manifest"]

    def prepare_slot(
        current_url: str,
        *,
        identifier: str,
        display_name: str,
        conflict_suffix: str,
        source_container: dict[str, Any],
    ) -> str:
        if current_url and is_remote_avatar(current_url):
            return current_url
        if current_url:
            existing = read_local_avatar(manifest_dir, current_url)
            if existing is not None:
                target = copied_avatar_path(identifier, current_url)
                target = add_avatar_asset(assets, target, existing, conflict_suffix=conflict_suffix)
                source_container["avatar_url"] = target
                return target
        target = default_avatar_path(identifier)
        target = add_avatar_asset(
            assets,
            target,
            render_placeholder_avatar(identifier, display_name),
            conflict_suffix=conflict_suffix,
        )
        source_container["avatar_url"] = target
        return target

    manifest["avatar_url"] = prepare_slot(
        manifest["avatar_url"],
        identifier=manifest["slug"],
        display_name=manifest["name"],
        conflict_suffix="package",
        source_container=source_manifest,
    )

    if manifest["type"] == "expert":
        source_primary = source_manifest.setdefault("agent", {})
    else:
        source_primary = source_manifest.setdefault("primary_agent", {})
    primary = manifest["primary_agent"]
    primary["avatar_url"] = prepare_slot(
        primary["avatar_url"],
        identifier=primary["id"],
        display_name=primary["display_name"],
        conflict_suffix="agent",
        source_container=source_primary,
    )

    if manifest["type"] == "team":
        source_subagents = source_manifest.setdefault("subagents", [])
        for index, sub in enumerate(manifest["subagents"]):
            if index >= len(source_subagents) or not isinstance(source_subagents[index], dict):
                fail(f"subagents[{index}] must be a mapping")
            sub["avatar_url"] = prepare_slot(
                sub["avatar_url"],
                identifier=sub["id"],
                display_name=sub["display_name"],
                conflict_suffix="agent",
                source_container=source_subagents[index],
            )

    manifest["avatar_assets"] = assets


def write_avatar_assets(project_dir: Path, manifest: dict[str, Any]) -> None:
    (project_dir / AVATARS_DIR).mkdir(parents=True, exist_ok=True)
    for relative_path, content in manifest.get("avatar_assets", {}).items():
        target = project_dir / validate_local_avatar_path(relative_path, "avatar asset")
        try:
            contract.validate_avatar_bytes(content, target.suffix, f"avatar asset {relative_path}")
        except contract.ContractError as exc:
            fail(str(exc))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def validate_permission_value(value: Any, field: str) -> Any:
    if isinstance(value, str):
        if value not in ACTION_VALUES:
            fail(f"{field} must be allow, ask, or deny")
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                fail(f"{field} keys must be strings")
            result[key] = validate_permission_value(nested, f"{field}.{key}")
        return result
    fail(f"{field} must be allow/ask/deny or a mapping")


def normalize_permission(raw: Any, field: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        fail(f"{field} must be a mapping")
    return {key: validate_permission_value(value, f"{field}.{key}") for key, value in raw.items()}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        fail("manifest root must be a JSON object")
    return data


def dump_yaml(data: dict[str, Any]) -> str:
    return renderers.dump_yaml(data)


def validate_package_file_path(
    value: Any,
    field: str,
    *,
    allowed_suffixes: set[str] | None = None,
    required_prefix: str | None = None,
    allow_glob: bool = False,
) -> str:
    try:
        normalized = contract.posix_relative_path(value, field, allow_glob=allow_glob)
    except contract.ContractError as exc:
        fail(str(exc))
    path = Path(normalized)
    if required_prefix and not normalized.startswith(required_prefix.rstrip("/") + "/"):
        fail(f"{field} must be under {required_prefix}/")
    if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
        fail(f"{field} must use one of these suffixes: {', '.join(sorted(allowed_suffixes))}")
    return normalized


def validate_text_resource_list(
    raw: Any,
    field: str,
    *,
    required_prefix: str,
) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        fail(f"{field} must be a list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            fail(f"{field}[{index}] must be a mapping")
        unknown = sorted(set(item) - {"path", "content"})
        if unknown:
            fail(f"{field}[{index}] contains unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(
            item.get("path"),
            f"{field}[{index}].path",
            required_prefix=required_prefix,
        )
        content = optional_text(item.get("content"), f"{field}[{index}].content")
        if not content.strip():
            fail(f"{field}[{index}].content must be non-empty")
        if path in seen:
            fail(f"{field}[{index}].path duplicates {path}")
        seen.add(path)
        result.append({"path": path, "content": content})
    return result


def resource_paths(resources: list[dict[str, str]]) -> set[str]:
    return {item["path"] for item in resources}


def normalize_commands(raw: Any, *, agent_ids: set[str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        fail("runtime_extensions.commands must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            fail(f"runtime_extensions.commands[{index}] must be a mapping")
        unknown = sorted(set(item) - {"name", "template", "description", "agent", "subtask", "model"})
        if unknown:
            fail(f"runtime_extensions.commands[{index}] contains unsupported fields: {', '.join(unknown)}")
        name = validate_slug(item.get("name"), f"runtime_extensions.commands[{index}].name")
        if name in seen:
            fail(f"runtime_extensions.commands[{index}].name duplicates {name}")
        seen.add(name)
        template = optional_text(item.get("template"), f"runtime_extensions.commands[{index}].template")
        if not template.strip():
            fail(f"runtime_extensions.commands[{index}].template must be non-empty")
        command: dict[str, Any] = {
            "name": name,
            "template": template,
            "description": optional_text(item.get("description"), f"runtime_extensions.commands[{index}].description"),
        }
        if item.get("agent") is not None:
            command_agent = validate_slug(
                item.get("agent"),
                f"runtime_extensions.commands[{index}].agent",
            )
            if command_agent not in agent_ids:
                fail(
                    f"runtime_extensions.commands[{index}].agent references "
                    f"undeclared agent {command_agent}"
                )
            command["agent"] = command_agent
        if item.get("subtask") is not None:
            if not isinstance(item["subtask"], bool):
                fail(f"runtime_extensions.commands[{index}].subtask must be a boolean")
            command["subtask"] = item["subtask"]
        if item.get("model") is not None:
            try:
                command["model"] = contract.normalize_provider_model(
                    item.get("model"),
                    f"runtime_extensions.commands[{index}].model",
                )
            except contract.ContractError as exc:
                fail(str(exc))
        result.append(command)
    return result


def normalize_embedded_files(
    raw: Any,
    field: str,
    *,
    allowed_suffixes: set[str],
) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        fail(f"{field} must be a list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            fail(f"{field}[{index}] must be a mapping")
        unknown = sorted(set(item) - {"path", "content"})
        if unknown:
            fail(f"{field}[{index}] contains unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(
            item.get("path"),
            f"{field}[{index}].path",
            allowed_suffixes=allowed_suffixes,
        )
        content = optional_text(item.get("content"), f"{field}[{index}].content")
        if not content.strip():
            fail(f"{field}[{index}].content must be non-empty")
        if path in seen:
            fail(f"{field}[{index}].path duplicates {path}")
        seen.add(path)
        result.append({"path": path, "content": content})
    return result


def validate_package_json(raw: Any) -> dict[str, Any]:
    try:
        return contract.normalize_package_dependencies(
            raw,
            "runtime_extensions.plugins.package_json",
        )
    except contract.ContractError as exc:
        fail(str(exc))


def normalize_plugins(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"npm": [], "local": [], "package_json": {}}
    if not isinstance(raw, dict):
        fail("runtime_extensions.plugins must be a mapping")
    unknown = sorted(set(raw) - {"npm", "local", "package_json"})
    if unknown:
        fail(f"runtime_extensions.plugins contains unsupported fields: {', '.join(unknown)}")
    npm = text_list(raw.get("npm"), "runtime_extensions.plugins.npm")
    duplicate_npm = contract.first_duplicate(npm)
    if duplicate_npm is not None:
        fail(f"runtime_extensions.plugins.npm duplicates {duplicate_npm}")
    for index, package_name in enumerate(npm):
        if not package_name.strip() or any(char.isspace() for char in package_name):
            fail(f"runtime_extensions.plugins.npm[{index}] must be a non-empty package name")
    return {
        "npm": npm,
        "local": normalize_embedded_files(
            raw.get("local"),
            "runtime_extensions.plugins.local",
            allowed_suffixes={".js", ".ts"},
        ),
        "package_json": validate_package_json(raw.get("package_json")),
    }


def normalize_references(raw: Any, slug: str, reference_file_paths: set[str]) -> dict[str, Any]:
    try:
        return contract.normalize_reference_entries(
            raw,
            "runtime_extensions.references",
            slug=slug,
            reference_file_paths=reference_file_paths,
        )
    except contract.ContractError as exc:
        fail(str(exc))


def normalize_instructions(raw: Any, slug: str, local_file_paths: set[str]) -> list[str]:
    values = text_list(raw, "runtime_extensions.instructions")
    duplicate_instruction = contract.first_duplicate(values)
    if duplicate_instruction is not None:
        fail(f"runtime_extensions.instructions duplicates {duplicate_instruction}")
    result: list[str] = []
    for index, value in enumerate(values):
        if not value.strip():
            fail(f"runtime_extensions.instructions[{index}] must be non-empty")
        if value.lower().startswith("https://"):
            print(
                f"warning: runtime_extensions.instructions[{index}] is remote and not reproducible: {value}",
                file=sys.stderr,
            )
            result.append(value)
            continue
        if re.match(r"^http://", value, re.IGNORECASE):
            fail(f"runtime_extensions.instructions[{index}] must use https, not http")
        path = validate_package_file_path(
            value,
            f"runtime_extensions.instructions[{index}]",
            allow_glob=True,
        )
        expected = contract.instruction_prefix(slug) + "/"
        if not path.startswith(expected):
            fail(f"runtime_extensions.instructions[{index}] must be under {contract.instruction_prefix(slug)}/")
        if not contract.package_glob_matches(path, local_file_paths):
            fail(f"runtime_extensions.instructions[{index}] has no matching instruction_files entry")
        result.append(path)
    return result


def normalize_lsp(raw: Any) -> bool | dict[str, Any] | None:
    try:
        return contract.normalize_lsp_config(raw)
    except contract.ContractError as exc:
        fail(str(exc))


def normalize_runtime_extensions(
    raw: Any,
    slug: str,
    *,
    agent_ids: set[str],
) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        fail("runtime_extensions must be a mapping")
    unknown = sorted(
        set(raw)
        - {
            "commands", "custom_tools", "plugins", "reference_files",
            "instruction_files", "references", "instructions", "lsp",
        }
    )
    if unknown:
        fail(f"runtime_extensions contains unsupported fields: {', '.join(unknown)}")
    reference_files = validate_text_resource_list(
        raw.get("reference_files"),
        "runtime_extensions.reference_files",
        required_prefix=f"{EXPERT_DIR}/{REFERENCES_DIR}/{slug}",
    )
    instruction_files = validate_text_resource_list(
        raw.get("instruction_files"),
        "runtime_extensions.instruction_files",
        required_prefix=f"{EXPERT_DIR}/{INSTRUCTIONS_DIR}/{slug}",
    )
    reference_file_paths = resource_paths(reference_files)
    instruction_file_paths = resource_paths(instruction_files)
    return {
        "commands": normalize_commands(raw.get("commands"), agent_ids=agent_ids),
        "custom_tools": normalize_embedded_files(
            raw.get("custom_tools"),
            "runtime_extensions.custom_tools",
            allowed_suffixes={".js", ".ts"},
        ),
        "plugins": normalize_plugins(raw.get("plugins")),
        "reference_files": reference_files,
        "instruction_files": instruction_files,
        "references": normalize_references(raw.get("references"), slug, reference_file_paths),
        "instructions": normalize_instructions(
            raw.get("instructions"),
            slug,
            instruction_file_paths,
        ),
        "lsp": normalize_lsp(raw.get("lsp")),
    }


def normalize_package_resources(
    raw: Any,
    *,
    declared_skills: set[str],
    manifest_dir: Path,
    skill_mode: str,
) -> tuple[list[dict[str, str]], dict[str, bytes]]:
    if raw is None:
        return [], {}
    if not isinstance(raw, list):
        fail("package_resources must be a list")
    normalized: list[dict[str, str]] = []
    assets: dict[str, bytes] = {}
    seen: set[str] = set()
    prefix = f"{EXPERT_DIR}/{SKILLS_SUBDIR}/"
    for index, item in enumerate(raw):
        field = f"package_resources[{index}]"
        if not isinstance(item, dict):
            fail(f"{field} must be a mapping")
        unknown = sorted(set(item) - {"path", "kind", "sha256"})
        if unknown:
            fail(f"{field} contains unsupported fields: {', '.join(unknown)}")
        path = validate_package_file_path(item.get("path"), f"{field}.path", required_prefix=prefix.rstrip("/"))
        parts = Path(path).parts
        if len(parts) < 4 or parts[2] not in declared_skills:
            fail(f"{field}.path must be inside a declared supplemental skill")
        if Path(path).name == "SKILL.md" and skill_mode == "legacy":
            fail(f"{field}.path must not declare generated SKILL.md")
        if path in seen:
            fail(f"{field}.path duplicates {path}")
        seen.add(path)
        kind = item.get("kind")
        if kind not in {"text", "binary"}:
            fail(f"{field}.kind must be text or binary")
        source = manifest_dir / path
        if source.is_symlink():
            fail(f"{field}.path must not reference a symlink")
        if not source.is_file():
            fail(f"{field}.path source file does not exist: {source}")
        content = source.read_bytes()
        if kind == "text":
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                fail(f"{field}.path must contain UTF-8 text")
        digest = contract.sha256_bytes(content)
        provided = item.get("sha256")
        if skill_mode == "unified" and provided is None:
            fail(f"{field}.sha256 is required for unified skill files")
        if provided is not None and (not isinstance(provided, str) or not contract.SHA256_RE.fullmatch(provided)):
            fail(f"{field}.sha256 must be a lowercase SHA-256 digest")
        if provided is not None and provided != digest:
            fail(f"{field}.sha256 does not match source file; expected {digest}")
        normalized.append({"path": path, "kind": kind, "sha256": digest})
        assets[path] = content
    return normalized, assets


def normalize_mcp(raw: Any) -> dict[str, dict[str, Any]]:
    try:
        return contract.normalize_mcp_servers(raw)
    except contract.ContractError as exc:
        fail(str(exc))


def normalize_role(
    raw: Any,
    field: str,
    *,
    expected_mode: str,
    default_max_turns: int,
    skill_mode: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        fail(f"{field} must be a mapping")
    role_id = validate_slug(raw.get("id"), f"{field}.id")
    mode = raw.get("mode", expected_mode)
    if mode != expected_mode:
        fail(f"{field}.mode must be {expected_mode}")
    display_name = raw.get("name", raw.get("title", role_id.replace("-", " ").title()))
    if not isinstance(display_name, str):
        fail(f"{field}.name must be a string")
    role_display_name = optional_text(raw.get("display_name"), f"{field}.display_name", default=display_name)
    profession = optional_text(raw.get("profession"), f"{field}.profession", default=display_name)
    description = optional_text(raw.get("description"), f"{field}.description", default=f"{role_id} expert agent")
    color = raw.get("color", DEFAULT_COLOR)
    if not isinstance(color, str) or not COLOR_RE.fullmatch(color):
        fail(f"{field}.color must be a hex color or supported theme color")
    try:
        runtime_options = contract.normalize_agent_runtime_options(
            raw,
            field,
            expected_mode=expected_mode,
            default_steps=default_max_turns,
        )
    except contract.ContractError as exc:
        fail(str(exc))
    try:
        role_skills = (
            skill_contract.normalize_role_refs(raw.get("skills"), f"{field}.skills")
            if skill_mode == "unified"
            else contract.skill_purposes(raw.get("skills"), f"{field}.skills")
        )
    except contract.ContractError as exc:
        fail(str(exc))
    role = {
        "id": role_id,
        "mode": mode,
        "name": display_name,
        "title": display_name,
        "display_name": role_display_name,
        "profession": profession,
        "description": description,
        "avatar_url": validate_avatar_url(raw.get("avatar_url"), f"{field}.avatar_url"),
        "color": color,
        **runtime_options,
        "responsibilities": text_list(raw.get("responsibilities"), f"{field}.responsibilities"),
        "workflow": text_list(raw.get("workflow"), f"{field}.workflow"),
        "quality_gates": text_list(raw.get("quality_gates"), f"{field}.quality_gates"),
        "route_triggers": text_list(raw.get("route_triggers"), f"{field}.route_triggers"),
        "handoff_contract": text_list(raw.get("handoff_contract"), f"{field}.handoff_contract"),
        "skills" if skill_mode == "unified" else "skill_purposes": role_skills,
        "mcp": text_list(raw.get("mcp"), f"{field}.mcp"),
        "custom_tools": text_list(raw.get("custom_tools"), f"{field}.custom_tools"),
        "permission": normalize_permission(raw.get("permission"), f"{field}.permission"),
        "permission_reason": optional_text(
            raw.get("permission_reason"), f"{field}.permission_reason"
        ),
        "tools": raw.get("tools", {}),
    }
    if not isinstance(role["tools"], dict):
        fail(f"{field}.tools must be a mapping")
    for mcp_name in role["mcp"]:
        validate_slug(mcp_name, f"{field}.mcp[]")
    duplicate_mcp = contract.first_duplicate(role["mcp"])
    if duplicate_mcp is not None:
        fail(f"{field}.mcp duplicates {duplicate_mcp}")
    return role


def normalize_manifest(raw: dict[str, Any], *, manifest_dir: Path | None = None) -> dict[str, Any]:
    try:
        manifest_contract.assert_manifest_contract(raw)
    except contract.ContractError as exc:
        fail(str(exc))
    manifest_dir = (manifest_dir or Path.cwd()).resolve()
    slug = validate_slug(raw.get("slug"), "slug")
    expert_type = raw.get("type")
    if expert_type not in {"expert", "team"}:
        fail("type must be expert or team")
    source_manifest = json.loads(json.dumps(raw, ensure_ascii=False))
    skill_mode = skill_contract.schema_mode(raw)
    try:
        skill_catalog = (
            skill_contract.normalize_catalog(raw.get("skills"))
            if skill_mode == "unified"
            else []
        )
    except contract.ContractError as exc:
        fail(str(exc))

    name = optional_text(raw.get("name"), "name", default=slug.replace("-", " ").title())
    summary = optional_text(raw.get("summary"), "summary")
    description = optional_text(raw.get("description"), "description")
    if not description:
        fail("description is required")
    tags = text_list(raw.get("tags"), "tags")
    quick_prompts = text_list(raw.get("quick_prompts"), "quick_prompts")
    default_prompt = optional_text(
        raw.get("default_prompt"),
        "default_prompt",
        default=quick_prompts[0] if quick_prompts else "",
    )
    if raw.get("default_prompt") is not None and quick_prompts and default_prompt != quick_prompts[0]:
        fail("default_prompt must match quick_prompts[0]")
    try:
        common_skills = (
            contract.common_skill_names(slug, raw.get("common_skills"))
            if skill_mode == "legacy"
            else []
        )
    except contract.ContractError as exc:
        fail(str(exc))

    if expert_type == "expert":
        if "subagents" in raw or "primary_agent" in raw:
            fail("type expert must use agent and must not define primary_agent or subagents")
        raw_agent = raw.get("agent")
        agent = normalize_role(
            raw_agent,
            "agent",
            expected_mode="primary",
            default_max_turns=80,
            skill_mode=skill_mode,
        )
        top_profession = optional_text(raw.get("profession"), "profession")
        agent["name"] = name
        agent["title"] = name
        agent["display_name"] = name
        if not isinstance(raw_agent, dict) or not raw_agent.get("profession"):
            agent["profession"] = top_profession or name
        source_agent = dict(raw_agent) if isinstance(raw_agent, dict) else {}
        source_agent["name"] = name
        source_agent["display_name"] = name
        if not source_agent.get("profession"):
            source_agent["profession"] = agent["profession"]
        source_manifest["agent"] = source_agent
        primary = agent
        subagents: list[dict[str, Any]] = []
    else:
        if "agent" in raw:
            fail("type team must use primary_agent and subagents, not agent")
        primary = normalize_role(
            raw.get("primary_agent"),
            "primary_agent",
            expected_mode="primary",
            default_max_turns=150,
            skill_mode=skill_mode,
        )
        subagents_raw = raw.get("subagents")
        if not isinstance(subagents_raw, list) or not subagents_raw:
            fail("subagents must contain at least one role for type team")
        subagent_items = cast(list[Any], subagents_raw)
        subagents = [
            normalize_role(
                item,
                f"subagents[{index}]",
                expected_mode="subagent",
                default_max_turns=50,
                skill_mode=skill_mode,
            )
            for index, item in enumerate(subagent_items)
        ]

    ids = [primary["id"], *[item["id"] for item in subagents]]
    if len(ids) != len(set(ids)):
        fail("agent ids must be unique")
    workflows = workflow_autonomy.normalize_workflows(
        raw,
        role_ids=set(ids),
        primary_id=primary["id"],
    )

    role_skills: list[str] = []
    if skill_mode == "legacy":
        for role in [primary, *subagents]:
            for purpose in role["skill_purposes"]:
                if purpose.startswith(f"{slug}-"):
                    fail(
                        f"{role['id']}.skills purpose must be a purpose, not a complete skill name"
                    )
            role["skills"] = [
                f"{slug}-{role['id']}-{purpose}"
                for purpose in role.pop("skill_purposes")
            ]
            role_skills.extend(role["skills"])
        for skill_name in [*common_skills, *role_skills]:
            validate_slug(skill_name, "skill name")
        if len([*common_skills, *role_skills]) != len(
            set([*common_skills, *role_skills])
        ):
            fail("generated skill names must be unique")
    else:
        declared = {item["name"] for item in skill_catalog}
        for role in [primary, *subagents]:
            try:
                role["skills"] = skill_contract.normalize_role_refs(
                    role["skills"],
                    f"{role['id']}.skills",
                    declared=declared,
                )
            except contract.ContractError as exc:
                fail(str(exc))
            role_skills.extend(role["skills"])

    mcp = normalize_mcp(raw.get("mcp_servers"))
    runtime_extensions = normalize_runtime_extensions(
        raw.get("runtime_extensions"),
        slug,
        agent_ids=set(ids),
    )
    if any(
        [
            runtime_extensions["commands"],
            runtime_extensions["custom_tools"],
            runtime_extensions["plugins"]["npm"],
            runtime_extensions["plugins"]["local"],
            runtime_extensions["plugins"]["package_json"],
            runtime_extensions["references"],
            runtime_extensions["reference_files"],
            runtime_extensions["instructions"],
            runtime_extensions["instruction_files"],
            runtime_extensions["lsp"] is not None,
        ]
    ):
        source_manifest["runtime_extensions"] = runtime_extensions
    declared_skills = (
        set([*common_skills, *role_skills])
        if skill_mode == "legacy"
        else {item["name"] for item in skill_catalog}
    )
    package_resources, package_resource_assets = normalize_package_resources(
        raw.get("package_resources"),
        declared_skills=declared_skills,
        manifest_dir=manifest_dir,
        skill_mode=skill_mode,
    )
    if skill_mode == "unified":
        declared_paths = {item["path"] for item in package_resources}
        for skill_name in sorted(declared_skills):
            required = f"{EXPERT_DIR}/{SKILLS_SUBDIR}/{skill_name}/SKILL.md"
            if required not in declared_paths:
                fail(
                    f"skills.{skill_name}: package_resources must declare {required}"
                )
        source_manifest["skills"] = skill_catalog
    if package_resources:
        source_manifest["package_resources"] = package_resources
    else:
        source_manifest.pop("package_resources", None)
    for role in [primary, *subagents]:
        for name_ in role["mcp"]:
            if name_ not in mcp:
                fail(f"agent {role['id']} references unknown mcp server {name_}")
        role["allowed_skills"] = (
            [*common_skills, *role["skills"]]
            if skill_mode == "legacy"
            else list(role["skills"])
        )
        explicit_skill_permission = role["permission"].get("skill")
        if skill_mode == "unified" and explicit_skill_permission is not None:
            fail(
                f"{role['id']}.permission.skill is derived from role skills and must be omitted"
            )
        if explicit_skill_permission is not None:
            if not isinstance(explicit_skill_permission, dict):
                fail(f"{role['id']}.permission.skill must be a mapping")
            for skill_name in explicit_skill_permission:
                if skill_name != "*" and skill_name not in role["allowed_skills"]:
                    fail(
                        f"{role['id']}.permission.skill references undeclared skill {skill_name}"
                    )
        role["permission"], role["permission_audit"] = build_role_permission(
            role,
            workflows=workflows,
            mcp_names=list(mcp.keys()),
            custom_tool_paths=[item["path"] for item in runtime_extensions["custom_tools"]],
            subagent_ids=[item["id"] for item in subagents],
            is_primary=role["id"] == primary["id"],
        )

    return {
        "slug": slug,
        "type": expert_type,
        "version": raw.get("version"),
        "name": name,
        "summary": summary,
        "description": description,
        "language": optional_text(raw.get("language"), "language", default="zh"),
        "avatar_url": validate_avatar_url(raw.get("avatar_url"), "avatar_url"),
        "tags": tags,
        "quick_prompts": quick_prompts,
        "default_prompt": default_prompt,
        "profession": optional_text(raw.get("profession"), "profession"),
        "category_id": optional_text(raw.get("category_id"), "category_id"),
        "display_description": optional_text(raw.get("display_description"), "display_description", default=description),
        "workflows": workflows,
        "primary_agent": primary,
        "subagents": subagents,
        "skill_mode": skill_mode,
        "skill_catalog": skill_catalog,
        "common_skills": common_skills,
        "role_skills": role_skills,
        "mcp": mcp,
        "runtime_extensions": runtime_extensions,
        "package_resources": package_resources,
        "package_resource_assets": package_resource_assets,
        "source_manifest": source_manifest,
    }


def bullet_list(items: list[str], fallback: str) -> str:
    values = items or [fallback]
    return "\n".join(f"- {item}" for item in values)


def build_role_permission(
    role: dict[str, Any],
    *,
    workflows: list[dict[str, Any]],
    mcp_names: list[str],
    custom_tool_paths: list[str],
    subagent_ids: list[str],
    is_primary: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return permission_policy.build_role_permission(
            role,
            workflows=workflows,
            mcp_names=mcp_names,
            custom_tool_paths=custom_tool_paths,
            subagent_ids=subagent_ids,
            is_primary=is_primary,
            legacy_tools_permission=permission_policy.tools_to_permission(
                role["tools"], f"{role['id']}.tools"
            ),
        )
    except permission_policy.PermissionPolicyError as exc:
        fail(f"{role['id']}: {exc}")


def role_skill_lines(role: dict[str, Any]) -> str:
    return "\n".join(f"- `/{skill}` and load/use skill `{skill}`" for skill in role["allowed_skills"])


def render_team_roster(manifest: dict[str, Any]) -> str:
    rows = ["| Agent ID | Display name | Profession | Responsibility |", "|---|---|---|---|"]
    for role in [manifest["primary_agent"], *manifest["subagents"]]:
        responsibility = role["responsibilities"][0] if role["responsibilities"] else role["description"]
        rows.append(f"| `{role['id']}` | {role['display_name']} | {role['profession']} | {responsibility} |")
    return "\n".join(rows)


def render_direct_routing_table(manifest: dict[str, Any]) -> str:
    rows = ["| 问法 / 触发场景 | 直接调谁 | 职责边界 |", "|---|---|---|"]
    for role in manifest["subagents"]:
        trigger = role["route_triggers"][0] if role["route_triggers"] else role["description"]
        rows.append(f"| {trigger} | `{role['id']}` | {role['profession']} |")
    if len(rows) == 2:
        rows.append("| 单专家任务 | 专家 agent | 直接执行专家工作流 |")
    return "\n".join(rows)


def render_subagent_naming(manifest: dict[str, Any]) -> str:
    if not manifest["subagents"]:
        return "- none"
    return "\n".join(
        f"- `subagent_type: \"{role['id']}\"`"
        for role in manifest["subagents"]
    )


def render_workflows(manifest: dict[str, Any]) -> str:
    workflows = manifest["workflows"]
    if not workflows:
        return bullet_list(
            manifest["primary_agent"]["workflow"],
            "澄清范围、分派团员、整合产出，并按验收标准完成验证。",
        )
    return workflow_autonomy.render_all_workflows(workflows)


def render_role_workflow(role: dict[str, Any], manifest: dict[str, Any], fallback: str) -> str:
    if workflow_autonomy.has_autonomy_contract(manifest["workflows"]):
        projection = workflow_autonomy.render_role_workflows(
            manifest["workflows"],
            role["id"],
        )
        if projection:
            return projection
    return bullet_list(role["workflow"], fallback)


def render_primary_workflows(manifest: dict[str, Any]) -> str:
    rendered = render_workflows(manifest)
    if manifest["type"] == "team" and workflow_autonomy.has_autonomy_contract(
        manifest["workflows"]
    ):
        rendered += (
            "\n\n### 自主度委派合同\n\n"
            "调用 `task` 委派当前 phase 时，prompt 必须包含该 Agent 的生效自主度、"
            "允许执行器、执行标准、验收标准和证据要求；不得只转发业务目标。"
        )
    return rendered


def render_role_triggers(role: dict[str, Any]) -> str:
    return bullet_list(role["route_triggers"], role["description"])


def render_handoff_contract(role: dict[str, Any]) -> str:
    return bullet_list(
        role["handoff_contract"],
        "返回任务理解、完成结果、证据、验收状态、失败标准和剩余风险。",
    )


def compact_description(parts: list[str], *, limit: int = 1024) -> str:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def agent_trigger_items(
    role: dict[str, Any],
    manifest: dict[str, Any],
    *,
    is_primary: bool,
) -> list[str]:
    items: list[str] = []
    items.extend(role["route_triggers"])
    if manifest["type"] == "expert":
        items.extend(manifest["quick_prompts"])
    elif is_primary:
        items.extend(
            workflow["trigger"]
            for workflow in manifest["workflows"]
            if workflow["trigger"]
        )
        items.extend(manifest["quick_prompts"])
    deduplicated: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in deduplicated:
            deduplicated.append(cleaned)
    return deduplicated


def render_agent_description(
    role: dict[str, Any],
    manifest: dict[str, Any],
    *,
    is_primary: bool,
) -> str:
    triggers = agent_trigger_items(role, manifest, is_primary=is_primary)
    if manifest["type"] == "expert":
        prefix = "当用户需要以下能力时使用："
    elif is_primary:
        prefix = "当请求需要跨角色编排、验收或最终集成时使用："
    else:
        prefix = "由团长在以下场景委派："
    trigger_text = "；".join(triggers[:3]) if triggers else role["description"]
    return compact_description([role["description"], f"{prefix}{trigger_text}"])


def render_trigger_examples(
    role: dict[str, Any],
    manifest: dict[str, Any],
    *,
    is_primary: bool,
) -> str:
    triggers = agent_trigger_items(role, manifest, is_primary=is_primary)
    if not triggers:
        triggers = [role["description"]]
    return "\n".join(f"- {item}" for item in triggers[:4])


def render_edge_case_guidance(manifest: dict[str, Any], *, is_primary: bool) -> str:
    lines = [
        "- 输入不足时先指出缺口，并只询问会改变执行结果的关键信息。",
        "- 工具、skill 或外部依赖不可用时，报告已验证事实、受影响的验收标准和可执行替代方案。",
        "- 验证失败时不要宣称完成；先修复、返工，或明确记录阻塞和剩余风险。",
    ]
    if manifest["type"] == "team" and is_primary:
        lines.append("- 团员结果未通过验收时，使用原 `task_id` 返回失败项和补救要求，不另开无上下文任务。")
    elif manifest["type"] == "team":
        lines.append("- 任务超出本角色职责时停止扩张范围，把越界部分和建议路由对象回传团长。")
    else:
        lines.append("- 请求超出本专家职责时明确边界，不模拟不存在的团队或专业能力。")
    return "\n".join(lines)


def render_agent(role: dict[str, Any], manifest: dict[str, Any], *, is_primary: bool) -> str:
    generated_description = render_agent_description(
        role,
        manifest,
        is_primary=is_primary,
    )
    frontmatter = {
        "name": role["id"],
        "description": generated_description,
        "displayName": {"en": role["display_name"], "zh": role["display_name"]},
        "profession": {"en": role["profession"], "zh": role["profession"]},
        "steps": role["steps"],
        "mode": role["mode"],
        "color": role["color"],
        "permission": role["permission"],
    }
    for key in contract.AGENT_OPTIONAL_RUNTIME_KEYS:
        if key in role:
            frontmatter[key] = role[key]
    if role["avatar_url"]:
        frontmatter["avatar_url"] = role["avatar_url"]

    if manifest["type"] == "expert":
        body = renderers.render_spec("expert-agent",
            agent_id=role["id"],
            title=role["title"],
            expert_name=manifest["name"],
            description=manifest["description"],
            display_name=role["display_name"],
            profession=role["profession"],
            default_prompt=manifest["default_prompt"] or "none",
            allowed_skills=role_skill_lines(role),
            route_triggers=render_role_triggers(role),
            trigger_examples=render_trigger_examples(role, manifest, is_primary=True),
            edge_case_guidance=render_edge_case_guidance(manifest, is_primary=True),
            handoff_contract=render_handoff_contract(role),
            responsibilities=bullet_list(role["responsibilities"], role["description"]),
            workflow=(
                render_workflows(manifest)
                if workflow_autonomy.has_autonomy_contract(manifest["workflows"])
                else bullet_list(
                    role["workflow"],
                    "Clarify the request, produce the expert output, and verify it.",
                )
            ),
            quality_gates=bullet_list(
                role["quality_gates"],
                "Before completion, verify artifacts, cite evidence, and record unresolved risk.",
            ),
        )
    elif is_primary:
        subagent_calls = "\n".join(
            f"- 调用 `task(subagent_type=\"{sub['id']}\")`：{sub['description']}"
            for sub in manifest["subagents"]
        )
        body = renderers.render_spec("primary-agent",
            agent_id=role["id"],
            team_slug=manifest["slug"],
            title=role["title"],
            expert_name=manifest["name"],
            description=manifest["description"],
            display_name=role["display_name"],
            profession=role["profession"],
            default_prompt=manifest["default_prompt"] or "none",
            allowed_skills=role_skill_lines(role),
            team_roster=render_team_roster(manifest),
            direct_routes=render_direct_routing_table(manifest),
            workflows=render_primary_workflows(manifest),
            subagent_calls=subagent_calls,
            subagent_naming=render_subagent_naming(manifest),
            trigger_examples=render_trigger_examples(role, manifest, is_primary=True),
            edge_case_guidance=render_edge_case_guidance(manifest, is_primary=True),
            handoff_contract=render_handoff_contract(role),
            quality_gates=bullet_list(
                role["quality_gates"],
                "Before completion, verify artifacts, cite evidence, and record unresolved risk.",
            ),
        )
    else:
        body = renderers.render_spec("subagent",
            agent_id=role["id"],
            title=role["title"],
            expert_name=manifest["name"],
            description=manifest["description"],
            display_name=role["display_name"],
            profession=role["profession"],
            allowed_skills=role_skill_lines(role),
            route_triggers=render_role_triggers(role),
            trigger_examples=render_trigger_examples(role, manifest, is_primary=False),
            edge_case_guidance=render_edge_case_guidance(manifest, is_primary=False),
            handoff_contract=render_handoff_contract(role),
            responsibilities=bullet_list(role["responsibilities"], role["description"]),
            workflow=render_role_workflow(
                role,
                manifest,
                "Receive the assignment, execute your role-specific work, and report verification evidence.",
            ),
            quality_gates=bullet_list(
                role["quality_gates"],
                "Return findings, files touched, verification status, and open risks.",
            ),
        )
    return renderers.render_frontmatter(frontmatter, body)


def skill_resource_paths(manifest: dict[str, Any], skill_name: str) -> list[str]:
    prefix = f"{EXPERT_DIR}/{SKILLS_SUBDIR}/{skill_name}/"
    paths: list[str] = []
    for item in manifest["package_resources"]:
        path = item["path"]
        if path.startswith(prefix):
            paths.append(path[len(prefix) :])
    return sorted(paths)


def render_skill_resource_navigation(manifest: dict[str, Any], skill_name: str) -> str:
    paths = skill_resource_paths(manifest, skill_name)
    if not paths:
        return "- 当前没有声明额外资源。只使用本 SKILL.md 中的流程，不要假设存在 scripts、references 或 templates。"
    lines: list[str] = []
    for path in paths:
        category = Path(path).parts[0] if Path(path).parts else "resource"
        if category == "scripts":
            guidance = "需要确定性执行时使用；先确认参数、输入、输出和 workspace 边界。"
        elif category == "references":
            guidance = "仅在当前任务需要该领域资料时读取，不要一次性加载全部 reference。"
        elif category in {"assets", "templates"}:
            guidance = "生成交付物时按需复制或改写，不把二进制/模板全文加载为说明。"
        elif category == "examples":
            guidance = "需要确认格式或边界时读取，并根据当前任务调整。"
        else:
            guidance = "仅在当前任务明确需要时读取或使用。"
        lines.append(f"- `{path}`：{guidance}")
    return "\n".join(lines)


def render_common_skill_description(manifest: dict[str, Any]) -> str:
    examples = "；".join(manifest["quick_prompts"][:2])
    return compact_description(
        [
            f"当 `{manifest['name']}` 的 agent 需要统一任务澄清、证据、验证和交付格式时使用。",
            f"典型请求：{examples}" if examples else "适用于本包的所有正式交付。",
        ]
    )


def render_role_skill_description(role: dict[str, Any], manifest: dict[str, Any]) -> str:
    triggers = agent_trigger_items(
        role,
        manifest,
        is_primary=role["id"] == manifest["primary_agent"]["id"],
    )
    trigger_text = "；".join(triggers[:3]) if triggers else role["description"]
    return compact_description(
        [
            f"当 `{manifest['name']}` 的 `{role['display_name']}` 需要执行角色方法、输出合同和质量门控时使用。",
            f"触发场景：{trigger_text}",
        ]
    )


def render_skill(
    skill_name: str,
    description: str,
    content: str,
    *,
    metadata: dict[str, str],
) -> str:
    try:
        renderers.validate_skill_description(skill_name, description)
    except ValueError as exc:
        fail(str(exc))
    frontmatter = {
        "name": skill_name,
        "description": description,
        "compatibility": "opencode",
        "metadata": metadata,
    }
    return renderers.render_frontmatter(frontmatter, content)


def render_command(command: dict[str, Any]) -> str:
    frontmatter: dict[str, Any] = {}
    if command["description"]:
        frontmatter["description"] = command["description"]
    for key in ["agent", "subtask", "model"]:
        if key in command:
            frontmatter[key] = command[key]
    return renderers.render_frontmatter(frontmatter, command["template"])


def render_generated_workflow_command(
    workflow: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    command = workflow["command"]
    if command is None:
        fail(f"workflow {workflow['name']} does not declare a command")
    return renderers.render_frontmatter(
        {
            "description": workflow_autonomy.workflow_command_description(workflow),
            "agent": manifest["primary_agent"]["id"],
        },
        workflow_autonomy.render_workflow_command(workflow),
    )


def workflow_commands(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        workflow
        for workflow in manifest["workflows"]
        if workflow["contract_enabled"] and workflow["command"] is not None
    ]


def runtime_extensions_config(manifest: dict[str, Any]) -> dict[str, Any]:
    ext = manifest["runtime_extensions"]
    config: dict[str, Any] = {}
    if ext["plugins"]["npm"]:
        config["plugin"] = ext["plugins"]["npm"]
    if ext["references"]:
        config["references"] = {
            contract.namespaced_reference_alias(manifest["slug"], alias): entry
            for alias, entry in ext["references"].items()
        }
    if ext["instructions"]:
        config["instructions"] = ext["instructions"]
    if ext["lsp"] is not None:
        config["lsp"] = ext["lsp"]
    return config


def runtime_agent_config(
    role: dict[str, Any],
    manifest: dict[str, Any],
    *,
    is_primary: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mode": role["mode"],
        "description": render_agent_description(role, manifest, is_primary=is_primary),
        "steps": role["steps"],
    }
    for key in contract.AGENT_OPTIONAL_RUNTIME_KEYS:
        if key in role:
            result[key] = role[key]
    result["permission"] = role["permission"]
    return result


def render_runtime_config(manifest: dict[str, Any]) -> dict[str, Any]:
    primary = manifest["primary_agent"]
    subagents = manifest["subagents"]
    config: dict[str, Any] = {
        "$schema": contract.OPENCODE_SCHEMA,
        "agent": {},
    }
    if manifest["mcp"]:
        config["mcp"] = manifest["mcp"]
    config.update(runtime_extensions_config(manifest))
    config["agent"][primary["id"]] = runtime_agent_config(
        primary,
        manifest,
        is_primary=True,
    )
    for sub in subagents:
        config["agent"][sub["id"]] = runtime_agent_config(
            sub,
            manifest,
            is_primary=False,
        )
    return config


def role_summary(role: dict[str, Any]) -> str:
    return role["responsibilities"][0] if role["responsibilities"] else role["description"]


def render_readme_type(manifest: dict[str, Any]) -> str:
    if manifest["type"] == "team":
        total = 1 + len(manifest["subagents"])
        return f"Team 型（{total} 人专家团：1 位团长 + {len(manifest['subagents'])} 位团员）"
    return "单专家型（1 位专家 agent）"


def render_readme_feature_summary(manifest: dict[str, Any]) -> str:
    lines = [manifest["description"]]
    if manifest["summary"]:
        lines.append(f"\n定位摘要：{manifest['summary']}")
    if manifest["tags"]:
        lines.append("\n标签：" + "、".join(manifest["tags"]))
    if manifest["profession"]:
        lines.append(f"\n专业定位：{manifest['profession']}")
    return "\n".join(lines)


def render_readme_roles_section(manifest: dict[str, Any]) -> tuple[str, str]:
    if manifest["type"] == "team":
        rows = ["| 角色 | Agent ID | 名称 | 职责 |", "|---|---|---|---|"]
        primary = manifest["primary_agent"]
        rows.append(
            f"| 团长 | `{primary['id']}` | {primary['display_name']} · {primary['profession']} | {role_summary(primary)} |"
        )
        for role in manifest["subagents"]:
            rows.append(
                f"| 团员 | `{role['id']}` | {role['display_name']} · {role['profession']} | {role_summary(role)} |"
            )
        return "团队角色", "\n".join(rows)

    role = manifest["primary_agent"]
    lines = [
        "| 项目 | 内容 |",
        "|---|---|",
        f"| Agent ID | `{role['id']}` |",
        f"| 名称 | {role['display_name']} |",
        f"| 专业定位 | {role['profession']} |",
        f"| 核心职责 | {role_summary(role)} |",
    ]
    if role["route_triggers"]:
        lines.append(f"| 触发场景 | {'；'.join(role['route_triggers'])} |")
    return "专家能力", "\n".join(lines)


def render_readme_skills(manifest: dict[str, Any]) -> str:
    if manifest["skill_mode"] == "unified":
        rows = ["| 技能 | 来源 | 编辑策略 | 分配角色 |", "|---|---|---|---|"]
        roles = [manifest["primary_agent"], *manifest["subagents"]]
        for item in manifest["skill_catalog"]:
            assigned = [
                f"`{role['id']}`"
                for role in roles
                if item["name"] in role["skills"]
            ]
            rows.append(
                f"| `{item['name']}` | `{item['origin']}` | "
                f"`{item['edit_policy']}` | {', '.join(assigned) or '未分配'} |"
            )
        if not manifest["skill_catalog"]:
            rows.append("| 无 | — | — | — |")
        return "\n".join(rows)
    rows = ["| 技能 | 用途 |", "|---|---|"]
    for skill_name in manifest["common_skills"]:
        rows.append(f"| `{skill_name}` | 通用工作方法、交付格式和质量门控 |")
    for role in [manifest["primary_agent"], *manifest["subagents"]]:
        for skill_name in role["skills"]:
            rows.append(f"| `{skill_name}` | `{role['id']}` 的专用技能 |")
    return "\n".join(rows)


def render_runtime_extensions_summary(manifest: dict[str, Any]) -> str:
    ext = manifest["runtime_extensions"]
    rows = ["| 能力 | 生成位置 / 配置字段 | 状态 |", "|---|---|---|"]
    total_commands = len(ext["commands"]) + len(workflow_commands(manifest))
    rows.append(f"| 自定义命令 | `.opencode/commands/` | {total_commands} 个 |")
    rows.append(f"| 自定义工具 | `.opencode/tools/` | {len(ext['custom_tools'])} 个 |")
    local_plugins = len(ext["plugins"]["local"])
    npm_plugins = len(ext["plugins"]["npm"])
    rows.append(f"| 插件 | `.opencode/plugins/` / `opencode.json.plugin` | 本地 {local_plugins} 个，npm {npm_plugins} 个 |")
    rows.append(f"| References | `.opencode/references/` / `opencode.json.references` | {len(ext['references'])} 个别名 |")
    rows.append(f"| 自定义指令 | `.opencode/instructions/` / `opencode.json.instructions` | {len(ext['instructions'])} 条 |")
    rows.append(f"| LSP | `opencode.json.lsp` | {'已配置' if ext['lsp'] is not None else '未配置'} |")
    if not manifest["mcp"]:
        rows.append("| MCP | `opencode.json.mcp` | 未配置 |")
    else:
        rows.append(f"| MCP | `opencode.json.mcp` | {len(manifest['mcp'])} 个 |")
    return "\n".join(rows)


def render_agent_runtime_summary(manifest: dict[str, Any]) -> str:
    rows = [
        "| Agent | steps | model | variant | temperature | top_p | hidden | options |",
        "|---|---:|---|---|---:|---:|---|---|",
    ]
    for role in [manifest["primary_agent"], *manifest["subagents"]]:
        options = role.get("options")
        option_keys = (
            "、".join(f"`{key}`" for key in sorted(options))
            if isinstance(options, dict)
            else "继承"
        )
        hidden = "是" if role.get("hidden") is True else "否" if "hidden" in role else "未声明"
        values = [
            f"`{role['id']}`",
            str(role["steps"]),
            f"`{role['model']}`" if "model" in role else "继承",
            f"`{role['variant']}`" if "variant" in role else "继承",
            str(role["temperature"]) if "temperature" in role else "继承",
            str(role["top_p"]) if "top_p" in role else "继承",
            hidden,
            option_keys,
        ]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def render_permission_summary(manifest: dict[str, Any]) -> str:
    rows = [
        "| Agent | 来源 | 生效自主度 | 参与档位 | `*` | edit | bash | webfetch | external_directory | doom_loop | 提权理由 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for role in [manifest["primary_agent"], *manifest["subagents"]]:
        permission = role["permission"]
        audit = role["permission_audit"]
        bash = permission.get("bash")
        bash_action = bash.get("*") if isinstance(bash, dict) else bash
        external = permission.get("external_directory")
        external_action = external.get("*") if isinstance(external, dict) else external
        levels = "、".join(audit["levels"]) if audit["levels"] else "未声明"
        reason = audit["permission_reason"] or "无"
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{role['id']}`",
                    audit["source"],
                    audit["effective"],
                    levels,
                    str(permission.get("*", "legacy")),
                    str(permission.get("edit", "继承")),
                    str(bash_action or "继承"),
                    str(permission.get("webfetch", "继承")),
                    str(external_action or "继承"),
                    str(permission.get("doom_loop", "继承")),
                    reason,
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def render_readme_runtime_extensions(manifest: dict[str, Any]) -> str:
    rows = [
        render_runtime_extensions_summary(manifest),
        "\n### Agent 运行参数",
        render_agent_runtime_summary(manifest),
        "\n未声明的可选参数继承 OpenCode、模型或 provider 默认值。",
        "\n### Agent 权限基线",
        render_permission_summary(manifest),
    ]
    if manifest["mcp"]:
        rows.append("\n### MCP")
        rows.append("| MCP | 类型 | 启用状态 |")
        rows.append("|---|---|---|")
        for name, entry in manifest["mcp"].items():
            rows.append(f"| `{name}` | {entry.get('type', 'unknown')} | {entry.get('enabled', False)} |")
    else:
        rows.append("\n未在 `expert.json` 中配置 MCP，因此生成的 MobileWork 运行时配置文件不包含 MCP 占位。")
    ext = manifest["runtime_extensions"]
    generated_commands = workflow_commands(manifest)
    if ext["commands"] or generated_commands:
        rows.append("\n### 自定义命令")
        rows.extend(
            f"- `/{workflow['command']['name']}`：{workflow['command']['description']}（由 workflow 合同生成）"
            for workflow in generated_commands
        )
        rows.extend(f"- `/{command['name']}`：{command['description'] or '自定义工作流命令'}" for command in ext["commands"])
    if ext["custom_tools"]:
        rows.append("\n### 自定义工具")
        rows.extend(f"- `.opencode/tools/{item['path']}`" for item in ext["custom_tools"])
    if ext["plugins"]["npm"] or ext["plugins"]["local"]:
        rows.append("\n### 插件")
        rows.extend(f"- npm：`{item}`" for item in ext["plugins"]["npm"])
        rows.extend(f"- 本地：`.opencode/plugins/{item['path']}`" for item in ext["plugins"]["local"])
    if ext["references"]:
        rows.append("\n### References")
        rows.extend(f"- `{name}`" for name in ext["references"])
    if ext["instructions"]:
        rows.append("\n### 自定义指令")
        rows.extend(f"- `{item}`" for item in ext["instructions"])
    rows.append("\n凭证请使用环境变量或密钥管理器，不要把真实 token、API key 或私有 endpoint 写入包文件。")
    return "\n".join(rows)


def render_readme_mcp_note(manifest: dict[str, Any]) -> str:
    return render_readme_runtime_extensions(manifest)


def render_settings_summary(manifest: dict[str, Any]) -> str:
    names = contract.extract_env_references(render_runtime_config(manifest))
    if not names:
        return (
            "当前生成配置不引用环境变量，因此包根目录不生成 `.env.example`。"
            "如后续增加凭证或可配置值，请在 `expert.json` 的现有 OpenCode 配置字段中使用 `{env:VARIABLE}`。"
        )
    lines = [
        "运行前在 MobileWork/OpenCode 进程环境中提供以下变量；包内 `.env.example` 只记录名称和占位值，不会自动注入配置："
    ]
    lines.extend(f"- `{name}`" for name in names)
    return "\n".join(lines)


def write_text_file(base: Path, relative_path: str, content: str, *, ensure_newline: bool = True) -> None:
    target = base / validate_package_file_path(relative_path, "runtime extension path")
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = content if not ensure_newline or content.endswith("\n") else content + "\n"
    target.write_text(rendered, encoding="utf-8")


def write_runtime_extensions(project_dir: Path, manifest: dict[str, Any]) -> None:
    ext = manifest["runtime_extensions"]
    package_runtime_dir = project_dir / EXPERT_DIR
    for workflow in workflow_commands(manifest):
        write_text_file(
            package_runtime_dir / COMMANDS_SUBDIR,
            f"{workflow['command']['name']}.md",
            render_generated_workflow_command(workflow, manifest),
        )
    for command in ext["commands"]:
        write_text_file(
            package_runtime_dir / COMMANDS_SUBDIR,
            f"{command['name']}.md",
            render_command(command),
        )
    for item in ext["custom_tools"]:
        write_text_file(package_runtime_dir / TOOLS_SUBDIR, item["path"], item["content"])
    for item in ext["plugins"]["local"]:
        write_text_file(package_runtime_dir / PLUGINS_SUBDIR, item["path"], item["content"])
    if ext["plugins"]["package_json"]:
        (package_runtime_dir / "package.json").write_text(
            json.dumps(ext["plugins"]["package_json"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    for item in ext["reference_files"]:
        write_text_file(project_dir, item["path"], item["content"], ensure_newline=False)
    for item in ext["instruction_files"]:
        write_text_file(project_dir, item["path"], item["content"], ensure_newline=False)


def write_package_resources(project_dir: Path, manifest: dict[str, Any]) -> None:
    for relative_path, content in manifest["package_resource_assets"].items():
        target = project_dir / validate_package_file_path(relative_path, "package_resources path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def render_readme_legacy_mcp_note(manifest: dict[str, Any]) -> str:
    if not manifest["mcp"]:
        return "未在 `expert.json` 中配置 MCP，因此生成的 MobileWork 运行时配置文件不包含 MCP 占位。"
    rows = ["| MCP | 类型 | 启用状态 |", "|---|---|---|"]
    for name, entry in manifest["mcp"].items():
        rows.append(f"| `{name}` | {entry.get('type', 'unknown')} | {entry.get('enabled', False)} |")
    rows.append("\n凭证请使用环境变量或密钥管理器，不要把真实 token、API key 或私有 endpoint 写入包文件。")
    return "\n".join(rows)


def render_readme_notes(manifest: dict[str, Any]) -> str:
    if manifest["type"] == "team":
        return "\n".join(
            [
                "- 团长通过 `task.subagent_type` 调度团员，并保存 `task_id` 用于返工。",
                "- 团员专业产出必须来自对应 `task` 结果，团长不要代写团员结论。",
                "- 并行阶段只适用于输入输出独立、无共享写入冲突且验收标准可分别检查的分支。",
            ]
        )
    return "\n".join(
        [
            "- 这是单专家包，不调用 `task` 调度 subagent。",
            "- 专家需要直接完成工作流，并在最终输出中说明证据、验证状态和剩余风险。",
        ]
    )


def render_readme(manifest: dict[str, Any]) -> str:
    roles_heading, roles_content = render_readme_roles_section(manifest)
    return renderers.render_spec("readme",
        expert_name=manifest["name"],
        display_description=manifest["display_description"] or manifest["description"],
        type_label=render_readme_type(manifest),
        feature_summary=render_readme_feature_summary(manifest),
        roles_heading=roles_heading,
        roles_content=roles_content,
        workflow=render_workflows(manifest),
        skills_table=render_readme_skills(manifest),
        quick_prompts="\n".join(f"- {item}" for item in manifest["quick_prompts"]) or "- 暂无预设示例，请直接描述你的目标。",
        mcp_note=render_readme_mcp_note(manifest),
        settings=render_settings_summary(manifest),
        notes=render_readme_notes(manifest),
    )


def _write_project_locked(manifest: dict[str, Any], output_root: Path, *, force: bool) -> Path:
    project_dir = output_root / manifest["slug"]
    if project_dir.exists():
        if not force:
            fail(f"{project_dir} already exists; pass --force to replace it")
    staging_root = Path(tempfile.mkdtemp(prefix=f".{manifest['slug']}.staging-", dir=output_root))
    staged_project = staging_root / manifest["slug"]
    agents_dir = staged_project / EXPERT_DIR / AGENTS_SUBDIR
    skills_dir = staged_project / EXPERT_DIR / SKILLS_SUBDIR
    agents_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    write_avatar_assets(staged_project, manifest)

    (staged_project / MANIFEST_FILE).write_text(
        json.dumps(manifest["source_manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    existing_gitignore = ""
    if project_dir.is_dir() and (project_dir / ".gitignore").is_file():
        existing_gitignore = (project_dir / ".gitignore").read_text(encoding="utf-8")
    (staged_project / ".gitignore").write_text(
        gitignore_contract.merge_content(existing_gitignore), encoding="utf-8"
    )
    runtime_config = render_runtime_config(manifest)
    (staged_project / RUNTIME_CONFIG).write_text(
        json.dumps(runtime_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    env_names = contract.extract_env_references(runtime_config)
    if env_names:
        (staged_project / ".env.example").write_text(
            contract.render_env_example(env_names),
            encoding="utf-8",
        )
    write_runtime_extensions(staged_project, manifest)

    primary = manifest["primary_agent"]
    (agents_dir / f"{primary['id']}.md").write_text(
        render_agent(primary, manifest, is_primary=True), encoding="utf-8"
    )
    for sub in manifest["subagents"]:
        (agents_dir / f"{sub['id']}.md").write_text(
            render_agent(sub, manifest, is_primary=False), encoding="utf-8"
        )

    if manifest["skill_mode"] == "legacy":
        for skill_name in manifest["common_skills"]:
            common_content = renderers.render_spec(
                "common-skill",
                expert_name=manifest["name"],
                description=manifest["description"],
                expert_type=manifest["type"],
                when_to_use="\n".join(
                    f"- {item}" for item in manifest["quick_prompts"][:4]
                )
                or "- 本包任一 agent 需要统一任务澄清、证据、验证和交付格式时。",
                resource_navigation=render_skill_resource_navigation(
                    manifest, skill_name
                ),
            )
            if workflow_autonomy.has_autonomy_contract(manifest["workflows"]):
                common_content += (
                    "\n## Workflow 自主度合同\n\n"
                    + render_workflows(manifest)
                    + "\n"
                )
            skill_path = skills_dir / skill_name
            skill_path.mkdir()
            (skill_path / "SKILL.md").write_text(
                render_skill(
                    skill_name,
                    render_common_skill_description(manifest),
                    common_content,
                    metadata={
                        "package": manifest["slug"],
                        "role": "all",
                        "type": "common",
                    },
                ),
                encoding="utf-8",
            )
        for role in [primary, *manifest["subagents"]]:
            for skill_name in role["skills"]:
                skill_path = skills_dir / skill_name
                skill_path.mkdir()
                content = renderers.render_spec(
                    "role-skill",
                    title=role["title"],
                    expert_name=manifest["name"],
                    responsibilities=bullet_list(
                        role["responsibilities"],
                        f"为 {role['title']} 提供聚焦的专业指引。",
                    ),
                    when_to_use=render_trigger_examples(
                        role,
                        manifest,
                        is_primary=role["id"] == primary["id"],
                    ),
                    workflow=render_role_workflow(
                        role,
                        manifest,
                        "在专业范围内执行方法并验证结果。",
                    ),
                    resource_navigation=render_skill_resource_navigation(
                        manifest, skill_name
                    ),
                    handoff_contract=render_handoff_contract(role),
                    quality_gates=bullet_list(
                        role["quality_gates"],
                        "交付前验证专业工作是否满足要求。",
                    ),
                )
                (skill_path / "SKILL.md").write_text(
                    render_skill(
                        skill_name,
                        render_role_skill_description(role, manifest),
                        content,
                        metadata={
                            "package": manifest["slug"],
                            "role": role["id"],
                            "type": "role",
                        },
                    ),
                    encoding="utf-8",
                )

    write_package_resources(staged_project, manifest)
    (staged_project / "README.md").write_text(render_readme(manifest), encoding="utf-8")

    import validate_expert

    validation = validate_expert.validate_package(staged_project)
    if not validation.ok:
        details = "; ".join(validation.errors[:8])
        shutil.rmtree(staging_root, ignore_errors=True)
        fail(f"generated staging package failed validation: {details}")

    backup = output_root / f".{manifest['slug']}.backup-{uuid.uuid4().hex}"
    replaced = False
    installed = False
    committed = False
    git_history_moved = False
    try:
        if project_dir.exists():
            os.replace(project_dir, backup)
            replaced = True
        os.replace(staged_project, project_dir)
        installed = True
        if replaced and (backup / ".git").is_dir():
            os.replace(backup / ".git", project_dir / ".git")
            git_history_moved = True
        validate_generated_project(project_dir, output_root, manifest["slug"])
        validation = validate_expert.validate_package(project_dir)
        if not validation.ok:
            details = "; ".join(validation.errors[:8])
            fail(f"generated live package failed validation: {details}")
        committed = True
    except BaseException:
        if git_history_moved and (project_dir / ".git").is_dir() and backup.is_dir():
            os.replace(project_dir / ".git", backup / ".git")
        if not committed and installed and project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        if not committed and replaced and backup.exists():
            os.replace(backup, project_dir)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    if replaced and backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as error:
            print(
                f"warning: package committed but backup cleanup failed; recovery copy kept at {backup}: {error}",
                file=sys.stderr,
            )
    return project_dir


def package_identity(raw: dict[str, Any]) -> tuple[str, str]:
    package_type = raw.get("type")
    if package_type == "expert":
        role = raw.get("agent")
    elif package_type == "team":
        role = raw.get("primary_agent")
    else:
        fail("controlled target expert.json has invalid type")
    if not isinstance(role, dict) or not isinstance(role.get("id"), str) or not role["id"].strip():
        fail("controlled target expert.json has invalid primary Agent ID")
    return package_type, role["id"].strip()


def write_project(manifest: dict[str, Any], output_root: Path, *, force: bool) -> Path:
    with package_lock(output_root, manifest["slug"]):
        return _write_project_locked(manifest, output_root, force=force)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Path to expert.json")
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Assert the host-resolved output root; arbitrary custom destinations are rejected"
        ),
    )
    output.add_argument(
        "--my-experts",
        action="store_true",
        help="Assert that the current host is managed MobileWork",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing output project directory")
    parser.add_argument(
        "--expected-revision",
        help="Require the existing controlled target to match this SHA-256 revision",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("git") is None:
        fail("Git is required to create or modify a version-controlled expert source")
    if args.manifest.name != MANIFEST_FILE:
        fail(f"manifest file must be named {MANIFEST_FILE}")
    raw = load_json(args.manifest)
    controlled_target_raw = os.environ.get(CONTROLLED_TARGET_ENV, "").strip()
    requested_identity = package_identity(raw) if controlled_target_raw else None
    if controlled_target_raw:
        early_target = execution_context.canonical_path(Path(controlled_target_raw))
        if early_target.is_dir():
            current_identity = package_identity(load_json(early_target / MANIFEST_FILE))
            if requested_identity is not None and requested_identity[0] != current_identity[0]:
                fail("controlled target cannot change expert type")
            if requested_identity is not None and requested_identity[1] != current_identity[1]:
                fail("controlled target cannot change primary Agent ID")
    manifest = normalize_manifest(raw, manifest_dir=args.manifest.parent)
    prepare_avatar_assets(manifest, args.manifest.parent)
    if args.my_experts and os.environ.get(execution_context.HOST_ENV, "").strip() != execution_context.MOBILEWORK_HOST:
        fail("HOST_CONTRACT_INCOMPLETE: --my-experts requires the MobileWork host contract")
    output_dir = normalized_output_dir(args.output_dir)
    try:
        execution_context.validate_package_target(
            execution_context.resolve_execution_context(
                requested_output_dir=args.output_dir,
            ),
            manifest["slug"],
        )
    except execution_context.ExecutionContextError as error:
        fail(f"{error.code}: {error}")
    if controlled_target_raw:
        controlled_target = execution_context.canonical_path(Path(controlled_target_raw))
        source_manifest = execution_context.canonical_path(args.manifest)
        if controlled_target.name != manifest["slug"]:
            fail("controlled target slug does not match expert.json")
        if output_dir != controlled_target.parent:
            fail("controlled target requires its parent as --output-dir")
        try:
            source_manifest.relative_to(controlled_target)
        except ValueError:
            pass
        else:
            fail("controlled target requires a temporary manifest outside the source package")
        if not args.expected_revision:
            fail("controlled target requires --expected-revision")
    output_dir.mkdir(parents=True, exist_ok=True)
    if controlled_target_raw:
        with package_lock(output_dir, manifest["slug"]):
            current_target = output_dir / manifest["slug"]
            if not current_target.is_dir():
                fail("controlled target package does not exist")
            current_revision = calculate_package_revision(current_target)
            if current_revision != args.expected_revision:
                fail(
                    "controlled target revision conflict: "
                    f"expected {args.expected_revision}, got {current_revision}"
                )
            current_manifest = load_json(current_target / MANIFEST_FILE)
            current_type, current_primary_id = package_identity(current_manifest)
            requested_type, requested_primary_id = requested_identity or (
                manifest["type"],
                manifest["primary_agent"]["id"],
            )
            if requested_type != current_type:
                fail("controlled target cannot change expert type")
            if requested_primary_id != current_primary_id:
                fail("controlled target cannot change primary Agent ID")
            project_dir = _write_project_locked(manifest, output_dir, force=args.force)
    else:
        project_dir = write_project(manifest, output_dir, force=args.force)
    validate_generated_project(project_dir, output_dir, manifest["slug"])
    import expert_vcs

    try:
        vcs = expert_vcs.initialize_repository(project_dir)
    except expert_vcs.ExpertVcsError as exc:
        fail(f"expert was generated but local Git initialization failed: {exc}")
    print(
        "VERSION_PENDING: expert source changed successfully; ask the user whether to "
        "publish the proposed SemVer with version_expert.py. "
        + json.dumps(vcs, ensure_ascii=False),
        file=sys.stderr,
    )
    print(project_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
