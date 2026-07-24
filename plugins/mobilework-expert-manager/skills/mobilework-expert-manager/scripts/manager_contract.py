#!/usr/bin/env python3
"""Version-independent contract and explicit host capability resolution."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


POLICY_PATH = Path(__file__).with_name("manager-contract.json")
TARGET_VERSION_ENV = "MOBILEWORK_TARGET_OPENCODE_VERSION"
HOST_CONTRACT_KEYS = frozenset({"schemaVersion", "opencodeVersion", "capabilities"})


class ManagerContractError(ValueError):
    """Raised when manager or host contract data is invalid."""


@dataclass(frozen=True)
class TargetContract:
    version: str
    source: str
    capabilities: dict[str, Any]
    capability_verified: bool
    host_contract_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManagerContractError(f"{field} must be a JSON object")
    return dict(value)


def _version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManagerContractError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if normalized.startswith("v") and len(normalized) > 1:
        normalized = normalized[1:]
    return normalized


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerContractError(f"cannot read manager contract {path}: {exc}") from exc
    policy = _object(raw, "manager contract")
    if policy.get("schemaVersion") != 1:
        raise ManagerContractError("manager contract schemaVersion must be 1")
    if not isinstance(policy.get("contractVersion"), str) or not policy["contractVersion"]:
        raise ManagerContractError("manager contract contractVersion must be non-empty")
    if "targetOpenCodeVersion" in policy:
        raise ManagerContractError("manager contract must not hardcode targetOpenCodeVersion")
    return policy


def load_host_contract(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerContractError(f"cannot read host contract {path}: {exc}") from exc
    contract = _object(raw, "host contract")
    unknown = sorted(set(contract) - HOST_CONTRACT_KEYS)
    if unknown:
        raise ManagerContractError(f"host contract contains unknown fields: {', '.join(unknown)}")
    if contract.get("schemaVersion") != 1:
        raise ManagerContractError("host contract schemaVersion must be 1")
    contract["opencodeVersion"] = _version(
        contract.get("opencodeVersion"), "host contract opencodeVersion"
    )
    capabilities = contract.get("capabilities", {})
    if not isinstance(capabilities, dict) or any(
        not isinstance(key, str) or not key for key in capabilities
    ):
        raise ManagerContractError("host contract capabilities must be a string-keyed object")
    contract["capabilities"] = dict(capabilities)
    contract["path"] = str(path)
    return contract


def resolve_target(
    *,
    cli_version: str | None = None,
    env: Mapping[str, str] | None = None,
    host_contract: Path | None = None,
) -> TargetContract:
    environment = os.environ if env is None else env
    host = load_host_contract(host_contract) if host_contract is not None else None
    environment_version = environment.get(TARGET_VERSION_ENV)

    if cli_version is not None:
        version = _version(cli_version, "target OpenCode version")
        source = "cli"
    elif environment_version:
        version = _version(environment_version, TARGET_VERSION_ENV)
        source = "environment"
    elif host is not None:
        version = host["opencodeVersion"]
        source = "host-contract"
    else:
        version = "unknown"
        source = "unknown"

    capabilities = dict(host["capabilities"]) if host is not None else {}
    capability_verified = bool(capabilities) and host is not None and (
        host["opencodeVersion"] == version
    )
    return TargetContract(
        version=version,
        source=source,
        capabilities=capabilities,
        capability_verified=capability_verified,
        host_contract_path=host["path"] if host is not None else "",
    )
