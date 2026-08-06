#!/usr/bin/env python3
"""Reproducible, secret-safe provenance for manager evidence."""

from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import manager_contract
import output_sanitizer
import safe_input


SKIP_PARTS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store"}


def file_sha256(
    path: Path,
    *,
    limits: safe_input.InputLimits | None = None,
) -> str:
    snapshot = safe_input.inspect(path, limits)
    if snapshot.kind != "file":
        raise safe_input.InputInspectionError(
            "INPUT_KIND_MISMATCH", "expected a regular file", path.name
        )
    return snapshot.sha256


def tree_sha256(
    root: Path,
    *,
    limits: safe_input.InputLimits | None = None,
) -> str:
    snapshot = safe_input.inspect(
        root,
        limits,
        exclusions=safe_input.InputExclusions(
            directory_names=frozenset(SKIP_PARTS),
            file_names=frozenset(SKIP_NAMES),
            file_suffixes=frozenset(SKIP_SUFFIXES),
        ),
    )
    if snapshot.kind != "directory":
        raise safe_input.InputInspectionError(
            "INPUT_KIND_MISMATCH", "expected a directory", root.name
        )
    included = [
        item
        for item in snapshot.files
        if not any(part in SKIP_PARTS for part in Path(item.relative_path).parts)
        and Path(item.relative_path).suffix not in SKIP_SUFFIXES
        and Path(item.relative_path).name not in SKIP_NAMES
    ]
    return safe_input.digest_tree(included)


def collect(
    *,
    input_path: Path | None = None,
    input_snapshot: safe_input.InputSnapshot | None = None,
    input_error: safe_input.InputInspectionError | None = None,
    target: manager_contract.TargetContract | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if sum(value is not None for value in (input_path, input_snapshot, input_error)) > 1:
        raise ValueError(
            "input_path, input_snapshot, and input_error are mutually exclusive"
        )
    skill_root = Path(__file__).resolve().parents[1]
    resolved = target or manager_contract.resolve_target()
    try:
        policy = manager_contract.load_policy()
    except manager_contract.ManagerContractError as exc:
        return {
            "skillPath": str(skill_root),
            "skillTreeSha256": "",
            "contractVersion": "invalid",
            "findingCatalogVersion": 0,
            "pythonVersion": platform.python_version(),
            "targetOpenCode": resolved.as_dict(),
            "inputSha256": input_snapshot.sha256 if input_snapshot is not None else "",
            "inputInspection": {"status": "blocked"},
            "limits": dict(limits or {}),
            "managerContract": {
                "status": "invalid",
                "error": output_sanitizer.sanitize_exception(exc),
            },
            "invocation": {
                "inputKind": "blocked",
                "arguments": [],
                "redacted": True,
            },
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }
    input_hash = ""
    input_kind = "none"
    input_inspection: dict[str, Any] = {"status": "not-run"}
    active_limits = dict(limits or {})
    if input_snapshot is not None:
        input_hash = input_snapshot.sha256
        input_kind = input_snapshot.kind
        if not active_limits:
            active_limits = input_snapshot.limits.as_dict()
        input_inspection = {
            "status": "passed",
            "entryCount": input_snapshot.entry_count,
            "fileCount": input_snapshot.file_count,
            "totalBytes": input_snapshot.total_bytes,
            "excludedEntryCount": len(input_snapshot.excluded_entries),
            "excludedPaths": [
                item.relative_path for item in input_snapshot.excluded_entries
            ],
        }
    elif input_error is not None:
        input_kind = "none" if input_error.code == "INPUT_NOT_FOUND" else "rejected"
        input_inspection = {
            "status": "rejected",
            "code": input_error.code,
            "path": input_error.path,
        }
    elif input_path is not None:
        try:
            inspected = safe_input.inspect(input_path, limits or None)
        except safe_input.InputInspectionError as exc:
            input_kind = "none" if exc.code == "INPUT_NOT_FOUND" else "rejected"
            input_inspection = {
                "status": "rejected",
                "code": exc.code,
                "path": exc.path,
            }
        else:
            input_hash = inspected.sha256
            input_kind = inspected.kind
            if not active_limits:
                active_limits = inspected.limits.as_dict()
            input_inspection = {
                "status": "passed",
                "entryCount": inspected.entry_count,
                "fileCount": inspected.file_count,
                "totalBytes": inspected.total_bytes,
                "excludedEntryCount": len(inspected.excluded_entries),
                "excludedPaths": [
                    item.relative_path for item in inspected.excluded_entries
                ],
            }
    return {
        "skillPath": str(skill_root),
        "skillTreeSha256": tree_sha256(skill_root),
        "contractVersion": policy["contractVersion"],
        "findingCatalogVersion": policy["findingCatalogVersion"],
        "pythonVersion": platform.python_version(),
        "targetOpenCode": resolved.as_dict(),
        "inputSha256": input_hash,
        "inputInspection": input_inspection,
        "limits": active_limits,
        "invocation": {
            "inputKind": input_kind,
            "arguments": [],
            "redacted": True,
        },
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
