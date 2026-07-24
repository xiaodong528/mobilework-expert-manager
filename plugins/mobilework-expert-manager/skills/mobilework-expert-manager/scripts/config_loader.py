#!/usr/bin/env python3
"""Pure-config verification using an explicitly trusted OpenCode sidecar."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import manager_contract
import package_contract


VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")


class ConfigLoadError(ValueError):
    pass


def _sidecar(path: Path) -> Path:
    lexical = path.expanduser().absolute()
    if lexical.is_symlink():
        raise ConfigLoadError("trusted sidecar path must not be a symlink")
    resolved = lexical.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ConfigLoadError("trusted sidecar must be an executable regular file")
    return resolved


def _run(path: Path, args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(path), *args], cwd=cwd, env=env, text=True,
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigLoadError(f"trusted sidecar invocation failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ConfigLoadError(f"trusted sidecar {' '.join(args)} failed: {detail}")
    return result


def verify(
    workspace: Path,
    sidecar: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    binary = _sidecar(sidecar)
    config = workspace / package_contract.WORKSPACE_RUNTIME_DIR / package_contract.WORKSPACE_CONFIG
    if not config.is_file() or config.is_symlink():
        raise ConfigLoadError(f"workspace config is missing or unsafe: {config}")
    target = target or manager_contract.resolve_target()
    with tempfile.TemporaryDirectory(prefix="mobilework-pure-config-") as temp:
        temp_root = Path(temp)
        environment = dict(os.environ)
        environment.update({
            "OPENCODE_CONFIG": str(config),
            "OPENCODE_CONFIG_DIR": str(workspace / package_contract.WORKSPACE_RUNTIME_DIR),
            "XDG_CONFIG_HOME": str(temp_root / "config"),
            "XDG_CACHE_HOME": str(temp_root / "cache"),
            "XDG_DATA_HOME": str(temp_root / "data"),
            "XDG_STATE_HOME": str(temp_root / "state"),
            "NO_COLOR": "1",
        })
        version_result = _run(binary, ["--version"], cwd=workspace, env=environment)
        match = VERSION_RE.search(version_result.stdout + "\n" + version_result.stderr)
        if match is None:
            raise ConfigLoadError("trusted sidecar did not report a parseable version")
        actual_version = match.group(1)
        if target.version != "unknown" and target.version != actual_version:
            raise ConfigLoadError(
                f"target OpenCode version {target.version} conflicts with sidecar {actual_version}"
            )
        loaded = _run(binary, ["debug", "config", "--pure"], cwd=workspace, env=environment)
    try:
        resolved = json.loads(loaded.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"trusted sidecar returned non-JSON config: {exc}") from exc
    if not isinstance(resolved, dict):
        raise ConfigLoadError("trusted sidecar resolved config must be a JSON object")
    return {
        "ok": True,
        "schemaVersion": 2,
        "evidenceLevel": "config-loadable",
        "gates": {
            "archive": "not-run", "contract": "passed", "portability": "passed",
            "install": "passed", "configLoad": "passed",
        },
        "runtime": {"status": "not-tested", "reason": "pure-config-only"},
        "provenance": {
            "sidecarPath": str(binary),
            "sidecarActualVersion": actual_version,
            "targetOpenCode": target.as_dict(),
            "workspaceConfig": str(config),
        },
        "resolvedConfigKeys": sorted(resolved),
    }
