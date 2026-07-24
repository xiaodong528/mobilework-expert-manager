#!/usr/bin/env python3
"""Install a validated MobileWork expert package into workspace .opencode."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import package_contract as contract
import provenance
import validate_expert


RUNTIME_CONFIG = "opencode.json"


class InstallRecoveryError(RuntimeError):
    def __init__(self, message: str, recovery_paths: list[str]) -> None:
        super().__init__(message)
        self.recovery_paths = recovery_paths


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"{path} root must be an object")
    return data


def load_jsonc(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    try:
        return contract.parse_jsonc(path.read_text(encoding="utf-8"), str(path))
    except contract.ContractError as exc:
        fail(str(exc))


def receipt_path(runtime_dir: Path, slug: str) -> Path:
    return runtime_dir / contract.INSTALL_RECEIPT_DIR / f"{slug}.json"


def load_receipts(runtime_dir: Path) -> dict[str, dict[str, Any]]:
    root = runtime_dir / contract.INSTALL_RECEIPT_DIR
    if not root.is_dir():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        data = load_json(path)
        slug = data.get("slug")
        if isinstance(slug, str):
            result[slug] = data
    return result


def file_owners(receipts: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    for slug, receipt in receipts.items():
        files = receipt.get("files", {})
        if not isinstance(files, dict):
            continue
        for relative in files:
            owners.setdefault(str(relative), set()).add(slug)
    return owners


def config_owners(
    receipts: dict[str, dict[str, Any]],
    section: str,
    key: str,
) -> set[str]:
    owners: set[str] = set()
    for slug, receipt in receipts.items():
        values = receipt.get("config_values", {})
        if not isinstance(values, dict):
            continue
        section_values = values.get(section, {})
        if isinstance(section_values, dict) and key in section_values:
            owners.add(slug)
    return owners


def list_owners(
    receipts: dict[str, dict[str, Any]],
    section: str,
    item: str,
) -> set[str]:
    owners: set[str] = set()
    for slug, receipt in receipts.items():
        values = receipt.get("config_values", {})
        if not isinstance(values, dict):
            continue
        section_values = values.get(section, [])
        if isinstance(section_values, list) and item in section_values:
            owners.add(slug)
    return owners


def dependency_owners(
    receipts: dict[str, dict[str, Any]],
    section: str,
    name: str,
) -> set[str]:
    owners: set[str] = set()
    for slug, receipt in receipts.items():
        dependencies = receipt.get("dependencies", {})
        if not isinstance(dependencies, dict):
            continue
        values = dependencies.get(section, {})
        if isinstance(values, dict) and name in values:
            owners.add(slug)
    return owners


def merge_mapping(
    config: dict[str, Any],
    section: str,
    incoming: Any,
    *,
    slug: str,
    force: bool,
    receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if incoming is None:
        return {}
    if not isinstance(incoming, dict):
        fail(f"package {RUNTIME_CONFIG}.{section} must be an object")
    current = config.setdefault(section, {})
    if not isinstance(current, dict):
        fail(f"workspace {contract.WORKSPACE_CONFIG}.{section} must be an object")
    recorded: dict[str, Any] = {}
    for key, value in incoming.items():
        if key in current:
            owners = config_owners(receipts, section, str(key))
            other_owners = owners - {slug}
            if other_owners:
                fail(f"{section}.{key} is owned by another expert: {', '.join(sorted(other_owners))}")
            if slug not in owners:
                fail(f"{section}.{key} conflicts with unowned workspace config")
            if current[key] != value and not force:
                fail(f"{section}.{key} can only be upgraded by the same slug with --force")
        current[key] = value
        recorded[str(key)] = value
    return recorded


def merge_list(config: dict[str, Any], section: str, incoming: Any) -> list[str]:
    if incoming is None:
        return []
    if not isinstance(incoming, list) or not all(isinstance(item, str) for item in incoming):
        fail(f"package {RUNTIME_CONFIG}.{section} must be a list of strings")
    current = config.setdefault(section, [])
    if not isinstance(current, list):
        fail(f"workspace {contract.WORKSPACE_CONFIG}.{section} must be a list")
    for item in incoming:
        if item not in current:
            current.append(item)
    return list(incoming)


def prune_owned_config(
    config: dict[str, Any],
    runtime: dict[str, Any],
    *,
    slug: str,
    own_receipt: dict[str, Any] | None,
    receipts: dict[str, dict[str, Any]],
    force: bool,
) -> None:
    if not own_receipt or not force:
        return
    old_values = own_receipt.get("config_values", {})
    if not isinstance(old_values, dict):
        fail(f"receipt for {slug} has invalid config_values")

    for section in ["agent", "mcp", "references", "lsp"]:
        old_section = old_values.get(section, {})
        if not isinstance(old_section, dict):
            continue
        incoming_section = runtime.get(section)
        incoming_keys = set(incoming_section) if isinstance(incoming_section, dict) else set()
        current_section = config.get(section)
        for key, old_value in old_section.items():
            if key in incoming_keys:
                continue
            if not isinstance(current_section, dict) or key not in current_section:
                continue
            if current_section[key] != old_value:
                fail(f"cannot remove changed owned config {section}.{key}; workspace value drifted from receipt")
            if config_owners(receipts, section, str(key)) - {slug}:
                continue
            del current_section[key]
        if isinstance(current_section, dict) and not current_section:
            config.pop(section, None)

    old_scalar = old_values.get("__scalar__", {})
    if isinstance(old_scalar, dict) and "lsp" in old_scalar and not isinstance(runtime.get("lsp"), bool):
        if "lsp" in config:
            if config["lsp"] != old_scalar["lsp"]:
                fail("cannot remove changed owned config lsp; workspace value drifted from receipt")
            if not (config_owners(receipts, "__scalar__", "lsp") - {slug}):
                config.pop("lsp")

    for section in ["plugin", "instructions"]:
        old_items = old_values.get(section, [])
        if not isinstance(old_items, list):
            continue
        incoming_items = runtime.get(section)
        incoming_set = set(incoming_items) if isinstance(incoming_items, list) else set()
        current_items = config.get(section)
        if current_items is None:
            continue
        if not isinstance(current_items, list):
            fail(f"workspace {contract.WORKSPACE_CONFIG}.{section} must be a list")
        for item in old_items:
            if not isinstance(item, str) or item in incoming_set:
                continue
            if list_owners(receipts, section, item) - {slug}:
                continue
            current_items[:] = [value for value in current_items if value != item]
        if not current_items:
            config.pop(section, None)


def prune_owned_dependencies(
    package_json: dict[str, Any],
    incoming: dict[str, Any],
    *,
    slug: str,
    own_receipt: dict[str, Any] | None,
    receipts: dict[str, dict[str, Any]],
    force: bool,
) -> None:
    if not own_receipt or not force:
        return
    old_dependencies = own_receipt.get("dependencies", {})
    if not isinstance(old_dependencies, dict):
        fail(f"receipt for {slug} has invalid dependencies")
    for section in ["dependencies", "devDependencies"]:
        old_section = old_dependencies.get(section, {})
        if not isinstance(old_section, dict):
            continue
        incoming_section = incoming.get(section, {})
        incoming_names = set(incoming_section) if isinstance(incoming_section, dict) else set()
        current = package_json.get(section)
        if current is None:
            continue
        if not isinstance(current, dict):
            fail(f"workspace package.json.{section} must be an object")
        for name, old_version in old_section.items():
            if name in incoming_names or name not in current:
                continue
            if current[name] != old_version:
                fail(f"cannot remove changed owned dependency {section}.{name}; version drifted from receipt")
            if dependency_owners(receipts, section, str(name)) - {slug}:
                continue
            del current[name]
        if not current:
            package_json.pop(section, None)


def merge_lsp(
    config: dict[str, Any],
    incoming: Any,
    *,
    slug: str,
    force: bool,
    receipts: dict[str, dict[str, Any]],
) -> Any:
    if incoming is None:
        return None
    if isinstance(incoming, dict):
        return merge_mapping(
            config,
            "lsp",
            incoming,
            slug=slug,
            force=force,
            receipts=receipts,
        )
    if not isinstance(incoming, bool):
        fail("package opencode.json.lsp must be a boolean or mapping")
    if "lsp" in config:
        owners = config_owners(receipts, "__scalar__", "lsp")
        other_owners = owners - {slug}
        if other_owners:
            fail(f"lsp is owned by another expert: {', '.join(sorted(other_owners))}")
        if slug not in owners:
            fail("lsp conflicts with unowned workspace config")
        if config["lsp"] != incoming and not force:
            fail("lsp can only be upgraded by the same slug with --force")
    config["lsp"] = incoming
    return incoming


def rebase_runtime_config(runtime: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(runtime, ensure_ascii=False))
    references = result.get("references")
    if isinstance(references, dict):
        result["references"] = {
            alias: contract.rebase_reference_entry(entry)
            for alias, entry in references.items()
        }
    instructions = result.get("instructions")
    if isinstance(instructions, list):
        result["instructions"] = [contract.rebase_instruction_entry(item) for item in instructions]
    return result


def merge_package_json(
    target: dict[str, Any],
    incoming: dict[str, Any],
    *,
    slug: str,
    force: bool,
    receipts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    try:
        normalized_incoming = contract.normalize_package_dependencies(
            incoming,
            ".opencode/package.json",
        )
    except contract.ContractError as exc:
        fail(str(exc))
    recorded: dict[str, dict[str, str]] = {}
    for section in contract.PACKAGE_DEPENDENCY_SECTIONS:
        values = normalized_incoming.get(section, {})
        current = target.setdefault(section, {})
        if not isinstance(current, dict):
            fail(f"workspace package.json.{section} must be an object")
        recorded[section] = {}
        for name, version in values.items():
            for other_section in contract.PACKAGE_DEPENDENCY_SECTIONS:
                if other_section == section:
                    continue
                other_values = target.get(other_section)
                if isinstance(other_values, dict) and name in other_values:
                    fail(
                        f"{section}.{name} conflicts with the existing dependency group "
                        f"{other_section}"
                    )
            if name in current and current[name] != version:
                owners = dependency_owners(receipts, section, name)
                other_owners = owners - {slug}
                if other_owners:
                    fail(
                        f"{section}.{name} version conflicts with another expert: "
                        f"{', '.join(sorted(other_owners))}"
                    )
                if slug not in owners:
                    fail(f"{section}.{name} version conflicts with unowned workspace dependency")
                if not force:
                    fail(f"{section}.{name} can only be upgraded by the same slug with --force")
            current[name] = version
            recorded[section][name] = version
    return recorded


def copy_sources(package_runtime: Path) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for path in sorted(package_runtime.rglob("*")):
        if not path.is_file() or path.name == "package.json":
            continue
        relative = path.relative_to(package_runtime).as_posix()
        sources[relative] = path
    return sources


def commit_transaction(
    runtime_dir: Path,
    staged: dict[str, Path],
    stale: list[str],
) -> None:
    backup_root = runtime_dir / f".install-backup-{uuid.uuid4().hex}"
    backups: dict[str, Path] = {}
    written: list[Path] = []
    preserve_backup = False
    try:
        for relative in sorted(set([*staged, *stale])):
            target = runtime_dir / relative
            if target.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
                backups[relative] = backup
        for relative, source in sorted(staged.items()):
            target = runtime_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            written.append(target)
    except Exception as commit_error:
        recovery_errors: list[Exception] = []
        recovery_paths: list[str] = []
        for target in reversed(written):
            try:
                if target.exists():
                    target.unlink()
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)
                recovery_paths.append(str(target))
        for relative, backup in reversed(list(backups.items())):
            target = runtime_dir / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    raise FileNotFoundError(f"rollback backup is missing: {backup}")
                os.replace(backup, target)
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)
                recovery_paths.extend([str(backup), str(target)])
        if recovery_errors:
            preserve_backup = True
            unique_paths = list(dict.fromkeys(recovery_paths))
            raise InstallRecoveryError(
                "install rollback failed; preserve recovery paths: " + ", ".join(unique_paths),
                unique_paths,
            ) from commit_error
        raise
    finally:
        if not preserve_backup:
            shutil.rmtree(backup_root, ignore_errors=True)


def install_package(package_dir: Path, workspace_dir: Path, *, force: bool) -> dict[str, Any]:
    package_dir = package_dir.expanduser().absolute()
    if package_dir.is_symlink():
        fail(f"package directory must not be a symlink: {package_dir}")
    package_dir = package_dir.resolve()
    workspace_dir = workspace_dir.expanduser().resolve()
    if not package_dir.is_dir():
        fail(f"package directory does not exist: {package_dir}")
    if not workspace_dir.is_dir():
        fail(f"workspace directory does not exist: {workspace_dir}")

    validation = validate_expert.validate_package(package_dir)
    if not validation.ok:
        fail("package validation failed: " + "; ".join(validation.errors[:8]))
    manifest = load_json(package_dir / validate_expert.MANIFEST_FILE)
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not contract.NAME_RE.fullmatch(slug):
        fail("expert.json slug is invalid")
    runtime = rebase_runtime_config(load_json(package_dir / RUNTIME_CONFIG))

    runtime_dir = workspace_dir / contract.WORKSPACE_RUNTIME_DIR
    if runtime_dir.is_symlink():
        fail(f"workspace runtime directory must not be a symlink: {runtime_dir}")
    runtime_dir_existed = runtime_dir.exists()
    if runtime_dir_existed:
        try:
            contract.assert_no_symlinks(runtime_dir)
        except contract.ContractError as exc:
            fail(f"workspace runtime contains an unsafe symlink: {exc}")
    config_path = runtime_dir / contract.WORKSPACE_CONFIG
    receipts = load_receipts(runtime_dir)
    own_receipt = receipts.get(slug)
    if own_receipt and not force:
        fail(f"{slug} is already installed; rerun with --force to upgrade it")
    owners = file_owners(receipts)

    package_runtime = package_dir / contract.PACKAGE_RUNTIME_DIR
    sources = copy_sources(package_runtime)
    file_hashes: dict[str, str] = {}
    for relative, source in sources.items():
        target = runtime_dir / relative
        file_hashes[relative] = contract.sha256_file(source)
        if target.is_symlink():
            fail(f"workspace target must not be a symlink: {target}")
        if not target.exists():
            continue
        if target.is_dir():
            fail(f"target path is a directory but package provides a file: {target}")
        path_owners = owners.get(relative, set())
        other_owners = path_owners - {slug}
        if other_owners:
            fail(
                f"resource conflict at {relative}; owned by {', '.join(sorted(other_owners))}; "
                "--force only upgrades the same slug"
            )
        if slug not in path_owners:
            fail(f"resource conflict at {relative} with unowned workspace file")
        if not force and contract.sha256_file(target) != file_hashes[relative]:
            fail(f"resource conflict at {relative}; --force only upgrades the same slug")

    config = load_jsonc(config_path)
    prune_owned_config(
        config,
        runtime,
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=force,
    )
    config_values: dict[str, Any] = {}
    for section in ["agent", "mcp", "references"]:
        values = merge_mapping(
            config,
            section,
            runtime.get(section),
            slug=slug,
            force=force,
            receipts=receipts,
        )
        if values:
            config_values[section] = values
    for section in ["plugin", "instructions"]:
        values = merge_list(config, section, runtime.get(section))
        if values:
            config_values[section] = values
    lsp = merge_lsp(config, runtime.get("lsp"), slug=slug, force=force, receipts=receipts)
    if lsp is not None:
        if isinstance(lsp, dict):
            config_values["lsp"] = lsp
        else:
            config_values["__scalar__"] = {"lsp": lsp}

    target_package_json_path = runtime_dir / "package.json"
    target_package_json = load_json(target_package_json_path) if target_package_json_path.exists() else {}
    package_json_path = package_runtime / "package.json"
    incoming_package_json = load_json(package_json_path) if package_json_path.exists() else {}
    prune_owned_dependencies(
        target_package_json,
        incoming_package_json,
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=force,
    )
    dependencies = merge_package_json(
        target_package_json,
        incoming_package_json,
        slug=slug,
        force=force,
        receipts=receipts,
    )

    runtime_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{slug}.install-", dir=runtime_dir))
    staged: dict[str, Path] = {}
    try:
        for relative, source in sources.items():
            target = staging_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged[relative] = target
        staged_config = staging_root / contract.WORKSPACE_CONFIG
        staged_config.write_text(contract.dump_json(config), encoding="utf-8")
        staged[contract.WORKSPACE_CONFIG] = staged_config
        if incoming_package_json or target_package_json_path.exists():
            staged_package_json = staging_root / "package.json"
            staged_package_json.write_text(contract.dump_json(target_package_json), encoding="utf-8")
            staged["package.json"] = staged_package_json

        receipt = {
            "contract": 1,
            "slug": slug,
            "files": file_hashes,
            "config_values": config_values,
            "dependencies": dependencies,
        }
        receipt_relative = f"{contract.INSTALL_RECEIPT_DIR}/{slug}.json"
        staged_receipt = staging_root / receipt_relative
        staged_receipt.parent.mkdir(parents=True, exist_ok=True)
        staged_receipt.write_text(contract.dump_json(receipt), encoding="utf-8")
        staged[receipt_relative] = staged_receipt

        stale: list[str] = []
        if own_receipt and force:
            old_files = own_receipt.get("files", {})
            if isinstance(old_files, dict):
                stale = sorted(
                    relative
                    for relative in set(str(item) for item in old_files) - set(sources)
                    if not (owners.get(relative, set()) - {slug})
                )
        commit_transaction(runtime_dir, staged, stale)
    except BaseException:
        if not runtime_dir_existed:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    receipt_file = receipt_path(runtime_dir, slug)
    evidence = provenance.collect(input_path=package_dir)
    evidence.update({
        "temporaryInstallTarget": str(workspace_dir),
        "receipt": {
            "path": str(receipt_file),
            "sha256": contract.sha256_file(receipt_file),
            "fileCount": len(file_hashes),
            "configSections": sorted(config_values),
        },
    })
    return {
        "ok": True,
        "schemaVersion": 2,
        "evidenceLevel": "installable",
        "gates": {
            "archive": "not-run",
            "contract": "passed",
            "portability": "passed",
            "install": "passed",
            "configLoad": "not-run",
        },
        "runtime": {"status": "not-tested", "reason": "install-only"},
        "provenance": evidence,
        "status": "installable",
        "runtime_status": "runtime-not-tested",
        "workspace": str(workspace_dir),
        "package": str(package_dir),
        "slug": slug,
        "runtime_dir": str(runtime_dir),
        "config": str(config_path),
        "files": sorted(file_hashes),
        "references": sorted((runtime.get("references") or {}).keys()),
        "instructions": runtime.get("instructions") or [],
        "required_environment": contract.extract_env_references(runtime),
        "receipt": str(receipt_file),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path, help="Generated expert package directory")
    parser.add_argument("--workspace-dir", required=True, type=Path, help="Target MobileWork workspace directory")
    parser.add_argument("--force", action="store_true", help="Upgrade resources owned by the same expert slug")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = install_package(args.package_dir, args.workspace_dir, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
