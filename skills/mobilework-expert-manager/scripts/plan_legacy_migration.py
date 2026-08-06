#!/usr/bin/env python3
"""Produce a read-only migration plan for a legacy expert directory or ZIP."""

from __future__ import annotations

import argparse
from pathlib import Path

import cli_contract
import migration_planner
import output_sanitizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--format", choices=("human", "json", "markdown"), default="human")
    return parser.parse_args()


def _legacy_main() -> int:
    args = parse_args()
    try:
        result = migration_planner.plan(args.source)
    except migration_planner.MigrationPlanError as exc:
        print(
            output_sanitizer.json_dumps({
                "ok": False,
                "code": "MIGRATION_PLAN_INPUT_ERROR",
                "message": output_sanitizer.sanitize_exception(exc),
            })
        )
        return 1
    safe_result = output_sanitizer.sanitize_mapping(result)
    if args.format == "json":
        print(output_sanitizer.json_dumps(safe_result, indent=2))
    elif args.format == "markdown":
        print(
            output_sanitizer.sanitize_text(
                migration_planner.render_markdown(safe_result)
            ),
            end="",
        )
    else:
        lines = (
            f"Read-only migration plan for {safe_result.get('slug') or safe_result['source']}",
            f"Automatic actions: {len(safe_result['automaticActions'])}",
            f"User decisions: {safe_result['unconfirmedCount']}",
            "No source files were changed and no package code was executed.",
        )
        print(output_sanitizer.sanitize_text("\n".join(lines)))
    return 0


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "plan-legacy-migration",
        _legacy_main,
        argv=argv,
        default_format="human",
        delegated_output_flags=("format",),
    )


if __name__ == "__main__":
    raise SystemExit(main())
