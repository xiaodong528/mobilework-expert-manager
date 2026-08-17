#!/usr/bin/env python3
"""Statically diagnose an untrusted OpenCode skill directory or ZIP."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import archive_inspector
import cli_contract
import manager_contract
import package_contract
import skill_contract
import validate_expert
from validation_result import ValidationResult


def locate_skill_root(source: Path) -> Path:
    source = source.expanduser().absolute()
    if source.is_symlink():
        raise package_contract.ContractError(
            f"skill input root must not be a symlink: {source.name}"
        )
    if (source / "SKILL.md").is_file():
        return source
    candidates = sorted(
        path
        for path in source.iterdir()
        if path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file()
    ) if source.is_dir() else []
    if len(candidates) != 1:
        raise package_contract.ContractError(
            f"skill input must contain exactly one skill root; found {len(candidates)}"
        )
    return candidates[0]


def materialize_skill(source: Path, target: Path) -> Path:
    """Copy or safely extract one skill beneath ``target`` and return its root."""

    source = source.expanduser().absolute()
    if source.is_dir():
        source_root = locate_skill_root(source)
        copied_root = target / source_root.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root, copied_root, symlinks=True)
        return copied_root
    if not source.is_file():
        raise package_contract.ContractError(f"skill source does not exist: {source}")
    if source.suffix.lower() != ".zip":
        raise package_contract.ContractError(
            f"unsupported skill source: {source.name}; expected a directory or ZIP"
        )
    inspection = archive_inspector.inspect_archive(source)
    if inspection.errors:
        raise package_contract.ContractError(inspection.errors[0].message)
    with zipfile.ZipFile(source) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise package_contract.ContractError(f"ZIP CRC failed: {bad}")
    archive_inspector.safe_extract(source, target, inspection)
    return locate_skill_root(target / inspection.roots[0])


def diagnose_root(root: Path, result: ValidationResult) -> None:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        result.error(
            f"skill directory is invalid: {root}",
            code="SKILL_DIRECTORY_INVALID",
            phase="skill",
            root_cause="invalid-skill-root",
            evidence=root.name,
        )
        result.finalize_contract()
        return
    name = root.name
    if not package_contract.NAME_RE.fullmatch(name) or len(name) > 64:
        result.error(
            f"skill directory name must be lowercase-hyphen and 64 characters or fewer: {name}",
            code="SKILL_NAME_INVALID",
            phase="skill",
            path=name,
            root_cause="invalid-skill-name",
            evidence=name,
        )
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        result.error(
            "skill root is missing SKILL.md",
            code="SKILL_MARKDOWN_MISSING",
            phase="skill",
            path="SKILL.md",
            root_cause="missing-skill-markdown",
            evidence="SKILL.md",
        )
    else:
        frontmatter = validate_expert.parse_frontmatter(
            skill_md,
            result,
            require_block_yaml=True,
        )
        if frontmatter is not None:
            skill_contract.add_skill_markdown_issues(
                result,
                skill_contract.validate_skill_frontmatter(
                    frontmatter,
                    directory_name=name,
                ),
                path="SKILL.md",
            )
        try:
            markdown = skill_md.read_text(encoding="utf-8")
            line_count = len(markdown.splitlines())
        except (OSError, UnicodeError):
            markdown = ""
            line_count = 0
        skill_contract.add_skill_markdown_issues(
            result,
            skill_contract.skill_markdown_recommendations(line_count, markdown),
            path="SKILL.md",
        )

    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        relative_base = base.relative_to(root)
        for entry in [*directories, *files]:
            path = base / entry
            relative = (relative_base / entry).as_posix()
            if path.is_symlink():
                result.error(
                    f"symlink is not allowed: {relative}",
                    code="PACKAGE_SYMLINK_FORBIDDEN",
                    phase="security",
                    path=relative,
                    root_cause="unsafe-path",
                    evidence=relative,
                )
            if entry in validate_expert.FORBIDDEN_DISTRIBUTION_DIRS:
                result.error(
                    f"non-distributable directory in skill: {relative}",
                    code="PACKAGE_NON_DISTRIBUTABLE_CONTENT",
                    phase="security",
                    path=relative,
                    root_cause="non-distributable-content",
                    evidence=relative,
                )
        for entry in files:
            path = base / entry
            relative = (relative_base / entry).as_posix()
            if entry in validate_expert.FORBIDDEN_DISTRIBUTION_FILES:
                result.error(
                    f"non-distributable file in skill: {relative}",
                    code="PACKAGE_NON_DISTRIBUTABLE_CONTENT",
                    phase="security",
                    path=relative,
                    root_cause="non-distributable-content",
                    evidence=relative,
                )
            if path.suffix in validate_expert.FORBIDDEN_DISTRIBUTION_SUFFIXES:
                result.error(
                    f"non-distributable file suffix in skill: {relative}",
                    code="PACKAGE_NON_DISTRIBUTABLE_CONTENT",
                    phase="security",
                    path=relative,
                    root_cause="non-distributable-content",
                    evidence=relative,
                )

    validate_expert.scan_secrets(root, result)
    validate_expert.scan_portability(root, result)
    validate_expert.check_static_syntax(root, result)
    try:
        resources = skill_contract.file_resources(root, package_root=root)
        result.provenance["skill"] = {
            "name": name,
            "treeSha256": skill_contract.tree_sha256(root),
            "files": resources,
        }
    except (OSError, package_contract.ContractError) as exc:
        result.error(
            f"cannot inventory skill files: {exc}",
            code="SKILL_INVENTORY_FAILED",
            phase="skill",
            root_cause="skill-inventory-failed",
            evidence="",
        )
    result.set_gate("portability", "failed" if result.errors else "passed")
    result.finalize_contract()


def diagnose(
    source: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> ValidationResult:
    source = source.expanduser().absolute()
    result = ValidationResult(
        execution_reason="untrusted-skill",
        input_path=source,
        target=target,
    )
    if result.input_inspection_error is not None:
        if result.input_inspection_error.code == "INPUT_NOT_FOUND":
            result.error(
                f"skill source does not exist: {source}",
                code="SKILL_SOURCE_MISSING",
                phase="skill",
                root_cause="missing-skill-source",
                evidence=source.name,
            )
            result.block_downstream_gates()
            result.finalize_contract()
            return result
        return result.block_input_preflight()
    snapshot = result.input_snapshot
    if snapshot is None:
        raise RuntimeError("input preflight produced neither a snapshot nor an error")
    if snapshot.kind == "directory":
        with tempfile.TemporaryDirectory(
            prefix="mobilework-skill-snapshot-diagnosis-"
        ) as temp:
            staged_source = snapshot.materialize(Path(temp) / source.name)
            try:
                root = locate_skill_root(staged_source)
            except (OSError, package_contract.ContractError) as exc:
                result.error(
                    str(exc),
                    code="SKILL_ROOT_INVALID",
                    phase="skill",
                    root_cause="invalid-skill-root",
                    evidence=source.name,
                )
                result.block_downstream_gates()
                result.finalize_contract()
                return result
            diagnose_root(root, result)
        result.execution["reason"] = "untrusted-skill-directory"
        return result
    if source.suffix.lower() != ".zip":
        result.error(
            f"unsupported skill source: {source.name}; expected a directory or ZIP",
            code="SKILL_SOURCE_UNSUPPORTED",
            phase="skill",
            root_cause="unsupported-skill-source",
            evidence=source.name,
        )
        result.block_downstream_gates()
        result.finalize_contract()
        return result
    archive_bytes = snapshot.read_bytes()
    try:
        inspection = archive_inspector.inspect_archive(
            archive_bytes,
            display_path=source,
        )
        result.provenance["inputLimits"] = result.provenance.get("limits", {})
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
            result.block_downstream_gates()
            return result
        result.set_gate("archive", "passed")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            bad = archive.testzip()
            if bad is not None:
                result.error(
                    f"ZIP CRC failed: {bad}",
                    code="ZIP_CRC_FAILED",
                    phase="archive",
                    root_cause="corrupt-archive",
                    evidence=bad,
                )
                result.block_downstream_gates()
                return result
        with tempfile.TemporaryDirectory(prefix="mobilework-skill-diagnosis-") as temp:
            extraction_root = Path(temp)
            archive_inspector.safe_extract(
                archive_bytes,
                extraction_root,
                inspection,
            )
            root = locate_skill_root(extraction_root / inspection.roots[0])
            diagnose_root(root, result)
            result.execution["reason"] = "untrusted-skill-zip"
            return result
    except (OSError, ValueError, zipfile.BadZipFile, package_contract.ContractError) as exc:
        result.error(
            f"cannot read skill ZIP: {exc}",
            code="ZIP_INVALID",
            phase="archive",
            root_cause="corrupt-archive",
            evidence=source.name,
        )
        result.block_downstream_gates()
        result.finalize_contract()
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Untrusted skill directory or ZIP")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--schema-version", choices=(1, 2), type=int, default=2)
    parser.add_argument("--target-opencode-version")
    parser.add_argument("--host-contract", type=Path)
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Request runtime execution (always blocked)",
    )
    return parser.parse_args()


def _legacy_main() -> int:
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
        result.print_summary(
            output_format=args.format,
            schema_version=args.schema_version,
        )
        return 2
    except Exception as exc:
        result = ValidationResult(
            execution_reason="manager-internal-error",
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
        result.print_summary(
            output_format=args.format,
            schema_version=args.schema_version,
        )
        return 3
    if args.runtime:
        result.execution["reason"] = "untrusted-runtime-blocked"
        result.print_summary(
            output_format=args.format,
            schema_version=args.schema_version,
        )
        return 4
    result.print_summary(output_format=args.format, schema_version=args.schema_version)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "diagnose-skill",
        _legacy_main,
        argv=argv,
        default_format="human",
        delegated_output_flags=("format", "schema-version"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
