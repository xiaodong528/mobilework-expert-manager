#!/usr/bin/env python3
"""Propose or explicitly confirm a local SemVer release for a trusted expert source."""

from __future__ import annotations

import argparse
from pathlib import Path

import cli_contract
import expert_vcs
import output_sanitizer


def _legacy_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--version", help="User-confirmed SemVer; defaults to the current proposal")
    parser.add_argument("--confirm", action="store_true", help="Create the local release commit and annotated tag")
    args = parser.parse_args()
    try:
        proposal = expert_vcs.propose_version(args.package_dir)
        if not args.confirm:
            print(
                output_sanitizer.json_dumps(
                    {"ok": True, "proposal": proposal.as_dict()}, indent=2
                )
            )
            return 0
        result = expert_vcs.release(args.package_dir, args.version or proposal.version)
    except expert_vcs.ExpertVcsError as exc:
        print(
            output_sanitizer.json_dumps({
                "ok": False,
                "code": "EXPERT_VCS_ERROR",
                "message": output_sanitizer.sanitize_exception(exc),
            })
        )
        return 2
    print(output_sanitizer.json_dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "version-expert", _legacy_main, argv=argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
