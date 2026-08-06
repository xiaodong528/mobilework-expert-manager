#!/usr/bin/env python3
"""Shared schema, rendering, and exit handling for manager CLI entrypoints."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TextIO

import output_sanitizer
import manager_contract


_EMERGENCY_POLICY: dict[str, Any] = {
    # Used only to emit one stable exit-3 document when the canonical policy
    # cannot be loaded. No action is allowed to execute under this fallback.
    "cli": {
        "defaultSchemaVersion": 2,
        "supportedSchemaVersions": [1, 2],
        "formats": ["human", "json"],
        "v2Fields": [
            "schemaVersion",
            "operation",
            "ok",
            "status",
            "evidenceLevel",
            "gates",
            "runtime",
            "execution",
            "provenance",
            "findings",
            "data",
        ],
        "exitCodes": {
            "success": 0,
            "contractOrSafety": 1,
            "argumentEnvironmentOrVersion": 2,
            "internal": 3,
            "runtimePolicyBlocked": 4,
        },
    },
    "gates": {
        "names": ["archive", "contract", "portability", "install", "configLoad"],
        "values": ["passed", "failed", "blocked", "not-run"],
    },
    "evidenceLevels": ["invalid", "valid", "installable", "config-loadable"],
    "runtimeStatuses": ["not-tested", "blocked", "verified"],
}
try:
    _CANONICAL_POLICY = manager_contract.load_policy()
except manager_contract.ManagerContractError:
    _CANONICAL_POLICY = _EMERGENCY_POLICY
_CLI_POLICY = _CANONICAL_POLICY["cli"]
SCHEMA_V2_FIELDS = tuple(_CLI_POLICY["v2Fields"])
SUPPORTED_SCHEMA_VERSIONS = tuple(_CLI_POLICY["supportedSchemaVersions"])
SUPPORTED_FORMATS = tuple(_CLI_POLICY["formats"])
GATE_NAMES = tuple(_CANONICAL_POLICY["gates"]["names"])
GATE_VALUES = tuple(_CANONICAL_POLICY["gates"]["values"])
EVIDENCE_LEVELS = tuple(_CANONICAL_POLICY["evidenceLevels"])
RUNTIME_STATUSES = tuple(_CANONICAL_POLICY["runtimeStatuses"])
DEFAULT_SCHEMA_VERSION = int(_CLI_POLICY["defaultSchemaVersion"])
LEGACY_SCHEMA_VERSION = min(SUPPORTED_SCHEMA_VERSIONS)
VALID_EXIT_CODES = frozenset(int(value) for value in _CLI_POLICY["exitCodes"].values())


def requested_output(argv: Sequence[str]) -> tuple[str, int]:
    """Recover valid renderer options even when argument parsing later fails."""

    output_format = "json"
    schema_version = DEFAULT_SCHEMA_VERSION
    schema_strings = {str(item) for item in SUPPORTED_SCHEMA_VERSIONS}
    for index, token in enumerate(argv):
        if token == "--format" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in SUPPORTED_FORMATS:
                output_format = candidate
        elif token.startswith("--format="):
            candidate = token.split("=", 1)[1]
            if candidate in SUPPORTED_FORMATS:
                output_format = candidate
        elif token == "--schema-version" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in schema_strings:
                schema_version = int(candidate)
        elif token.startswith("--schema-version="):
            candidate = token.split("=", 1)[1]
            if candidate in schema_strings:
                schema_version = int(candidate)
    return output_format, schema_version


class ExitCode(IntEnum):
    """Stable process exits shared by schema-v2 manager commands."""

    SUCCESS = _CLI_POLICY["exitCodes"]["success"]
    CONTRACT_OR_SAFETY_FAILURE = _CLI_POLICY["exitCodes"]["contractOrSafety"]
    ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR = _CLI_POLICY["exitCodes"][
        "argumentEnvironmentOrVersion"
    ]
    INTERNAL_ERROR = _CLI_POLICY["exitCodes"]["internal"]
    RUNTIME_POLICY_BLOCKED = _CLI_POLICY["exitCodes"]["runtimePolicyBlocked"]


_DEFAULT_EXIT_CODES = {
    "success": int(ExitCode.SUCCESS),
    "contractOrSafety": int(ExitCode.CONTRACT_OR_SAFETY_FAILURE),
    "argumentEnvironmentOrVersion": int(
        ExitCode.ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR
    ),
    "internal": int(ExitCode.INTERNAL_ERROR),
    "runtimePolicyBlocked": int(ExitCode.RUNTIME_POLICY_BLOCKED),
}


@dataclass(frozen=True)
class CliPolicy:
    """Validated CLI subset optionally injected from manager-contract.json."""

    default_schema_version: int = DEFAULT_SCHEMA_VERSION
    supported_schema_versions: tuple[int, ...] = SUPPORTED_SCHEMA_VERSIONS
    formats: tuple[str, ...] = SUPPORTED_FORMATS
    v2_fields: tuple[str, ...] = SCHEMA_V2_FIELDS
    gate_names: tuple[str, ...] = GATE_NAMES
    gate_values: tuple[str, ...] = GATE_VALUES
    evidence_levels: tuple[str, ...] = EVIDENCE_LEVELS
    runtime_statuses: tuple[str, ...] = RUNTIME_STATUSES
    exit_codes: Mapping[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_EXIT_CODES)
    )

    @classmethod
    def from_policy(cls, policy: Mapping[str, Any] | None = None) -> CliPolicy:
        if policy is None:
            return cls()
        raw = policy.get("cli", policy.get("cliContract", {}))
        if not isinstance(raw, Mapping):
            raise CliArgumentError("manager CLI policy must be an object")

        default_schema = raw.get("defaultSchemaVersion", DEFAULT_SCHEMA_VERSION)
        supported_raw = raw.get(
            "supportedSchemaVersions", list(SUPPORTED_SCHEMA_VERSIONS)
        )
        formats_raw = raw.get("formats", list(SUPPORTED_FORMATS))
        fields_raw = raw.get("v2Fields", list(SCHEMA_V2_FIELDS))
        exits_raw = raw.get("exitCodes", dict(_DEFAULT_EXIT_CODES))
        gates_raw = policy.get(
            "gates",
            {"names": list(GATE_NAMES), "values": list(GATE_VALUES)},
        )
        evidence_raw = policy.get("evidenceLevels", list(EVIDENCE_LEVELS))
        runtime_raw = policy.get("runtimeStatuses", list(RUNTIME_STATUSES))

        if (
            isinstance(default_schema, bool)
            or not isinstance(default_schema, int)
        ):
            raise CliArgumentError("defaultSchemaVersion must be an integer")
        if not isinstance(supported_raw, Sequence) or isinstance(
            supported_raw, (str, bytes)
        ):
            raise CliArgumentError("supportedSchemaVersions must be an array")
        supported = tuple(supported_raw)
        if (
            any(isinstance(item, bool) or not isinstance(item, int) for item in supported)
            or supported != SUPPORTED_SCHEMA_VERSIONS
        ):
            raise CliArgumentError(
                "supportedSchemaVersions must match the manager contract"
            )
        if default_schema not in supported:
            raise CliArgumentError(
                "defaultSchemaVersion must be a supported schema version"
            )

        if not isinstance(formats_raw, Sequence) or isinstance(
            formats_raw, (str, bytes)
        ):
            raise CliArgumentError("formats must be an array")
        formats = tuple(formats_raw)
        if formats != SUPPORTED_FORMATS:
            raise CliArgumentError("formats must match the manager contract")

        if not isinstance(fields_raw, Sequence) or isinstance(
            fields_raw, (str, bytes)
        ):
            raise CliArgumentError("v2Fields must be an array")
        fields = tuple(fields_raw)
        if fields != SCHEMA_V2_FIELDS:
            raise CliArgumentError(
                "schema v2 fields must match the canonical 11-field envelope"
            )

        if not isinstance(exits_raw, Mapping):
            raise CliArgumentError("exitCodes must be an object")
        exits = dict(exits_raw)
        if exits != _DEFAULT_EXIT_CODES:
            raise CliArgumentError("exitCodes must match the manager contract")
        if not isinstance(gates_raw, Mapping):
            raise CliArgumentError("gates must be an object")
        gate_names = tuple(gates_raw.get("names", ()))
        gate_values = tuple(gates_raw.get("values", ()))
        if gate_names != GATE_NAMES or gate_values != GATE_VALUES:
            raise CliArgumentError("gate names and values must match the manager contract")
        if not isinstance(evidence_raw, Sequence) or isinstance(
            evidence_raw, (str, bytes)
        ):
            raise CliArgumentError("evidenceLevels must be an array")
        evidence_levels = tuple(evidence_raw)
        if evidence_levels != EVIDENCE_LEVELS:
            raise CliArgumentError("evidenceLevels must match the manager contract")
        if not isinstance(runtime_raw, Sequence) or isinstance(
            runtime_raw, (str, bytes)
        ):
            raise CliArgumentError("runtimeStatuses must be an array")
        runtime_statuses = tuple(runtime_raw)
        if runtime_statuses != RUNTIME_STATUSES:
            raise CliArgumentError("runtimeStatuses must match the manager contract")
        return cls(
            default_schema_version=default_schema,
            supported_schema_versions=supported,
            formats=formats,
            v2_fields=fields,
            gate_names=gate_names,
            gate_values=gate_values,
            evidence_levels=evidence_levels,
            runtime_statuses=runtime_statuses,
            exit_codes=exits,
        )


class CliFailure(Exception):
    """A failure that can be rendered without a traceback."""

    exit_code = ExitCode.INTERNAL_ERROR
    default_code = "MANAGER_INTERNAL_ERROR"
    default_status = "internal-error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: str | None = None,
        phase: str = "manager",
        attempted: bool = False,
        execution_policy: str = "manager-cli",
        provenance: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if not isinstance(attempted, bool):
            raise TypeError("attempted must be boolean")
        self.code = code or self.default_code
        self.status = status or self.default_status
        self.phase = phase
        self.attempted = attempted
        self.execution_policy = execution_policy
        self.provenance = dict(provenance or {})
        self.data = dict(data or {})


class CliContractError(CliFailure):
    exit_code = ExitCode.CONTRACT_OR_SAFETY_FAILURE
    default_code = "MANAGER_CONTRACT_ERROR"
    default_status = "invalid"


class CliArgumentError(CliFailure):
    exit_code = ExitCode.ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR
    default_code = "MANAGER_ARGUMENT_ERROR"
    default_status = "argument-error"


class CliInternalError(CliFailure):
    exit_code = ExitCode.INTERNAL_ERROR
    default_code = "MANAGER_INTERNAL_ERROR"
    default_status = "internal-error"


class CliRuntimePolicyError(CliFailure):
    exit_code = ExitCode.RUNTIME_POLICY_BLOCKED
    default_code = "MANAGER_RUNTIME_POLICY_BLOCKED"
    default_status = "blocked"


# Concise aliases for callers that prefer the plan terminology.
ArgumentFailure = CliArgumentError
InternalFailure = CliInternalError


def default_gates() -> dict[str, str]:
    return {name: "not-run" for name in GATE_NAMES}


@dataclass(frozen=True)
class CliResult:
    """One canonical execution result, adaptable without re-running work."""

    operation: str
    ok: bool
    status: str
    evidence_level: str
    gates: Mapping[str, str]
    runtime: Mapping[str, Any]
    execution: Mapping[str, Any]
    provenance: Mapping[str, Any]
    findings: Sequence[Mapping[str, Any]]
    data: Mapping[str, Any]
    exit_code: int | ExitCode = ExitCode.SUCCESS
    legacy_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise CliArgumentError("operation must be a non-empty string")
        if not isinstance(self.status, str) or not self.status.strip():
            raise CliArgumentError("status must be a non-empty string")
        if not isinstance(self.evidence_level, str) or not self.evidence_level.strip():
            raise CliArgumentError("evidenceLevel must be a non-empty string")
        try:
            normalized_exit = int(self.exit_code)
        except (TypeError, ValueError) as error:
            raise CliArgumentError(
                "exit code must match the manager contract"
            ) from error
        if isinstance(self.exit_code, bool) or normalized_exit not in VALID_EXIT_CODES:
            raise CliArgumentError("exit code must match the manager contract")

    def as_v2(self) -> dict[str, Any]:
        payload = {
            "schemaVersion": DEFAULT_SCHEMA_VERSION,
            "operation": self.operation,
            "ok": self.ok,
            "status": self.status,
            "evidenceLevel": self.evidence_level,
            "gates": dict(self.gates),
            "runtime": dict(self.runtime),
            "execution": dict(self.execution),
            "provenance": dict(self.provenance),
            "findings": [dict(item) for item in self.findings],
            "data": dict(self.data),
        }
        if tuple(payload) != SCHEMA_V2_FIELDS:
            raise CliInternalError("schema v2 envelope construction drifted")
        return output_sanitizer.sanitize_mapping(payload)

    def as_v1(self) -> dict[str, Any]:
        if self.legacy_payload is not None:
            payload = dict(self.legacy_payload)
            payload["schemaVersion"] = LEGACY_SCHEMA_VERSION
        else:
            payload = {
                "schemaVersion": LEGACY_SCHEMA_VERSION,
                "operation": self.operation,
                "ok": self.ok,
                "status": self.status,
                "findings": [dict(item) for item in self.findings],
                "data": dict(self.data),
            }
        return output_sanitizer.sanitize_mapping(payload)

    def as_dict(
        self,
        *,
        schema_version: int | None = None,
        policy: CliPolicy | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_policy = (
            policy
            if isinstance(policy, CliPolicy)
            else CliPolicy.from_policy(policy)
        )
        if not isinstance(self.ok, bool):
            raise CliArgumentError("ok must be a boolean")
        normalized_exit = int(self.exit_code)
        if self.ok != (normalized_exit == int(ExitCode.SUCCESS)):
            raise CliArgumentError("ok must agree with the fixed process exit code")
        if set(self.gates) != set(resolved_policy.gate_names) or any(
            value not in resolved_policy.gate_values for value in self.gates.values()
        ):
            raise CliArgumentError("gates do not match the manager contract")
        if self.evidence_level not in resolved_policy.evidence_levels:
            raise CliArgumentError("evidenceLevel does not match the manager contract")
        runtime_status = self.runtime.get("status")
        if runtime_status not in resolved_policy.runtime_statuses:
            raise CliArgumentError("runtime.status does not match the manager contract")
        if not self.ok and (
            self.evidence_level == "config-loadable" or runtime_status == "verified"
        ):
            raise CliArgumentError(
                "failed results cannot claim config-loadable or verified evidence"
            )
        if self.ok and any(
            str(finding.get("severity", "")).lower() == "error"
            for finding in self.findings
        ):
            raise CliArgumentError("successful results cannot contain error findings")
        selected = (
            resolved_policy.default_schema_version
            if schema_version is None
            else schema_version
        )
        if selected not in resolved_policy.supported_schema_versions:
            raise CliArgumentError(f"unsupported schema version {selected}")
        return self.as_v1() if selected == LEGACY_SCHEMA_VERSION else self.as_v2()


def help_result(operation: str, help_text: str) -> CliResult:
    return CliResult(
        operation=operation,
        ok=True,
        status="help",
        evidence_level="invalid",
        gates=default_gates(),
        runtime={"status": "not-tested", "reason": "help-only"},
        execution={
            "policy": "manager-cli-help",
            "attempted": False,
            "reason": "help-only",
        },
        provenance={},
        findings=(),
        data={"help": help_text},
        exit_code=ExitCode.SUCCESS,
        legacy_payload={"ok": True, "status": "help", "help": help_text},
    )


def finding_for_failure(failure: CliFailure) -> dict[str, str]:
    message = output_sanitizer.sanitize_exception(failure)
    return {
        "code": failure.code,
        "severity": "error",
        "phase": failure.phase,
        "path": "",
        "location": failure.phase,
        "message": message,
        "rootCause": failure.status,
        "remediation": "Review the reported failure and retry with corrected inputs.",
        "evidence": "",
    }


def result_for_failure(operation: str, failure: CliFailure) -> CliResult:
    runtime_status = (
        "blocked"
        if failure.exit_code == ExitCode.RUNTIME_POLICY_BLOCKED
        else "not-tested"
    )
    return CliResult(
        operation=operation,
        ok=False,
        status=failure.status,
        evidence_level="invalid",
        gates=default_gates(),
        runtime={"status": runtime_status, "reason": failure.status},
        execution={
            "policy": failure.execution_policy,
            "attempted": failure.attempted,
            "reason": failure.status,
        },
        provenance=failure.provenance,
        findings=(finding_for_failure(failure),),
        data=failure.data,
        exit_code=failure.exit_code,
    )


def render_human(result: CliResult) -> str:
    help_text = result.data.get("help")
    if result.status == "help" and isinstance(help_text, str):
        return output_sanitizer.sanitize_text(help_text)
    lines = [f"{result.operation}: {result.status}"]
    for finding in result.findings:
        code = str(finding.get("code", "MANAGER_FINDING"))
        message = str(finding.get("message", ""))
        lines.append(f"- [{code}] {message}")
    rendered_fields: set[tuple[str, str]] = set()

    def append_field(label: str, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        item = (label, value)
        if item in rendered_fields:
            return
        rendered_fields.add(item)
        lines.append(f"- {label}: {value}")

    def append_state(label: str, source: Mapping[str, Any], key: str) -> None:
        if key not in source:
            return
        value = source[key]
        if value is None:
            append_field(label, "unknown")
        elif isinstance(value, bool):
            append_field(label, str(value).lower())

    def append_recovery_paths(source: Mapping[str, Any]) -> None:
        values = source.get("recoveryPaths")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return
        for value in values:
            if isinstance(value, str):
                append_field(
                    "recoveryPath",
                    output_sanitizer.sanitize_text(value),
                )

    for source in (result.data, result.provenance):
        append_field("slug", source.get("slug"))
        append_field("previewSha256", source.get("previewSha256"))
        append_field("backupId", source.get("backupId"))
        append_field("backupSha256", source.get("backupSha256"))
        append_field("restoredPreviewSha256", source.get("restoredPreviewSha256"))
        append_state("committed", source, "committed")
        append_state("rollbackVerified", source, "rollbackVerified")
        append_recovery_paths(source)
        backup = source.get("driftBackup")
        if isinstance(backup, Mapping):
            append_field("backupId", backup.get("backupId"))
            append_field("backupSha256", backup.get("backupSha256"))
            append_field("previewSha256", backup.get("previewSha256"))
    return output_sanitizer.sanitize_text("\n".join(lines))


def write_diagnostic(message: str, *, stderr: TextIO | None = None) -> None:
    destination = sys.stderr if stderr is None else stderr
    destination.write(output_sanitizer.sanitize_text(message).rstrip("\n") + "\n")


def emit(
    result: CliResult,
    *,
    output_format: str,
    schema_version: int | None = None,
    policy: CliPolicy | Mapping[str, Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    diagnostics: Sequence[str] = (),
    human_renderer: Callable[[CliResult], str] | None = None,
) -> int:
    resolved_policy = (
        policy if isinstance(policy, CliPolicy) else CliPolicy.from_policy(policy)
    )
    if output_format not in resolved_policy.formats:
        raise CliArgumentError(f"unsupported output format {output_format}")
    destination = sys.stdout if stdout is None else stdout
    for diagnostic in diagnostics:
        write_diagnostic(diagnostic, stderr=stderr)
    if output_format == "json":
        payload = result.as_dict(
            schema_version=schema_version,
            policy=resolved_policy,
        )
        destination.write(output_sanitizer.json_dumps(payload, indent=2) + "\n")
    else:
        renderer = render_human if human_renderer is None else human_renderer
        rendered = output_sanitizer.sanitize_text(renderer(result))
        destination.write(rendered.rstrip("\n") + "\n")
    return int(result.exit_code)


def run_cli(
    operation: str,
    action: Callable[[], CliResult],
    *,
    output_format: str = "human",
    schema_version: int | None = None,
    policy: CliPolicy | Mapping[str, Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    human_renderer: Callable[[CliResult], str] | None = None,
) -> int:
    """Execute once, convert known failures, and emit without traceback output."""

    try:
        resolved_policy = (
            policy
            if isinstance(policy, CliPolicy)
            else CliPolicy.from_policy(policy)
        )
    except CliFailure as error:
        result = result_for_failure(
            operation,
            CliInternalError(
                str(error),
                code="MANAGER_POLICY_INVALID",
                phase="manager-contract",
            ),
        )
        fallback_format = (
            output_format if output_format in SUPPORTED_FORMATS else "human"
        )
        fallback_schema = (
            schema_version
            if schema_version in SUPPORTED_SCHEMA_VERSIONS
            else DEFAULT_SCHEMA_VERSION
        )
        return emit(
            result,
            output_format=fallback_format,
            schema_version=fallback_schema,
            policy=None,
            stdout=stdout,
            stderr=stderr,
            diagnostics=(
                f"error: [{result.findings[0].get('code', 'MANAGER_FINDING')}] "
                f"{result.findings[0].get('message', '')}",
            ),
            human_renderer=None,
        )

    try:
        result = action()
    except CliFailure as failure:
        result = result_for_failure(operation, failure)
    except Exception as error:
        result = result_for_failure(
            operation,
            CliInternalError(output_sanitizer.sanitize_exception(error)),
        )
    else:
        try:
            if not isinstance(result, CliResult):
                raise CliInternalError("manager CLI action returned an invalid result")
            return emit(
                result,
                output_format=output_format,
                schema_version=schema_version,
                policy=resolved_policy,
                stdout=stdout,
                stderr=stderr,
                diagnostics=(
                    tuple(
                        f"error: [{item.get('code', 'MANAGER_FINDING')}] "
                        f"{item.get('message', '')}"
                        for item in result.findings
                        if not result.ok
                    )
                ),
                human_renderer=human_renderer,
            )
        except Exception as error:
            result = result_for_failure(
                operation,
                CliInternalError(output_sanitizer.sanitize_exception(error)),
            )

    fallback_format = output_format if output_format in SUPPORTED_FORMATS else "human"
    fallback_schema = (
        schema_version
        if schema_version in SUPPORTED_SCHEMA_VERSIONS
        else DEFAULT_SCHEMA_VERSION
    )
    return emit(
        result,
        output_format=fallback_format,
        schema_version=fallback_schema,
        policy=None,
        stdout=stdout,
        stderr=stderr,
        diagnostics=(
            f"error: [{result.findings[0].get('code', 'MANAGER_FINDING')}] "
            f"{result.findings[0].get('message', '')}",
        ),
        human_renderer=None,
    )


def _split_legacy_output_args(
    argv: Sequence[str],
    *,
    default_format: str,
) -> tuple[list[str], str, int]:
    """Remove shared renderer flags before invoking an unchanged legacy parser."""

    if default_format not in SUPPORTED_FORMATS:
        raise CliArgumentError(f"unsupported default output format {default_format}")
    remaining: list[str] = []
    output_format = default_format
    schema_version = DEFAULT_SCHEMA_VERSION
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--format", "--schema-version"}:
            if index + 1 >= len(argv):
                raise CliArgumentError(f"{token} requires a value")
            value = argv[index + 1]
            index += 2
            if token == "--format":
                if value not in SUPPORTED_FORMATS:
                    raise CliArgumentError(
                        "--format must be one of: " + ", ".join(SUPPORTED_FORMATS)
                    )
                output_format = value
            else:
                try:
                    parsed_schema = int(value)
                except ValueError as error:
                    raise CliArgumentError(
                        "--schema-version must be 1 or 2"
                    ) from error
                if parsed_schema not in SUPPORTED_SCHEMA_VERSIONS:
                    raise CliArgumentError("--schema-version must be 1 or 2")
                schema_version = parsed_schema
            continue
        if token.startswith("--format="):
            value = token.split("=", 1)[1]
            if value not in SUPPORTED_FORMATS:
                raise CliArgumentError(
                    "--format must be one of: " + ", ".join(SUPPORTED_FORMATS)
                )
            output_format = value
            index += 1
            continue
        if token.startswith("--schema-version="):
            value = token.split("=", 1)[1]
            try:
                parsed_schema = int(value)
            except ValueError as error:
                raise CliArgumentError(
                    "--schema-version must be 1 or 2"
                ) from error
            if parsed_schema not in SUPPORTED_SCHEMA_VERSIONS:
                raise CliArgumentError("--schema-version must be 1 or 2")
            schema_version = parsed_schema
            index += 1
            continue
        remaining.append(token)
        index += 1
    return remaining, output_format, schema_version


def _legacy_finding(
    *,
    code: str,
    message: str,
    status: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": "error",
        "phase": "manager",
        "path": "",
        "location": "manager",
        "message": output_sanitizer.sanitize_text(message),
        "rootCause": status,
        "remediation": "Review the reported failure and retry with corrected inputs.",
        "evidence": "",
    }


def legacy_result(
    operation: str,
    payload: Mapping[str, Any],
    *,
    exit_code: int | ExitCode,
    fallback_message: str = "",
) -> CliResult:
    """Adapt one already-executed legacy payload into the canonical result."""

    normalized_exit = int(exit_code)
    if normalized_exit not in VALID_EXIT_CODES:
        normalized_exit = int(ExitCode.INTERNAL_ERROR)
    ok = normalized_exit == int(ExitCode.SUCCESS)
    safe_payload = output_sanitizer.sanitize_mapping(dict(payload))
    raw_status = safe_payload.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        status = raw_status
    elif ok:
        status = "success"
    else:
        status = {
            int(ExitCode.CONTRACT_OR_SAFETY_FAILURE): "invalid",
            int(ExitCode.ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR): "argument-error",
            int(ExitCode.INTERNAL_ERROR): "internal-error",
            int(ExitCode.RUNTIME_POLICY_BLOCKED): "blocked",
        }[normalized_exit]

    evidence = safe_payload.get("evidenceLevel", "invalid")
    if evidence not in EVIDENCE_LEVELS:
        evidence = "invalid"

    raw_gates = safe_payload.get("gates")
    gates = dict(raw_gates) if isinstance(raw_gates, Mapping) else default_gates()
    if set(gates) != set(GATE_NAMES) or any(
        value not in GATE_VALUES for value in gates.values()
    ):
        gates = default_gates()

    raw_runtime = safe_payload.get("runtime")
    runtime = dict(raw_runtime) if isinstance(raw_runtime, Mapping) else {}
    if runtime.get("status") not in RUNTIME_STATUSES:
        runtime = {
            "status": (
                "blocked"
                if normalized_exit == int(ExitCode.RUNTIME_POLICY_BLOCKED)
                else "not-tested"
            ),
            "reason": status,
        }

    raw_execution = safe_payload.get("execution")
    execution = (
        dict(raw_execution)
        if isinstance(raw_execution, Mapping)
        else {
            "policy": "legacy-manager-cli-adapter",
            "attempted": True,
            "reason": status,
        }
    )
    raw_provenance = safe_payload.get("provenance")
    provenance = (
        dict(raw_provenance) if isinstance(raw_provenance, Mapping) else {}
    )
    raw_findings = safe_payload.get("findings")
    findings = [
        dict(item)
        for item in raw_findings
        if isinstance(item, Mapping)
    ] if isinstance(raw_findings, Sequence) and not isinstance(
        raw_findings, (str, bytes)
    ) else []
    if not ok and not any(
        str(item.get("severity", "error")).lower() == "error"
        for item in findings
    ):
        code = safe_payload.get("code")
        message = safe_payload.get("message")
        findings.append(
            _legacy_finding(
                code=(code if isinstance(code, str) and code else "MANAGER_CLI_FAILURE"),
                message=(
                    message
                    if isinstance(message, str) and message
                    else fallback_message or status
                ),
                status=status,
            )
        )
    return CliResult(
        operation=operation,
        ok=ok,
        status=status,
        evidence_level=evidence,
        gates=gates,
        runtime=runtime,
        execution=execution,
        provenance=provenance,
        findings=findings,
        data=safe_payload,
        exit_code=normalized_exit,
        legacy_payload=safe_payload,
    )


def run_legacy_entrypoint(
    operation: str,
    legacy_main: Callable[[], int | None],
    *,
    argv: Sequence[str] | None = None,
    default_format: str = "json",
    delegated_output_flags: Sequence[str] = (),
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run an unchanged CLI once, then render schema v1 or v2 from that result.

    The adapter captures every legacy stdout/stderr sink, so JSON mode always
    emits exactly one document and diagnostics are sanitized before stderr.
    """

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        delegated_argv, output_format, schema_version = _split_legacy_output_args(
            raw_argv,
            default_format=default_format,
        )
    except CliFailure as failure:
        requested_format, requested_schema = requested_output(raw_argv)
        return run_cli(
            operation,
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            stdout=stdout,
            stderr=stderr,
        )

    for flag in delegated_output_flags:
        if flag == "format":
            delegated_argv.extend(("--format", "json"))
        elif flag == "schema-version":
            delegated_argv.extend(("--schema-version", "2"))
        else:
            failure = CliInternalError(
                f"unsupported delegated output flag {flag}"
            )
            return run_cli(
                operation,
                lambda: (_ for _ in ()).throw(failure),
                output_format=output_format,
                schema_version=schema_version,
                stdout=stdout,
                stderr=stderr,
            )

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    original_argv = sys.argv
    returned: int | None = None
    raised_exit: SystemExit | None = None
    unexpected: BaseException | None = None
    try:
        sys.argv = [original_argv[0], *delegated_argv]
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(
            captured_stderr
        ):
            try:
                returned = legacy_main()
            except SystemExit as error:
                raised_exit = error
            except BaseException as error:  # preserve one-document output on all sinks
                unexpected = error
    finally:
        sys.argv = original_argv

    legacy_stdout = captured_stdout.getvalue().strip()
    legacy_stderr = captured_stderr.getvalue().strip()
    diagnostics = tuple(
        line for line in legacy_stderr.splitlines() if line.strip()
    )

    if unexpected is not None:
        failure = CliInternalError(output_sanitizer.sanitize_exception(unexpected))
        result = result_for_failure(operation, failure)
    elif raised_exit is not None and not isinstance(raised_exit.code, int):
        failure = CliContractError(
            output_sanitizer.sanitize_text(str(raised_exit.code or "operation failed"))
        )
        result = result_for_failure(operation, failure)
    else:
        raw_exit = raised_exit.code if raised_exit is not None else returned
        if raw_exit is None:
            normalized_exit = int(ExitCode.SUCCESS)
        elif isinstance(raw_exit, bool) or not isinstance(raw_exit, int):
            normalized_exit = int(ExitCode.INTERNAL_ERROR)
        elif raw_exit in VALID_EXIT_CODES:
            normalized_exit = raw_exit
        else:
            normalized_exit = int(ExitCode.INTERNAL_ERROR)

        if legacy_stdout:
            try:
                parsed_payload = json.loads(legacy_stdout)
            except json.JSONDecodeError:
                parsed_payload = {"output": legacy_stdout}
            if not isinstance(parsed_payload, Mapping):
                parsed_payload = {"result": parsed_payload}
        else:
            parsed_payload = {}
        fallback_message = legacy_stderr or legacy_stdout
        result = legacy_result(
            operation,
            parsed_payload,
            exit_code=normalized_exit,
            fallback_message=fallback_message,
        )

    if not result.ok:
        diagnostics = (*diagnostics, *(
            f"error: [{item.get('code', 'MANAGER_FINDING')}] "
            f"{item.get('message', '')}"
            for item in result.findings
        ))
    def render_legacy_human(value: CliResult) -> str:
        original_output = value.data.get("output")
        if isinstance(original_output, str) and original_output:
            return original_output
        return render_human(value)

    return emit(
        result,
        output_format=output_format,
        schema_version=schema_version,
        stdout=stdout,
        stderr=stderr,
        diagnostics=diagnostics,
        human_renderer=render_legacy_human,
    )
