#!/usr/bin/env python3
"""Deterministic host and output routing for MobileWork expert packages."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


HOST_ENV = "MOBILEWORK_EXPERT_MANAGER_HOST"
MY_EXPERTS_ENV = "MOBILEWORK_MY_EXPERTS_DIR"
MOBILEWORK_HOST = "mobilework"
CREATION_TARGETS = ("my-experts", "workspace", "custom")
REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ExecutionContextError(ValueError):
    """Raised when host routing is incomplete, ambiguous, or unsafe."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutionContext:
    host_mode: str
    workspace_root: Path
    output_root: Path
    path_source: str
    target_mode: str

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 2,
            "ok": True,
            "hostMode": self.host_mode,
            "workspaceRoot": str(self.workspace_root),
            "outputRoot": str(self.output_root),
            "pathSource": self.path_source,
            "targetMode": self.target_mode,
            "errors": [],
        }


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _absolute_path(raw: str, *, code: str, field: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ExecutionContextError(code, f"{field} must be an absolute path")
    return canonical_path(candidate)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def validate_custom_output_root(path: Path) -> Path:
    """Validate an existing user-selected parent without following links."""
    if not path.is_absolute():
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            "custom output parent must be an absolute path",
        )
    candidate = _lexical_absolute(path)
    if candidate.parent == candidate:
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            "custom output parent must not be a filesystem root",
        )

    try:
        metadata = os.lstat(candidate)
    except OSError as error:
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            f"custom output parent must be an existing directory: {error}",
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            "custom output parent must not be a symlink or reparse point",
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            "custom output parent must be a directory",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            f"custom output parent cannot be resolved safely: {error}",
        ) from error
    if resolved.parent == resolved:
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            "custom output parent must not resolve to a filesystem root",
        )
    return resolved


def _user_home(source: Mapping[str, str]) -> Path:
    raw = source.get("HOME", "").strip() or source.get("USERPROFILE", "").strip()
    if raw:
        return _absolute_path(
            raw,
            code="CREATION_TARGET_PATH_INVALID",
            field="user home",
        )
    return canonical_path(Path.home())


def resolve_execution_context(
    *,
    env: Mapping[str, str] | None = None,
    workspace_root: Path | None = None,
    requested_output_dir: Path | None = None,
    creation_target: str | None = None,
) -> ExecutionContext:
    source = os.environ if env is None else env
    workspace = canonical_path(workspace_root or Path.cwd())
    host = source.get(HOST_ENV, "").strip()
    managed_root = source.get(MY_EXPERTS_ENV, "").strip()

    if creation_target is not None and creation_target not in CREATION_TARGETS:
        raise ExecutionContextError(
            "CREATION_TARGET_PATH_INVALID",
            f"creation target must be one of: {', '.join(CREATION_TARGETS)}",
        )

    if bool(host) != bool(managed_root):
        raise ExecutionContextError(
            "HOST_CONTRACT_INCOMPLETE",
            f"{HOST_ENV} and {MY_EXPERTS_ENV} must be provided together",
        )

    if host:
        if host != MOBILEWORK_HOST:
            raise ExecutionContextError(
                "HOST_CONTRACT_INCOMPLETE",
                f"{HOST_ENV} must be exactly {MOBILEWORK_HOST}",
            )
        managed_output = _absolute_path(
            managed_root,
            code="HOST_CONTRACT_INCOMPLETE",
            field=MY_EXPERTS_ENV,
        )
        if is_within(managed_output, workspace):
            raise ExecutionContextError(
                "TARGET_OUTSIDE_ROOT",
                "MobileWork personal experts root must remain outside the current workspace",
            )
        host_mode = MOBILEWORK_HOST
    else:
        managed_output = None
        host_mode = "workspace"

    if creation_target == "custom":
        if requested_output_dir is None:
            raise ExecutionContextError(
                "CREATION_TARGET_PATH_INVALID",
                "--creation-target custom requires --output-dir",
            )
        output = validate_custom_output_root(requested_output_dir)
        path_source = "user-selected-custom"
    elif creation_target == "workspace":
        output = workspace
        path_source = "user-selected-workspace"
    elif creation_target == "my-experts":
        output = managed_output or (
            _user_home(source) / ".mobilework" / "experts" / "personal"
        )
        output = canonical_path(output)
        path_source = (
            "mobilework-main-process" if managed_output is not None else "user-home"
        )
    elif managed_output is not None:
        output = managed_output
        path_source = "mobilework-main-process"
    else:
        output = workspace
        path_source = "current-workspace"

    context = ExecutionContext(
        host_mode=host_mode,
        workspace_root=workspace,
        output_root=output,
        path_source=path_source,
        target_mode=creation_target or "host-resolved",
    )

    if requested_output_dir is not None and creation_target != "custom":
        requested = requested_output_dir.expanduser()
        if not requested.is_absolute():
            raise ExecutionContextError(
                "OUTPUT_ROOT_MISMATCH",
                "--output-dir must be an absolute path matching the resolved output root",
            )
        requested = canonical_path(requested)
        if requested != context.output_root:
            raise ExecutionContextError(
                "OUTPUT_ROOT_MISMATCH",
                f"requested output root {requested} does not match resolved root {context.output_root}",
            )
    return context


def validate_package_target(context: ExecutionContext, slug: str) -> Path:
    target = context.output_root / slug
    if target.is_symlink():
        raise ExecutionContextError(
            "TARGET_OUTSIDE_ROOT",
            f"package target must not be a symlink: {target}",
        )
    resolved = canonical_path(target)
    if resolved.parent != context.output_root:
        raise ExecutionContextError(
            "TARGET_OUTSIDE_ROOT",
            f"package target escapes resolved output root: {target}",
        )
    return target
