#!/usr/bin/env python3
"""Schema-v2 findings, gates, provenance, grouping, and v1 adaptation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import finding_catalog
import manager_contract
import output_sanitizer
import provenance
import safe_input


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
        return output_sanitizer.sanitize_mapping(asdict(self))


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
        input_snapshot: safe_input.InputSnapshot | None = None,
        input_error: safe_input.InputInspectionError | None = None,
        target: manager_contract.TargetContract | None = None,
    ) -> None:
        if sum(
            value is not None
            for value in (input_path, input_snapshot, input_error)
        ) > 1:
            raise ValueError(
                "input_path, input_snapshot, and input_error are mutually exclusive"
            )
        self.findings: list[Finding] = []
        self.execution = {
            "policy": "static-only",
            "attempted": False,
            "reason": execution_reason,
        }
        self.gates = {name: "not-run" for name in GATE_NAMES}
        self.evidence_level = "valid"
        self.runtime = {"status": "not-tested", "reason": execution_reason}
        self.input_snapshot = input_snapshot
        self.input_inspection_error = input_error
        if self.input_snapshot is None and input_path is not None:
            try:
                self.input_snapshot = safe_input.inspect(Path(input_path))
            except safe_input.InputInspectionError as exc:
                self.input_inspection_error = exc
        self.provenance = provenance.collect(
            input_snapshot=self.input_snapshot,
            input_error=self.input_inspection_error,
            target=target,
        )
        manager_contract_evidence = self.provenance.get("managerContract")
        if (
            isinstance(manager_contract_evidence, dict)
            and manager_contract_evidence.get("status") == "invalid"
        ):
            self.error(
                "manager contract is invalid: "
                + str(manager_contract_evidence.get("error", "unavailable")),
                code="MANAGER_CONTRACT_INVALID",
                phase="manager",
                root_cause="invalid-manager-contract",
                remediation=(
                    "Restore scripts/manager-contract.json from the canonical "
                    "skill package before retrying."
                ),
                evidence="",
            )
            self.block_downstream_gates()

    def block_input_preflight(self) -> ValidationResult:
        failure = self.input_inspection_error
        if failure is None:
            raise ValueError("input preflight did not fail")
        if failure.code == "INPUT_NOT_FOUND":
            root_cause = "missing-input"
            remediation = "Provide an existing input path and retry."
        elif failure.code.startswith(
            ("INPUT_ENTRY_", "INPUT_FILE_", "INPUT_TOTAL_", "INPUT_PATH_")
        ):
            root_cause = "input-resource-limit"
            remediation = (
                "Reduce the input size, entry count, path length, or path depth "
                "and retry."
            )
        elif failure.code == "INPUT_CHANGED_DURING_SCAN":
            root_cause = "input-changed-during-scan"
            remediation = "Stop concurrent writes and retry with a stable input tree."
        else:
            root_cause = "unsafe-input-type"
            remediation = (
                "Replace the unsafe filesystem object with owned regular files "
                "and directories."
            )
        self.error(
            str(failure),
            code=failure.code,
            phase="input-preflight",
            path=failure.path,
            root_cause=root_cause,
            remediation=remediation,
            evidence=failure.path,
        )
        self.set_gate("portability", "blocked")
        self.set_gate("install", "blocked")
        self.set_gate("configLoad", "blocked")
        self.finalize_contract()
        return self

    def set_gate(self, name: str, value: str) -> None:
        if name not in self.gates:
            raise ValueError(f"unknown validation gate {name}")
        if value not in GATE_VALUES:
            raise ValueError(f"invalid gate value {value}")
        self.gates[name] = value

    def block_downstream_gates(self) -> None:
        """Mark gates that cannot run after an input or contract failure."""

        for name in ("portability", "install", "configLoad"):
            if self.gates[name] == "not-run":
                self.gates[name] = "blocked"

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
        safe_message = output_sanitizer.sanitize_text(message)
        safe_evidence = (
            ""
            if "SECRET" in actual_code
            else output_sanitizer.sanitize_text(evidence or safe_message[:240])
        )
        self.findings.append(
            Finding(
                code=actual_code,
                severity=severity,
                phase=actual_phase,
                path=output_sanitizer.sanitize_text(
                    path if path is not None else inferred_path
                ),
                location=output_sanitizer.sanitize_text(
                    location if location is not None else inferred_location
                ),
                message=safe_message,
                rootCause=output_sanitizer.sanitize_text(root_cause or inferred_root),
                remediation=output_sanitizer.sanitize_text(
                    remediation or inferred_remediation
                ),
                evidence=safe_evidence[:240],
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
            return output_sanitizer.sanitize_mapping(
                {"schemaVersion": 1, "status": self.status, **common}
            )
        if schema_version != 2:
            raise ValueError(f"unsupported schema version {schema_version}")
        return output_sanitizer.sanitize_mapping(
            {
                "schemaVersion": 2,
                "evidenceLevel": self.evidence_level,
                "gates": dict(self.gates),
                "runtime": dict(self.runtime),
                "provenance": dict(self.provenance),
                **common,
            }
        )

    def print_summary(self, *, output_format: str = "human", schema_version: int = 2) -> None:
        if output_format == "json":
            print(json.dumps(self.as_dict(schema_version=schema_version), ensure_ascii=False, indent=2))
            return
        errors = [item for item in self.findings if item.severity == "error"]
        warnings = [item for item in self.findings if item.severity == "warning"]
        if errors:
            print(f"ERRORS ({len(errors)}):")
            for item in errors:
                print(f"- [{item.code}] {output_sanitizer.sanitize_text(item.message)}")
        if warnings:
            print(f"WARNINGS ({len(warnings)}):")
            for item in warnings:
                print(f"- [{item.code}] {output_sanitizer.sanitize_text(item.message)}")
        if self.findings:
            print(f"{len(self.groups())} root causes affecting {len(self.findings)} validation points.")
        if self.ok:
            print(f"Expert package is valid at evidence level {self.evidence_level}; runtime was not tested.")
