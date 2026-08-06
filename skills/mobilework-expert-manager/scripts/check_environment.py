#!/usr/bin/env python3
"""Check host dependencies required by MobileWork expert-manager features."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import cli_contract
import execution_context
import manager_contract
import output_sanitizer


MINIMUM_PYTHON = (3, 10)
FEATURES = (
    "core",
    "excel",
    "package",
    "bundle-docx",
    "git",
    "config-load",
    "coverage",
)


class ManagerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise cli_contract.CliArgumentError(message)


def module_status(name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(name) is not None
    return {"kind": "python-module", "name": name, "available": available}


def command_status(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"kind": "command", "name": name, "available": path is not None, "path": path}


def explicit_sidecar_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "kind": "explicit-path",
            "name": "trusted-opencode-sidecar",
            "available": False,
            "required": True,
            "path": None,
            "reason": "config-load requires an explicit --sidecar path",
        }
    lexical = path.expanduser().absolute()
    resolved = lexical.resolve()
    available = (
        not lexical.is_symlink()
        and resolved.is_file()
        and os.access(resolved, os.X_OK)
    )
    return {
        "kind": "explicit-path",
        "name": "trusted-opencode-sidecar",
        "available": available,
        "required": True,
        "path": str(resolved),
        "reason": None if available else "sidecar must be an executable regular file and not a symlink",
    }


def selected_features(values: list[str]) -> list[str]:
    requested = values or ["core"]
    if "all" in requested:
        return list(FEATURES)
    return [feature for feature in FEATURES if feature in requested]


def check_environment(
    features: list[str],
    *,
    env: dict[str, str] | None = None,
    workspace_root: Path | None = None,
    sidecar: Path | None = None,
    host_contract: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    python_ok = sys.version_info >= MINIMUM_PYTHON
    checks.append(
        {
            "kind": "python-version",
            "name": "python",
            "available": python_ok,
            "required": ">=3.10",
            "actual": ".".join(str(part) for part in sys.version_info[:3]),
        }
    )
    if "core" in features:
        checks.append(module_status("yaml"))
    if "excel" in features:
        checks.append(module_status("openpyxl"))
    if "package" in features:
        checks.append(command_status("unzip"))
    if "bundle-docx" in features:
        checks.append(module_status("zipfile"))
        checks.append(module_status("xml.etree.ElementTree"))
    if "git" in features:
        checks.append(command_status("git"))
    if "coverage" in features:
        checks.append(module_status("coverage"))
    if "config-load" in features:
        checks.append(explicit_sidecar_status(sidecar))
        try:
            target = manager_contract.resolve_target(
                env=os.environ if env is None else env,
                host_contract=host_contract,
            )
            checks.append(
                {
                    "kind": "target-contract",
                    "name": "target-opencode-contract",
                    "available": True,
                    "required": False,
                    "version": target.version,
                    "source": target.source,
                    "capabilityVerified": target.capability_verified,
                    "hostContractPath": target.host_contract_path or None,
                }
            )
        except manager_contract.ManagerContractError as error:
            checks.append(
                {
                    "kind": "target-contract",
                    "name": "target-opencode-contract",
                    "available": False,
                    "required": True,
                    "reason": output_sanitizer.sanitize_exception(error),
                }
            )
    missing = [
        check["name"]
        for check in checks
        if check.get("required", True) and not check["available"]
    ]
    routing: dict[str, Any]
    routing_error: dict[str, str] | None = None
    try:
        routing = execution_context.resolve_execution_context(
            env=os.environ if env is None else env,
            workspace_root=workspace_root,
        ).as_dict()
    except execution_context.ExecutionContextError as error:
        routing_error = {
            "code": error.code,
            "message": output_sanitizer.sanitize_exception(error),
        }
        routing = {
            "version": 1,
            "ok": False,
            "hostMode": None,
            "workspaceRoot": str(
                execution_context.canonical_path(workspace_root or Path.cwd())
            ),
            "outputRoot": None,
            "pathSource": None,
            "errors": [routing_error],
        }
    return output_sanitizer.sanitize_mapping(
        {
            "ok": not missing and routing_error is None,
            "features": features,
            "checks": checks,
            "missing": missing,
            "executionContext": routing,
            "hostMode": routing["hostMode"],
            "workspaceRoot": routing["workspaceRoot"],
            "outputRoot": routing["outputRoot"],
            "pathSource": routing["pathSource"],
            "errors": routing["errors"],
        }
    )


def validate_feature_request(values: list[str], sidecar: Path | None) -> None:
    if "all" in values and sidecar is None:
        raise cli_contract.CliArgumentError(
            "--feature all requires an explicit --sidecar path",
            code="ENVIRONMENT_SIDECAR_REQUIRED",
            status="environment-argument-error",
            phase="environment-preflight",
            execution_policy="read-only-environment-preflight",
            data={
                "requestedFeatures": list(values),
                "requiredArgument": "--sidecar",
            },
        )


def build_parser(policy: dict[str, Any]) -> ManagerArgumentParser:
    parser = ManagerArgumentParser(description=__doc__)
    parser.add_argument(
        "--feature",
        action="append",
        choices=(*FEATURES, "all"),
        default=[],
        help="Feature dependencies to check; repeat as needed (default: core)",
    )
    parser.add_argument(
        "--sidecar",
        type=Path,
        help="Explicit trusted OpenCode sidecar path for config-load preflight; never executed",
    )
    parser.add_argument(
        "--host-contract",
        type=Path,
        help="Optional explicit read-only host contract for config-load preflight",
    )
    parser.add_argument(
        "--format",
        choices=policy["cli"]["formats"],
        default="json",
    )
    parser.add_argument(
        "--schema-version",
        choices=policy["cli"]["supportedSchemaVersions"],
        default=policy["cli"]["defaultSchemaVersion"],
        type=int,
    )
    return parser


def parse_args(
    argv: list[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> argparse.Namespace:
    resolved_policy = manager_contract.load_policy() if policy is None else policy
    args = build_parser(resolved_policy).parse_args(argv)
    validate_feature_request(args.feature, args.sidecar)
    return args


def _result(payload: dict[str, Any], policy: dict[str, Any]) -> cli_contract.CliResult:
    ok = payload.get("ok") is True
    findings: list[dict[str, str]] = []
    reported_codes: set[str] = set()
    errors = payload.get("errors", [])
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "ENVIRONMENT_CONTEXT_ERROR")
            reported_codes.add(code)
            findings.append(
                {
                    "code": code,
                    "severity": "error",
                    "phase": "environment-preflight",
                    "path": "",
                    "location": "execution-context",
                    "message": str(item.get("message") or "execution context is unavailable"),
                    "rootCause": "environment-unavailable",
                    "remediation": "Correct the requested environment before execution.",
                    "evidence": "",
                }
            )
    missing = payload.get("missing", [])
    if isinstance(missing, list):
        for name in missing:
            code = (
                "MANAGER_VERSION_CONTRACT_ERROR"
                if name == "target-opencode-contract"
                else "ENVIRONMENT_DEPENDENCY_MISSING"
            )
            if code in reported_codes:
                continue
            findings.append(
                {
                    "code": code,
                    "severity": "error",
                    "phase": "environment-preflight",
                    "path": "",
                    "location": str(name),
                    "message": f"required environment dependency is unavailable: {name}",
                    "rootCause": "environment-unavailable",
                    "remediation": "Provide the required dependency or narrow the requested features.",
                    "evidence": "",
                }
            )
    return cli_contract.CliResult(
        operation="check-environment",
        ok=ok,
        status="environment-ready" if ok else "environment-unavailable",
        evidence_level="valid" if ok else "invalid",
        gates=cli_contract.default_gates(),
        runtime={"status": "not-tested", "reason": "preflight-only"},
        execution={
            "policy": "read-only-environment-preflight",
            "attempted": False,
            "reason": "preflight-only",
        },
        provenance={
            "contractVersion": policy["contractVersion"],
            "managerContractSha256": manager_contract.policy_sha256(),
        },
        findings=findings,
        data={key: value for key, value in payload.items() if key != "ok"},
        exit_code=(
            cli_contract.ExitCode.SUCCESS
            if ok
            else cli_contract.ExitCode.ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR
        ),
        legacy_payload=payload,
    )


def _execute(args: argparse.Namespace, policy: dict[str, Any]) -> cli_contract.CliResult:
    return _result(
        check_environment(
            selected_features(args.feature),
            sidecar=args.sidecar,
            host_contract=args.host_contract,
        ),
        policy,
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    requested_format, requested_schema = cli_contract.requested_output(raw_argv)
    try:
        policy = manager_contract.load_policy()
        cli_contract.CliPolicy.from_policy(policy)
    except (manager_contract.ManagerContractError, cli_contract.CliFailure) as error:
        failure = cli_contract.CliInternalError(
            str(error),
            code="MANAGER_POLICY_INVALID",
            phase="manager-contract",
        )
        return cli_contract.run_cli(
            "check-environment",
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=None,
        )
    if any(token in {"-h", "--help"} for token in raw_argv):
        return cli_contract.run_cli(
            "check-environment",
            lambda: cli_contract.help_result(
                "check-environment",
                build_parser(policy).format_help(),
            ),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    try:
        args = parse_args(raw_argv, policy)
    except cli_contract.CliFailure as failure:
        return cli_contract.run_cli(
            "check-environment",
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    return cli_contract.run_cli(
        "check-environment",
        lambda: _execute(args, policy),
        output_format=args.format,
        schema_version=args.schema_version,
        policy=policy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
