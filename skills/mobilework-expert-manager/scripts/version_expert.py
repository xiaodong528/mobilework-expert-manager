#!/usr/bin/env python3
"""Propose or explicitly confirm a local SemVer release for a trusted expert source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import expert_vcs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--version", help="User-confirmed SemVer; defaults to the current proposal")
    parser.add_argument("--confirm", action="store_true", help="Create the local release commit and annotated tag")
    args = parser.parse_args()
    try:
        proposal = expert_vcs.propose_version(args.package_dir)
        if not args.confirm:
            print(json.dumps({"ok": True, "proposal": proposal.as_dict()}, ensure_ascii=False, indent=2))
            return 0
        result = expert_vcs.release(args.package_dir, args.version or proposal.version)
    except expert_vcs.ExpertVcsError as exc:
        print(json.dumps({"ok": False, "code": "EXPERT_VCS_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
