#!/usr/bin/env python3
"""Manifest-driven creation and static validation for MobileWork expert bundles."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import archive_inspector
import diagnose_expert
import manager_contract
import provenance


MANIFEST_NAME = "bundle-manifest.json"
SUMMARY_NAME = "bundle-summary.md"
CONTROL_PREFIX = "MOBILEWORK_BUNDLE_FIELD "


class BundleContractError(ValueError):
    pass


def _hash(path: Path) -> str:
    return provenance.file_sha256(path)


def _read_package_identity(path: Path) -> tuple[str, str]:
    inspection = archive_inspector.inspect_archive(path)
    if inspection.errors:
        raise BundleContractError(
            f"package ZIP preflight failed for {path.name}: "
            + ", ".join(sorted({item.code for item in inspection.errors}))
        )
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise BundleContractError(f"package ZIP CRC failed for {path.name}: {bad}")
    with tempfile.TemporaryDirectory(prefix="mobilework-bundle-identity-") as temp:
        root = Path(temp)
        archive_inspector.safe_extract(path, root, inspection)
        manifest_path = root / inspection.roots[0] / "expert.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BundleContractError(f"cannot read {path.name} expert.json: {exc}") from exc
    slug = manifest.get("slug")
    version = manifest.get("version")
    if not isinstance(slug, str) or not slug:
        raise BundleContractError(f"{path.name} expert.json slug is required")
    if not isinstance(version, str) or not version:
        version = "unreleased"
    return slug, version


def controlled_fields(manifest: dict[str, Any]) -> dict[str, str]:
    tests = manifest.get("tests", {})
    packages = manifest.get("packages", [])
    return {
        "schemaVersion": str(manifest.get("schemaVersion", "")),
        "contractVersion": str(manifest.get("contractVersion", "")),
        "packageCount": str(len(packages) if isinstance(packages, list) else 0),
        "testsCollected": str(tests.get("collected", "")) if isinstance(tests, dict) else "",
        "testsPassed": str(tests.get("passed", "")) if isinstance(tests, dict) else "",
        "testsFailed": str(tests.get("failed", "")) if isinstance(tests, dict) else "",
        "testsSkipped": str(tests.get("skipped", "")) if isinstance(tests, dict) else "",
    }


def render_summary(manifest: dict[str, Any]) -> str:
    fields = controlled_fields(manifest)
    lines = ["# MobileWork expert bundle", "", "## Controlled fields", ""]
    lines.extend(f"{CONTROL_PREFIX}{key}={value}" for key, value in fields.items())
    lines.extend(["", "## Packages", ""])
    for package in manifest.get("packages", []):
        lines.append(f"- `{package['slug']}` `{package['version']}` — `{package['file']}`")
    return "\n".join(lines) + "\n"


def create_manifest(
    bundle_dir: Path,
    package_zips: list[Path],
    *,
    tests: dict[str, int] | None = None,
    source_repository: str | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.expanduser().resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    packages: list[dict[str, str]] = []
    names: set[str] = set()
    slugs: set[str] = set()
    for source in package_zips:
        source = source.expanduser().resolve()
        if source.name in names:
            raise BundleContractError(f"duplicate bundle package filename: {source.name}")
        slug, version = _read_package_identity(source)
        if slug in slugs:
            raise BundleContractError(f"duplicate bundle package slug: {slug}")
        names.add(source.name)
        slugs.add(slug)
        target = bundle_dir / source.name
        if target.resolve() != source:
            if target.exists():
                raise BundleContractError(f"bundle target already exists: {target}")
            target.write_bytes(source.read_bytes())
        packages.append({
            "file": source.name,
            "slug": slug,
            "version": version,
            "sha256": _hash(target),
        })
    policy = manager_contract.load_policy()
    test_summary = {"collected": 0, "passed": 0, "failed": 0, "skipped": 0}
    test_summary.update(tests or {})
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "contractVersion": policy["contractVersion"],
        "generatorVersion": provenance.tree_sha256(Path(__file__).resolve().parents[1]),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
        "tests": test_summary,
        "documents": {"markdown": SUMMARY_NAME, "docx": None},
    }
    if source_repository:
        manifest["sourceRepository"] = source_repository
    if source_commit:
        manifest["sourceCommit"] = source_commit
    (bundle_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (bundle_dir / SUMMARY_NAME).write_text(render_summary(manifest), encoding="utf-8")
    return manifest


def _parse_controlled_text(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if CONTROL_PREFIX not in line:
            continue
        value = line.split(CONTROL_PREFIX, 1)[1]
        key, separator, field_value = value.partition("=")
        if separator and key:
            fields[key.strip()] = field_value.strip()
    return fields


def _docx_text(path: Path) -> str:
    inspection = archive_inspector.inspect_archive(path, require_single_root=False)
    if inspection.errors:
        raise BundleContractError("DOCX preflight failed: " + ", ".join(sorted({item.code for item in inspection.errors})))
    if "word/document.xml" not in inspection.members:
        raise BundleContractError("DOCX is missing word/document.xml")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise BundleContractError(f"DOCX CRC failed: {bad}")
        data = archive.read("word/document.xml")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise BundleContractError(f"DOCX document.xml is invalid: {exc}") from exc
    return "\n".join(text for text in root.itertext() if text)


def _document_findings(bundle_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    expected = controlled_fields(manifest)
    documents = manifest.get("documents", {})
    findings: list[dict[str, str]] = []
    if not isinstance(documents, dict):
        return [{"code": "BUNDLE_DOCUMENTS_INVALID", "message": "documents must be an object"}]
    for kind in ("markdown", "docx"):
        relative = documents.get(kind)
        if relative is None:
            continue
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            findings.append({"code": "BUNDLE_DOCUMENT_PATH_INVALID", "message": f"invalid {kind} path"})
            continue
        path = bundle_dir / relative
        try:
            text = path.read_text(encoding="utf-8") if kind == "markdown" else _docx_text(path)
        except (OSError, UnicodeDecodeError, BundleContractError) as exc:
            findings.append({"code": "BUNDLE_DOCUMENT_UNREADABLE", "message": f"cannot read {relative}: {exc}"})
            continue
        actual = _parse_controlled_text(text)
        for key, value in expected.items():
            if actual.get(key) != value:
                findings.append({
                    "code": "BUNDLE_DOCUMENT_FIELD_DRIFT",
                    "message": f"{relative} controlled field {key} expected {value!r}, found {actual.get(key)!r}",
                })
    return findings


def validate_bundle(bundle_dir: Path) -> dict[str, Any]:
    bundle_dir = bundle_dir.expanduser().resolve()
    try:
        manifest = json.loads((bundle_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleContractError(f"cannot read bundle manifest: {exc}") from exc
    findings: list[dict[str, str]] = []
    policy = manager_contract.load_policy()
    if manifest.get("schemaVersion") != 1:
        findings.append({"code": "BUNDLE_SCHEMA_VERSION_INVALID", "message": "schemaVersion must be 1"})
    if manifest.get("contractVersion") != policy["contractVersion"]:
        findings.append({"code": "BUNDLE_CONTRACT_VERSION_MISMATCH", "message": "contractVersion does not match the manager contract"})
    if not isinstance(manifest.get("generatorVersion"), str) or not manifest.get("generatorVersion"):
        findings.append({"code": "BUNDLE_GENERATOR_VERSION_MISSING", "message": "generatorVersion is required"})
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise BundleContractError("bundle packages must be a list")
    declared_files: set[str] = set()
    declared_slugs: set[str] = set()
    for index, item in enumerate(packages):
        if not isinstance(item, dict):
            findings.append({"code": "BUNDLE_PACKAGE_ENTRY_INVALID", "message": f"packages[{index}] must be an object"})
            continue
        relative = item.get("file")
        if not isinstance(relative, str) or Path(relative).name != relative:
            findings.append({"code": "BUNDLE_PACKAGE_PATH_INVALID", "message": f"invalid package path at index {index}"})
            continue
        if relative in declared_files:
            findings.append({"code": "BUNDLE_PACKAGE_FILE_DUPLICATE", "message": f"duplicate package file {relative}"})
            continue
        declared_files.add(relative)
        declared_slug = item.get("slug")
        if not isinstance(declared_slug, str) or not declared_slug:
            findings.append({"code": "BUNDLE_PACKAGE_SLUG_INVALID", "message": f"invalid slug at index {index}"})
        elif declared_slug in declared_slugs:
            findings.append({"code": "BUNDLE_PACKAGE_SLUG_DUPLICATE", "message": f"duplicate package slug {declared_slug}"})
        else:
            declared_slugs.add(declared_slug)
        path = bundle_dir / relative
        if not path.is_file():
            findings.append({"code": "BUNDLE_PACKAGE_MISSING", "message": f"missing package ZIP {relative}"})
            continue
        actual_hash = _hash(path)
        if actual_hash != item.get("sha256"):
            findings.append({"code": "BUNDLE_PACKAGE_HASH_MISMATCH", "message": f"SHA-256 mismatch for {relative}"})
            continue
        result = diagnose_expert.diagnose(path)
        if not result.ok:
            findings.append({"code": "BUNDLE_PACKAGE_INVALID", "message": f"static validation failed for {relative}"})
            continue
        try:
            actual_slug, actual_version = _read_package_identity(path)
        except BundleContractError as exc:
            findings.append({"code": "BUNDLE_PACKAGE_IDENTITY_UNREADABLE", "message": f"cannot read identity for {relative}: {exc}"})
            continue
        if actual_slug != item.get("slug"):
            findings.append({"code": "BUNDLE_PACKAGE_SLUG_MISMATCH", "message": f"slug mismatch for {relative}"})
        if actual_version != item.get("version"):
            findings.append({"code": "BUNDLE_PACKAGE_VERSION_MISMATCH", "message": f"version mismatch for {relative}"})
    actual_files = {path.name for path in bundle_dir.glob("*.zip") if path.is_file()}
    for extra in sorted(actual_files - declared_files):
        findings.append({"code": "BUNDLE_UNDECLARED_PACKAGE_ZIP", "message": f"undeclared package ZIP {extra}"})
    findings.extend(_document_findings(bundle_dir, manifest))
    tests = manifest.get("tests", {})
    if not isinstance(tests, dict) or any(not isinstance(tests.get(key), int) or tests.get(key) < 0 for key in ("collected", "passed", "failed", "skipped")):
        findings.append({"code": "BUNDLE_TEST_SUMMARY_INVALID", "message": "test summary must contain non-negative integer counts"})
    elif tests["passed"] + tests["failed"] + tests["skipped"] != tests["collected"]:
        findings.append({"code": "BUNDLE_TEST_SUMMARY_MISMATCH", "message": "test counts do not add up to collected"})
    return {"ok": not findings, "schemaVersion": 1, "packageCount": len(packages), "findings": findings}
