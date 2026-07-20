#!/usr/bin/env python3
"""Deterministic host and output routing for MobileWork expert packages."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


HOST_ENV = "MOBILEWORK_EXPERT_MANAGER_HOST"
MY_EXPERTS_ENV = "MOBILEWORK_MY_EXPERTS_DIR"
MOBILEWORK_HOST = "mobilework"


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

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "ok": True,
            "hostMode": self.host_mode,
            "workspaceRoot": str(self.workspace_root),
            "outputRoot": str(self.output_root),
            "pathSource": self.path_source,
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


def resolve_execution_context(
    *,
    env: Mapping[str, str] | None = None,
    workspace_root: Path | None = None,
    requested_output_dir: Path | None = None,
) -> ExecutionContext:
    source = os.environ if env is None else env
    workspace = canonical_path(workspace_root or Path.cwd())
    host = source.get(HOST_ENV, "").strip()
    managed_root = source.get(MY_EXPERTS_ENV, "").strip()

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
        output = _absolute_path(
            managed_root,
            code="HOST_CONTRACT_INCOMPLETE",
            field=MY_EXPERTS_ENV,
        )
        if is_within(output, workspace):
            raise ExecutionContextError(
                "TARGET_OUTSIDE_ROOT",
                "MobileWork my-experts root must remain outside the current workspace",
            )
        context = ExecutionContext(
            host_mode=MOBILEWORK_HOST,
            workspace_root=workspace,
            output_root=output,
            path_source="mobilework-main-process",
        )
    else:
        context = ExecutionContext(
            host_mode="workspace",
            workspace_root=workspace,
            output_root=workspace,
            path_source="current-workspace",
        )

    if requested_output_dir is not None:
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
