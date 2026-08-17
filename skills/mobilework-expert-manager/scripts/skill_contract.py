#!/usr/bin/env python3
"""Unified and legacy skill contracts for MobileWork expert packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import package_contract


SKILL_ORIGINS = frozenset({"uploaded", "managed", "legacy-migrated"})
EDIT_POLICIES = frozenset({"preserved", "managed"})
SKILL_ENTRY_KEYS = frozenset({"name", "origin", "edit_policy"})
SKILL_FRONTMATTER_FIELDS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_COMPATIBILITY_LENGTH = 500
RECOMMENDED_SKILL_MARKDOWN_LINES = 500
SKILL_RESOURCE_REFERENCE_RE = re.compile(
    r"(?:\]\(|`)((?:scripts|references|assets)/[^\s)`#]+)"
)


@dataclass(frozen=True)
class SkillMarkdownIssue:
    code: str
    severity: str
    field: str
    message: str
    root_cause: str
    remediation: str
    evidence: str


def _markdown_issue(
    code: str,
    field: str,
    message: str,
    *,
    root_cause: str = "invalid-skill-frontmatter",
    remediation: str = "Correct SKILL.md frontmatter and retry.",
    evidence: str = "",
    severity: str = "error",
) -> SkillMarkdownIssue:
    return SkillMarkdownIssue(
        code=code,
        severity=severity,
        field=field,
        message=message,
        root_cause=root_cause,
        remediation=remediation,
        evidence=evidence,
    )


def validate_skill_frontmatter(
    frontmatter: Any,
    *,
    directory_name: str,
    expected_compatibility: str | None = None,
) -> list[SkillMarkdownIssue]:
    """Validate the normative Agent Skills frontmatter contract without coercion."""

    if not isinstance(frontmatter, dict):
        return [
            _markdown_issue(
                "SKILL_FRONTMATTER_INVALID",
                "frontmatter",
                "frontmatter must be a mapping",
                evidence=type(frontmatter).__name__,
            )
        ]

    issues: list[SkillMarkdownIssue] = []
    unexpected = sorted(
        (key for key in frontmatter if key not in SKILL_FRONTMATTER_FIELDS),
        key=lambda key: str(key),
    )
    if unexpected:
        rendered = ", ".join(str(key) for key in unexpected)
        issues.append(
            _markdown_issue(
                "SKILL_FRONTMATTER_FIELD_UNSUPPORTED",
                "frontmatter",
                f"frontmatter contains unsupported fields: {rendered}",
                root_cause="unsupported-skill-frontmatter",
                remediation=(
                    "Remove unsupported top-level fields or move custom string values "
                    "under metadata."
                ),
                evidence=rendered,
            )
        )

    name = frontmatter.get("name")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > MAX_SKILL_NAME_LENGTH
        or not package_contract.NAME_RE.fullmatch(name)
    ):
        issues.append(
            _markdown_issue(
                "SKILL_NAME_INVALID",
                "frontmatter.name",
                (
                    "frontmatter name must contain 1-64 lowercase ASCII letters, "
                    "numbers, or single hyphens"
                ),
                root_cause="invalid-skill-name",
                remediation=(
                    "Use a 1-64 character lowercase kebab-case name without leading, "
                    "trailing, or consecutive hyphens."
                ),
                evidence=type(name).__name__ if not isinstance(name, str) else str(len(name)),
            )
        )
    if isinstance(name, str) and name != directory_name:
        issues.append(
            _markdown_issue(
                "SKILL_NAME_MISMATCH",
                "frontmatter.name",
                f"frontmatter name must equal skill directory {directory_name}",
                root_cause="skill-name-mismatch",
                remediation="Rename the skill directory or correct frontmatter name.",
                evidence=directory_name,
            )
        )

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        issues.append(
            _markdown_issue(
                "SKILL_DESCRIPTION_INVALID",
                "frontmatter.description",
                "frontmatter description must be non-empty",
                root_cause="invalid-skill-description",
                remediation=(
                    "Describe what the skill does and when an agent should use it."
                ),
                evidence=type(description).__name__,
            )
        )
    elif len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        issues.append(
            _markdown_issue(
                "SKILL_DESCRIPTION_INVALID",
                "frontmatter.description",
                "frontmatter description must be 1024 characters or fewer",
                root_cause="invalid-skill-description",
                remediation="Shorten the description to 1024 characters or fewer.",
                evidence=str(len(description)),
            )
        )

    license_value = frontmatter.get("license")
    if "license" in frontmatter and (
        not isinstance(license_value, str) or not license_value.strip()
    ):
        issues.append(
            _markdown_issue(
                "SKILL_LICENSE_INVALID",
                "frontmatter.license",
                "optional frontmatter license must be a non-empty string",
                remediation="Use a short license name or bundled license-file reference.",
                evidence=type(license_value).__name__,
            )
        )

    compatibility = frontmatter.get("compatibility")
    if "compatibility" in frontmatter:
        if (
            not isinstance(compatibility, str)
            or not compatibility.strip()
            or len(compatibility) > MAX_SKILL_COMPATIBILITY_LENGTH
        ):
            issues.append(
                _markdown_issue(
                    "SKILL_COMPATIBILITY_INVALID",
                    "frontmatter.compatibility",
                    (
                        "optional frontmatter compatibility must be a non-empty "
                        "string of 500 characters or fewer"
                    ),
                    remediation=(
                        "Remove compatibility when unnecessary or describe environment "
                        "requirements in 1-500 characters."
                    ),
                    evidence=(
                        type(compatibility).__name__
                        if not isinstance(compatibility, str)
                        else str(len(compatibility))
                    ),
                )
            )
        elif (
            expected_compatibility is not None
            and compatibility != expected_compatibility
        ):
            issues.append(
                _markdown_issue(
                    "SKILL_COMPATIBILITY_INVALID",
                    "frontmatter.compatibility",
                    (
                        "optional frontmatter compatibility must equal "
                        f"{expected_compatibility}"
                    ),
                    remediation=(
                        f"Set compatibility to {expected_compatibility} for this "
                        "MobileWork-managed legacy skill."
                    ),
                    evidence="compatibility-mismatch",
                )
            )

    metadata = frontmatter.get("metadata")
    if "metadata" in frontmatter:
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()
        ):
            issues.append(
                _markdown_issue(
                    "SKILL_METADATA_INVALID",
                    "frontmatter.metadata",
                    "optional frontmatter metadata must map strings to strings",
                    remediation="Quote every metadata key and value as a YAML string.",
                    evidence=type(metadata).__name__,
                )
            )

    allowed_tools = frontmatter.get("allowed-tools")
    if "allowed-tools" in frontmatter and (
        not isinstance(allowed_tools, str) or not allowed_tools.strip()
    ):
        issues.append(
            _markdown_issue(
                "SKILL_ALLOWED_TOOLS_INVALID",
                "frontmatter.allowed-tools",
                "optional frontmatter allowed-tools must be a non-empty string",
                remediation=(
                    "Use the experimental space-separated string form, or remove "
                    "allowed-tools."
                ),
                evidence=type(allowed_tools).__name__,
            )
        )
    return issues


def skill_markdown_recommendations(
    line_count: int,
    markdown: str = "",
) -> list[SkillMarkdownIssue]:
    issues: list[SkillMarkdownIssue] = []
    if line_count > RECOMMENDED_SKILL_MARKDOWN_LINES:
        issues.append(
            _markdown_issue(
                "SKILL_MARKDOWN_LENGTH_RECOMMENDED",
                "SKILL.md",
                (
                    f"SKILL.md has {line_count} lines; Agent Skills recommends "
                    f"{RECOMMENDED_SKILL_MARKDOWN_LINES} lines or fewer"
                ),
                root_cause="skill-progressive-disclosure",
                remediation=(
                    "Move detailed material into focused references and keep SKILL.md "
                    "as the routing and workflow entrypoint."
                ),
                evidence=str(line_count),
                severity="warning",
            )
        )
    deep_references = sorted(
        {
            match.group(1)
            for match in SKILL_RESOURCE_REFERENCE_RE.finditer(markdown)
            if len(Path(match.group(1)).parts) > 2
        }
    )
    if deep_references:
        issues.append(
            _markdown_issue(
                "SKILL_REFERENCE_DEPTH_RECOMMENDED",
                "SKILL.md",
                "Agent Skills recommends keeping file references one level deep",
                root_cause="skill-progressive-disclosure",
                remediation=(
                    "Link focused files directly from SKILL.md and avoid deep "
                    "reference chains where practical."
                ),
                evidence=", ".join(deep_references),
                severity="warning",
            )
        )
    return issues


def add_skill_markdown_issues(
    result: Any,
    issues: list[SkillMarkdownIssue],
    *,
    path: str,
) -> None:
    """Project shared issues into the manager's structured finding result."""

    for issue in issues:
        result.add(
            f"{path}: {issue.message}",
            severity=issue.severity,
            code=issue.code,
            phase="skill",
            path=path,
            location=issue.field,
            root_cause=issue.root_cause,
            remediation=issue.remediation,
            evidence=issue.evidence,
        )


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
    for field, role in _role_entries(migrated):
        role_id = role.get("id")
        role["skills"] = _dedupe([*common, *old_role_skills.get(str(role_id), [])])
        permission = role.get("permission")
        if isinstance(permission, dict):
            permission.pop("skill", None)
        role["mode"] = "subagent" if field.startswith("subagents[") else "all"
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
