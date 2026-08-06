#!/usr/bin/env python3
"""Import an untrusted skill into a MobileWork expert and assign it to roles."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import create_expert
import cli_contract
import diagnose_skill
import execution_context
import manifest_contract
import output_sanitizer
import package_contract
import safe_input
import skill_contract
import validate_expert


class ImportSkillError(RuntimeError):
    """Raised when a skill import cannot be committed safely."""


def _inspect_source(source: Path) -> safe_input.InputSnapshot:
    try:
        return safe_input.inspect(source)
    except safe_input.InputInspectionError as exc:
        raise ImportSkillError(str(exc)) from exc


def load_manifest(package_dir: Path) -> dict[str, Any]:
    path = package_dir / create_expert.MANIFEST_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImportSkillError(f"cannot read expert.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ImportSkillError("expert.json must contain an object")
    return value


def package_roles(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    roles = manifest_contract.manifest_roles(manifest)
    if not roles:
        raise ImportSkillError("expert.json does not declare valid roles")
    return roles


def assignment_ids(
    manifest: dict[str, Any],
    *,
    requested: list[str],
    all_members: bool,
) -> list[str]:
    roles = package_roles(manifest)
    role_ids = [
        role["id"]
        for _field, role in roles
        if isinstance(role.get("id"), str)
    ]
    if manifest.get("type") == "expert":
        if requested or all_members:
            raise ImportSkillError(
                "single experts assign uploaded skills automatically; "
                "do not pass --assign-to or --all-members"
            )
        return role_ids
    if manifest.get("type") != "team":
        raise ImportSkillError("expert.json type must be expert or team")
    if all_members:
        return role_ids
    if not requested:
        raise ImportSkillError(
            "expert teams require --assign-to <agent-id> or --all-members"
        )
    duplicates = package_contract.first_duplicate(requested)
    if duplicates is not None:
        raise ImportSkillError(f"--assign-to duplicates {duplicates}")
    unknown = sorted(set(requested) - set(role_ids))
    if unknown:
        raise ImportSkillError(
            f"--assign-to references unknown Agent IDs: {', '.join(unknown)}"
        )
    return requested


def rebuild_skill_resources(package_dir: Path, manifest: dict[str, Any]) -> None:
    resources: list[dict[str, str]] = []
    skills_root = (
        package_dir
        / package_contract.PACKAGE_RUNTIME_DIR
        / package_contract.SKILLS_SUBDIR
    )
    expected = set(skill_contract.catalog_names(manifest))
    actual = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    } if skills_root.is_dir() else set()
    if actual != expected:
        raise ImportSkillError(
            "skill directories do not match the unified catalog; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    for name in sorted(expected):
        root = skills_root / name
        package_contract.assert_no_symlinks(root)
        resources.extend(
            skill_contract.file_resources(root, package_root=package_dir)
        )
    manifest["package_resources"] = sorted(resources, key=lambda item: item["path"])


def update_assignments(
    manifest: dict[str, Any],
    *,
    skill_name: str,
    assigned_to: list[str],
) -> None:
    selected = set(assigned_to)
    for _field, role in package_roles(manifest):
        role_id = role.get("id")
        refs = skill_contract.normalize_role_refs(
            role.get("skills"),
            f"{role_id}.skills",
        )
        if role_id in selected and skill_name not in refs:
            refs.append(skill_name)
        role["skills"] = refs
        permission = role.get("permission")
        if isinstance(permission, dict):
            permission.pop("skill", None)


def import_skill(
    package_dir: Path,
    source: Path,
    *,
    assign_to: list[str],
    all_members: bool,
    replace: bool,
    confirm_managed: bool,
) -> dict[str, Any]:
    package_dir = execution_context.canonical_path(package_dir)
    source = source.expanduser().absolute()
    if replace != confirm_managed:
        raise ImportSkillError(
            "--replace and --confirm-managed must be provided together"
        )
    source_snapshot = _inspect_source(source)
    validation = validate_expert.validate_package(package_dir)
    if not validation.ok:
        raise ImportSkillError(
            "target expert package is invalid: " + "; ".join(validation.errors[:8])
        )
    initial_revision = create_expert.calculate_package_revision(package_dir)

    with tempfile.TemporaryDirectory(prefix="mobilework-skill-import-") as temp:
        temp_root = Path(temp)
        staged_source = source_snapshot.materialize(
            temp_root / "source-snapshot" / source_snapshot.source.name
        )
        source_root = diagnose_skill.materialize_skill(
            staged_source,
            temp_root / "uploaded",
        )
        diagnosis = diagnose_skill.diagnose(source_root)
        if not diagnosis.ok:
            raise ImportSkillError(
                "uploaded skill failed static diagnosis: "
                + "; ".join(diagnosis.errors[:8])
            )
        temp_package = temp_root / package_dir.name
        shutil.copytree(
            package_dir,
            temp_package,
            ignore=shutil.ignore_patterns(".git"),
        )
        manifest = load_manifest(temp_package)
        assigned = assignment_ids(
            manifest,
            requested=assign_to,
            all_members=all_members,
        )
        if skill_contract.schema_mode(manifest) == "legacy":
            manifest = skill_contract.migrate_legacy_manifest(
                temp_package,
                manifest,
            )

        skill_name = source_root.name
        source_tree = skill_contract.tree_sha256(source_root)
        skills_root = (
            temp_package
            / package_contract.PACKAGE_RUNTIME_DIR
            / package_contract.SKILLS_SUBDIR
        )
        destination = skills_root / skill_name
        catalog = skill_contract.normalize_catalog(manifest.get("skills"))
        existing = next(
            (item for item in catalog if item["name"] == skill_name),
            None,
        )
        action = "imported"
        if existing is not None:
            existing_tree = skill_contract.tree_sha256(destination)
            if existing_tree == source_tree:
                action = "reused"
            else:
                if not replace:
                    raise ImportSkillError(
                        f"skill {skill_name} already exists with different content"
                    )
                shutil.rmtree(destination)
                shutil.copytree(source_root, destination)
                existing["edit_policy"] = "managed"
                action = "replaced"
        else:
            if destination.exists():
                raise ImportSkillError(
                    f"undeclared destination already exists: {destination}"
                )
            shutil.copytree(source_root, destination)
            catalog.append(
                {
                    "name": skill_name,
                    "origin": "uploaded",
                    "edit_policy": "preserved",
                }
            )
        manifest["skills"] = catalog
        manifest.pop("common_skills", None)
        update_assignments(
            manifest,
            skill_name=skill_name,
            assigned_to=assigned,
        )
        rebuild_skill_resources(temp_package, manifest)
        (temp_package / create_expert.MANIFEST_FILE).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            normalized = create_expert.normalize_manifest(
                manifest,
                manifest_dir=temp_package,
            )
            create_expert.prepare_avatar_assets(normalized, temp_package)
            with create_expert.package_lock(package_dir.parent, manifest["slug"]):
                if not package_dir.is_dir():
                    raise ImportSkillError(
                        "target expert package disappeared during import"
                    )
                current_revision = create_expert.calculate_package_revision(package_dir)
                if current_revision != initial_revision:
                    raise ImportSkillError(
                        "target expert package changed during import; "
                        "retry from the new revision"
                    )
                written = create_expert._write_project_locked(
                    normalized,
                    package_dir.parent,
                    force=True,
                )
        except SystemExit as exc:
            raise ImportSkillError(str(exc)) from exc
        final_validation = validate_expert.validate_package(written)
        if not final_validation.ok:
            raise ImportSkillError(
                "committed package failed validation: "
                + "; ".join(final_validation.errors[:8])
            )
        final_manifest = load_manifest(written)
        final_entry = skill_contract.skill_entry(final_manifest, skill_name)
        return {
            "status": "package-valid",
            "runtimeStatus": "runtime-not-tested",
            "action": action,
            "package": str(written),
            "skill": skill_name,
            "treeSha256": skill_contract.tree_sha256(
                written
                / package_contract.PACKAGE_RUNTIME_DIR
                / package_contract.SKILLS_SUBDIR
                / skill_name
            ),
            "assignedTo": assigned,
            "origin": final_entry["origin"] if final_entry else "",
            "editPolicy": final_entry["edit_policy"] if final_entry else "",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--skill", required=True, type=Path)
    assignment = parser.add_mutually_exclusive_group()
    assignment.add_argument("--assign-to", action="append", default=[])
    assignment.add_argument("--all-members", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--confirm-managed", action="store_true")
    return parser.parse_args()


def _legacy_main() -> int:
    args = parse_args()
    try:
        result = import_skill(
            args.package_dir,
            args.skill,
            assign_to=args.assign_to,
            all_members=args.all_members,
            replace=args.replace,
            confirm_managed=args.confirm_managed,
        )
    except (ImportSkillError, package_contract.ContractError) as exc:
        print(f"error: {output_sanitizer.sanitize_exception(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "error: internal manager failure: "
            + output_sanitizer.sanitize_exception(exc),
            file=sys.stderr,
        )
        return 3
    print(output_sanitizer.json_dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "import-skill", _legacy_main, argv=argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
