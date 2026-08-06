#!/usr/bin/env python3
"""Immutable package snapshots shared by packaging and installation."""

from __future__ import annotations

import contextlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import manager_contract
import safe_input
import validate_expert
from validation_result import ValidationResult


def inspect_directory(package_dir: Path) -> safe_input.InputSnapshot:
    """Snapshot one package directory without following or reopening its files."""

    snapshot = safe_input.inspect_package(package_dir)
    if snapshot.kind != "directory":
        raise safe_input.InputInspectionError(
            "INPUT_KIND_MISMATCH",
            "package input must be a directory",
            package_dir.name,
        )
    return snapshot


@contextlib.contextmanager
def materialized(snapshot: safe_input.InputSnapshot) -> Iterator[Path]:
    """Expose snapshot bytes at a temporary trusted path for existing validators."""

    with tempfile.TemporaryDirectory(prefix="mobilework-package-snapshot-") as temp:
        root = Path(temp) / (snapshot.source.name or "package")
        snapshot.materialize(root)
        yield root


def validate_snapshot(
    snapshot: safe_input.InputSnapshot,
    *,
    target: manager_contract.TargetContract | None = None,
) -> ValidationResult:
    """Validate only the immutable bytes captured in ``snapshot``."""

    with materialized(snapshot) as package_dir:
        return validate_expert.validate_package(
            package_dir,
            target=target,
            input_snapshot=snapshot,
        )


def _inspection_failure_result(
    error: safe_input.InputInspectionError,
    *,
    target: manager_contract.TargetContract | None = None,
) -> ValidationResult:
    result = ValidationResult(
        execution_reason="package-input-rejected",
        input_error=error,
        target=target,
    )
    return result.block_input_preflight()


def inspect_and_validate(
    package_dir: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> tuple[safe_input.InputSnapshot | None, ValidationResult]:
    """Return one snapshot and validation result, or a stable preflight failure."""

    try:
        snapshot = inspect_directory(package_dir)
    except safe_input.InputInspectionError as error:
        return None, _inspection_failure_result(error, target=target)
    return snapshot, validate_snapshot(snapshot, target=target)


def load_json(snapshot: safe_input.InputSnapshot, relative_path: str) -> dict[str, Any]:
    """Decode an object from captured package bytes."""

    try:
        value = json.loads(snapshot.read_text(relative_path))
    except KeyError as error:
        raise ValueError(f"snapshot is missing {relative_path}") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot parse snapshot {relative_path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"snapshot {relative_path} root must be an object")
    return value
