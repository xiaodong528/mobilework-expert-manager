#!/usr/bin/env python3
"""Pure contract-3 projection and evidence hashing for expert installs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import manager_contract
import package_contract
import safe_input


CONFIG_MAPPING_SECTIONS = ("agent", "mcp", "references")
CONFIG_LIST_SECTIONS = ("plugin", "instructions")
CONFIG_SECTIONS = (*CONFIG_MAPPING_SECTIONS, *CONFIG_LIST_SECTIONS, "lsp")


@dataclass(frozen=True)
class ProjectionMismatch:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def build(
    *,
    sources: Mapping[str, bytes],
    runtime: Mapping[str, Any],
    dependencies: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the package-owned projection, excluding unrelated workspace state."""

    policy = manager_contract.load_policy()["receiptContract"]
    config = {
        section: json.loads(json.dumps(runtime[section], ensure_ascii=False))
        for section in CONFIG_SECTIONS
        if section in runtime
    }
    normalized_dependencies = package_contract.normalize_package_dependencies(
        dict(dependencies),
        ".opencode/package.json",
    )
    return {
        "schemaVersion": policy["projectionSchemaVersion"],
        "files": {
            relative: package_contract.sha256_bytes(content)
            for relative, content in sorted(sources.items())
        },
        "config": config,
        "dependencies": normalized_dependencies,
        "bindings": json.loads(json.dumps(dict(bindings), ensure_ascii=False)),
    }


def sha256(projection: Mapping[str, Any]) -> str:
    return manager_contract.canonical_json_sha256(
        dict(projection),
        domain="mobilework-install-projection-v1",
    )


def receipt_evidence(
    *,
    snapshot: safe_input.InputSnapshot,
    target: manager_contract.TargetContract,
    projection: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "packageTreeSha256": snapshot.sha256,
        "manifestSha256": snapshot.file("expert.json").sha256,
        "managerContractSha256": manager_contract.policy_sha256(),
        "targetOpenCodeVersion": target.version,
        "targetCapabilitiesSha256": manager_contract.target_capabilities_sha256(target),
        "projectionSha256": sha256(projection),
    }


def _digest(value: Any, *, domain: str) -> str:
    return manager_contract.canonical_json_sha256(value, domain=domain)


def _mismatch(code: str, path: str, message: str) -> ProjectionMismatch:
    return ProjectionMismatch(code=code, path=path, message=message)


def _contains_projection(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _contains_projection(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    return actual == expected


def verify_resolved_config(
    resolved: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> list[ProjectionMismatch]:
    """Require sidecar output to contain every package-projected config value."""

    mismatches: list[ProjectionMismatch] = []
    expected_config = projection.get("config", {})
    if not isinstance(expected_config, Mapping):
        return [
            _mismatch(
                "CONFIG_RESOLVED_PROJECTION_MISMATCH",
                "projection.config",
                "package config projection is invalid",
            )
        ]
    for section, expected in expected_config.items():
        if section not in resolved or not _contains_projection(resolved[section], expected):
            mismatches.append(
                _mismatch(
                    "CONFIG_RESOLVED_PROJECTION_MISMATCH",
                    f"resolvedConfig.{section}",
                    f"sidecar-resolved config does not contain projected {section} values",
                )
            )
    return sorted(mismatches, key=lambda item: (item.path, item.code, item.message))


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    snapshot: safe_input.InputSnapshot,
    target: manager_contract.TargetContract,
    projection: Mapping[str, Any],
) -> list[ProjectionMismatch]:
    """Recompute contract-3 evidence from package bytes and target facts."""

    mismatches: list[ProjectionMismatch] = []
    policy = manager_contract.load_policy()["receiptContract"]
    if receipt.get("contract") not in policy["configLoadableVersions"]:
        mismatches.append(
            _mismatch(
                "CONFIG_RECEIPT_CONTRACT_UNTRUSTED",
                "receipt.contract",
                "config evidence requires an install receipt written with contract 3",
            )
        )
        return mismatches

    expected_evidence = receipt_evidence(
        snapshot=snapshot,
        target=target,
        projection=projection,
    )
    for field, expected in expected_evidence.items():
        if receipt.get(field) != expected:
            mismatches.append(
                _mismatch(
                    "CONFIG_RECEIPT_HASH_MISMATCH",
                    f"receipt.{field}",
                    f"receipt {field} does not match the supplied package and target contract",
                )
            )

    projected_files = projection.get("files", {})
    if receipt.get("files") != projected_files:
        mismatches.append(
            _mismatch(
                "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                "receipt.files",
                "receipt file ownership does not match the package projection",
            )
        )

    projected_config = projection.get("config", {})
    receipt_config = receipt.get("config_values", {})
    if not isinstance(receipt_config, dict):
        mismatches.append(
            _mismatch(
                "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                "receipt.config_values",
                "receipt config ownership must be an object",
            )
        )
    else:
        for section in (*CONFIG_MAPPING_SECTIONS, "lsp", "__scalar__"):
            expected = projected_config.get(section)
            if section == "__scalar__":
                expected = (
                    {"lsp": projected_config["lsp"]}
                    if isinstance(projected_config.get("lsp"), bool)
                    else None
                )
            elif section == "lsp" and isinstance(projected_config.get("lsp"), bool):
                expected = None
            actual = receipt_config.get(section)
            if expected is None:
                if actual not in (None, {}):
                    mismatches.append(
                        _mismatch(
                            "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                            f"receipt.config_values.{section}",
                            f"receipt owns config section {section} that is absent from the projection",
                        )
                    )
            elif actual != expected:
                mismatches.append(
                    _mismatch(
                        "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                        f"receipt.config_values.{section}",
                        f"receipt config ownership for {section} does not match the projection",
                    )
                )
        for section in CONFIG_LIST_SECTIONS:
            expected_items = projected_config.get(section, [])
            actual_items = receipt_config.get(section, [])
            if (
                not isinstance(expected_items, list)
                or not isinstance(actual_items, list)
                or any(item not in expected_items for item in actual_items)
            ):
                mismatches.append(
                    _mismatch(
                        "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                        f"receipt.config_values.{section}",
                        f"receipt list ownership for {section} is not a subset of the projection",
                    )
                )

    projected_dependencies = projection.get("dependencies", {})
    receipt_dependencies = receipt.get("dependencies", {})
    if not isinstance(receipt_dependencies, dict):
        mismatches.append(
            _mismatch(
                "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                "receipt.dependencies",
                "receipt dependency ownership must be an object",
            )
        )
    else:
        for section in package_contract.PACKAGE_DEPENDENCY_SECTIONS:
            expected = projected_dependencies.get(section, {})
            actual = receipt_dependencies.get(section, {})
            if not isinstance(expected, dict) or not isinstance(actual, dict) or any(
                name not in expected or expected[name] != version
                for name, version in actual.items()
            ):
                mismatches.append(
                    _mismatch(
                        "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                        f"receipt.dependencies.{section}",
                        f"receipt dependency ownership for {section} is not a subset of the projection",
                    )
                )

    if receipt.get("bindings", {}) != projection.get("bindings", {}):
        mismatches.append(
            _mismatch(
                "CONFIG_RECEIPT_PROJECTION_MISMATCH",
                "receipt.bindings",
                "receipt bindings do not match the package projection",
            )
        )
    return sorted(mismatches, key=lambda item: (item.path, item.code, item.message))


def _snapshot_file(path: Path) -> safe_input.InputFile | None:
    try:
        snapshot = safe_input.inspect(path)
    except safe_input.InputInspectionError as exc:
        if exc.code == "INPUT_NOT_FOUND":
            return None
        raise package_contract.ContractError(
            f"unsafe or changing projected file {path}: {exc.code}"
        ) from exc
    if snapshot.kind != "file" or len(snapshot.files) != 1:
        raise package_contract.ContractError(f"unsafe projected file: {path}")
    return snapshot.files[0]


def _load_json_file(path: Path, *, jsonc: bool = False) -> dict[str, Any]:
    captured = _snapshot_file(path)
    if captured is None:
        return {}
    text = captured.content.decode("utf-8")
    if jsonc:
        return package_contract.parse_jsonc(text, str(path))
    value = json.loads(text)
    if not isinstance(value, dict):
        raise package_contract.ContractError(f"{path} root must be an object")
    return value


def verify_workspace_projection(
    runtime_dir: Path,
    projection: Mapping[str, Any],
) -> list[ProjectionMismatch]:
    """Verify full projected values while allowing unrelated workspace additions."""

    mismatches: list[ProjectionMismatch] = []
    for relative, expected_hash in sorted(dict(projection.get("files", {})).items()):
        target = runtime_dir / relative
        try:
            captured = _snapshot_file(target)
        except package_contract.ContractError:
            captured = None
        if captured is None:
            mismatches.append(
                _mismatch(
                    "CONFIG_PROJECTED_FILE_MISSING",
                    relative,
                    "projected package file is missing or unsafe",
                )
            )
        elif captured.sha256 != expected_hash:
            mismatches.append(
                _mismatch(
                    "CONFIG_PROJECTED_FILE_CHANGED",
                    relative,
                    "projected package file differs from the supplied package",
                )
            )

    try:
        config = _load_json_file(
            runtime_dir / package_contract.WORKSPACE_CONFIG,
            jsonc=True,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, package_contract.ContractError):
        config = {}
        mismatches.append(
            _mismatch(
                "CONFIG_WORKSPACE_CONFIG_INVALID",
                package_contract.WORKSPACE_CONFIG,
                "workspace config is missing, unsafe, or invalid",
            )
        )
    expected_config = projection.get("config", {})
    if isinstance(expected_config, dict):
        for section in CONFIG_MAPPING_SECTIONS:
            expected_values = expected_config.get(section, {})
            if not isinstance(expected_values, dict):
                continue
            actual_values = config.get(section, {})
            for key, expected in expected_values.items():
                if not isinstance(actual_values, dict) or actual_values.get(key) != expected:
                    mismatches.append(
                        _mismatch(
                            "CONFIG_PROJECTED_VALUE_MISMATCH",
                            f"{package_contract.WORKSPACE_CONFIG}.{section}.{key}",
                            "workspace projected mapping value does not match the package",
                        )
                    )
        for section in CONFIG_LIST_SECTIONS:
            expected_items = expected_config.get(section, [])
            actual_items = config.get(section, [])
            if isinstance(expected_items, list):
                for item in expected_items:
                    if not isinstance(actual_items, list) or item not in actual_items:
                        mismatches.append(
                            _mismatch(
                                "CONFIG_PROJECTED_VALUE_MISMATCH",
                                f"{package_contract.WORKSPACE_CONFIG}.{section}",
                                "workspace projected list entry is missing",
                            )
                        )
        if "lsp" in expected_config:
            expected_lsp = expected_config["lsp"]
            actual_lsp = config.get("lsp")
            if isinstance(expected_lsp, dict):
                for key, value in expected_lsp.items():
                    if not isinstance(actual_lsp, dict) or actual_lsp.get(key) != value:
                        mismatches.append(
                            _mismatch(
                                "CONFIG_PROJECTED_VALUE_MISMATCH",
                                f"{package_contract.WORKSPACE_CONFIG}.lsp.{key}",
                                "workspace projected LSP value does not match the package",
                            )
                        )
            elif actual_lsp != expected_lsp:
                mismatches.append(
                    _mismatch(
                        "CONFIG_PROJECTED_VALUE_MISMATCH",
                        f"{package_contract.WORKSPACE_CONFIG}.lsp",
                        "workspace projected LSP value does not match the package",
                    )
                )

    try:
        package_json = _load_json_file(runtime_dir / "package.json")
    except (OSError, UnicodeError, json.JSONDecodeError, package_contract.ContractError):
        package_json = {}
        mismatches.append(
            _mismatch(
                "CONFIG_WORKSPACE_DEPENDENCIES_INVALID",
                "package.json",
                "workspace package.json is unsafe or invalid",
            )
        )
    expected_dependencies = projection.get("dependencies", {})
    if isinstance(expected_dependencies, dict):
        for section in package_contract.PACKAGE_DEPENDENCY_SECTIONS:
            expected_values = expected_dependencies.get(section, {})
            actual_values = package_json.get(section, {})
            if not isinstance(expected_values, dict):
                continue
            for name, version in expected_values.items():
                if not isinstance(actual_values, dict) or actual_values.get(name) != version:
                    mismatches.append(
                        _mismatch(
                            "CONFIG_PROJECTED_DEPENDENCY_MISMATCH",
                            f"package.json.{section}.{name}",
                            "workspace dependency does not match the package projection",
                        )
                    )
    return sorted(mismatches, key=lambda item: (item.path, item.code, item.message))
