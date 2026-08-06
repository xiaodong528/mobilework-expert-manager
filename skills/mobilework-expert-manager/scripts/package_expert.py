#!/usr/bin/env python3
"""Create and fully verify a distributable MobileWork expert zip."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import package_contract as contract
import archive_inspector
import cli_contract
import output_sanitizer
import package_snapshot
import safe_input
import scan_portable_artifacts
import validate_expert


EXCLUDED_DIRS = {
    ".cache",
    ".git",
    ".serena",
    ".venv",
    "__" + "pycache__",
    "node_modules",
    "venv",
}
EXCLUDED_FILES = {".DS_Store", ".env"}
EXCLUDED_SUFFIXES = {".log", ".py" + "c", ".pyo"}


def fail(message: str) -> None:
    raise SystemExit(f"error: {output_sanitizer.sanitize_text(message)}")


def should_skip(path: Path, package_dir: Path) -> bool:
    return should_skip_relative(path.relative_to(package_dir).as_posix())


def should_skip_relative(relative: str) -> bool:
    rel = Path(relative)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return True
    if rel.name in EXCLUDED_FILES:
        return True
    return rel.suffix in EXCLUDED_SUFFIXES


def _snapshot(package: Path | safe_input.InputSnapshot) -> safe_input.InputSnapshot:
    return (
        package
        if isinstance(package, safe_input.InputSnapshot)
        else package_snapshot.inspect_directory(package)
    )


def package_slug(package: Path | safe_input.InputSnapshot) -> str:
    try:
        manifest = package_snapshot.load_json(
            _snapshot(package), validate_expert.MANIFEST_FILE
        )
    except (safe_input.InputInspectionError, ValueError) as exc:
        fail(f"cannot read expert.json: {exc}")
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not slug:
        fail("expert.json slug is required")
    return slug


def write_zip(
    package: Path | safe_input.InputSnapshot,
    zip_path: Path,
    slug: str,
) -> None:
    try:
        snapshot = _snapshot(package)
        manifest = package_snapshot.load_json(
            snapshot, validate_expert.MANIFEST_FILE
        )
        declared_files = contract.declared_package_files(manifest)
    except (contract.ContractError, safe_input.InputInspectionError, ValueError) as exc:
        fail(str(exc))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        skills_relative = (
            f"{contract.PACKAGE_RUNTIME_DIR}/{contract.SKILLS_SUBDIR}"
        )
        if skills_relative in snapshot.directories and not any(
            item.relative_path.startswith(skills_relative + "/")
            for item in snapshot.files
        ):
            archive.writestr(
                f"{slug}/{skills_relative}/",
                b"",
            )
        for item in snapshot.files:
            relative = item.relative_path
            if should_skip_relative(relative):
                continue
            if not contract.is_allowed_package_path(Path(relative)):
                fail(f"path is outside the package allowlist: {relative}")
            if relative not in declared_files:
                fail(f"path is not declared by expert.json: {relative}")
            archive.writestr(f"{slug}/{relative}", item.content)


def test_zip_python(zip_path: Path, slug: str) -> None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                fail(f"zip CRC check failed: {bad_file}")
            roots = {Path(name).parts[0] for name in archive.namelist() if Path(name).parts}
            if roots != {slug}:
                fail(f"zip must contain exactly one top-level {slug}/ directory")
    except (OSError, zipfile.BadZipFile) as exc:
        fail(f"zip integrity test failed: {exc}")


def test_zip_external(zip_path: Path) -> None:
    try:
        proc = subprocess.run(["unzip", "-t", str(zip_path)], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        fail("unzip is required unless --skip-unzip-test is used")
    if proc.returncode != 0:
        print(output_sanitizer.sanitize_text(proc.stdout or ""), end="")
        print(
            output_sanitizer.sanitize_text(proc.stderr or ""),
            end="",
            file=sys.stderr,
        )
        fail(f"zip integrity test failed: {zip_path}")


def verify_extracted_package(zip_path: Path, output_dir: Path, slug: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f".{slug}-extract-", dir=output_dir) as temp_dir:
        extract_root = Path(temp_dir)
        inspection = archive_inspector.inspect_archive(zip_path)
        if inspection.errors:
            fail(
                "zip preflight failed: "
                + ", ".join(sorted({item.code for item in inspection.errors}))
            )
        archive_inspector.safe_extract(zip_path, extract_root, inspection)
        extracted = extract_root / slug
        result = validate_expert.validate_package(extracted)
        if not result.ok:
            result.print_summary()
            fail("extracted package failed validation")
        findings = scan_portable_artifacts.scan_root(extracted)
        errors = [item for item in findings if item.get("severity", "error") == "error"]
        if errors:
            print(
                output_sanitizer.json_dumps(
                    {"ok": False, "findings": findings},
                    indent=2,
                )
            )
            fail("extracted package failed portability scan")


def make_zip(
    package_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
    run_external_test: bool = True,
    input_snapshot: safe_input.InputSnapshot | None = None,
) -> Path:
    package_dir = package_dir.expanduser().absolute()
    output_dir = output_dir.expanduser().resolve()
    if input_snapshot is None:
        snapshot, validation = package_snapshot.inspect_and_validate(package_dir)
    else:
        snapshot = input_snapshot
        validation = package_snapshot.validate_snapshot(snapshot)
    if snapshot is None or not validation.ok:
        fail("package validation failed: " + "; ".join(validation.errors[:8]))
    slug = package_slug(snapshot)
    zip_path = output_dir / f"{slug}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    if zip_path.exists() and not force:
        fail(f"target zip already exists; rerun with --force to replace it: {zip_path}")

    handle, temporary_name = tempfile.mkstemp(prefix=f".{slug}-", suffix=".zip", dir=output_dir)
    os.close(handle)
    temporary_zip = Path(temporary_name)
    try:
        write_zip(snapshot, temporary_zip, slug)
        test_zip_python(temporary_zip, slug)
        if run_external_test:
            test_zip_external(temporary_zip)
        verify_extracted_package(temporary_zip, output_dir, slug)
        if zip_path.exists() and not force:
            fail(f"target zip appeared during packaging; rerun with --force: {zip_path}")
        os.replace(temporary_zip, zip_path)
    finally:
        temporary_zip.unlink(missing_ok=True)
    return zip_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path, help="Expert package directory")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for the zip output")
    parser.add_argument("--force", action="store_true", help="Replace an existing <slug>.zip after verification")
    parser.add_argument(
        "--skip-unzip-test",
        action="store_true",
        help="Skip external unzip -t only; Python and extracted-package verification still run",
    )
    return parser.parse_args()


def _legacy_main() -> int:
    args = parse_args()
    try:
        package_dir = args.package_dir.expanduser().absolute()
        snapshot, result = package_snapshot.inspect_and_validate(package_dir)
        if not result.ok:
            result.print_summary()
            return 1
        if snapshot is None:
            fail("package snapshot is unavailable after successful validation")
        zip_path = make_zip(
            package_dir,
            args.output_dir,
            force=args.force,
            run_external_test=not args.skip_unzip_test,
            input_snapshot=snapshot,
        )
        print(
            output_sanitizer.json_dumps(
                {"ok": True, "zip_path": str(zip_path)},
                indent=2,
            )
        )
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(
            "error: internal manager failure: "
            + output_sanitizer.sanitize_exception(exc),
            file=sys.stderr,
        )
        return 3


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "package-expert", _legacy_main, argv=argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
