#!/usr/bin/env python3
"""Schema-v2 findings, gates, provenance, grouping, and v1 adaptation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import finding_catalog
import manager_contract
import provenance


EVIDENCE_LEVELS = ("invalid", "valid", "installable", "config-loadable")
GATE_NAMES = ("archive", "contract", "portability", "install", "configLoad")
GATE_VALUES = frozenset({"passed", "failed", "blocked", "not-run"})


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    phase: str
    path: str
    location: str
    message: str
    rootCause: str
    remediation: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _location(message: str, phase: str) -> tuple[str, str]:
    prefix, separator, _rest = message.partition(":")
    prefix = prefix.strip()
    if not separator:
        return "", phase
    if prefix.endswith((".json", ".jsonc", ".md", ".yaml", ".yml")) or "/" in prefix:
        return prefix, phase
    if phase in {"manifest", "permission", "workflow"}:
        return "expert.json", prefix
    return "", prefix or phase


class ValidationResult:
    def __init__(
        self,
        *,
        execution_reason: str = "untrusted-package",
        input_path: Any | None = None,
        target: manager_contract.TargetContract | None = None,
    ) -> None:
        self.findings: list[Finding] = []
        self.execution = {
            "policy": "static-only",
            "attempted": False,
            "reason": execution_reason,
        }
        self.gates = {name: "not-run" for name in GATE_NAMES}
        self.evidence_level = "valid"
        self.runtime = {"status": "not-tested", "reason": execution_reason}
        self.provenance = provenance.collect(input_path=input_path, target=target)

    def set_gate(self, name: str, value: str) -> None:
        if name not in self.gates:
            raise ValueError(f"unknown validation gate {name}")
        if value not in GATE_VALUES:
            raise ValueError(f"invalid gate value {value}")
        self.gates[name] = value

    def add(
        self,
        message: str,
        *,
        severity: str,
        code: str | None = None,
        phase: str | None = None,
        path: str | None = None,
        location: str | None = None,
        root_cause: str | None = None,
        remediation: str | None = None,
        evidence: str | None = None,
    ) -> None:
        inferred_code, inferred_phase, inferred_root, inferred_remediation = (
            finding_catalog.classify(message, severity)
        )
        actual_phase = phase or inferred_phase
        inferred_path, inferred_location = _location(message, actual_phase)
        actual_code = code or inferred_code
        safe_evidence = "" if "SECRET" in actual_code else (evidence or message[:240])
        self.findings.append(
            Finding(
                code=actual_code,
                severity=severity,
                phase=actual_phase,
                path=path if path is not None else inferred_path,
                location=location if location is not None else inferred_location,
                message=message,
                rootCause=root_cause or inferred_root,
                remediation=remediation or inferred_remediation,
                evidence=safe_evidence,
            )
        )
        if severity == "error":
            self.evidence_level = "invalid"
            if actual_phase == "archive":
                self.gates["archive"] = "failed"
            elif actual_phase == "portability":
                self.gates["portability"] = "failed"
            else:
                self.gates["contract"] = "failed"

    def error(self, message: str, **kwargs: Any) -> None:
        self.add(message, severity="error", **kwargs)

    def warn(self, message: str, **kwargs: Any) -> None:
        self.add(message, severity="warning", **kwargs)

    @property
    def errors(self) -> list[str]:
        return [item.message for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[str]:
        return [item.message for item in self.findings if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        return "invalid" if not self.ok else "runtime-not-tested"

    def finalize_contract(self) -> None:
        self.gates["contract"] = "failed" if self.errors else "passed"
        if self.gates["archive"] == "not-run":
            self.gates["archive"] = "not-run"
        self.evidence_level = "invalid" if self.errors else "valid"

    def groups(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.rootCause, []).append(finding)
        return [
            {
                "rootCause": root_cause,
                "severity": "error" if any(item.severity == "error" for item in items) else "warning",
                "count": len(items),
                "codes": sorted({item.code for item in items}),
            }
            for root_cause, items in sorted(grouped.items())
        ]

    def _common(self) -> dict[str, Any]:
        groups = self.groups()
        return {
            "ok": self.ok,
            "rawFindingCount": len(self.findings),
            "rootCauseCount": len(groups),
            "groups": groups,
            "findings": [item.as_dict() for item in self.findings],
            "execution": dict(self.execution),
        }

    def as_dict(self, *, schema_version: int = 2) -> dict[str, Any]:
        common = self._common()
        if schema_version == 1:
            return {"schemaVersion": 1, "status": self.status, **common}
        if schema_version != 2:
            raise ValueError(f"unsupported schema version {schema_version}")
        return {
            "schemaVersion": 2,
            "evidenceLevel": self.evidence_level,
            "gates": dict(self.gates),
            "runtime": dict(self.runtime),
            "provenance": dict(self.provenance),
            **common,
        }

    def print_summary(self, *, output_format: str = "human", schema_version: int = 2) -> None:
        if output_format == "json":
            print(json.dumps(self.as_dict(schema_version=schema_version), ensure_ascii=False, indent=2))
            return
        errors = [item for item in self.findings if item.severity == "error"]
        warnings = [item for item in self.findings if item.severity == "warning"]
        if errors:
            print(f"ERRORS ({len(errors)}):")
            for item in errors:
                print(f"- [{item.code}] {item.message}")
        if warnings:
            print(f"WARNINGS ({len(warnings)}):")
            for item in warnings:
                print(f"- [{item.code}] {item.message}")
        if self.findings:
            print(f"{len(self.groups())} root causes affecting {len(self.findings)} validation points.")
        if self.ok:
            print(f"Expert package is valid at evidence level {self.evidence_level}; runtime was not tested.")
