#!/usr/bin/env python3
"""Create a manifest-driven MobileWork expert bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import bundle_contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--package-zip", type=Path, action="append", required=True)
    parser.add_argument("--tests-collected", type=int, default=0)
    parser.add_argument("--tests-passed", type=int, default=0)
    parser.add_argument("--tests-failed", type=int, default=0)
    parser.add_argument("--tests-skipped", type=int, default=0)
    parser.add_argument("--source-repository")
    parser.add_argument("--source-commit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = bundle_contract.create_manifest(
            args.bundle_dir,
            args.package_zip,
            tests={
                "collected": args.tests_collected,
                "passed": args.tests_passed,
                "failed": args.tests_failed,
                "skipped": args.tests_skipped,
            },
            source_repository=args.source_repository,
            source_commit=args.source_commit,
        )
    except bundle_contract.BundleContractError as exc:
        print(json.dumps({"ok": False, "code": "BUNDLE_CREATE_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
