#!/usr/bin/env python3
"""Produce a read-only migration plan for a legacy expert directory or ZIP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import migration_planner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--format", choices=("human", "json", "markdown"), default="human")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = migration_planner.plan(args.source)
    except migration_planner.MigrationPlanError as exc:
        print(json.dumps({"ok": False, "code": "MIGRATION_PLAN_INPUT_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 1
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(migration_planner.render_markdown(result), end="")
    else:
        print(f"Read-only migration plan for {result.get('slug') or result['source']}")
        print(f"Automatic actions: {len(result['automaticActions'])}")
        print(f"User decisions: {result['unconfirmedCount']}")
        print("No source files were changed and no package code was executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
