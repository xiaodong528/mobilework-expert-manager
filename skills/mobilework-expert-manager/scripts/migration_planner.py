#!/usr/bin/env python3
"""Read-only legacy expert migration analysis."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import archive_inspector
import provenance


class MigrationPlanError(ValueError):
    pass


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "expert.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationPlanError(f"cannot read legacy expert.json: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationPlanError("legacy expert.json must be a JSON object")
    return value


def _role_pointers(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    roles: list[tuple[str, dict[str, Any]]] = []
    if isinstance(manifest.get("agent"), dict):
        roles.append(("/agent", manifest["agent"]))
    if isinstance(manifest.get("primary_agent"), dict):
        roles.append(("/primary_agent", manifest["primary_agent"]))
    for index, role in enumerate(manifest.get("subagents", [])):
        if isinstance(role, dict):
            roles.append((f"/subagents/{index}", role))
    return roles


def _directory_name_warnings(root: Path) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(directory_names)
        base = Path(current)
        for name in [*directory_names, *sorted(file_names)]:
            path = (base / name).relative_to(root).as_posix()
            if any(marker in name for marker in archive_inspector.MOJIBAKE_MARKERS):
                warnings.append({
                    "code": "MIGRATION_FILENAME_MOJIBAKE",
                    "severity": "warning",
                    "path": path,
                    "message": "filename appears to contain mojibake and needs user-confirmed renaming",
                })
    return warnings


def _plan_root(
    root: Path,
    source: Path,
    *,
    source_warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    manifest = _load_manifest(root)
    actions: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    moves: list[dict[str, str]] = []
    decisions: list[dict[str, str]] = []
    permission_changes: list[dict[str, str]] = []
    regenerate: set[str] = {"README.md", "opencode.json"}
    combined_warnings = [*(source_warnings or []), *_directory_name_warnings(root)]
    source_warnings = list(
        {
            (item.get("code", ""), item.get("path", "")): item
            for item in combined_warnings
        }.values()
    )

    common = manifest.get("common_skills")
    if isinstance(common, list) and any(isinstance(item, str) for item in common):
        replacement = [
            {"purpose": item} if isinstance(item, str) else item for item in common
        ]
        patches.append({"op": "replace", "path": "/common_skills", "value": replacement})
        actions.append({"code": "MIGRATE_LEGACY_COMMON_SKILLS", "mechanical": True})

    for pointer, role in _role_pointers(manifest):
        skills = role.get("skills")
        if isinstance(skills, list) and any(isinstance(item, str) for item in skills):
            patches.append({
                "op": "replace", "path": f"{pointer}/skills",
                "value": [{"purpose": item} if isinstance(item, str) else item for item in skills],
            })
            actions.append({"code": "MIGRATE_LEGACY_ROLE_SKILLS", "mechanical": True, "path": pointer})
        for legacy_key in ("maxTurns", "max_turns", "maxSteps"):
            if legacy_key in role:
                value = role[legacy_key]
                patches.extend([
                    {"op": "remove", "path": f"{pointer}/{legacy_key}"},
                    {"op": "add", "path": f"{pointer}/steps", "value": value},
                ])
                actions.append({"code": "MIGRATE_LEGACY_STEPS", "mechanical": True, "path": pointer})
        permission = role.get("permission")
        if isinstance(permission, dict) and isinstance(permission.get("bash"), dict) and permission["bash"].get("*") == "allow":
            permission_changes.append({
                "path": f"{pointer}/permission/bash/*",
                "from": "allow", "to": "calculated-autonomy-baseline",
                "reason": "unconditional Bash allow is not emitted by the current contract",
            })
            decisions.append({
                "code": "CONFIRM_BASH_REQUIREMENTS", "path": pointer,
                "question": "Which exact Bash command patterns are required by this role?",
            })
        role_id = role.get("id")
        if isinstance(role_id, str) and role_id:
            regenerate.add(f".opencode/agents/{role_id}.md")

    if (root / "AGENTS.md").exists():
        decisions.append({
            "code": "CONFIRM_ROOT_INSTRUCTIONS",
            "path": "AGENTS.md",
            "question": "Which rules belong in a namespaced instruction file?",
        })

    for warning in source_warnings:
        if warning.get("code") == "MIGRATION_FILENAME_MOJIBAKE":
            decisions.append({
                "code": "CONFIRM_MOJIBAKE_FILENAME",
                "path": warning["path"],
                "question": "What is the intended Unicode filename for this mojibake resource?",
            })

    references = manifest.get("references", [])
    if isinstance(references, list):
        slug = str(manifest.get("slug", "expert"))
        for index, item in enumerate(references):
            if not isinstance(item, dict):
                continue
            old_path = item.get("path")
            if isinstance(old_path, str) and old_path.startswith("references/"):
                destination = f".opencode/references/{slug}/{Path(old_path).name}"
                moves.append({"from": old_path, "to": destination})
                patches.append({"op": "replace", "path": f"/references/{index}/path", "value": destination})
                actions.append({"code": "MOVE_REFERENCE_NAMESPACE", "mechanical": True, "path": old_path})

    return {
        "schemaVersion": 1,
        "mode": "read-only",
        "source": str(source),
        "slug": manifest.get("slug"),
        "type": manifest.get("type"),
        "automaticActions": actions,
        "jsonPatchCandidates": patches,
        "resourceMoves": moves,
        "businessDecisions": decisions,
        "permissionChanges": permission_changes,
        "sourceWarnings": source_warnings,
        "unconfirmedCount": len(decisions),
        "regenerate": sorted(regenerate),
        "execution": {"attempted": False, "reason": "read-only-migration-planner"},
        "provenance": provenance.collect(input_path=source),
    }


def plan(source: Path) -> dict[str, Any]:
    source = source.expanduser().absolute()
    if source.is_dir():
        return _plan_root(source, source)
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise MigrationPlanError("source must be an expert directory or ZIP")
    inspection = archive_inspector.inspect_archive(source)
    if inspection.errors:
        codes = ", ".join(sorted({item.code for item in inspection.errors}))
        raise MigrationPlanError(f"archive preflight failed: {codes}")
    with zipfile.ZipFile(source) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise MigrationPlanError(f"ZIP CRC failed: {bad}")
    with tempfile.TemporaryDirectory(prefix="mobilework-migration-plan-") as temp:
        target = Path(temp)
        archive_inspector.safe_extract(source, target, inspection)
        warnings = [
            {
                "code": item.code,
                "severity": item.severity,
                "path": item.path,
                "message": item.message,
            }
            for item in inspection.warnings
        ]
        return _plan_root(target / inspection.roots[0], source, source_warnings=warnings)


def render_markdown(plan_data: dict[str, Any]) -> str:
    lines = [
        "# MobileWork legacy migration plan", "",
        f"- Source: `{plan_data['source']}`",
        f"- Mode: `{plan_data['mode']}`",
        f"- Automatic actions: {len(plan_data['automaticActions'])}",
        f"- User decisions: {plan_data['unconfirmedCount']}", "",
        "## Automatic actions", "",
    ]
    if plan_data["automaticActions"]:
        lines.extend(
            f"- `{item['code']}`" + (f" at `{item['path']}`" if item.get("path") else "")
            for item in plan_data["automaticActions"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Candidate JSON Patch", "", "```json",
        json.dumps(plan_data["jsonPatchCandidates"], ensure_ascii=False, indent=2),
        "```", "", "## Resource moves", "",
    ])
    if plan_data["resourceMoves"]:
        lines.extend(
            f"- `{item['from']}` → `{item['to']}`" for item in plan_data["resourceMoves"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Permission changes", ""])
    if plan_data["permissionChanges"]:
        lines.extend(
            f"- `{item['path']}`: `{item['from']}` → `{item['to']}` — {item['reason']}"
            for item in plan_data["permissionChanges"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Source warnings", ""])
    if plan_data["sourceWarnings"]:
        lines.extend(
            f"- `[{item['code']}]` `{item['path']}`: {item['message']}"
            for item in plan_data["sourceWarnings"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Decisions", ""])
    if plan_data["businessDecisions"]:
        lines.extend(f"- {item['question']} (`{item['path']}`)" for item in plan_data["businessDecisions"])
    else:
        lines.append("- None")
    lines.extend(["", "## Regenerate after migration", ""])
    lines.extend(f"- `{path}`" for path in plan_data["regenerate"])
    return "\n".join(lines) + "\n"
