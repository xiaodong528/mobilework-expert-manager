#!/usr/bin/env python3
"""Validate a MobileWork expert bundle without executing package code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import bundle_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args()
    try:
        result = bundle_contract.validate_bundle(args.bundle_dir)
    except bundle_contract.BundleContractError as exc:
        print(json.dumps({"ok": False, "code": "BUNDLE_INPUT_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
