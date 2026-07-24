#!/usr/bin/env python3
"""Statically diagnose an untrusted MobileWork expert directory or ZIP."""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import archive_inspector
import manager_contract
import validate_expert
from validation_result import ValidationResult


def diagnose(
    source: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> ValidationResult:
    source = source.expanduser().absolute()
    if source.is_dir():
        result = validate_expert.validate_package(source, target=target)
        result.execution["reason"] = "untrusted-directory"
        return result
    result = ValidationResult(execution_reason="untrusted-zip", input_path=source, target=target)
    if not source.is_file():
        result.error(
            f"diagnostic source does not exist: {source}",
            code="DIAGNOSTIC_SOURCE_MISSING",
            phase="diagnostic",
            root_cause="missing-diagnostic-source",
            evidence=str(source),
        )
        return result
    if source.suffix.lower() != ".zip":
        result.error(
            f"unsupported diagnostic source: {source.name}",
            code="DIAGNOSTIC_SOURCE_UNSUPPORTED",
            phase="diagnostic",
            root_cause="unsupported-diagnostic-source",
            evidence=source.name,
        )
        return result
    try:
        inspection = archive_inspector.inspect_archive(source)
        result.provenance["limits"] = inspection.limits.as_dict()
        for issue in inspection.issues:
            result.add(
                issue.message,
                severity=issue.severity,
                code=issue.code,
                phase="archive",
                path=issue.path,
                root_cause=issue.root_cause,
                evidence=issue.path,
            )
        if inspection.errors:
            result.set_gate("contract", "blocked")
            result.set_gate("portability", "blocked")
            return result
        result.set_gate("archive", "passed")
        with zipfile.ZipFile(source) as archive:
            bad = archive.testzip()
            if bad is not None:
                result.error(
                    f"ZIP CRC failed: {bad}",
                    code="ZIP_CRC_FAILED",
                    phase="archive",
                    root_cause="corrupt-archive",
                    evidence=bad,
                )
                result.set_gate("contract", "blocked")
                result.set_gate("portability", "blocked")
                return result
            with tempfile.TemporaryDirectory(prefix="mobilework-static-diagnosis-") as temp:
                extraction_root = Path(temp)
                archive_inspector.safe_extract(source, extraction_root, inspection)
                validated = validate_expert.validate_package(
                    extraction_root / inspection.roots[0], target=target
                )
                validated.findings = [*result.findings, *validated.findings]
                validated.gates["archive"] = "passed"
                validated.provenance["inputSha256"] = result.provenance["inputSha256"]
                validated.provenance["limits"] = result.provenance["limits"]
                validated.execution["reason"] = "untrusted-zip"
                return validated
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        result.error(
            f"cannot read ZIP: {exc}",
            code="ZIP_INVALID",
            phase="archive",
            root_cause="corrupt-archive",
            evidence=source.name,
        )
        result.set_gate("contract", "blocked")
        result.set_gate("portability", "blocked")
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Untrusted package directory or ZIP")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--schema-version", choices=(1, 2), type=int, default=2)
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
    parser.add_argument("--runtime", action="store_true", help="Request runtime execution (always blocked)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target = manager_contract.resolve_target(
            cli_version=args.target_opencode_version,
            host_contract=args.host_contract,
        )
        result = diagnose(args.source, target=target)
    except manager_contract.ManagerContractError as exc:
        result = ValidationResult(
            execution_reason="version-contract-error",
            input_path=args.source,
            target=manager_contract.TargetContract(
                version="unknown",
                source="version-contract-error",
                capabilities={},
                capability_verified=False,
            ),
        )
        result.error(
            f"version contract error: {exc}",
            code="MANAGER_VERSION_CONTRACT_ERROR",
            phase="manager",
            root_cause="invalid-version-contract",
            evidence="",
        )
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 2
    except Exception as exc:
        result = ValidationResult(
            execution_reason="manager-internal-error",
            input_path=args.source,
            target=manager_contract.TargetContract(
                version="unknown",
                source="manager-internal-error",
                capabilities={},
                capability_verified=False,
            ),
        )
        result.error(
            f"internal manager failure: {exc}",
            code="MANAGER_INTERNAL_ERROR",
            phase="manager",
            root_cause="manager-internal-error",
            evidence="",
        )
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 3
    if args.runtime:
        result.execution["reason"] = "untrusted-runtime-blocked"
        result.print_summary(output_format=args.format, schema_version=args.schema_version)
        return 4
    result.print_summary(output_format=args.format, schema_version=args.schema_version)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
