#!/usr/bin/env python3
"""Reproducible, secret-safe provenance for manager evidence."""

from __future__ import annotations

import hashlib
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import manager_contract


SKIP_PARTS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(name for name in directory_names if name not in SKIP_PARTS)
        current = Path(current_root)
        for name in sorted(file_names):
            path = current / name
            if path.suffix not in SKIP_SUFFIXES and path.name not in SKIP_NAMES:
                files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        encoded = relative.as_posix().encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def collect(
    *,
    input_path: Path | None = None,
    target: manager_contract.TargetContract | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill_root = Path(__file__).resolve().parents[1]
    policy = manager_contract.load_policy()
    resolved = target or manager_contract.resolve_target()
    input_hash = ""
    if input_path is not None:
        if input_path.is_file():
            input_hash = file_sha256(input_path)
        elif input_path.is_dir():
            input_hash = tree_sha256(input_path)
    return {
        "skillPath": str(skill_root),
        "skillTreeSha256": tree_sha256(skill_root),
        "contractVersion": policy["contractVersion"],
        "findingCatalogVersion": policy["findingCatalogVersion"],
        "pythonVersion": platform.python_version(),
        "targetOpenCode": resolved.as_dict(),
        "inputSha256": input_hash,
        "limits": dict(limits or {}),
        "invocation": {
            "inputKind": (
                "file" if input_path is not None and input_path.is_file()
                else "directory" if input_path is not None and input_path.is_dir()
                else "none"
            ),
            "arguments": [],
            "redacted": True,
        },
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
