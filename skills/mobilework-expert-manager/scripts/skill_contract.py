#!/usr/bin/env python3
"""Unified and legacy skill contracts for MobileWork expert packages."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import package_contract


SKILL_ORIGINS = frozenset({"uploaded", "managed", "legacy-migrated"})
EDIT_POLICIES = frozenset({"preserved", "managed"})
SKILL_ENTRY_KEYS = frozenset({"name", "origin", "edit_policy"})


def schema_mode(manifest: dict[str, Any]) -> str:
    """Return ``unified`` for new manifests and ``legacy`` for purpose manifests."""

    if "skills" in manifest and "common_skills" in manifest:
        raise package_contract.ContractError(
            "expert.json: skills and common_skills cannot be mixed"
        )
    if "skills" in manifest:
        return "unified"
    if "common_skills" in manifest:
        return "legacy"
    for _field, role in _role_entries(manifest):
        role_skills = role.get("skills")
        if isinstance(role_skills, list) and any(
            isinstance(item, dict) for item in role_skills
        ):
            return "legacy"
    return "unified"


def normalize_catalog(value: Any, field: str = "skills") -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise package_contract.ContractError(f"{field}: must be a list")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise package_contract.ContractError(f"{item_field}: must be a mapping")
        unexpected = sorted(set(item) - SKILL_ENTRY_KEYS)
        if unexpected:
            raise package_contract.ContractError(
                f"{item_field}: unknown fields {', '.join(unexpected)}"
            )
        if set(item) != SKILL_ENTRY_KEYS:
            missing = sorted(SKILL_ENTRY_KEYS - set(item))
            raise package_contract.ContractError(
                f"{item_field}: missing fields {', '.join(missing)}"
            )
        name = item.get("name")
        origin = item.get("origin")
        edit_policy = item.get("edit_policy")
        if (
            not isinstance(name, str)
            or not package_contract.NAME_RE.fullmatch(name)
            or len(name) > 64
        ):
            raise package_contract.ContractError(
                f"{item_field}.name: must be lowercase-hyphen and 64 characters or fewer"
            )
        if name in seen:
            raise package_contract.ContractError(f"{item_field}.name: duplicates {name}")
        if origin not in SKILL_ORIGINS:
            raise package_contract.ContractError(
                f"{item_field}.origin: must be uploaded, managed, or legacy-migrated"
            )
        if edit_policy not in EDIT_POLICIES:
            raise package_contract.ContractError(
                f"{item_field}.edit_policy: must be preserved or managed"
            )
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "origin": origin,
                "edit_policy": edit_policy,
            }
        )
    return normalized


def normalize_role_refs(
    value: Any,
    field: str,
    *,
    declared: set[str] | None = None,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise package_contract.ContractError(f"{field}: must be a list of skill names")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, str) or not package_contract.NAME_RE.fullmatch(item):
            raise package_contract.ContractError(
                f"{item_field}: must be a lowercase-hyphen skill name"
            )
        if item in seen:
            raise package_contract.ContractError(f"{item_field}: duplicates {item}")
        if declared is not None and item not in declared:
            raise package_contract.ContractError(
                f"{item_field}: references undeclared skill {item}"
            )
        seen.add(item)
        normalized.append(item)
    return normalized


def legacy_common_names(manifest: dict[str, Any]) -> list[str]:
    slug = manifest.get("slug")
    if not isinstance(slug, str):
        raise package_contract.ContractError("expert.json slug is invalid")
    return package_contract.common_skill_names(slug, manifest.get("common_skills"))


def catalog_names(manifest: dict[str, Any]) -> list[str]:
    if schema_mode(manifest) == "legacy":
        names = legacy_common_names(manifest)
        for index, role in enumerate(_roles(manifest)):
            names.extend(
                package_contract.role_skill_names(
                    str(manifest.get("slug", "")),
                    role,
                    f"roles[{index}]",
                )
            )
        return _dedupe(names)
    return [item["name"] for item in normalize_catalog(manifest.get("skills"))]


def role_skill_names(
    manifest: dict[str, Any],
    role: dict[str, Any],
    field: str,
) -> list[str]:
    if schema_mode(manifest) == "legacy":
        return [
            *legacy_common_names(manifest),
            *package_contract.role_skill_names(
                str(manifest.get("slug", "")),
                role,
                field,
            ),
        ]
    declared = set(catalog_names(manifest))
    return normalize_role_refs(role.get("skills"), f"{field}.skills", declared=declared)


def role_assignments(manifest: dict[str, Any]) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    for field, role in _role_entries(manifest):
        role_id = role.get("id")
        if isinstance(role_id, str):
            assignments[role_id] = role_skill_names(manifest, role, field)
    return assignments


def validate_manifest_skills(manifest: dict[str, Any]) -> None:
    mode = schema_mode(manifest)
    if mode == "legacy":
        legacy_common_names(manifest)
        for field, role in _role_entries(manifest):
            package_contract.role_skill_names(
                str(manifest.get("slug", "")),
                role,
                field,
            )
        return
    catalog = normalize_catalog(manifest.get("skills"))
    declared = {item["name"] for item in catalog}
    for field, role in _role_entries(manifest):
        normalize_role_refs(role.get("skills"), f"{field}.skills", declared=declared)


def skill_entry(manifest: dict[str, Any], name: str) -> dict[str, str] | None:
    if schema_mode(manifest) == "legacy":
        return None
    return next(
        (item for item in normalize_catalog(manifest.get("skills")) if item["name"] == name),
        None,
    )


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _regular_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content_digest = hashlib.sha256(path.read_bytes()).digest()
        digest.update(content_digest)
    return digest.hexdigest()


def file_resources(root: Path, *, package_root: Path) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    for path in _regular_files(root):
        content = path.read_bytes()
        try:
            content.decode("utf-8")
            kind = "text"
        except UnicodeDecodeError:
            kind = "binary"
        resources.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "kind": kind,
                "sha256": package_contract.sha256_bytes(content),
            }
        )
    return resources


def migrate_legacy_manifest(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a byte-preserving unified manifest for a valid legacy package."""

    if schema_mode(manifest) != "legacy":
        normalized = json.loads(json.dumps(manifest, ensure_ascii=False))
        validate_manifest_skills(normalized)
        return normalized

    migrated = json.loads(json.dumps(manifest, ensure_ascii=False))
    common = legacy_common_names(manifest)
    old_role_skills: dict[str, list[str]] = {}
    ordered_names = list(common)
    for field, role in _role_entries(manifest):
        role_id = role.get("id")
        if not isinstance(role_id, str):
            raise package_contract.ContractError(f"{field}.id: must be lowercase-hyphen")
        owned = package_contract.role_skill_names(
            str(manifest.get("slug", "")),
            role,
            field,
        )
        old_role_skills[role_id] = owned
        ordered_names.extend(owned)
    ordered_names = _dedupe(ordered_names)

    skills_root = package_dir / package_contract.PACKAGE_RUNTIME_DIR / package_contract.SKILLS_SUBDIR
    actual_directories = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    } if skills_root.is_dir() else set()
    expected = set(ordered_names)
    if actual_directories != expected:
        missing = sorted(expected - actual_directories)
        extra = sorted(actual_directories - expected)
        raise package_contract.ContractError(
            "legacy skill directories do not match expert.json; "
            f"missing={missing}, extra={extra}"
        )

    resources: list[dict[str, str]] = []
    for name in ordered_names:
        skill_root = skills_root / name
        package_contract.assert_no_symlinks(skill_root)
        if not (skill_root / "SKILL.md").is_file():
            raise package_contract.ContractError(
                f"legacy skill is missing SKILL.md: {name}"
            )
        resources.extend(file_resources(skill_root, package_root=package_dir))

    migrated["skills"] = [
        {
            "name": name,
            "origin": "legacy-migrated",
            "edit_policy": "managed",
        }
        for name in ordered_names
    ]
    migrated.pop("common_skills", None)
    migrated["package_resources"] = sorted(resources, key=lambda item: item["path"])
    for _field, role in _role_entries(migrated):
        role_id = role.get("id")
        role["skills"] = _dedupe([*common, *old_role_skills.get(str(role_id), [])])
        permission = role.get("permission")
        if isinstance(permission, dict):
            permission.pop("skill", None)
    validate_manifest_skills(migrated)
    return migrated


def _role_entries(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    if manifest.get("type") == "expert":
        role = manifest.get("agent")
        return [("agent", role)] if isinstance(role, dict) else []
    entries: list[tuple[str, dict[str, Any]]] = []
    primary = manifest.get("primary_agent")
    if isinstance(primary, dict):
        entries.append(("primary_agent", primary))
    subagents = manifest.get("subagents")
    if isinstance(subagents, list):
        entries.extend(
            (f"subagents[{index}]", role)
            for index, role in enumerate(subagents)
            if isinstance(role, dict)
        )
    return entries


def _roles(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [role for _field, role in _role_entries(manifest)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise package_contract.ContractError(f"skill directory is invalid: {root}")
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in [*directories, *names]:
            path = base / name
            if path.is_symlink():
                raise package_contract.ContractError(
                    f"symlink is not allowed: {path.relative_to(root).as_posix()}"
                )
        files.extend(base / name for name in names)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())
