#!/usr/bin/env python3
"""Validate a MobileWork expert bundle without executing package code."""

from __future__ import annotations

import argparse
from pathlib import Path

import bundle_contract
import cli_contract
import output_sanitizer


def _legacy_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args()
    try:
        result = bundle_contract.validate_bundle(args.bundle_dir)
    except bundle_contract.BundleContractError as exc:
        print(
            output_sanitizer.json_dumps({
                "ok": False,
                "code": "BUNDLE_INPUT_ERROR",
                "message": output_sanitizer.sanitize_exception(exc),
            })
        )
        return 2
    print(output_sanitizer.json_dumps(result, indent=2))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "validate-expert-bundle", _legacy_main, argv=argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
