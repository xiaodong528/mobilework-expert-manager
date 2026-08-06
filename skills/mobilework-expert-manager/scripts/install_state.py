#!/usr/bin/env python3
"""Receipt ownership and read-only installed-state verification.

This module deliberately does not mutate the runtime.  Install, upgrade, removal,
and recovery entrypoints can use the same report as their pre-write ownership gate.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import output_sanitizer
import package_contract as contract
import manager_contract
import safe_input


MAPPING_CONFIG_SECTIONS = frozenset({"agent", "mcp", "references", "lsp"})
LIST_CONFIG_SECTIONS = frozenset({"plugin", "instructions"})
DEPENDENCY_SECTIONS = tuple(contract.PACKAGE_DEPENDENCY_SECTIONS)

_BASE_RECEIPT_FIELDS = frozenset(
    {"contract", "slug", "files", "config_values", "dependencies", "bindings"}
)
_PREVIEW_FIELDS = frozenset(
    {"code", "kind", "path", "expectedSha256", "actualSha256"}
)


class InstallStateError(ValueError):
    """Raised when a receipt or runtime state cannot be verified safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(output_sanitizer.sanitize_text(message))


def _raise(code: str, message: str) -> None:
    raise InstallStateError(code, message)


@dataclass(frozen=True)
class RuntimeInputSnapshot:
    """Coherent merge inputs captured from protected bytes."""

    config: dict[str, Any]
    config_present: bool
    config_file: safe_input.InputFile | None
    package_json: dict[str, Any]
    package_json_present: bool
    package_json_file: safe_input.InputFile | None
    receipts: dict[str, dict[str, Any]]
    receipt_snapshot: safe_input.InputSnapshot | None
    target_files: dict[str, safe_input.InputFile | None]
    fingerprint: str
    content_state_sha256: str
    receipt_set_sha256: str


@dataclass(frozen=True)
class ReceiptPolicy:
    """Validated receipt rules loaded lazily from the canonical manager contract."""

    read_versions: frozenset[int]
    extended_ownership_versions: frozenset[int]
    hash_fields: tuple[str, ...]
    version_fields: tuple[str, ...]
    drift_preview_schema_version: int


@lru_cache(maxsize=1)
def receipt_policy() -> ReceiptPolicy:
    """Load receipt policy on first use, never during module import.

    CLI entrypoints must be importable even when the canonical policy is damaged
    so their centralized emitter can return one sanitized internal-error result.
    """

    policy = manager_contract.load_policy()["receiptContract"]
    return ReceiptPolicy(
        read_versions=frozenset(policy["readVersions"]),
        extended_ownership_versions=frozenset(
            policy["extendedOwnershipVersions"]
        ),
        hash_fields=tuple(policy["v3Sha256Fields"]),
        version_fields=tuple(policy["v3StringFields"]),
        drift_preview_schema_version=policy["driftPreviewSchemaVersion"],
    )


def contract_3_hash_fields() -> tuple[str, ...]:
    return receipt_policy().hash_fields


def contract_3_version_fields() -> tuple[str, ...]:
    return receipt_policy().version_fields


def extended_ownership_contracts() -> frozenset[int]:
    return receipt_policy().extended_ownership_versions


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _raise("INSTALL_RECEIPT_INVALID", "receipt contains a non-canonical JSON value")


def value_sha256(value: Any) -> str:
    """Hash a JSON value without placing that value in verification output."""

    return manager_contract.canonical_json_sha256(
        value,
        domain="mobilework-owned-state-value-v1",
    )


def canonical_preview_sha256(
    preview: list[dict[str, Any]],
    *,
    slug: str = "",
    receipt_sha256: str = "",
    content_state_sha256: str = "",
    receipt_set_sha256: str = "",
) -> str:
    """Bind a destructive confirmation to drift and the complete captured state.

    Callers that omit the state fields receive the legacy deterministic preview
    digest for read-only compatibility.  Install and recovery paths always pass
    all fields and therefore use the stronger v2 confirmation domain.
    """

    if not any((slug, receipt_sha256, content_state_sha256, receipt_set_sha256)):
        return manager_contract.canonical_json_sha256(
            {
                "schemaVersion": receipt_policy().drift_preview_schema_version,
                "drift": preview,
            },
            domain="mobilework-owned-state-drift-preview-v1",
        )
    return manager_contract.canonical_json_sha256(
        {
            "confirmationSchemaVersion": 2,
            "previewSchemaVersion": receipt_policy().drift_preview_schema_version,
            "slug": slug,
            "receiptSha256": receipt_sha256,
            "contentStateSha256": content_state_sha256,
            "receiptSetSha256": receipt_set_sha256,
            "drift": preview,
        },
        domain="mobilework-owned-state-drift-confirmation-v2",
    )


def _validate_json_value(value: Any, field: str) -> None:
    try:
        _canonical_json_bytes(value)
    except InstallStateError:
        _raise("INSTALL_RECEIPT_INVALID", f"{field} must contain canonical JSON values")


def _validate_mapping_section(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"{field} must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            _raise("INSTALL_RECEIPT_INVALID", f"{field} keys must be non-empty strings")
        _validate_json_value(item, f"{field}.{key}")


def _validate_list_section(value: Any, field: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"{field} must be a list of non-empty strings",
        )
    if len(value) != len(set(value)):
        _raise("INSTALL_RECEIPT_INVALID", f"{field} must not contain duplicates")


def _validate_config_values(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"{field} must be an object")
    unknown = sorted(
        set(value)
        - MAPPING_CONFIG_SECTIONS
        - LIST_CONFIG_SECTIONS
        - {"__scalar__"}
    )
    if unknown:
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"{field} contains unsupported sections",
        )
    for section in sorted(MAPPING_CONFIG_SECTIONS & set(value)):
        _validate_mapping_section(value[section], f"{field}.{section}")
    for section in sorted(LIST_CONFIG_SECTIONS & set(value)):
        _validate_list_section(value[section], f"{field}.{section}")

    scalar = value.get("__scalar__", {})
    if not isinstance(scalar, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"{field}.__scalar__ must be an object")
    if set(scalar) - {"lsp"}:
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"{field}.__scalar__ contains unsupported keys",
        )
    if "lsp" in scalar and not isinstance(scalar["lsp"], bool):
        _raise("INSTALL_RECEIPT_INVALID", f"{field}.__scalar__.lsp must be a boolean")
    if "lsp" in scalar and "lsp" in value:
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"{field} must not own mapping and scalar lsp simultaneously",
        )
    return json.loads(_canonical_json_bytes(value).decode("utf-8"))


def _validate_dependencies(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"{field} must be an object")
    if set(value) - set(DEPENDENCY_SECTIONS):
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"{field} contains unsupported sections",
        )
    for section, entries in value.items():
        if not isinstance(entries, dict):
            _raise("INSTALL_RECEIPT_INVALID", f"{field}.{section} must be an object")
        for name, version in entries.items():
            if not isinstance(name, str) or not name:
                _raise(
                    "INSTALL_RECEIPT_INVALID",
                    f"{field}.{section} names must be non-empty strings",
                )
            if not isinstance(version, str) or not version:
                _raise(
                    "INSTALL_RECEIPT_INVALID",
                    f"{field}.{section} versions must be non-empty strings",
                )
    return json.loads(_canonical_json_bytes(value).decode("utf-8"))


def _validate_receipt(path: Path, data: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(data, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name} root must be an object")
    receipt_contract = data.get("contract")
    if (
        isinstance(receipt_contract, bool)
        or not isinstance(receipt_contract, int)
        or receipt_contract not in receipt_policy().read_versions
    ):
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name} has unsupported contract")

    contract_3_fields = frozenset(
        (*contract_3_hash_fields(), *contract_3_version_fields())
    )
    allowed = _BASE_RECEIPT_FIELDS | (
        contract_3_fields if receipt_contract == 3 else frozenset()
    )
    if set(data) - allowed:
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name} contains unsupported fields")

    slug = data.get("slug")
    if not isinstance(slug, str) or not contract.NAME_RE.fullmatch(slug):
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"receipt {path.name} slug must be lowercase kebab-case",
        )
    if path.stem != slug:
        _raise(
            "INSTALL_RECEIPT_INVALID",
            f"receipt {path.name} filename must match its slug",
        )

    files = data.get("files")
    if not isinstance(files, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name}.files must be an object")
    normalized_files: dict[str, str] = {}
    for relative, digest in files.items():
        if not isinstance(relative, str):
            _raise(
                "INSTALL_RECEIPT_INVALID",
                f"receipt {path.name}.files paths must be strings",
            )
        try:
            normalized = contract.posix_relative_path(
                relative,
                f"receipt {path.name}.files",
            )
        except contract.ContractError:
            _raise(
                "INSTALL_RECEIPT_INVALID",
                f"receipt {path.name}.files contains an unsafe path",
            )
        if (
            normalized != relative
            or normalized.split("/", 1)[0] not in contract.RUNTIME_DIRS
        ):
            _raise(
                "INSTALL_RECEIPT_INVALID",
                f"receipt {path.name}.files must stay in a managed runtime directory",
            )
        if not isinstance(digest, str) or not contract.SHA256_RE.fullmatch(digest):
            _raise(
                "INSTALL_RECEIPT_INVALID",
                f"receipt {path.name}.files digests must be lowercase SHA-256",
            )
        normalized_files[normalized] = digest

    config_values = _validate_config_values(
        data.get("config_values", {}),
        f"receipt {path.name}.config_values",
    )
    dependencies = _validate_dependencies(
        data.get("dependencies", {}),
        f"receipt {path.name}.dependencies",
    )
    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name}.bindings must be an object")
    _validate_json_value(bindings, f"receipt {path.name}.bindings")

    if receipt_contract == 3:
        missing = sorted(contract_3_fields - set(data))
        if missing:
            _raise(
                "INSTALL_RECEIPT_INVALID",
                f"receipt {path.name} is missing required contract 3 evidence",
            )
        for field in contract_3_hash_fields():
            digest = data.get(field)
            if not isinstance(digest, str) or not contract.SHA256_RE.fullmatch(digest):
                _raise(
                    "INSTALL_RECEIPT_INVALID",
                    f"receipt {path.name}.{field} must be a lowercase SHA-256",
                )
        for field in contract_3_version_fields():
            version = data.get(field)
            if (
                not isinstance(version, str)
                or not version
                or version.strip() != version
                or len(version) > 128
                or any(character.isspace() for character in version)
            ):
                _raise(
                    "INSTALL_RECEIPT_INVALID",
                    f"receipt {path.name}.{field} must be a version identifier",
                )

    normalized_receipt: dict[str, Any] = {
        "contract": receipt_contract,
        "slug": slug,
        "files": dict(sorted(normalized_files.items())),
        "config_values": config_values,
        "dependencies": dependencies,
    }
    if bindings:
        normalized_receipt["bindings"] = json.loads(
            _canonical_json_bytes(bindings).decode("utf-8")
        )
    if receipt_contract == 3:
        for field in (*contract_3_hash_fields(), *contract_3_version_fields()):
            normalized_receipt[field] = data[field]
    return slug, normalized_receipt


def parse_receipt_bytes(path: Path, content: bytes) -> dict[str, Any]:
    """Strictly parse and normalize receipt bytes already captured by safe_input."""

    try:
        data = json.loads(
            content.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (UnicodeError, ValueError):
        _raise("INSTALL_RECEIPT_INVALID", f"cannot parse receipt {path.name}")
    _slug, receipt = _validate_receipt(path, data)
    return receipt


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        snapshot = safe_input.inspect(path)
        data = json.loads(snapshot.read_text())
    except (safe_input.InputInspectionError, UnicodeError, json.JSONDecodeError):
        _raise("INSTALL_RECEIPT_INVALID", f"cannot parse receipt {path.name}")
    if not isinstance(data, dict):
        _raise("INSTALL_RECEIPT_INVALID", f"receipt {path.name} root must be an object")
    return data


def load_receipts(runtime_dir: Path) -> dict[str, dict[str, Any]]:
    """Load and strictly validate all contract 1/2/3 receipts."""

    receipt_root = Path(runtime_dir) / contract.INSTALL_RECEIPT_DIR
    if not receipt_root.exists():
        return {}
    try:
        metadata = receipt_root.lstat()
    except OSError:
        _raise("INSTALL_RECEIPT_INVALID", "cannot inspect install receipt directory")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _raise("INSTALL_RECEIPT_INVALID", "install receipt directory must be a directory")

    result: dict[str, dict[str, Any]] = {}
    for path in sorted(receipt_root.glob("*.json"), key=lambda item: item.name):
        slug, receipt = _validate_receipt(path, _read_receipt(path))
        if slug in result:
            _raise("INSTALL_RECEIPT_INVALID", "duplicate install receipts")
        result[slug] = receipt
    return result


def _capture_optional_file(
    path: Path,
    label: str,
    limits: safe_input.InputLimits | None = None,
) -> safe_input.InputFile | None:
    try:
        snapshot = safe_input.inspect(path, limits=limits)
    except safe_input.InputInspectionError as exc:
        if exc.code == "INPUT_NOT_FOUND":
            return None
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            f"{label} cannot be captured safely ({exc.code})",
        )
    if snapshot.kind != "file" or len(snapshot.files) != 1:
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            f"{label} must be a regular file",
        )
    return snapshot.files[0]


def _file_capture_state(captured: safe_input.InputFile | None) -> dict[str, Any]:
    if captured is None:
        return {"present": False}
    return {
        "present": True,
        "device": captured.device,
        "inode": captured.inode,
        "size": captured.size,
        "mtimeNs": captured.mtime_ns,
        "ctimeNs": captured.ctime_ns,
        "mode": captured.mode,
        "fileAttributes": captured.file_attributes,
        "sha256": captured.sha256,
    }


def _file_content_state(captured: safe_input.InputFile | None) -> dict[str, Any]:
    """Return a stable, identity-independent description of captured bytes."""

    if captured is None:
        return {"present": False}
    return {
        "present": True,
        "size": captured.size,
        "mode": captured.mode,
        "fileAttributes": captured.file_attributes,
        "sha256": captured.sha256,
    }


def _metadata_identity(metadata: Any) -> list[int]:
    return [
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(getattr(metadata, "st_file_attributes", 0)),
    ]


def _capture_receipt_tree(
    runtime_dir: Path,
    limits: safe_input.InputLimits | None = None,
) -> tuple[safe_input.InputSnapshot | None, list[int] | None]:
    receipt_root = runtime_dir / contract.INSTALL_RECEIPT_DIR
    try:
        snapshot = safe_input.inspect(receipt_root, limits=limits)
    except safe_input.InputInspectionError as exc:
        if exc.code == "INPUT_NOT_FOUND":
            return None, None
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            f"install receipt set cannot be captured safely ({exc.code})",
        )
    if snapshot.kind != "directory":
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            "install receipt root must be a directory",
        )
    try:
        identity = _metadata_identity(receipt_root.lstat())
    except OSError:
        _raise(
            "INSTALL_INPUT_STATE_CHANGED",
            "install receipt set changed after capture",
        )
    return snapshot, identity


def _parse_captured_config(
    captured: safe_input.InputFile | None,
) -> dict[str, Any]:
    if captured is None or not captured.content.strip():
        return {}
    try:
        data = contract.parse_jsonc(
            captured.content.decode("utf-8"),
            contract.WORKSPACE_CONFIG,
        )
    except (UnicodeError, contract.ContractError):
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            f"workspace {contract.WORKSPACE_CONFIG} is invalid",
        )
    if not isinstance(data, dict):
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            f"workspace {contract.WORKSPACE_CONFIG} root must be an object",
        )
    return data


def _parse_captured_package_json(
    captured: safe_input.InputFile | None,
) -> dict[str, Any]:
    if captured is None:
        return {}
    try:
        data = json.loads(
            captured.content.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (UnicodeError, ValueError):
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            "workspace package.json is invalid",
        )
    if not isinstance(data, dict):
        _raise(
            "INSTALL_INPUT_STATE_INVALID",
            "workspace package.json root must be an object",
        )
    return data


def _parse_captured_receipts(
    runtime_dir: Path,
    snapshot: safe_input.InputSnapshot | None,
) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    if snapshot.directories:
        _raise(
            "INSTALL_RECEIPT_INVALID",
            "install receipt root must not contain nested directories",
        )
    result: dict[str, dict[str, Any]] = {}
    receipt_root = runtime_dir / contract.INSTALL_RECEIPT_DIR
    for captured in snapshot.files:
        relative = Path(captured.relative_path)
        if len(relative.parts) != 1 or relative.suffix != ".json":
            _raise(
                "INSTALL_RECEIPT_INVALID",
                "install receipt root contains an unsupported entry",
            )
        receipt = parse_receipt_bytes(receipt_root / relative.name, captured.content)
        slug = receipt["slug"]
        if slug in result:
            _raise("INSTALL_RECEIPT_INVALID", "duplicate install receipts")
        result[slug] = receipt
    return result


def capture_runtime_inputs(
    runtime_dir: Path,
    *,
    target_paths: Iterable[str] = (),
) -> RuntimeInputSnapshot:
    """Capture every byte used by an install merge plus exact target states.

    The returned parsed values are derived only from these protected captures.
    Comparing ``fingerprint`` immediately before commit prevents a staged merge
    from overwriting concurrent user additions or receipt changes.
    """

    runtime_dir = Path(runtime_dir)
    limits = safe_input.default_limits()
    config_capture = _capture_optional_file(
        runtime_dir / contract.WORKSPACE_CONFIG,
        f"workspace {contract.WORKSPACE_CONFIG}",
        limits,
    )
    package_capture = _capture_optional_file(
        runtime_dir / "package.json",
        "workspace package.json",
        limits,
    )
    receipt_snapshot, receipt_identity = _capture_receipt_tree(runtime_dir, limits)
    receipts = _parse_captured_receipts(runtime_dir, receipt_snapshot)

    target_states: dict[str, dict[str, Any]] = {}
    target_files: dict[str, safe_input.InputFile | None] = {}
    managed_targets = set(target_paths)
    for receipt in receipts.values():
        files = receipt.get("files", {})
        if isinstance(files, Mapping):
            managed_targets.update(
                relative for relative in files if isinstance(relative, str)
            )
    requested_paths = {
        contract.WORKSPACE_CONFIG,
        "package.json",
        *managed_targets,
    }
    if receipt_snapshot is not None:
        requested_paths.update(
            f"{contract.INSTALL_RECEIPT_DIR}/{item.relative_path}"
            for item in receipt_snapshot.files
        )
    for relative in sorted(requested_paths):
        try:
            safe_input.check_relative_path(relative, limits)
        except safe_input.InputInspectionError as exc:
            _raise(
                "INSTALL_INPUT_LIMIT_EXCEEDED",
                f"workspace install path exceeds the input limits ({exc.code})",
            )
    if len(requested_paths) > limits.max_entries:
        _raise(
            "INSTALL_INPUT_LIMIT_EXCEEDED",
            "workspace install state exceeds the aggregate entry limit",
        )

    captured_by_path: dict[str, safe_input.InputFile] = {}
    captured_total_bytes = 0

    def add_captured(relative: str, captured: safe_input.InputFile | None) -> None:
        nonlocal captured_total_bytes
        if captured is None or relative in captured_by_path:
            return
        captured_by_path[relative] = captured
        captured_total_bytes += captured.size
        if captured_total_bytes > limits.max_total_bytes:
            _raise(
                "INSTALL_INPUT_LIMIT_EXCEEDED",
                "workspace install state exceeds the aggregate byte limit",
            )

    add_captured(contract.WORKSPACE_CONFIG, config_capture)
    add_captured("package.json", package_capture)
    if receipt_snapshot is not None:
        for item in receipt_snapshot.files:
            add_captured(
                f"{contract.INSTALL_RECEIPT_DIR}/{item.relative_path}",
                item,
            )

    receipt_prefix = f"{contract.INSTALL_RECEIPT_DIR}/"
    for raw_relative in sorted(managed_targets):
        try:
            relative = contract.posix_relative_path(
                raw_relative,
                "install merge target",
            )
        except contract.ContractError:
            _raise(
                "INSTALL_INPUT_STATE_INVALID",
                "install merge target contains an unsafe path",
            )
        try:
            safe_input.check_relative_path(relative, limits)
        except safe_input.InputInspectionError as exc:
            _raise(
                "INSTALL_INPUT_LIMIT_EXCEEDED",
                f"install merge target exceeds the input path limits ({exc.code})",
            )
        if relative == contract.WORKSPACE_CONFIG:
            captured_target = config_capture
        elif relative == "package.json":
            captured_target = package_capture
        elif relative.startswith(receipt_prefix) and receipt_snapshot is not None:
            receipt_relative = relative[len(receipt_prefix) :]
            try:
                captured_target = receipt_snapshot.file(receipt_relative)
            except KeyError:
                captured_target = None
        else:
            captured_target = _capture_optional_file(
                runtime_dir / relative,
                f"install merge target {relative}",
                limits,
            )
        target_files[relative] = captured_target
        target_states[relative] = _file_capture_state(captured_target)
        add_captured(relative, captured_target)

    receipt_state: dict[str, Any] = {"present": receipt_snapshot is not None}
    if receipt_snapshot is not None:
        receipt_state.update(
            {
                "identity": receipt_identity,
                "sha256": receipt_snapshot.sha256,
                "directories": list(receipt_snapshot.directories),
                "files": {
                    item.relative_path: _file_capture_state(item)
                    for item in receipt_snapshot.files
                },
            }
        )
    fingerprint = manager_contract.canonical_json_sha256(
        {
            "config": _file_capture_state(config_capture),
            "packageJson": _file_capture_state(package_capture),
            "receipts": receipt_state,
            "targets": target_states,
        },
        domain="mobilework-install-input-state-v1",
    )
    receipt_set_sha256 = (
        receipt_snapshot.sha256
        if receipt_snapshot is not None
        else manager_contract.canonical_json_sha256(
            {"present": False},
            domain="mobilework-install-receipt-set-v1",
        )
    )
    content_state_sha256 = manager_contract.canonical_json_sha256(
        {
            "config": _file_content_state(config_capture),
            "packageJson": _file_content_state(package_capture),
            "receipts": {
                "present": receipt_snapshot is not None,
                "sha256": receipt_snapshot.sha256 if receipt_snapshot else None,
                "directories": list(receipt_snapshot.directories)
                if receipt_snapshot
                else [],
                "files": {
                    item.relative_path: _file_content_state(item)
                    for item in receipt_snapshot.files
                }
                if receipt_snapshot
                else {},
            },
            "targets": {
                relative: _file_content_state(captured)
                for relative, captured in sorted(target_files.items())
            },
        },
        domain="mobilework-install-content-state-v1",
    )
    return RuntimeInputSnapshot(
        config=_parse_captured_config(config_capture),
        config_present=config_capture is not None,
        config_file=config_capture,
        package_json=_parse_captured_package_json(package_capture),
        package_json_present=package_capture is not None,
        package_json_file=package_capture,
        receipts=receipts,
        receipt_snapshot=receipt_snapshot,
        target_files=target_files,
        fingerprint=fingerprint,
        content_state_sha256=content_state_sha256,
        receipt_set_sha256=receipt_set_sha256,
    )


def captured_runtime_file(
    snapshot: RuntimeInputSnapshot,
    relative_path: str,
) -> safe_input.InputFile | None:
    """Return one target's already-protected bytes without another disk read."""

    relative = contract.posix_relative_path(relative_path, "captured runtime path")
    if relative == contract.WORKSPACE_CONFIG:
        return snapshot.config_file
    if relative == "package.json":
        return snapshot.package_json_file
    receipt_prefix = f"{contract.INSTALL_RECEIPT_DIR}/"
    if relative.startswith(receipt_prefix):
        if snapshot.receipt_snapshot is None:
            return None
        receipt_relative = relative[len(receipt_prefix) :]
        try:
            return snapshot.receipt_snapshot.file(receipt_relative)
        except KeyError:
            return None
    return snapshot.target_files.get(relative)


def _receipt_contract(receipt: Mapping[str, Any]) -> int | None:
    value = receipt.get("contract")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value not in receipt_policy().read_versions
    ):
        return None
    return int(value)


def file_owners(receipts: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """Return owners for receipt-managed files for every supported contract."""

    owners: dict[str, set[str]] = {}
    for slug, receipt in receipts.items():
        if _receipt_contract(receipt) is None:
            continue
        files = receipt.get("files", {})
        if not isinstance(files, Mapping):
            continue
        for relative in files:
            if isinstance(relative, str):
                owners.setdefault(relative, set()).add(slug)
    return owners


def config_owners(
    receipts: Mapping[str, Mapping[str, Any]],
    section: str,
    key: str,
) -> set[str]:
    """Return mapping/scalar owners; contract 1 ownership remains trusted."""

    owners: set[str] = set()
    for slug, receipt in receipts.items():
        if _receipt_contract(receipt) is None:
            continue
        values = receipt.get("config_values", {})
        if not isinstance(values, Mapping):
            continue
        section_values = values.get(section, {})
        if isinstance(section_values, Mapping) and key in section_values:
            owners.add(slug)
    return owners


def list_owners(
    receipts: Mapping[str, Mapping[str, Any]],
    section: str,
    item: str,
) -> set[str]:
    """Return list owners, excluding legacy contract 1 claims."""

    owners: set[str] = set()
    for slug, receipt in receipts.items():
        receipt_contract = _receipt_contract(receipt)
        if receipt_contract not in extended_ownership_contracts():
            continue
        values = receipt.get("config_values", {})
        if not isinstance(values, Mapping):
            continue
        section_values = values.get(section, [])
        if isinstance(section_values, list) and item in section_values:
            owners.add(slug)
    return owners


def dependency_owners(
    receipts: Mapping[str, Mapping[str, Any]],
    section: str,
    name: str,
) -> set[str]:
    """Return dependency owners, excluding legacy contract 1 claims."""

    owners: set[str] = set()
    for slug, receipt in receipts.items():
        receipt_contract = _receipt_contract(receipt)
        if receipt_contract not in extended_ownership_contracts():
            continue
        dependencies = receipt.get("dependencies", {})
        if not isinstance(dependencies, Mapping):
            continue
        entries = dependencies.get(section, {})
        if isinstance(entries, Mapping) and name in entries:
            owners.add(slug)
    return owners


def _safe_pointer_segment(value: str) -> str:
    sanitized = output_sanitizer.sanitize_text(value)
    return sanitized.replace("~", "~0").replace("/", "~1")


def _preview_item(
    code: str,
    kind: str,
    path: str,
    expected_sha256: str,
    actual_sha256: str | None,
) -> dict[str, Any]:
    item = {
        "code": code,
        "kind": kind,
        "path": path,
        "expectedSha256": expected_sha256,
        "actualSha256": actual_sha256,
    }
    sanitized = output_sanitizer.sanitize_mapping(item)
    sanitized = output_sanitizer.sanitize_mapping(sanitized)
    if set(sanitized) != _PREVIEW_FIELDS:
        _raise("OWNED_STATE_INTERNAL_ERROR", "owned-state preview schema changed")
    return sanitized


def _capture_file(path: Path) -> tuple[safe_input.InputFile | None, str | None]:
    try:
        snapshot = safe_input.inspect(path)
    except safe_input.InputInspectionError as exc:
        if exc.code == "INPUT_NOT_FOUND":
            return None, None
        return None, value_sha256({"inputError": exc.code})
    if snapshot.kind != "file" or len(snapshot.files) != 1:
        return None, value_sha256({"inputError": "INPUT_NOT_REGULAR_FILE"})
    return snapshot.files[0], None


def _verify_files(
    runtime_dir: Path,
    receipt: Mapping[str, Any],
    preview: list[dict[str, Any]],
    captured_files: Mapping[str, safe_input.InputFile | None] | None = None,
) -> None:
    files = receipt.get("files", {})
    if not isinstance(files, Mapping):
        return
    for relative, expected in sorted(files.items()):
        target = runtime_dir / str(relative)
        safe_path = output_sanitizer.sanitize_text(str(relative))
        if captured_files is None:
            captured, read_error = _capture_file(target)
        else:
            captured = captured_files.get(str(relative))
            read_error = None
        if captured is None and read_error is None:
            preview.append(
                _preview_item(
                    "OWNED_FILE_MISSING",
                    "file",
                    safe_path,
                    str(expected),
                    None,
                )
            )
            continue
        if captured is None:
            preview.append(
                _preview_item(
                    "OWNED_FILE_CHANGED",
                    "file",
                    safe_path,
                    str(expected),
                    read_error,
                )
            )
            continue
        actual = captured.sha256
        if actual != expected:
            preview.append(
                _preview_item(
                    "OWNED_FILE_CHANGED",
                    "file",
                    safe_path,
                    str(expected),
                    actual,
                )
            )


def _config_path(section: str, key: str) -> str:
    return (
        f"{contract.WORKSPACE_CONFIG}#/"
        f"{_safe_pointer_segment(section)}/{_safe_pointer_segment(key)}"
    )


def _list_item_path(section: str, item: str) -> str:
    return f"{contract.WORKSPACE_CONFIG}#/{section}/sha256:{value_sha256(item)}"


def _load_runtime_config(runtime_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = runtime_dir / contract.WORKSPACE_CONFIG
    captured, read_error = _capture_file(path)
    if captured is None:
        return None, read_error
    raw = captured.content
    raw_digest = captured.sha256
    try:
        config = contract.parse_jsonc(raw.decode("utf-8"), str(path.name))
    except (UnicodeError, contract.ContractError):
        return None, raw_digest
    return config, raw_digest


def _verify_config(
    runtime_dir: Path,
    receipt: Mapping[str, Any],
    preview: list[dict[str, Any]],
    snapshot: RuntimeInputSnapshot | None = None,
) -> None:
    values = receipt.get("config_values", {})
    if not isinstance(values, Mapping) or not values:
        return
    if snapshot is None:
        config, unreadable_digest = _load_runtime_config(runtime_dir)
    else:
        config = snapshot.config if snapshot.config_present else None
        unreadable_digest = None

    for section in sorted(MAPPING_CONFIG_SECTIONS):
        expected_section = values.get(section, {})
        if not isinstance(expected_section, Mapping):
            continue
        current_section = config.get(section) if config is not None else None
        for key, expected in sorted(expected_section.items()):
            path = _config_path(section, str(key))
            expected_digest = value_sha256(expected)
            if not isinstance(current_section, Mapping) or key not in current_section:
                code = (
                    "OWNED_CONFIG_CHANGED"
                    if config is None and unreadable_digest is not None
                    else "OWNED_CONFIG_MISSING"
                )
                actual = unreadable_digest if code == "OWNED_CONFIG_CHANGED" else None
            else:
                actual = value_sha256(current_section[key])
                code = "OWNED_CONFIG_CHANGED"
            if actual != expected_digest:
                preview.append(
                    _preview_item(
                        code,
                        "config-mapping",
                        path,
                        expected_digest,
                        actual,
                    )
                )

    scalar = values.get("__scalar__", {})
    if isinstance(scalar, Mapping) and "lsp" in scalar:
        expected = scalar["lsp"]
        expected_digest = value_sha256(expected)
        if config is None or "lsp" not in config:
            code = (
                "OWNED_CONFIG_CHANGED"
                if config is None and unreadable_digest is not None
                else "OWNED_CONFIG_MISSING"
            )
            actual = unreadable_digest if code == "OWNED_CONFIG_CHANGED" else None
        else:
            actual = value_sha256(config["lsp"])
            code = "OWNED_CONFIG_CHANGED"
        if actual != expected_digest:
            preview.append(
                _preview_item(
                    code,
                    "config-scalar",
                    f"{contract.WORKSPACE_CONFIG}#/lsp",
                    expected_digest,
                    actual,
                )
            )

    if _receipt_contract(receipt) not in extended_ownership_contracts():
        return
    for section in sorted(LIST_CONFIG_SECTIONS):
        expected_items = values.get(section, [])
        if not isinstance(expected_items, list):
            continue
        current_items = config.get(section) if config is not None else None
        for item in sorted(expected_items):
            if isinstance(current_items, list) and item in current_items:
                continue
            code = (
                "OWNED_CONFIG_CHANGED"
                if config is None and unreadable_digest is not None
                else "OWNED_CONFIG_MISSING"
            )
            preview.append(
                _preview_item(
                    code,
                    "config-list",
                    _list_item_path(section, item),
                    value_sha256(item),
                    unreadable_digest if code == "OWNED_CONFIG_CHANGED" else None,
                )
            )


def _load_package_json(runtime_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = runtime_dir / "package.json"
    captured, read_error = _capture_file(path)
    if captured is None:
        return None, read_error
    raw = captured.content
    raw_digest = captured.sha256
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, raw_digest
    if not isinstance(data, dict):
        return None, raw_digest
    return data, raw_digest


def _verify_dependencies(
    runtime_dir: Path,
    receipt: Mapping[str, Any],
    preview: list[dict[str, Any]],
    snapshot: RuntimeInputSnapshot | None = None,
) -> None:
    if _receipt_contract(receipt) not in extended_ownership_contracts():
        return
    dependencies = receipt.get("dependencies", {})
    if not isinstance(dependencies, Mapping) or not dependencies:
        return
    if snapshot is None:
        package_json, unreadable_digest = _load_package_json(runtime_dir)
    else:
        package_json = snapshot.package_json if snapshot.package_json_present else None
        unreadable_digest = None
    for section in DEPENDENCY_SECTIONS:
        expected_section = dependencies.get(section, {})
        if not isinstance(expected_section, Mapping):
            continue
        current_section = package_json.get(section) if package_json is not None else None
        for name, expected in sorted(expected_section.items()):
            path = f"package.json#/{section}/{_safe_pointer_segment(str(name))}"
            expected_digest = value_sha256(expected)
            if not isinstance(current_section, Mapping) or name not in current_section:
                code = (
                    "OWNED_DEPENDENCY_CHANGED"
                    if package_json is None and unreadable_digest is not None
                    else "OWNED_DEPENDENCY_MISSING"
                )
                actual = unreadable_digest if code == "OWNED_DEPENDENCY_CHANGED" else None
            else:
                actual = value_sha256(current_section[name])
                code = "OWNED_DEPENDENCY_CHANGED"
            if actual != expected_digest:
                preview.append(
                    _preview_item(
                        code,
                        "dependency",
                        path,
                        expected_digest,
                        actual,
                    )
                )


def _normalize_receipts_mapping(
    runtime_dir: Path,
    receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for slug, receipt in sorted(receipts.items()):
        if not isinstance(slug, str):
            _raise("INSTALL_RECEIPT_INVALID", "receipt index keys must be strings")
        receipt_path = runtime_dir / contract.INSTALL_RECEIPT_DIR / f"{slug}.json"
        normalized_slug, normalized_receipt = _validate_receipt(receipt_path, receipt)
        if normalized_slug != slug:
            _raise("INSTALL_RECEIPT_INVALID", "receipt index key must match receipt slug")
        normalized[slug] = normalized_receipt
    return normalized


def verify_owned_state(
    runtime_dir: Path,
    receipt: Mapping[str, Any],
    all_receipts: Mapping[str, Mapping[str, Any]],
    *,
    snapshot: RuntimeInputSnapshot | None = None,
) -> dict[str, Any]:
    """Compare receipt-owned state without treating unrelated additions as drift."""

    runtime_dir = Path(runtime_dir)
    normalized_receipts = _normalize_receipts_mapping(runtime_dir, all_receipts)
    slug = receipt.get("slug") if isinstance(receipt, Mapping) else None
    if not isinstance(slug, str):
        _raise("INSTALL_RECEIPT_INVALID", "receipt slug must be a string")
    receipt_path = runtime_dir / contract.INSTALL_RECEIPT_DIR / f"{slug}.json"
    normalized_slug, normalized_receipt = _validate_receipt(receipt_path, receipt)
    indexed_receipt = normalized_receipts.get(normalized_slug)
    if indexed_receipt is None:
        _raise("INSTALL_RECEIPT_SET_MISMATCH", "receipt is absent from all_receipts")
    if _canonical_json_bytes(indexed_receipt) != _canonical_json_bytes(normalized_receipt):
        _raise("INSTALL_RECEIPT_SET_MISMATCH", "receipt differs from all_receipts")

    snapshot_was_supplied = snapshot is not None
    if snapshot is None:
        files = normalized_receipt.get("files", {})
        snapshot = capture_runtime_inputs(
            runtime_dir,
            target_paths=files if isinstance(files, Mapping) else (),
        )
    snapshot_receipts = _normalize_receipts_mapping(runtime_dir, snapshot.receipts)
    if snapshot_was_supplied and _canonical_json_bytes(
        snapshot_receipts
    ) != _canonical_json_bytes(normalized_receipts):
        _raise(
            "INSTALL_RECEIPT_SET_MISMATCH",
            "captured receipt set differs from all_receipts",
        )

    preview: list[dict[str, Any]] = []
    _verify_files(
        runtime_dir,
        normalized_receipt,
        preview,
        snapshot.target_files,
    )
    _verify_config(runtime_dir, normalized_receipt, preview, snapshot)
    _verify_dependencies(runtime_dir, normalized_receipt, preview, snapshot)
    preview.sort(
        key=lambda item: (
            str(item["path"]),
            str(item["kind"]),
            str(item["code"]),
            str(item["expectedSha256"]),
            str(item["actualSha256"] or ""),
        )
    )
    receipt_sha256 = value_sha256(normalized_receipt)
    report = {
        "schemaVersion": receipt_policy().drift_preview_schema_version,
        "ok": not preview,
        "status": "clean" if not preview else "drifted",
        "slug": normalized_slug,
        "receiptContract": normalized_receipt["contract"],
        "preview": preview,
        "contentStateSha256": snapshot.content_state_sha256,
        "receiptSetSha256": snapshot.receipt_set_sha256,
        "receiptSha256": receipt_sha256,
        "previewSha256": canonical_preview_sha256(
            preview,
            slug=normalized_slug,
            receipt_sha256=receipt_sha256,
            content_state_sha256=snapshot.content_state_sha256,
            receipt_set_sha256=snapshot.receipt_set_sha256,
        ),
    }
    sanitized = output_sanitizer.sanitize_mapping(report)
    return output_sanitizer.sanitize_mapping(sanitized)
