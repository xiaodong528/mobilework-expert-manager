#!/usr/bin/env python3
"""Verify trusted installed config using an explicitly supplied OpenCode sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config_loader
import manager_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
    args = parser.parse_args()
    try:
        target = manager_contract.resolve_target(
            cli_version=args.target_opencode_version,
            host_contract=args.host_contract,
        )
        result = config_loader.verify(args.workspace, args.sidecar, target=target)
    except (manager_contract.ManagerContractError, config_loader.ConfigLoadError) as exc:
        print(json.dumps({"ok": False, "code": "CONFIG_LOAD_CONTRACT_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
