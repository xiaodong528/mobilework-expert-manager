#!/usr/bin/env python3
"""Check host dependencies required by MobileWork expert-manager features."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import execution_context
import manager_contract


MINIMUM_PYTHON = (3, 10)
FEATURES = (
    "core",
    "excel",
    "package",
    "bundle-docx",
    "git",
    "config-load",
    "coverage",
)


def module_status(name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(name) is not None
    return {"kind": "python-module", "name": name, "available": available}


def command_status(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"kind": "command", "name": name, "available": path is not None, "path": path}


def explicit_sidecar_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "kind": "explicit-path",
            "name": "trusted-opencode-sidecar",
            "available": False,
            "required": True,
            "path": None,
            "reason": "config-load requires an explicit --sidecar path",
        }
    lexical = path.expanduser().absolute()
    resolved = lexical.resolve()
    available = (
        not lexical.is_symlink()
        and resolved.is_file()
        and os.access(resolved, os.X_OK)
    )
    return {
        "kind": "explicit-path",
        "name": "trusted-opencode-sidecar",
        "available": available,
        "required": True,
        "path": str(resolved),
        "reason": None if available else "sidecar must be an executable regular file and not a symlink",
    }


def selected_features(values: list[str]) -> list[str]:
    requested = values or ["core"]
    if "all" in requested:
        return list(FEATURES)
    return [feature for feature in FEATURES if feature in requested]


def check_environment(
    features: list[str],
    *,
    env: dict[str, str] | None = None,
    workspace_root: Path | None = None,
    sidecar: Path | None = None,
    host_contract: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    python_ok = sys.version_info >= MINIMUM_PYTHON
    checks.append(
        {
            "kind": "python-version",
            "name": "python",
            "available": python_ok,
            "required": ">=3.10",
            "actual": ".".join(str(part) for part in sys.version_info[:3]),
        }
    )
    if "core" in features:
        checks.append(module_status("yaml"))
    if "excel" in features:
        checks.append(module_status("openpyxl"))
    if "package" in features:
        checks.append(command_status("unzip"))
    if "bundle-docx" in features:
        checks.append(module_status("zipfile"))
        checks.append(module_status("xml.etree.ElementTree"))
    if "git" in features:
        checks.append(command_status("git"))
    if "coverage" in features:
        checks.append(module_status("coverage"))
    if "config-load" in features:
        checks.append(explicit_sidecar_status(sidecar))
        try:
            target = manager_contract.resolve_target(
                env=os.environ if env is None else env,
                host_contract=host_contract,
            )
            checks.append(
                {
                    "kind": "target-contract",
                    "name": "target-opencode-contract",
                    "available": True,
                    "required": False,
                    "version": target.version,
                    "source": target.source,
                    "capabilityVerified": target.capability_verified,
                    "hostContractPath": target.host_contract_path or None,
                }
            )
        except manager_contract.ManagerContractError as error:
            checks.append(
                {
                    "kind": "target-contract",
                    "name": "target-opencode-contract",
                    "available": False,
                    "required": True,
                    "reason": str(error),
                }
            )
    missing = [
        check["name"]
        for check in checks
        if check.get("required", True) and not check["available"]
    ]
    routing: dict[str, Any]
    routing_error: dict[str, str] | None = None
    try:
        routing = execution_context.resolve_execution_context(
            env=os.environ if env is None else env,
            workspace_root=workspace_root,
        ).as_dict()
    except execution_context.ExecutionContextError as error:
        routing_error = {"code": error.code, "message": str(error)}
        routing = {
            "version": 1,
            "ok": False,
            "hostMode": None,
            "workspaceRoot": str(
                execution_context.canonical_path(workspace_root or Path.cwd())
            ),
            "outputRoot": None,
            "pathSource": None,
            "errors": [routing_error],
        }
    return {
        "ok": not missing and routing_error is None,
        "features": features,
        "checks": checks,
        "missing": missing,
        "executionContext": routing,
        "hostMode": routing["hostMode"],
        "workspaceRoot": routing["workspaceRoot"],
        "outputRoot": routing["outputRoot"],
        "pathSource": routing["pathSource"],
        "errors": routing["errors"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--feature",
        action="append",
        choices=(*FEATURES, "all"),
        default=[],
        help="Feature dependencies to check; repeat as needed (default: core)",
    )
    parser.add_argument(
        "--sidecar",
        type=Path,
        help="Explicit trusted OpenCode sidecar path for config-load preflight; never executed",
    )
    parser.add_argument(
        "--host-contract",
        type=Path,
        help="Optional explicit read-only host contract for config-load preflight",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = check_environment(
        selected_features(args.feature),
        sidecar=args.sidecar,
        host_contract=args.host_contract,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
