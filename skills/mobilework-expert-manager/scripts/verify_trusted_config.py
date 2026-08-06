#!/usr/bin/env python3
"""Verify trusted installed config using an explicitly supplied OpenCode sidecar."""

from __future__ import annotations

import argparse
import sys
from typing import Any
from pathlib import Path

import cli_contract
import manager_contract


def _guard_startup_policy() -> None:
    try:
        manager_contract.load_policy()
    except manager_contract.ManagerContractError as error:
        output_format, schema_version = cli_contract.requested_output(sys.argv[1:])
        failure = cli_contract.CliInternalError(
            str(error),
            code="MANAGER_POLICY_INVALID",
            phase="manager-contract",
        )
        raise SystemExit(
            cli_contract.run_cli(
                "verify-trusted-config",
                lambda: (_ for _ in ()).throw(failure),
                output_format=output_format,
                schema_version=schema_version,
            )
        )


if __name__ == "__main__":
    _guard_startup_policy()


import config_loader


class ManagerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise cli_contract.CliArgumentError(message)


def build_parser(policy: dict[str, Any]) -> ManagerArgumentParser:
    parser = ManagerArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
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
    argv: list[str] | None,
    policy: dict[str, Any],
) -> argparse.Namespace:
    parser = build_parser(policy)
    args = parser.parse_args(argv)
    if args.target_opencode_version is None and args.host_contract is None:
        raise cli_contract.CliArgumentError(
            "provide --target-opencode-version or --host-contract"
        )
    return args


def _success(payload: dict[str, Any]) -> cli_contract.CliResult:
    common = {
        "schemaVersion",
        "ok",
        "status",
        "evidenceLevel",
        "gates",
        "runtime",
        "execution",
        "provenance",
        "findings",
    }
    return cli_contract.CliResult(
        operation="verify-trusted-config",
        ok=True,
        status="config-loadable",
        evidence_level="config-loadable",
        gates=payload["gates"],
        runtime=payload["runtime"],
        execution=payload["execution"],
        provenance=payload["provenance"],
        findings=payload["findings"],
        data={key: value for key, value in payload.items() if key not in common},
        exit_code=cli_contract.ExitCode.SUCCESS,
        legacy_payload=payload,
    )


def _evidence_failure(error: config_loader.ConfigEvidenceError) -> cli_contract.CliResult:
    attempted = error.attempted
    gates = (
        {
            "archive": "not-run",
            "contract": "passed",
            "portability": "passed",
            "install": "passed",
            "configLoad": "failed",
        }
        if attempted
        else {
            "archive": "not-run",
            "contract": "failed",
            "portability": "blocked",
            "install": "failed",
            "configLoad": "blocked",
        }
    )
    return cli_contract.CliResult(
        operation="verify-trusted-config",
        ok=False,
        status="evidence-chain-invalid",
        evidence_level="invalid",
        gates=gates,
        runtime={"status": "not-tested", "reason": "config-evidence-chain-invalid"},
        execution={
            "policy": "trusted-sidecar-pure-config",
            "attempted": attempted,
            "reason": error.stage,
        },
        provenance=error.provenance,
        findings=error.findings,
        data={"code": error.code, "stage": error.stage},
        exit_code=cli_contract.ExitCode.CONTRACT_OR_SAFETY_FAILURE,
        legacy_payload={
            "ok": False,
            "code": error.code,
            "message": str(error),
            "findings": error.findings,
        },
    )


def _load_failure(error: config_loader.ConfigLoadError) -> cli_contract.CliResult:
    chain_verified = "receipt" in error.provenance
    rendered_failure = cli_contract.CliArgumentError(
        str(error),
        code="CONFIG_LOAD_CONTRACT_ERROR",
        status="config-load-error",
        phase=error.stage,
    )
    finding = cli_contract.finding_for_failure(rendered_failure)
    gates = {
        "archive": "not-run",
        "contract": "passed" if chain_verified else "not-run",
        "portability": "passed" if chain_verified else "blocked",
        "install": "passed" if chain_verified else "not-run",
        "configLoad": "failed" if chain_verified else "blocked",
    }
    return cli_contract.CliResult(
        operation="verify-trusted-config",
        ok=False,
        status="config-load-error",
        evidence_level="invalid",
        gates=gates,
        runtime={"status": "not-tested", "reason": error.stage},
        execution={
            "policy": "trusted-sidecar-pure-config",
            "attempted": error.attempted,
            "reason": error.stage,
        },
        provenance=error.provenance,
        findings=(finding,),
        data={"code": "CONFIG_LOAD_CONTRACT_ERROR", "stage": error.stage},
        exit_code=cli_contract.ExitCode.ARGUMENT_ENVIRONMENT_OR_VERSION_ERROR,
        legacy_payload={
            "ok": False,
            "code": "CONFIG_LOAD_CONTRACT_ERROR",
            "message": str(error),
        },
    )


def _execute(args: argparse.Namespace) -> cli_contract.CliResult:
    try:
        if args.host_contract is not None and args.target_opencode_version is not None:
            host = manager_contract.load_host_contract(args.host_contract)
            explicit = manager_contract.resolve_target(
                cli_version=args.target_opencode_version,
                env={},
            )
            if host["opencodeVersion"] != explicit.version:
                raise manager_contract.ManagerContractError(
                    "target OpenCode version conflicts with host contract"
                )
        target = manager_contract.resolve_target(
            cli_version=args.target_opencode_version,
            env={},
            host_contract=args.host_contract,
        )
    except manager_contract.ManagerContractError as exc:
        raise cli_contract.CliArgumentError(
            str(exc),
            code="MANAGER_VERSION_CONTRACT_ERROR",
            phase="manager-contract",
        ) from exc
    try:
        return _success(
            config_loader.verify(
                args.package_dir,
                args.workspace,
                args.sidecar,
                target=target,
            )
        )
    except config_loader.ConfigEvidenceError as exc:
        return _evidence_failure(exc)
    except config_loader.ConfigLoadError as exc:
        return _load_failure(exc)


def _requested_output(argv: list[str]) -> tuple[str, int]:
    return cli_contract.requested_output(argv)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    requested_format, requested_schema = _requested_output(raw_argv)
    try:
        policy = manager_contract.load_policy()
        cli_contract.CliPolicy.from_policy(policy)
    except (manager_contract.ManagerContractError, cli_contract.CliFailure) as exc:
        failure = cli_contract.CliInternalError(
            str(exc),
            code="MANAGER_POLICY_INVALID",
            phase="manager-contract",
        )
        return cli_contract.run_cli(
            "verify-trusted-config",
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=None,
        )
    if any(token in {"-h", "--help"} for token in raw_argv):
        help_text = build_parser(policy).format_help()
        return cli_contract.run_cli(
            "verify-trusted-config",
            lambda: cli_contract.help_result(
                "verify-trusted-config",
                help_text,
            ),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    try:
        args = parse_args(raw_argv, policy)
    except cli_contract.CliFailure as failure:
        return cli_contract.run_cli(
            "verify-trusted-config",
            lambda: (_ for _ in ()).throw(failure),
            output_format=requested_format,
            schema_version=requested_schema,
            policy=policy,
        )
    return cli_contract.run_cli(
        "verify-trusted-config",
        lambda: _execute(args),
        output_format=args.format,
        schema_version=args.schema_version,
        policy=policy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
