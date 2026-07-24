#!/usr/bin/env python3
"""Static, warning-first supply-chain audit for MobileWork expert packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RANGE_RE = re.compile(r"^(?:latest|next|\*|[~^<>]=?|\d+\.x(?:\.x)?|\d+\.\d+\.x)", re.I)
GIT_RE = re.compile(r"^(?:git(?:\+https?|\+ssh)?://|git@|https?://[^/]+/.+\.git(?:#|$))", re.I)
DOWNLOAD_TOKENS = ("curl ", "wget ", "npx ", "bunx ", "pip install", "npm install", "pnpm add")
LIFECYCLE_KEYS = {
    "preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishOnly",
    "publish", "postpublish",
}


@dataclass(frozen=True)
class SupplyFinding:
    code: str
    severity: str
    message: str
    path: str
    location: str
    root_cause: str
    remediation: str
    evidence: str


def _finding(
    code: str,
    severity: str,
    message: str,
    *,
    path: str,
    location: str,
    root_cause: str,
    remediation: str,
    evidence: str = "",
) -> SupplyFinding:
    return SupplyFinding(
        code, severity, message, path, location, root_cause, remediation, evidence[:240]
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _dependency_findings(package_json: Path, payload: dict[str, Any]) -> list[SupplyFinding]:
    findings: list[SupplyFinding] = []
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        for name, command in scripts.items():
            if name in LIFECYCLE_KEYS:
                findings.append(_finding(
                    "SUPPLY_PACKAGE_LIFECYCLE_SCRIPT", "error",
                    f"package lifecycle script is forbidden: {name}",
                    path=package_json.as_posix(), location=f"/scripts/{name}",
                    root_cause="package-lifecycle-script",
                    remediation="Remove package-manager lifecycle scripts.", evidence=str(command),
                ))
            if isinstance(command, str) and any(token in command.lower() for token in DOWNLOAD_TOKENS):
                findings.append(_finding(
                    "SUPPLY_RUNTIME_DOWNLOAD", "warning",
                    f"package script may download or install content at runtime: {name}",
                    path=package_json.as_posix(), location=f"/scripts/{name}",
                    root_cause="runtime-download",
                    remediation="Resolve and verify dependencies before distribution.", evidence=command,
                ))
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        dependencies = payload.get(section)
        if not isinstance(dependencies, dict):
            continue
        for name, specifier in dependencies.items():
            if not isinstance(specifier, str):
                continue
            location = f"/{section}/{name}"
            if GIT_RE.match(specifier):
                findings.append(_finding(
                    "SUPPLY_GIT_DEPENDENCY", "warning",
                    f"Git URL dependency is not registry-reproducible: {name}",
                    path=package_json.as_posix(), location=location,
                    root_cause="git-dependency",
                    remediation="Prefer an exact registry version and record integrity.", evidence=specifier,
                ))
            elif RANGE_RE.match(specifier):
                findings.append(_finding(
                    "SUPPLY_UNPINNED_DEPENDENCY", "warning",
                    f"dependency is not pinned to an exact version: {name}",
                    path=package_json.as_posix(), location=location,
                    root_cause="unlocked-dependency",
                    remediation="Use an exact version when reproducibility is required.", evidence=specifier,
                ))
    return findings


def audit_package(
    package_dir: Path,
    *,
    manifest: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> list[SupplyFinding]:
    """Audit package declarations without importing or executing package content."""

    package_dir = package_dir.expanduser().resolve()
    manifest = manifest or _read_json(package_dir / "expert.json") or {}
    config = config or _read_json(package_dir / "opencode.json") or {}
    findings: list[SupplyFinding] = []

    package_json = package_dir / ".opencode" / "package.json"
    payload = _read_json(package_json)
    if payload is not None:
        relative = Path(".opencode/package.json")
        findings.extend(_dependency_findings(relative, payload))

    plugins = config.get("plugin", [])
    if isinstance(plugins, list):
        for index, plugin in enumerate(plugins):
            if isinstance(plugin, str) and not plugin.startswith(("file:", "./", "/")) and "@" not in plugin.lstrip("@"):
                findings.append(_finding(
                    "SUPPLY_UNPINNED_PLUGIN", "warning",
                    f"npm Plugin is not pinned: {plugin}",
                    path="opencode.json", location=f"/plugin/{index}",
                    root_cause="unlocked-plugin",
                    remediation="Pin the Plugin to an exact version.", evidence=plugin,
                ))

    servers = manifest.get("mcp_servers", [])
    if isinstance(servers, list):
        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            name = str(server.get("name", index))
            command = server.get("command")
            enabled = server.get("enabled") is True
            if enabled:
                findings.append(_finding(
                    "SUPPLY_ENABLED_MCP", "warning",
                    f"enabled MCP expands runtime trust: {name}",
                    path="expert.json", location=f"/mcp_servers/{index}/enabled",
                    root_cause="enabled-external-runtime",
                    remediation="Keep MCP disabled until its endpoint and package are reviewed.", evidence=name,
                ))
            command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command or "")
            if "npx -y" in command_text.lower():
                findings.append(_finding(
                    "SUPPLY_NPX_AUTO_INSTALL", "warning",
                    f"MCP uses npx -y runtime installation: {name}",
                    path="expert.json", location=f"/mcp_servers/{index}/command",
                    root_cause="runtime-download",
                    remediation="Pre-resolve the MCP package or document the runtime download risk.", evidence=command_text,
                ))
    return findings


def add_to_result(result: Any, package_dir: Path, manifest: dict[str, Any], config: dict[str, Any]) -> None:
    for finding in audit_package(package_dir, manifest=manifest, config=config):
        result.add(
            finding.message,
            severity=finding.severity,
            code=finding.code,
            phase="supply-chain",
            path=finding.path,
            location=finding.location,
            root_cause=finding.root_cause,
            remediation=finding.remediation,
            evidence=finding.evidence,
        )
