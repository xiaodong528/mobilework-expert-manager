#!/usr/bin/env python3
"""Install or safely uninstall a MobileWork expert package in workspace .opencode."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cli_contract
import manager_contract


def _guard_startup_policy() -> None:
    try:
        manager_contract.load_policy()
    except manager_contract.ManagerContractError as error:
        output_format, schema_version = cli_contract.requested_output(sys.argv[1:])
        failure = cli_contract.CliInternalError(
            str(error),
            code="MANAGER_POLICY_INVALID",
            phase="manager-contract",
        )
        raise SystemExit(
            cli_contract.run_cli(
                "install-expert",
                lambda: (_ for _ in ()).throw(failure),
                output_format=output_format,
                schema_version=schema_version,
            )
        )


if __name__ == "__main__":
    _guard_startup_policy()


import package_contract as contract
import drift_backup
import install_state
import manifest_contract
import output_sanitizer
import package_snapshot
import posix_noreplace
import projection_contract
import provenance
import renderers
import safe_input
import secure_transaction
import validate_expert
import workspace_lock


RUNTIME_CONFIG = "opencode.json"


def _posix_platform() -> bool:
    return os.name == "posix"


def _posix_recovery_backend_available() -> bool:
    return _posix_platform() and posix_noreplace.available()


def _transaction_execution(operation: str, *, secure: bool) -> dict[str, Any]:
    if secure:
        return {
            "policy": "secure-posix-transaction",
            "attempted": True,
            "reason": operation,
            "transactionBackend": "posix-no-replace",
            "workspaceLockProtocol": 2,
            "transactionSecurity": "verified",
        }
    return {
        "policy": "windows-legacy-transaction-with-protocol-v2-lock",
        "attempted": True,
        "reason": operation,
        "transactionBackend": "windows-legacy",
        "workspaceLockProtocol": 2,
        "transactionSecurity": "partial",
    }


class InstallRecoveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        recovery_paths: list[str],
        *,
        code: str = "INSTALL_TRANSACTION_RECOVERY_REQUIRED",
        committed: bool | None = None,
        rollback_verified: bool | None = False,
    ) -> None:
        super().__init__(message)
        self.recovery_paths = recovery_paths
        self.code = code
        self.committed = committed
        self.rollback_verified = rollback_verified


class InstallDriftError(cli_contract.CliContractError):
    """Raised before workspace writes when receipt-owned state has drifted."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(
            "receipt-owned workspace state has drifted; review the preview before retrying",
            code="INSTALL_OWNED_STATE_DRIFT",
            status="drift-detected",
            phase="install-preflight",
        )
        self.report = report


def _confirmation_failure(message: str) -> cli_contract.CliContractError:
    return cli_contract.CliContractError(
        message,
        code="INSTALL_DRIFT_CONFIRMATION_MISMATCH",
        status="confirmation-mismatch",
        phase="install-preflight",
    )


def _backup_cli_evidence(record: drift_backup.BackupRecord) -> dict[str, str]:
    return {
        "backupId": record.backup_id,
        "backupSha256": record.backup_sha256,
        "previewSha256": record.preview_sha256,
        "slug": record.slug,
        "path": str(record.path),
    }


def _post_commit_failure(
    *,
    operation: str,
    code: str,
    message: str,
    backup: drift_backup.BackupRecord | None = None,
) -> cli_contract.CliInternalError:
    evidence = _backup_cli_evidence(backup) if backup is not None else None
    provenance = {"driftBackup": evidence} if evidence is not None else {}
    data: dict[str, Any] = {"committed": True, "readbackVerified": False}
    if evidence is not None:
        data["driftBackup"] = evidence
    return cli_contract.CliInternalError(
        message,
        code=code,
        status="committed-unverified",
        phase=f"{operation}-readback",
        attempted=True,
        execution_policy=f"{operation}-post-commit-readback",
        provenance=provenance,
        data=data,
    )


def _transaction_recovery_failure(
    *,
    operation: str,
    error: InstallRecoveryError,
    backup: drift_backup.BackupRecord | None = None,
) -> cli_contract.CliInternalError:
    backup_evidence = _backup_cli_evidence(backup) if backup is not None else None
    recovery_paths = [
        output_sanitizer.sanitize_text(path)
        for path in error.recovery_paths
    ]
    data: dict[str, Any] = {
        "committed": error.committed,
        "rollbackVerified": error.rollback_verified,
        "recoveryPaths": recovery_paths,
    }
    provenance: dict[str, Any] = {}
    if backup_evidence is not None:
        data["driftBackup"] = backup_evidence
        provenance["driftBackup"] = backup_evidence
    rollback_verified = (
        error.committed is False
        and error.rollback_verified is True
        and not recovery_paths
    )
    return cli_contract.CliInternalError(
        (
            "workspace transaction failed; rollback to the captured pre-state "
            "was verified"
            if rollback_verified
            else "workspace transaction recovery requires manual verification; "
            "preserve the reported recovery and backup evidence"
        ),
        code=error.code,
        status=("transaction-rolled-back" if rollback_verified else "transaction-recovery-required"),
        phase=f"{operation}-transaction",
        attempted=True,
        execution_policy=f"{operation}-transaction-recovery",
        provenance=provenance,
        data=data,
    )


class InstallProjection(NamedTuple):
    manifest: dict[str, Any]
    runtime: dict[str, Any]
    sources: dict[str, bytes]
    incoming_package_json: dict[str, Any]
    bindings: dict[str, Any]
    projection: dict[str, Any]
    reference_fallbacks: list[str]


def fail(message: str) -> None:
    raise SystemExit(f"error: {output_sanitizer.sanitize_text(message)}")


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


def _captured_target_state(
    runtime_dir: Path,
    snapshot: install_state.RuntimeInputSnapshot,
    relative: str,
) -> drift_backup.TargetState:
    captured = install_state.captured_runtime_file(snapshot, relative)
    if captured is None:
        return drift_backup.TargetState(relative, False, None, None)
    path = runtime_dir / relative
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise install_state.InstallStateError(
            "INSTALL_INPUT_STATE_CHANGED",
            "workspace target changed after its protected capture",
        ) from exc
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        stat.S_IMODE(metadata.st_mode),
        int(getattr(metadata, "st_file_attributes", 0)),
    )
    captured_identity = (
        captured.device,
        captured.inode,
        captured.size,
        captured.mtime_ns,
        captured.ctime_ns,
        captured.mode,
        captured.file_attributes,
    )
    if not stat.S_ISREG(metadata.st_mode) or identity != captured_identity:
        raise install_state.InstallStateError(
            "INSTALL_INPUT_STATE_CHANGED",
            "workspace target identity changed after its protected capture",
        )
    return drift_backup.TargetState(
        relative,
        True,
        captured.content,
        captured.mode,
    )


def _captured_target_states(
    runtime_dir: Path,
    snapshot: install_state.RuntimeInputSnapshot,
    relative_paths: set[str],
) -> dict[str, drift_backup.TargetState]:
    return {
        relative: _captured_target_state(runtime_dir, snapshot, relative)
        for relative in sorted(relative_paths)
    }


def _staged_target_states(
    staged: dict[str, Path],
    stale: list[str],
) -> dict[str, drift_backup.TargetState]:
    result: dict[str, drift_backup.TargetState] = {}
    for relative, source in sorted(staged.items()):
        metadata = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            fail("install staging contains a non-regular target")
        result[relative] = drift_backup.TargetState(
            relative,
            True,
            source.read_bytes(),
            stat.S_IMODE(metadata.st_mode),
        )
    for relative in sorted(stale):
        if relative not in result:
            result[relative] = drift_backup.TargetState(relative, False, None, None)
    return result


def _captured_receipt_bytes(
    snapshot: install_state.RuntimeInputSnapshot,
) -> dict[str, bytes]:
    if snapshot.receipt_snapshot is None:
        return {}
    return {
        item.relative_path: item.content
        for item in snapshot.receipt_snapshot.files
        if len(Path(item.relative_path).parts) == 1
        and item.relative_path.endswith(".json")
    }


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
            owners = install_state.config_owners(receipts, section, str(key))
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


def merge_list(
    config: dict[str, Any],
    section: str,
    incoming: Any,
    *,
    receipts: dict[str, dict[str, Any]],
) -> list[str]:
    if incoming is None:
        return []
    if not isinstance(incoming, list) or not all(isinstance(item, str) for item in incoming):
        fail(f"package {RUNTIME_CONFIG}.{section} must be a list of strings")
    current = config.setdefault(section, [])
    if not isinstance(current, list):
        fail(f"workspace {contract.WORKSPACE_CONFIG}.{section} must be a list")
    recorded: list[str] = []
    for item in incoming:
        if item not in current:
            current.append(item)
            recorded.append(item)
            continue
        if install_state.list_owners(receipts, section, item):
            recorded.append(item)
    return recorded


def prune_owned_config(
    config: dict[str, Any],
    runtime: dict[str, Any],
    *,
    slug: str,
    own_receipt: dict[str, Any] | None,
    receipts: dict[str, dict[str, Any]],
    force: bool,
    discard_drift: bool = False,
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
                if discard_drift:
                    continue
                fail(
                    f"cannot remove missing owned config {section}.{key}; "
                    "workspace value drifted from receipt"
                )
            if current_section[key] != old_value:
                if discard_drift:
                    if not (install_state.config_owners(receipts, section, str(key)) - {slug}):
                        del current_section[key]
                    continue
                fail(f"cannot remove changed owned config {section}.{key}; workspace value drifted from receipt")
            if install_state.config_owners(receipts, section, str(key)) - {slug}:
                continue
            del current_section[key]
        if isinstance(current_section, dict) and not current_section:
            config.pop(section, None)

    old_scalar = old_values.get("__scalar__", {})
    if isinstance(old_scalar, dict) and "lsp" in old_scalar and not isinstance(runtime.get("lsp"), bool):
        if "lsp" not in config:
            if discard_drift:
                pass
            else:
                fail("cannot remove missing owned config lsp; workspace value drifted from receipt")
        elif config["lsp"] != old_scalar["lsp"] and not discard_drift:
            fail("cannot remove changed owned config lsp; workspace value drifted from receipt")
        if "lsp" in config and not (install_state.config_owners(receipts, "__scalar__", "lsp") - {slug}):
            config.pop("lsp")

    if own_receipt.get("contract") not in install_state.extended_ownership_contracts():
        return

    for section in ["plugin", "instructions"]:
        old_items = old_values.get(section, [])
        if not isinstance(old_items, list):
            continue
        incoming_items = runtime.get(section)
        incoming_set = set(incoming_items) if isinstance(incoming_items, list) else set()
        removable = [
            item
            for item in old_items
            if isinstance(item, str) and item not in incoming_set
        ]
        if not removable:
            continue
        current_items = config.get(section)
        if current_items is None:
            if discard_drift:
                continue
            fail(
                f"cannot remove missing owned config {section}; "
                "workspace value drifted from receipt"
            )
        if not isinstance(current_items, list):
            fail(f"workspace {contract.WORKSPACE_CONFIG}.{section} must be a list")
        for item in removable:
            if item not in current_items:
                if discard_drift:
                    continue
                fail(
                    f"cannot remove missing owned config {section} entry {item}; "
                    "workspace value drifted from receipt"
                )
            if install_state.list_owners(receipts, section, item) - {slug}:
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
    discard_drift: bool = False,
) -> None:
    if not own_receipt or not force:
        return
    old_dependencies = own_receipt.get("dependencies", {})
    if not isinstance(old_dependencies, dict):
        fail(f"receipt for {slug} has invalid dependencies")
    if own_receipt.get("contract") not in install_state.extended_ownership_contracts():
        return
    for section in ["dependencies", "devDependencies"]:
        old_section = old_dependencies.get(section, {})
        if not isinstance(old_section, dict):
            continue
        incoming_section = incoming.get(section, {})
        incoming_names = set(incoming_section) if isinstance(incoming_section, dict) else set()
        current = package_json.get(section)
        removable_names = [name for name in old_section if name not in incoming_names]
        if not removable_names:
            continue
        if current is None:
            if discard_drift:
                continue
            fail(
                f"cannot remove missing owned dependency section {section}; "
                "workspace value drifted from receipt"
            )
        if not isinstance(current, dict):
            fail(f"workspace package.json.{section} must be an object")
        for name, old_version in old_section.items():
            if name in incoming_names:
                continue
            if name not in current:
                if discard_drift:
                    continue
                fail(
                    f"cannot remove missing owned dependency {section}.{name}; "
                    "workspace value drifted from receipt"
                )
            if current[name] != old_version:
                if discard_drift:
                    if not (install_state.dependency_owners(receipts, section, str(name)) - {slug}):
                        del current[name]
                    continue
                fail(f"cannot remove changed owned dependency {section}.{name}; version drifted from receipt")
            if install_state.dependency_owners(receipts, section, str(name)) - {slug}:
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
        owners = install_state.config_owners(receipts, "__scalar__", "lsp")
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
            existing = name in current
            owners = (
                install_state.dependency_owners(receipts, section, name)
                if existing
                else set()
            )
            if existing and current[name] != version:
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
            if not existing or owners:
                recorded[section][name] = version
    return recorded


def copy_sources(snapshot: safe_input.InputSnapshot) -> dict[str, bytes]:
    """Return managed runtime files from already captured package bytes."""

    sources: dict[str, bytes] = {}
    prefix = f"{contract.PACKAGE_RUNTIME_DIR}/"
    for item in snapshot.files:
        if not item.relative_path.startswith(prefix):
            continue
        relative = item.relative_path[len(prefix) :]
        if relative == "package.json":
            continue
        sources[relative] = item.content
    return sources


def role_reference_consumers(manifest: dict[str, Any]) -> dict[str, list[str]]:
    runtime_extensions = manifest.get("runtime_extensions", {})
    references = runtime_extensions.get("references", {}) if isinstance(runtime_extensions, dict) else {}
    aliases = list(references) if isinstance(references, dict) else []
    roles = manifest_contract.manifest_roles(manifest)
    explicit = any("references" in role for _field, role in roles)
    consumers = {alias: [] for alias in aliases}
    for _field, role in roles:
        role_id = role.get("id")
        if not isinstance(role_id, str):
            continue
        role_aliases = role.get("references", []) if explicit else aliases
        if not isinstance(role_aliases, list):
            continue
        for alias in role_aliases:
            if alias in consumers:
                consumers[alias].append(role_id)
    return consumers


def receipt_bindings(manifest: dict[str, Any]) -> dict[str, Any]:
    slug = str(manifest.get("slug", ""))
    references = {
        contract.namespaced_reference_alias(slug, alias): role_ids
        for alias, role_ids in role_reference_consumers(manifest).items()
        if role_ids
    }
    role_instruction_bindings: dict[str, list[str]] = {}
    for _field, role in manifest_contract.manifest_roles(manifest):
        role_id = role.get("id")
        aliases = role.get("instructions", [])
        if not isinstance(role_id, str) or not isinstance(aliases, list):
            continue
        for alias in aliases:
            if isinstance(alias, str):
                role_instruction_bindings.setdefault(alias, []).append(role_id)
    result: dict[str, Any] = {}
    if references:
        result["references"] = references
    if role_instruction_bindings:
        result["roleInstructions"] = role_instruction_bindings
    return result


def target_supports_references(target: manager_contract.TargetContract) -> bool:
    return target.capability_verified and target.capabilities.get("references") is True


def render_reference_fallback_skill(
    manifest: dict[str, Any],
    alias: str,
    entry: dict[str, Any],
    consumers: list[str],
) -> tuple[str, bytes]:
    slug = str(manifest["slug"])
    name = contract.reference_fallback_skill_name(slug, alias)
    description = entry.get("description") or "在任务需要时查阅本专家包随附资料。"
    relative_reference = entry["path"]
    body = "\n".join(
        [
            f"# {alias} 本地资料兼容入口",
            "",
            "目标 Runtime 没有原生 Reference 能力时使用本能力包。",
            f"只读查阅 `{relative_reference}`，使用时机：{description}",
            f"分配角色：{', '.join(f'`{item}`' for item in consumers)}。",
            "这项分配用于行为路由，不是文件系统访问隔离。不要执行资料目录中的代码或安装命令。",
        ]
    )
    rendered = renderers.render_frontmatter(
        {
            "name": name,
            "description": f"原生 Reference 不可用时，让已分配角色只读查阅 `{alias}` 资料。",
            "compatibility": "opencode",
            "metadata": {
                "package": slug,
                "reference": alias,
                "type": "reference-fallback",
            },
        },
        body,
    )
    return f"{contract.SKILLS_SUBDIR}/{name}/SKILL.md", rendered.encode("utf-8")


def apply_reference_capability(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    sources: dict[str, bytes],
    target: manager_contract.TargetContract,
) -> list[str]:
    runtime_extensions = manifest.get("runtime_extensions", {})
    references = runtime_extensions.get("references", {}) if isinstance(runtime_extensions, dict) else {}
    if not isinstance(references, dict) or not references or target_supports_references(target):
        return []

    git_aliases = sorted(
        alias
        for alias, entry in references.items()
        if isinstance(entry, dict) and "repository" in entry
    )
    if git_aliases:
        fail(
            "capability-missing: target Runtime has no verified Reference support for Git aliases "
            + ", ".join(git_aliases)
            + "; provide a trusted local checkout and import it as a local Reference"
        )

    runtime.pop("references", None)
    consumers = role_reference_consumers(manifest)
    generated: list[str] = []
    agents = runtime.get("agent")
    if not isinstance(agents, dict):
        fail("package opencode.json.agent must be an object")
    for alias, entry in references.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        path, content = render_reference_fallback_skill(
            manifest,
            alias,
            entry,
            consumers.get(alias, []),
        )
        if path in sources:
            fail(f"derived Reference fallback conflicts with package file {path}")
        sources[path] = content
        fallback_name = contract.reference_fallback_skill_name(str(manifest["slug"]), alias)
        generated.append(fallback_name)
        for role_id in consumers.get(alias, []):
            agent = agents.get(role_id)
            if not isinstance(agent, dict):
                fail(f"Reference consumer {role_id} is missing from package runtime config")
            permission = agent.get("permission")
            if not isinstance(permission, dict):
                fail(f"Reference consumer {role_id} has invalid permission config")
            skill_permission = permission.setdefault("skill", {"*": "deny"})
            if not isinstance(skill_permission, dict):
                fail(f"Reference consumer {role_id} has invalid permission.skill config")
            skill_permission[fallback_name] = "allow"
    return generated


def derive_install_projection(
    snapshot: safe_input.InputSnapshot,
    target: manager_contract.TargetContract,
) -> InstallProjection:
    """Derive the complete package-owned projection from one immutable snapshot."""

    try:
        manifest = package_snapshot.load_json(
            snapshot,
            validate_expert.MANIFEST_FILE,
        )
        runtime = rebase_runtime_config(
            package_snapshot.load_json(snapshot, RUNTIME_CONFIG)
        )
    except ValueError as exc:
        fail(str(exc))
    sources = copy_sources(snapshot)
    reference_fallbacks = apply_reference_capability(
        manifest,
        runtime,
        sources,
        target,
    )
    package_json_relative = f"{contract.PACKAGE_RUNTIME_DIR}/package.json"
    try:
        incoming_package_json = package_snapshot.load_json(
            snapshot,
            package_json_relative,
        )
    except ValueError as exc:
        if any(item.relative_path == package_json_relative for item in snapshot.files):
            fail(str(exc))
        incoming_package_json = {}
    bindings = receipt_bindings(manifest)
    projection = projection_contract.build(
        sources=sources,
        runtime=runtime,
        dependencies=incoming_package_json,
        bindings=bindings,
    )
    return InstallProjection(
        manifest=manifest,
        runtime=runtime,
        sources=sources,
        incoming_package_json=incoming_package_json,
        bindings=bindings,
        projection=projection,
        reference_fallbacks=reference_fallbacks,
    )


def commit_transaction(
    runtime_dir: Path,
    staged: dict[str, Path],
    stale: list[str],
    required_directories: list[str] | None = None,
    pre_commit_guard: Callable[[], None] | None = None,
    *,
    secure: bool = False,
) -> None:
    if secure:
        try:
            secure_transaction.commit(
                runtime_dir,
                staged,
                stale,
                required_directories or (),
                pre_commit_guard,
            )
        except secure_transaction.SecureTransactionRolledBackError as exc:
            raise cli_contract.CliInternalError(
                "workspace transaction failed; rollback to the captured "
                "pre-state was verified",
                code=exc.code,
                status="transaction-rolled-back",
                phase="install-transaction",
                attempted=True,
                execution_policy="secure-transaction-rolled-back",
                data={"committed": False, "rollbackVerified": True, "recoveryPaths": []},
            ) from exc
        except secure_transaction.SecureTransactionRecoveryError as exc:
            raise InstallRecoveryError(
                str(exc),
                exc.recovery_paths,
                code=exc.code,
                committed=exc.committed,
                rollback_verified=exc.rollback_verified,
            ) from exc
        except secure_transaction.SecureTransactionError as exc:
            if exc.code == "SECURE_TRANSACTION_NOREPLACE_REQUIRED":
                raise cli_contract.CliRuntimePolicyError(
                    "the target filesystem lost atomic no-replace support; "
                    "the attempted transaction was rolled back",
                    code="INSTALL_TRANSACTION_PLATFORM_BLOCKED",
                    phase="install-transaction",
                    attempted=True,
                    execution_policy="secure-transaction-no-replace",
                    data={"committed": False, "rollbackVerified": True},
                ) from exc
            raise install_state.InstallStateError(exc.code, str(exc)) from exc
        return

    # TODO(2026-08-04): Keep the legacy backend only for ordinary Windows writes.
    # Remove it when protocol-v2 locking and reparse-safe transactions pass the
    # Windows competition/crash-injection release gate.
    runtime_root = runtime_dir.resolve()
    normalized_staged: dict[str, Path] = {}
    normalized_stale: list[str] = []
    for label, values in (("staged", staged), ("stale", stale)):
        items = values.items() if isinstance(values, dict) else ((item, None) for item in values)
        for relative, source in items:
            try:
                normalized = contract.posix_relative_path(
                    relative,
                    f"install transaction {label} path",
                )
            except contract.ContractError as exc:
                fail(str(exc))
            target = (runtime_dir / normalized).resolve()
            if not target.is_relative_to(runtime_root):
                fail(f"install transaction {label} path escapes runtime directory: {relative}")
            if label == "staged":
                if normalized in normalized_staged:
                    fail(f"install transaction duplicates staged path: {normalized}")
                if source is None:
                    fail(f"install transaction staged source is missing: {normalized}")
                normalized_staged[normalized] = source
            else:
                normalized_stale.append(normalized)
    staged = normalized_staged
    stale = normalized_stale
    backup_root = runtime_dir / f".install-backup-{uuid.uuid4().hex}"
    backups: dict[str, Path] = {}
    written: list[Path] = []
    created_directories: list[Path] = []
    preserve_backup = False
    try:
        if pre_commit_guard is not None:
            pre_commit_guard()
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
        for relative in required_directories or []:
            target = runtime_dir / relative
            if target.exists():
                if target.is_symlink() or not target.is_dir():
                    raise FileExistsError(
                        f"required runtime directory conflicts with an existing path: {target}"
                    )
                continue
            target.mkdir(parents=True)
            created_directories.append(target)
    except Exception as commit_error:
        recovery_errors: list[Exception] = []
        recovery_paths: list[str] = []
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)
                recovery_paths.append(str(directory))
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


def install_package(
    package_dir: Path,
    workspace_dir: Path,
    *,
    force: bool,
    target: manager_contract.TargetContract | None = None,
    discard_drift: bool = False,
    expected_drift_sha256: str | None = None,
    confirm_discard_drift: str | None = None,
    mutation_lock: workspace_lock.WorkspaceMutationLock | None = None,
) -> dict[str, Any]:
    package_dir = package_dir.expanduser().absolute()
    workspace_dir = workspace_dir.expanduser().resolve()
    if not workspace_dir.is_dir():
        fail(f"workspace directory does not exist: {workspace_dir}")
    posix_backend = _posix_recovery_backend_available()
    supplied_confirmation = any(
        value is not None for value in (expected_drift_sha256, confirm_discard_drift)
    )
    if discard_drift != supplied_confirmation:
        raise _confirmation_failure(
            "discarding drift requires the complete confirmation tuple"
        )
    if discard_drift and (
        not force
        or expected_drift_sha256 is None
        or not contract.SHA256_RE.fullmatch(expected_drift_sha256)
        or confirm_discard_drift is None
        or not contract.NAME_RE.fullmatch(confirm_discard_drift)
    ):
        raise _confirmation_failure(
            "discarding drift requires --force, a lowercase SHA-256, and an exact slug"
        )
    if discard_drift and not posix_backend:
        raise cli_contract.CliRuntimePolicyError(
            "drift backup permissions are not verified on this platform",
            code="INSTALL_DRIFT_RECOVERY_PLATFORM_BLOCKED",
            phase="drift-recovery",
        )
    if _posix_platform() and not posix_backend:
        raise cli_contract.CliRuntimePolicyError(
            "workspace writes require atomic no-replace POSIX transactions",
            code="INSTALL_TRANSACTION_PLATFORM_BLOCKED",
            phase="install-transaction",
        )
    if mutation_lock is None:
        try:
            owned_lock = workspace_lock.acquire(workspace_dir)
        except workspace_lock.WorkspaceLockError as exc:
            raise cli_contract.CliContractError(
                "workspace mutation lock blocked this install",
                code=exc.code,
                status="mutation-locked",
                phase="workspace-lock",
            ) from exc
        try:
            return install_package(
                package_dir,
                workspace_dir,
                force=force,
                target=target,
                discard_drift=discard_drift,
                expected_drift_sha256=expected_drift_sha256,
                confirm_discard_drift=confirm_discard_drift,
                mutation_lock=owned_lock,
            )
        finally:
            owned_lock.release()
    assert mutation_lock is not None
    try:
        mutation_lock.assert_active_owner(workspace_dir)
    except workspace_lock.WorkspaceLockError as exc:
        raise cli_contract.CliContractError(
            "install does not own the active workspace mutation lock",
            code=exc.code,
            status="mutation-locked",
            phase="workspace-lock",
        ) from exc

    resolved_target = target or manager_contract.resolve_target(env={})
    snapshot, validation = package_snapshot.inspect_and_validate(
        package_dir,
        target=resolved_target,
    )
    if not validation.ok:
        fail("package validation failed: " + "; ".join(validation.errors[:8]))
    if snapshot is None:
        fail("package snapshot is unavailable after successful validation")
    derived = derive_install_projection(snapshot, resolved_target)
    manifest = derived.manifest
    runtime = derived.runtime
    sources = derived.sources
    incoming_package_json = derived.incoming_package_json
    reference_fallbacks = derived.reference_fallbacks
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not contract.NAME_RE.fullmatch(slug):
        fail("expert.json slug is invalid")

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
    merge_inputs = install_state.capture_runtime_inputs(
        runtime_dir,
        target_paths=sources,
    )
    receipts = merge_inputs.receipts
    own_receipt = receipts.get(slug)
    if own_receipt and not force:
        fail(f"{slug} is already installed; rerun with --force to upgrade it")
    owned_state: dict[str, Any] | None = None
    if own_receipt:
        owned_state = install_state.verify_owned_state(
            runtime_dir,
            own_receipt,
            receipts,
            snapshot=merge_inputs,
        )
        if not owned_state["ok"] and not discard_drift:
            raise InstallDriftError(owned_state)
    if discard_drift:
        if own_receipt is None or owned_state is None or owned_state["ok"]:
            raise _confirmation_failure(
                "discard confirmation is only valid for an installed slug with current drift"
            )
        if (
            confirm_discard_drift != slug
            or expected_drift_sha256 != owned_state["previewSha256"]
        ):
            raise _confirmation_failure(
                "discard slug or state-bound preview SHA-256 does not match current drift"
            )
    owners = install_state.file_owners(receipts)

    file_hashes = dict(derived.projection["files"])
    for relative, source in sources.items():
        target = runtime_dir / relative
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

    config = copy.deepcopy(merge_inputs.config)
    prune_owned_config(
        config,
        runtime,
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=force,
        discard_drift=discard_drift,
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
        values = merge_list(
            config,
            section,
            runtime.get(section),
            receipts=receipts,
        )
        if values:
            config_values[section] = values
    lsp = merge_lsp(config, runtime.get("lsp"), slug=slug, force=force, receipts=receipts)
    if lsp is not None:
        if isinstance(lsp, dict):
            config_values["lsp"] = lsp
        else:
            config_values["__scalar__"] = {"lsp": lsp}

    target_package_json_path = runtime_dir / "package.json"
    target_package_json = copy.deepcopy(merge_inputs.package_json)
    prune_owned_dependencies(
        target_package_json,
        incoming_package_json,
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=force,
        discard_drift=discard_drift,
    )
    dependencies = merge_package_json(
        target_package_json,
        incoming_package_json,
        slug=slug,
        force=force,
        receipts=receipts,
    )

    runtime_dir.mkdir(parents=True, exist_ok=True)
    staging_parent = (
        workspace_dir if posix_backend else runtime_dir
    )
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{slug}.install-", dir=staging_parent)
    )
    staged: dict[str, Path] = {}
    drift_backup_record: drift_backup.BackupRecord | None = None
    preserve_staging = False
    try:
        for relative, source in sources.items():
            target = staging_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source)
            staged[relative] = target
        staged_config = staging_root / contract.WORKSPACE_CONFIG
        staged_config.write_text(contract.dump_json(config), encoding="utf-8")
        staged[contract.WORKSPACE_CONFIG] = staged_config
        if incoming_package_json or merge_inputs.package_json_present:
            staged_package_json = staging_root / "package.json"
            staged_package_json.write_text(contract.dump_json(target_package_json), encoding="utf-8")
            staged["package.json"] = staged_package_json

        receipt = {
            "contract": manager_contract.load_policy()["receiptContract"][
                "writeVersion"
            ],
            "slug": slug,
            "files": file_hashes,
            "config_values": config_values,
            "dependencies": dependencies,
            **projection_contract.receipt_evidence(
                snapshot=snapshot,
                target=resolved_target,
                projection=derived.projection,
            ),
        }
        if derived.bindings:
            receipt["bindings"] = derived.bindings
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
        rechecked_inputs = install_state.capture_runtime_inputs(
            runtime_dir,
            target_paths=sources,
        )
        if rechecked_inputs.fingerprint != merge_inputs.fingerprint:
            raise install_state.InstallStateError(
                "INSTALL_INPUT_STATE_CHANGED",
                "workspace merge inputs changed during staging; no target state was committed",
            )
        rechecked_receipt = rechecked_inputs.receipts.get(slug)
        rechecked_state: dict[str, Any] | None = None
        if own_receipt is not None:
            if rechecked_receipt is None:
                raise install_state.InstallStateError(
                    "INSTALL_INPUT_STATE_CHANGED",
                    "installed receipt disappeared during staging",
                )
            rechecked_state = install_state.verify_owned_state(
                runtime_dir,
                rechecked_receipt,
                rechecked_inputs.receipts,
                snapshot=rechecked_inputs,
            )
            if not discard_drift and not rechecked_state["ok"]:
                raise InstallDriftError(rechecked_state)
            if discard_drift and (
                rechecked_state["ok"]
                or rechecked_state["previewSha256"] != expected_drift_sha256
                or rechecked_state["slug"] != confirm_discard_drift
            ):
                raise _confirmation_failure(
                    "owned or unrelated workspace state changed after drift confirmation"
                )
        transaction_paths = set(staged) | set(stale)
        def guard_workspace_owner() -> None:
            try:
                mutation_lock.assert_active_owner(workspace_dir)
            except workspace_lock.WorkspaceLockError as exc:
                raise install_state.InstallStateError(
                    exc.code,
                    "workspace mutation lock changed before commit",
                ) from exc

        pre_commit_guard: Callable[[], None] | None = guard_workspace_owner
        if discard_drift:
            assert rechecked_state is not None
            pre_targets = _captured_target_states(
                runtime_dir,
                rechecked_inputs,
                transaction_paths,
            )
            post_targets = _staged_target_states(staged, stale)
            post_receipts = _captured_receipt_bytes(rechecked_inputs)
            post_receipts[f"{slug}.json"] = staged_receipt.read_bytes()
            drift_backup_record = drift_backup.create_backup(
                runtime_dir,
                slug,
                str(expected_drift_sha256),
                pre_targets,
                drift_backup.target_state_sha256(post_targets),
                drift_backup.receipt_set_sha256(post_receipts),
            )

            def guard_confirmed_state() -> None:
                assert pre_commit_guard is not None
                guard_workspace_owner()
                guarded_inputs = install_state.capture_runtime_inputs(
                    runtime_dir,
                    target_paths=sources,
                )
                guarded_receipt = guarded_inputs.receipts.get(slug)
                if guarded_receipt is None:
                    raise install_state.InstallStateError(
                        "INSTALL_INPUT_STATE_CHANGED",
                        "installed receipt disappeared before commit",
                    )
                guarded_state = install_state.verify_owned_state(
                    runtime_dir,
                    guarded_receipt,
                    guarded_inputs.receipts,
                    snapshot=guarded_inputs,
                )
                if (
                    guarded_inputs.fingerprint != rechecked_inputs.fingerprint
                    or guarded_state["previewSha256"] != expected_drift_sha256
                    or guarded_state["slug"] != confirm_discard_drift
                    or guarded_state["ok"]
                ):
                    raise install_state.InstallStateError(
                        "INSTALL_INPUT_STATE_CHANGED",
                        "workspace state changed immediately before commit",
                    )

            pre_commit_guard = guard_confirmed_state
        commit_transaction(
            runtime_dir,
            staged,
            stale,
            required_directories=[contract.SKILLS_SUBDIR],
            pre_commit_guard=pre_commit_guard,
            secure=posix_backend,
        )
    except BaseException as exc:
        recovery_unverified = (
            isinstance(exc, InstallRecoveryError)
            and exc.rollback_verified is not True
        )
        if recovery_unverified:
            preserve_staging = True
        if not runtime_dir_existed and not isinstance(exc, InstallRecoveryError):
            shutil.rmtree(runtime_dir, ignore_errors=True)
        if drift_backup_record is not None and not isinstance(exc, KeyboardInterrupt):
            backup_evidence = _backup_cli_evidence(drift_backup_record)
            if isinstance(exc, InstallRecoveryError):
                raise _transaction_recovery_failure(
                    operation="install",
                    error=exc,
                    backup=drift_backup_record,
                ) from exc
            if isinstance(exc, install_state.InstallStateError):
                raise cli_contract.CliContractError(
                    "workspace changed after the drift backup was published; "
                    "the verified backup was retained for audit and recovery",
                    code=exc.code,
                    status="backup-retained",
                    phase="install-precommit",
                    attempted=True,
                    execution_policy="drift-backup-published",
                    provenance={"driftBackup": backup_evidence},
                    data={"driftBackup": backup_evidence, "committed": False},
                ) from exc
            if isinstance(exc, cli_contract.CliRuntimePolicyError):
                exc.provenance["driftBackup"] = backup_evidence
                exc.data["driftBackup"] = backup_evidence
                exc.data["committed"] = False
                exc.data.setdefault("rollbackVerified", True)
                raise
            if (
                isinstance(exc, cli_contract.CliInternalError)
                and exc.status == "transaction-rolled-back"
            ):
                exc.provenance["driftBackup"] = backup_evidence
                exc.data["driftBackup"] = backup_evidence
                raise
            raise cli_contract.CliInternalError(
                "install transaction failed after publishing a verified drift backup; "
                "preserve the reported backup evidence for recovery",
                code="INSTALL_DRIFT_TRANSACTION_FAILED",
                status="backup-retained",
                phase="install-transaction",
                attempted=True,
                execution_policy="drift-backup-published",
                provenance={"driftBackup": backup_evidence},
                data={"driftBackup": backup_evidence, "committed": False},
            ) from exc
        if isinstance(exc, InstallRecoveryError):
            raise _transaction_recovery_failure(
                operation="install",
                error=exc,
            ) from exc
        raise
    finally:
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)

    try:
        receipt_file = receipt_path(runtime_dir, slug)
        readback_inputs = install_state.capture_runtime_inputs(
            runtime_dir,
            target_paths=transaction_paths,
        )
        readback_receipts = readback_inputs.receipts
        readback_receipt = readback_receipts.get(slug)
        if readback_receipt is None:
            fail(f"install readback failed; receipt is missing for {slug}")
        readback_state = install_state.verify_owned_state(
            runtime_dir,
            readback_receipt,
            readback_receipts,
            snapshot=readback_inputs,
        )
        if not readback_state["ok"]:
            fail(f"install readback failed; owned state drifted for {slug}")
        evidence_mismatches = projection_contract.verify_receipt(
            readback_receipt,
            snapshot=snapshot,
            target=resolved_target,
            projection=derived.projection,
        )
        if evidence_mismatches:
            fail(
                "install readback failed; "
                + "; ".join(item.message for item in evidence_mismatches[:4])
            )
        projection_mismatches = projection_contract.verify_workspace_projection(
            runtime_dir,
            derived.projection,
        )
        if projection_mismatches:
            fail(
                "install readback failed; "
                + "; ".join(item.message for item in projection_mismatches[:4])
            )
        if drift_backup_record is not None:
            installed_targets = _captured_target_states(
                runtime_dir,
                readback_inputs,
                transaction_paths,
            )
            if (
                drift_backup.target_state_sha256(installed_targets)
                != drift_backup_record.post_state_sha256
                or drift_backup.receipt_set_sha256(
                    _captured_receipt_bytes(readback_inputs)
                )
                != drift_backup_record.receipt_set_sha256
            ):
                fail(
                    "install readback failed; committed state does not match "
                    "drift backup guards"
                )
        evidence = provenance.collect(input_snapshot=snapshot, target=resolved_target)
        evidence.update({
            "temporaryInstallTarget": str(workspace_dir),
            "receipt": {
                "path": str(receipt_file),
                "sha256": contract.sha256_file(receipt_file),
                "fileCount": len(file_hashes),
                "configSections": sorted(config_values),
            },
        })
        if drift_backup_record is not None:
            evidence["driftBackup"] = {
                "backupId": drift_backup_record.backup_id,
                "backupSha256": drift_backup_record.backup_sha256,
                "path": str(drift_backup_record.path),
            }
    except (Exception, SystemExit) as exc:
        raise _post_commit_failure(
            operation="install",
            code="INSTALL_COMMITTED_READBACK_FAILED",
            message=(
                "install target changes were committed but strict readback failed; "
                "treat the workspace state as unverified"
            ),
            backup=drift_backup_record,
        ) from exc
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
        "execution": _transaction_execution("install", secure=posix_backend),
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
        "reference_fallbacks": sorted(reference_fallbacks),
        "instructions": runtime.get("instructions") or [],
        "required_environment": contract.extract_env_references(runtime),
        "receipt": str(receipt_file),
        "driftBackup": (
            {
                "backupId": drift_backup_record.backup_id,
                "backupSha256": drift_backup_record.backup_sha256,
                "path": str(drift_backup_record.path),
                "previewSha256": drift_backup_record.preview_sha256,
            }
            if drift_backup_record is not None
            else None
        ),
    }


def _restore_blocked(message: str) -> cli_contract.CliContractError:
    return cli_contract.CliContractError(
        message,
        code="INSTALL_RESTORE_TARGET_CHANGED",
        status="restore-blocked",
        phase="drift-restore",
    )


def restore_drift_backup(
    workspace_dir: Path,
    *,
    backup_id: str,
    expected_backup_sha256: str,
    confirm_slug: str,
    mutation_lock: workspace_lock.WorkspaceMutationLock | None,
) -> dict[str, Any]:
    """Restore one exact pre-image only while the installed post-state is unchanged."""

    if mutation_lock is None:
        raise cli_contract.CliContractError(
            "drift restore requires the workspace mutation lock",
            code="WORKSPACE_LOCK_REQUIRED",
            status="mutation-locked",
            phase="workspace-lock",
        )
    if not _posix_recovery_backend_available():
        raise cli_contract.CliRuntimePolicyError(
            "drift restore permissions are not verified on this platform",
            code="INSTALL_DRIFT_RECOVERY_PLATFORM_BLOCKED",
            phase="drift-recovery",
        )
    if not contract.NAME_RE.fullmatch(confirm_slug):
        raise cli_contract.CliArgumentError(
            "restore confirmation slug is invalid",
            phase="arguments",
        )
    workspace_dir = workspace_dir.expanduser().resolve()
    try:
        mutation_lock.assert_active_owner(workspace_dir)
    except workspace_lock.WorkspaceLockError as exc:
        raise cli_contract.CliContractError(
            "drift restore does not own the active workspace lock",
            code=exc.code,
            status="mutation-locked",
            phase="workspace-lock",
        ) from exc
    runtime_dir = workspace_dir / contract.WORKSPACE_RUNTIME_DIR
    if runtime_dir.is_symlink() or not runtime_dir.is_dir():
        raise _restore_blocked("workspace runtime directory is not safe for restore")
    try:
        contract.assert_no_symlinks(runtime_dir)
    except contract.ContractError as exc:
        raise _restore_blocked("workspace runtime contains an unsafe path") from exc

    backup = drift_backup.load_and_verify_backup(
        runtime_dir,
        confirm_slug,
        backup_id,
        expected_backup_sha256,
    )
    target_paths = set(backup.targets_by_path)
    receipt_prefix = f"{contract.INSTALL_RECEIPT_DIR}/"
    preview_target_paths = {
        relative
        for relative in target_paths
        if relative not in {contract.WORKSPACE_CONFIG, "package.json"}
        and not relative.startswith(receipt_prefix)
    }
    current_inputs = install_state.capture_runtime_inputs(
        runtime_dir,
        target_paths=target_paths,
    )
    current_receipt = current_inputs.receipts.get(confirm_slug)
    if current_receipt is None or current_receipt.get("contract") != 3:
        raise _restore_blocked(
            "restore requires the unchanged contract 3 receipt produced by discard"
        )
    current_owned_state = install_state.verify_owned_state(
        runtime_dir,
        current_receipt,
        current_inputs.receipts,
        snapshot=current_inputs,
    )
    current_targets = _captured_target_states(
        runtime_dir,
        current_inputs,
        target_paths,
    )
    if (
        not current_owned_state["ok"]
        or drift_backup.target_state_sha256(current_targets)
        != backup.record.post_state_sha256
        or drift_backup.receipt_set_sha256(_captured_receipt_bytes(current_inputs))
        != backup.record.receipt_set_sha256
    ):
        raise _restore_blocked(
            "installed target or receipt set changed after the drift backup"
        )

    restored_receipts = _captured_receipt_bytes(current_inputs)
    for relative, target in backup.targets_by_path.items():
        if not relative.startswith(receipt_prefix):
            continue
        filename = relative[len(receipt_prefix) :]
        if target.present:
            assert target.content is not None
            restored_receipts[filename] = target.content
        else:
            restored_receipts.pop(filename, None)
    expected_restored_receipt_set = drift_backup.receipt_set_sha256(
        restored_receipts
    )
    expected_restored_targets = drift_backup.target_state_sha256(
        backup.targets_by_path
    )

    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{confirm_slug}.restore-", dir=workspace_dir)
    )
    preserve_staging = False
    try:
        staged, stale = drift_backup.stage_restore(backup, staging_root)

        def guard_restore_target() -> None:
            try:
                mutation_lock.assert_active_owner(workspace_dir)
            except workspace_lock.WorkspaceLockError as exc:
                raise _restore_blocked(
                    "workspace mutation lock changed before restore"
                ) from exc
            guarded_inputs = install_state.capture_runtime_inputs(
                runtime_dir,
                target_paths=target_paths,
            )
            guarded_receipt = guarded_inputs.receipts.get(confirm_slug)
            if guarded_receipt is None:
                raise _restore_blocked("installed receipt disappeared before restore")
            guarded_targets = _captured_target_states(
                runtime_dir,
                guarded_inputs,
                target_paths,
            )
            if (
                guarded_inputs.fingerprint != current_inputs.fingerprint
                or drift_backup.target_state_sha256(guarded_targets)
                != backup.record.post_state_sha256
                or drift_backup.receipt_set_sha256(
                    _captured_receipt_bytes(guarded_inputs)
                )
                != backup.record.receipt_set_sha256
            ):
                raise _restore_blocked(
                    "installed target changed immediately before restore"
                )

        try:
            commit_transaction(
                runtime_dir,
                staged,
                stale,
                pre_commit_guard=guard_restore_target,
                secure=True,
            )
        except InstallRecoveryError as exc:
            preserve_staging = exc.rollback_verified is not True
            raise _transaction_recovery_failure(
                operation="restore",
                error=exc,
                backup=backup.record,
            ) from exc
        except (
            cli_contract.CliInternalError,
            cli_contract.CliRuntimePolicyError,
        ) as exc:
            backup_evidence = _backup_cli_evidence(backup.record)
            exc.provenance["driftBackup"] = backup_evidence
            exc.data["driftBackup"] = backup_evidence
            raise
    finally:
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)

    try:
        restored_inputs = install_state.capture_runtime_inputs(
            runtime_dir,
            target_paths=target_paths,
        )
        restored_targets = _captured_target_states(
            runtime_dir,
            restored_inputs,
            target_paths,
        )
        restored_preview_inputs = install_state.capture_runtime_inputs(
            runtime_dir,
            target_paths=preview_target_paths,
        )
        restored_receipt = restored_preview_inputs.receipts.get(confirm_slug)
        if restored_receipt is None:
            raise _restore_blocked("restored receipt is missing")
        restored_state = install_state.verify_owned_state(
            runtime_dir,
            restored_receipt,
            restored_preview_inputs.receipts,
            snapshot=restored_preview_inputs,
        )
        if (
            drift_backup.target_state_sha256(restored_targets)
            != expected_restored_targets
            or drift_backup.receipt_set_sha256(
                _captured_receipt_bytes(restored_inputs)
            )
            != expected_restored_receipt_set
            or restored_state["previewSha256"] != backup.record.preview_sha256
        ):
            raise _restore_blocked(
                "restore readback does not match the backed-up pre-image"
            )
    except (Exception, SystemExit) as exc:
        raise _post_commit_failure(
            operation="restore",
            code="INSTALL_RESTORE_COMMITTED_READBACK_FAILED",
            message=(
                "restore target changes were committed but strict readback failed; "
                "treat the workspace state as unverified"
            ),
            backup=backup.record,
        ) from exc

    return {
        "ok": True,
        "schemaVersion": 2,
        "status": "drift-restored",
        "evidenceLevel": "valid",
        "gates": {
            "archive": "not-run",
            "contract": "passed",
            "portability": "not-run",
            "install": "blocked",
            "configLoad": "blocked",
        },
        "runtime": {"status": "not-tested", "reason": "restore-only"},
        "execution": {
            "policy": "verified-drift-restore",
            "attempted": True,
            "reason": "explicit-backup-confirmation",
        },
        "provenance": {
            "driftBackup": {
                "backupId": backup.record.backup_id,
                "backupSha256": backup.record.backup_sha256,
                "path": str(backup.record.path),
            }
        },
        "workspace": str(workspace_dir),
        "slug": confirm_slug,
        "backupId": backup.record.backup_id,
        "backupSha256": backup.record.backup_sha256,
        "restoredPreviewSha256": restored_state["previewSha256"],
        "restoredState": restored_state["status"],
    }


def uninstall_package(
    workspace_dir: Path,
    slug: str,
    *,
    mutation_lock: workspace_lock.WorkspaceMutationLock | None = None,
) -> dict[str, Any]:
    """Remove one receipt-owned expert without touching drifted or shared state."""

    if not contract.NAME_RE.fullmatch(slug):
        fail("--uninstall must be a lowercase kebab-case expert slug")
    workspace_dir = workspace_dir.expanduser().resolve()
    if not workspace_dir.is_dir():
        fail(f"workspace directory does not exist: {workspace_dir}")
    posix_backend = _posix_recovery_backend_available()
    if _posix_platform() and not posix_backend:
        raise cli_contract.CliRuntimePolicyError(
            "workspace writes require atomic no-replace POSIX transactions",
            code="INSTALL_TRANSACTION_PLATFORM_BLOCKED",
            phase="uninstall-transaction",
        )
    if mutation_lock is None:
        try:
            owned_lock = workspace_lock.acquire(workspace_dir)
        except workspace_lock.WorkspaceLockError as exc:
            raise cli_contract.CliContractError(
                "workspace mutation lock blocked this uninstall",
                code=exc.code,
                status="mutation-locked",
                phase="workspace-lock",
            ) from exc
        try:
            return uninstall_package(
                workspace_dir,
                slug,
                mutation_lock=owned_lock,
            )
        finally:
            owned_lock.release()
    assert mutation_lock is not None
    try:
        mutation_lock.assert_active_owner(workspace_dir)
    except workspace_lock.WorkspaceLockError as exc:
        raise cli_contract.CliContractError(
            "uninstall does not own the active workspace mutation lock",
            code=exc.code,
            status="mutation-locked",
            phase="workspace-lock",
        ) from exc
    runtime_dir = workspace_dir / contract.WORKSPACE_RUNTIME_DIR
    if runtime_dir.is_symlink() or not runtime_dir.is_dir():
        fail(f"workspace runtime directory does not exist safely: {runtime_dir}")
    try:
        contract.assert_no_symlinks(runtime_dir)
    except contract.ContractError as exc:
        fail(f"workspace runtime contains an unsafe symlink: {exc}")

    merge_inputs = install_state.capture_runtime_inputs(runtime_dir)
    receipts = merge_inputs.receipts
    own_receipt = receipts.get(slug)
    if own_receipt is None:
        fail(f"{slug} has no install receipt in this workspace")
    owned_state = install_state.verify_owned_state(
        runtime_dir,
        own_receipt,
        receipts,
        snapshot=merge_inputs,
    )
    if not owned_state["ok"]:
        raise InstallDriftError(owned_state)
    owners = install_state.file_owners(receipts)
    files = own_receipt.get("files", {})
    if not isinstance(files, dict):
        fail(f"receipt for {slug} has invalid files")
    removed_files: list[str] = []
    for relative, expected_hash in files.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            fail(f"receipt for {slug} has invalid file ownership")
        if owners.get(relative, set()) - {slug}:
            continue
        target = runtime_dir / relative
        if target.is_symlink():
            fail(f"cannot uninstall symlinked owned file: {relative}")
        if not target.exists():
            fail(f"cannot uninstall missing owned file: {relative}")
        if not target.is_file():
            fail(f"cannot uninstall non-file owned path: {relative}")
        if contract.sha256_file(target) != expected_hash:
            fail(f"cannot uninstall changed owned file: {relative}")
        removed_files.append(relative)

    config_path = runtime_dir / contract.WORKSPACE_CONFIG
    config = copy.deepcopy(merge_inputs.config)
    prune_owned_config(
        config,
        {},
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=True,
    )
    package_json_path = runtime_dir / "package.json"
    package_json_existed = merge_inputs.package_json_present
    package_json = copy.deepcopy(merge_inputs.package_json)
    prune_owned_dependencies(
        package_json,
        {},
        slug=slug,
        own_receipt=own_receipt,
        receipts=receipts,
        force=True,
    )

    staging_parent = (
        workspace_dir if posix_backend else runtime_dir
    )
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{slug}.uninstall-", dir=staging_parent)
    )
    staged: dict[str, Path] = {}
    preserve_staging = False
    try:
        staged_config = staging_root / contract.WORKSPACE_CONFIG
        staged_config.parent.mkdir(parents=True, exist_ok=True)
        staged_config.write_text(contract.dump_json(config), encoding="utf-8")
        staged[contract.WORKSPACE_CONFIG] = staged_config
        if package_json_existed:
            staged_package_json = staging_root / "package.json"
            staged_package_json.write_text(
                contract.dump_json(package_json), encoding="utf-8"
            )
            staged["package.json"] = staged_package_json
        receipt_relative = f"{contract.INSTALL_RECEIPT_DIR}/{slug}.json"
        stale = [*removed_files, receipt_relative]
        rechecked_inputs = install_state.capture_runtime_inputs(runtime_dir)
        if rechecked_inputs.fingerprint != merge_inputs.fingerprint:
            raise install_state.InstallStateError(
                "INSTALL_INPUT_STATE_CHANGED",
                "workspace merge inputs changed during staging; no target state was committed",
            )
        rechecked_receipt = rechecked_inputs.receipts.get(slug)
        if rechecked_receipt is None:
            raise install_state.InstallStateError(
                "INSTALL_INPUT_STATE_CHANGED",
                "installed receipt disappeared during staging",
            )
        rechecked = install_state.verify_owned_state(
            runtime_dir,
            rechecked_receipt,
            rechecked_inputs.receipts,
            snapshot=rechecked_inputs,
        )
        if not rechecked["ok"]:
            raise InstallDriftError(rechecked)
        def guard_workspace_owner() -> None:
            try:
                mutation_lock.assert_active_owner(workspace_dir)
            except workspace_lock.WorkspaceLockError as exc:
                raise install_state.InstallStateError(
                    exc.code,
                    "workspace mutation lock changed before uninstall commit",
                ) from exc

        pre_commit_guard: Callable[[], None] | None = guard_workspace_owner
        try:
            commit_transaction(
                runtime_dir,
                staged,
                stale,
                pre_commit_guard=pre_commit_guard,
                secure=posix_backend,
            )
        except InstallRecoveryError as exc:
            preserve_staging = exc.rollback_verified is not True
            raise _transaction_recovery_failure(
                operation="uninstall",
                error=exc,
            ) from exc
    finally:
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)

    try:
        if receipt_path(runtime_dir, slug).exists():
            fail(f"uninstall readback failed; receipt remains for {slug}")
        for relative in files:
            if owners.get(str(relative), set()) - {slug}:
                continue
            if (runtime_dir / str(relative)).exists():
                fail(f"uninstall readback failed; owned file remains: {relative}")
        if load_jsonc(config_path) != config:
            fail(f"uninstall readback failed; workspace config differs for {slug}")
        if package_json_existed and load_json(package_json_path) != package_json:
            fail(f"uninstall readback failed; workspace dependencies differ for {slug}")
    except (Exception, SystemExit) as exc:
        raise _post_commit_failure(
            operation="uninstall",
            code="UNINSTALL_COMMITTED_READBACK_FAILED",
            message=(
                "uninstall target changes were committed but strict readback failed; "
                "treat the workspace state as unverified"
            ),
        ) from exc
    return {
        "ok": True,
        "schemaVersion": 2,
        "status": "uninstalled",
        "execution": _transaction_execution("uninstall", secure=posix_backend),
        "runtime_status": "runtime-not-tested",
        "workspace": str(workspace_dir),
        "slug": slug,
        "removed_files": sorted(removed_files),
        "receipt": "removed",
    }


class ManagerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise cli_contract.CliArgumentError(message)


def build_parser(policy: dict[str, Any]) -> ManagerArgumentParser:
    parser = ManagerArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--package-dir", type=Path, help="Generated expert package directory")
    operation.add_argument("--uninstall", metavar="SLUG", help="Remove resources owned by an installed expert receipt")
    operation.add_argument(
        "--restore-drift-backup",
        metavar="BACKUP_ID",
        help="Restore one verified high-risk drift backup",
    )
    parser.add_argument("--workspace-dir", required=True, type=Path, help="Target MobileWork workspace directory")
    parser.add_argument("--force", action="store_true", help="Upgrade resources owned by the same expert slug")
    parser.add_argument(
        "--discard-drift",
        action="store_true",
        help="Explicitly discard confirmed receipt-owned drift during a forced upgrade",
    )
    parser.add_argument("--expected-drift-sha256")
    parser.add_argument("--confirm-discard-drift", metavar="SLUG")
    parser.add_argument("--expected-backup-sha256")
    parser.add_argument("--confirm-restore-drift", metavar="SLUG")
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
    parser.add_argument(
        "--format",
        choices=policy["cli"]["formats"],
        default="json",
    )
    parser.add_argument(
        "--schema-version",
        choices=policy["cli"]["supportedSchemaVersions"],
        default=policy["cli"]["defaultSchemaVersion"],
        type=int,
    )
    return parser


def parse_args(
    argv: list[str] | None,
    policy: dict[str, Any],
) -> argparse.Namespace:
    parser = build_parser(policy)
    args = parser.parse_args(argv)
    discard_values = (
        args.discard_drift,
        args.expected_drift_sha256 is not None,
        args.confirm_discard_drift is not None,
    )
    if any(discard_values) and not all(discard_values):
        raise cli_contract.CliArgumentError(
            "--discard-drift, --expected-drift-sha256, and "
            "--confirm-discard-drift must be provided together"
        )
    if any(discard_values) and (args.package_dir is None or not args.force):
        raise cli_contract.CliArgumentError(
            "discarding drift requires --package-dir and --force"
        )
    if args.expected_drift_sha256 is not None and not contract.SHA256_RE.fullmatch(
        args.expected_drift_sha256
    ):
        raise cli_contract.CliArgumentError(
            "--expected-drift-sha256 must be a lowercase SHA-256"
        )
    if args.confirm_discard_drift is not None and not contract.NAME_RE.fullmatch(
        args.confirm_discard_drift
    ):
        raise cli_contract.CliArgumentError(
            "--confirm-discard-drift must be a lowercase kebab-case expert slug"
        )

    restore_values = (
        args.expected_backup_sha256 is not None,
        args.confirm_restore_drift is not None,
    )
    if args.restore_drift_backup is not None and not all(restore_values):
        raise cli_contract.CliArgumentError(
            "restoring drift requires --expected-backup-sha256 and "
            "--confirm-restore-drift"
        )
    if args.restore_drift_backup is None and any(restore_values):
        raise cli_contract.CliArgumentError(
            "backup confirmation flags require --restore-drift-backup"
        )
    if args.expected_backup_sha256 is not None and not contract.SHA256_RE.fullmatch(
        args.expected_backup_sha256
    ):
        raise cli_contract.CliArgumentError(
            "--expected-backup-sha256 must be a lowercase SHA-256"
        )
    if args.confirm_restore_drift is not None and not contract.NAME_RE.fullmatch(
        args.confirm_restore_drift
    ):
        raise cli_contract.CliArgumentError(
            "--confirm-restore-drift must be a lowercase kebab-case expert slug"
        )
    if args.uninstall and args.force:
        raise cli_contract.CliArgumentError("--force is only valid with --package-dir")
    if args.restore_drift_backup is not None and any(
        (
            args.force,
            args.target_opencode_version is not None,
            args.host_contract is not None,
            any(discard_values),
        )
    ):
        raise cli_contract.CliArgumentError(
            "restore does not accept install, force, target-version, or discard flags"
        )
    return args


def _success_result(operation: str, payload: dict[str, Any]) -> cli_contract.CliResult:
    common = {
        "schemaVersion",
        "ok",
        "status",
        "evidenceLevel",
        "gates",
        "runtime",
        "execution",
        "provenance",
        "findings",
    }
    evidence_level = str(
        payload.get("evidenceLevel", "installable" if operation == "install-expert" else "valid")
    )
    gates = payload.get(
        "gates",
        {
            "archive": "not-run",
            "contract": "passed",
            "portability": "not-run",
            "install": "passed",
            "configLoad": "not-run",
        },
    )
    return cli_contract.CliResult(
        operation=operation,
        ok=True,
        status=str(payload["status"]),
        evidence_level=evidence_level,
        gates=gates,
        runtime=payload.get(
            "runtime",
            {"status": "not-tested", "reason": operation},
        ),
        execution=payload.get(
            "execution",
            {"policy": "install-transaction", "attempted": True, "reason": operation},
        ),
        provenance=payload.get("provenance", {}),
        findings=payload.get("findings", ()),
        data={key: value for key, value in payload.items() if key not in common},
        exit_code=cli_contract.ExitCode.SUCCESS,
        legacy_payload=payload,
    )


def _drift_result(
    error: InstallDriftError,
    *,
    operation: str,
) -> cli_contract.CliResult:
    finding = cli_contract.finding_for_failure(error)
    finding["remediation"] = (
        "Review previewSha256 and restore the receipt-owned state before retrying. "
        "The force-only path never discards drift."
    )
    finding["evidence"] = str(error.report["previewSha256"])
    return cli_contract.CliResult(
        operation=operation,
        ok=False,
        status="drift-detected",
        evidence_level="invalid",
        gates={
            "archive": "not-run",
            "contract": "passed",
            "portability": "passed",
            "install": "failed",
            "configLoad": "blocked",
        },
        runtime={"status": "not-tested", "reason": "install-preflight-drift"},
        execution={
            "policy": "install-preflight",
            "attempted": False,
            "reason": "owned-state-drift",
        },
        provenance={},
        findings=(finding,),
        data={
            "driftPreview": error.report["preview"],
            "previewSchemaVersion": error.report["schemaVersion"],
            "previewSha256": error.report["previewSha256"],
            "slug": error.report["slug"],
        },
        exit_code=cli_contract.ExitCode.CONTRACT_OR_SAFETY_FAILURE,
        legacy_payload={
            "ok": False,
            "status": "drift-detected",
            "code": "INSTALL_OWNED_STATE_DRIFT",
            "preview": error.report["preview"],
            "previewSha256": error.report["previewSha256"],
        },
    )


def _operation_for_args(args: argparse.Namespace) -> str:
    if args.restore_drift_backup is not None:
        return "restore-expert-drift"
    return "uninstall-expert" if args.uninstall else "install-expert"


def _execute_locked(
    args: argparse.Namespace,
    workspace: Path,
    operation: str,
    mutation_lock: workspace_lock.WorkspaceMutationLock | None,
) -> cli_contract.CliResult:
    if args.uninstall:
        if not contract.NAME_RE.fullmatch(args.uninstall):
            raise cli_contract.CliArgumentError(
                "--uninstall must be a lowercase kebab-case expert slug",
                code="MANAGER_ARGUMENT_ERROR",
                phase="arguments",
            )
        return _success_result(
            operation,
            uninstall_package(
                workspace,
                args.uninstall,
                mutation_lock=mutation_lock,
            ),
        )
    if args.restore_drift_backup is not None:
        return _success_result(
            operation,
            restore_drift_backup(
                workspace,
                backup_id=args.restore_drift_backup,
                expected_backup_sha256=args.expected_backup_sha256,
                confirm_slug=args.confirm_restore_drift,
                mutation_lock=mutation_lock,
            ),
        )
    package_dir = args.package_dir.expanduser().absolute()
    if not package_dir.is_dir():
        raise cli_contract.CliArgumentError(
            "package directory does not exist",
            code="INSTALL_ENVIRONMENT_ERROR",
            phase="environment",
        )
    try:
        target = manager_contract.resolve_target(
            cli_version=args.target_opencode_version,
            host_contract=args.host_contract,
        )
    except manager_contract.ManagerContractError as exc:
        raise cli_contract.CliArgumentError(
            f"manager-version-contract: {exc}",
            code="MANAGER_VERSION_CONTRACT_ERROR",
            phase="manager-contract",
        ) from exc
    try:
        payload = install_package(
            package_dir,
            workspace,
            force=args.force,
            target=target,
            discard_drift=args.discard_drift,
            expected_drift_sha256=args.expected_drift_sha256,
            confirm_discard_drift=args.confirm_discard_drift,
            mutation_lock=mutation_lock,
        )
    except InstallDriftError as exc:
        return _drift_result(exc, operation=operation)
    return _success_result(operation, payload)


def _lock_release_failure(
    operation: str,
    result: cli_contract.CliResult,
    error: workspace_lock.WorkspaceLockError,
) -> cli_contract.CliInternalError:
    attempted = bool(result.execution.get("attempted"))
    data: dict[str, Any] = {
        "completedStatus": result.status,
        "committed": result.ok and attempted,
        "lockReleaseVerified": False,
    }
    provenance: dict[str, Any] = {}
    for source in (result.data, result.provenance):
        backup = source.get("driftBackup")
        if isinstance(backup, dict):
            data["driftBackup"] = dict(backup)
            provenance["driftBackup"] = dict(backup)
            break
    return cli_contract.CliInternalError(
        "the manager action completed but workspace lock release was not verified; "
        "treat the lock and operation outcome conservatively",
        code=error.code,
        status="lock-release-unverified",
        phase="workspace-lock-release",
        attempted=attempted,
        execution_policy="workspace-lock-release",
        provenance=provenance,
        data=data,
    )


def _execute(args: argparse.Namespace) -> cli_contract.CliResult:
    operation = _operation_for_args(args)
    try:
        workspace = args.workspace_dir.expanduser().resolve()
        if not workspace.is_dir():
            raise cli_contract.CliArgumentError(
                "workspace directory does not exist",
                code="INSTALL_ENVIRONMENT_ERROR",
                phase="environment",
            )
        recovery_requested = bool(
            args.discard_drift or args.restore_drift_backup is not None
        )
        posix_backend = _posix_recovery_backend_available()
        if _posix_platform() and not posix_backend:
            raise cli_contract.CliRuntimePolicyError(
                "workspace writes require atomic no-replace POSIX transactions",
                code="INSTALL_TRANSACTION_PLATFORM_BLOCKED",
                phase="install-transaction",
            )
        if not _posix_platform() and recovery_requested:
            raise cli_contract.CliRuntimePolicyError(
                "drift backup and restore require the verified POSIX backend",
                code="INSTALL_DRIFT_RECOVERY_PLATFORM_BLOCKED",
                phase="drift-recovery",
            )
        try:
            mutation_lock = workspace_lock.acquire(workspace)
        except workspace_lock.WorkspaceLockError as exc:
            raise cli_contract.CliContractError(
                "workspace mutation lock blocked this operation",
                code=exc.code,
                status="mutation-locked",
                phase="workspace-lock",
            ) from exc
        try:
            result = _execute_locked(args, workspace, operation, mutation_lock)
        except BaseException as action_error:
            try:
                mutation_lock.release()
            except workspace_lock.WorkspaceLockError as release_error:
                if isinstance(action_error, cli_contract.CliFailure):
                    action_error.data["lockReleaseVerified"] = False
                    action_error.data["lockReleaseCode"] = release_error.code
            raise
        try:
            mutation_lock.release()
        except workspace_lock.WorkspaceLockError as exc:
            raise _lock_release_failure(operation, result, exc) from exc
        return result
    except InstallDriftError as exc:
        return _drift_result(exc, operation=operation)
    except install_state.InstallStateError as exc:
        raise cli_contract.CliContractError(
            f"invalid install receipt: {exc}",
            code=exc.code,
            phase="install-preflight",
        ) from exc
    except drift_backup.DriftBackupError as exc:
        recovery_paths = [
            output_sanitizer.sanitize_text(path)
            for path in exc.recovery_paths
        ]
        recovery_data: dict[str, Any] = {}
        if exc.attempted:
            recovery_data = {
                "committed": exc.committed,
                "rollbackVerified": exc.rollback_verified,
                "recoveryPaths": recovery_paths,
            }
            if exc.durability_unverified:
                recovery_data["durabilityUnverified"] = True
        if exc.code == "DRIFT_RECOVERY_PLATFORM_BLOCKED":
            raise cli_contract.CliRuntimePolicyError(
                "drift recovery is blocked on this platform",
                code="INSTALL_DRIFT_RECOVERY_PLATFORM_BLOCKED",
                phase="drift-recovery",
                attempted=exc.attempted,
                execution_policy="drift-backup-no-replace",
                data=recovery_data,
            ) from exc
        if exc.code == "DRIFT_BACKUP_ARGUMENT_INVALID":
            raise cli_contract.CliArgumentError(
                "drift backup arguments are invalid",
                code="MANAGER_ARGUMENT_ERROR",
                phase="arguments",
            ) from exc
        if exc.code in {
            "DRIFT_BACKUP_WRITE_FAILED",
            "DRIFT_BACKUP_ID_COLLISION",
            "DRIFT_BACKUP_ID_EXHAUSTED",
            "DRIFT_BACKUP_CLEANUP_FAILED",
            "DRIFT_RESTORE_STAGING_INVALID",
        }:
            raise cli_contract.CliInternalError(
                (
                    "drift backup cleanup requires manual verification; "
                    "preserve the reported recovery path"
                    if exc.code == "DRIFT_BACKUP_CLEANUP_FAILED"
                    else "drift recovery could not complete its private transaction"
                ),
                code=exc.code,
                status=(
                    "backup-recovery-required"
                    if exc.code == "DRIFT_BACKUP_CLEANUP_FAILED"
                    else None
                ),
                phase="drift-recovery",
                attempted=exc.attempted,
                execution_policy="drift-backup-private-transaction",
                data=recovery_data,
            ) from exc
        raise cli_contract.CliContractError(
            "drift backup failed strict safety verification",
            code=exc.code,
            status="backup-invalid",
            phase="drift-recovery",
            attempted=exc.attempted,
            execution_policy="drift-backup-private-transaction",
            data=recovery_data,
        ) from exc
    except SystemExit as exc:
        raise cli_contract.CliContractError(
            str(exc),
            code="INSTALL_CONTRACT_ERROR",
            phase="install",
        ) from exc


def _operation_hint(argv: list[str]) -> str:
    if any(
        token == "--restore-drift-backup"
        or token.startswith("--restore-drift-backup=")
        for token in argv
    ):
        return "restore-expert-drift"
    if any(
        token == "--uninstall" or token.startswith("--uninstall=")
        for token in argv
    ):
        return "uninstall-expert"
    return "install-expert"


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    requested_format, requested_schema = cli_contract.requested_output(raw_argv)
    operation_hint = _operation_hint(raw_argv)
    try:
        policy = manager_contract.load_policy()
        cli_contract.CliPolicy.from_policy(policy)
    except (manager_contract.ManagerContractError, cli_contract.CliFailure) as exc:
        failure = cli_contract.CliInternalError(
            str(exc),
            code="MANAGER_POLICY_INVALID",
            phase="manager-contract",
        )
        return cli_contract.run_cli(
            operation_hint,
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=None,
        )
    if any(token in {"-h", "--help"} for token in raw_argv):
        help_text = build_parser(policy).format_help()
        return cli_contract.run_cli(
            operation_hint,
            lambda: cli_contract.help_result(operation_hint, help_text),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    try:
        args = parse_args(raw_argv, policy)
    except cli_contract.CliFailure as failure:
        return cli_contract.run_cli(
            operation_hint,
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    operation = _operation_for_args(args)
    return cli_contract.run_cli(
        operation,
        lambda: _execute(args),
        output_format=args.format,
        schema_version=args.schema_version,
        policy=policy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
