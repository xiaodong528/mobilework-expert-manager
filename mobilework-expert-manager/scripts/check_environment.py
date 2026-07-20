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


MINIMUM_PYTHON = (3, 10)
FEATURES = ("core", "excel", "package")


def module_status(name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(name) is not None
    return {"kind": "python-module", "name": name, "available": available}


def command_status(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"kind": "command", "name": name, "available": path is not None, "path": path}


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
    if "excel" in features:
        checks.append(module_status("openpyxl"))
    if "package" in features:
        checks.append(command_status("unzip"))
    missing = [check["name"] for check in checks if not check["available"]]
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = check_environment(selected_features(args.feature))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
