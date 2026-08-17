#!/usr/bin/env python3
"""Import a confirmed local document source as a role-routed Reference."""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import archive_inspector
import cli_contract
import create_expert
import execution_context
import manifest_contract
import output_sanitizer
import package_contract
import safe_input
import validate_expert


class ImportReferenceError(RuntimeError):
    """Raised when a local Reference cannot be imported without guessing."""


WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
IGNORED_NAMES = frozenset({".DS_Store", "__MACOSX"})
FORBIDDEN_PARTS = frozenset({".git", "node_modules", "__pycache__"})
BINARY_SUFFIXES = frozenset(
    {
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".webp",
        ".xls",
        ".xlsx",
    }
)


def _binary_magic(data: bytes) -> str | None:
    signatures = (
        (b"%PDF-", "PDF"),
        (b"PK\x03\x04", "ZIP/OOXML"),
        (b"PK\x05\x06", "ZIP/OOXML"),
        (b"PK\x07\x08", "ZIP/OOXML"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE document"),
        (b"\x89PNG\r\n\x1a\n", "PNG image"),
        (b"\xff\xd8\xff", "JPEG image"),
        (b"GIF87a", "GIF image"),
        (b"GIF89a", "GIF image"),
        (b"BM", "BMP image"),
        (b"II*\x00", "TIFF image"),
        (b"MM\x00*", "TIFF image"),
    )
    for signature, label in signatures:
        if data.startswith(signature):
            return label
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "WebP image"
    return None


def _check_path_limits(
    relative: PurePosixPath,
    limits: archive_inspector.ArchiveLimits,
) -> None:
    value = relative.as_posix()
    if len(value) > limits.max_path_characters:
        raise ImportReferenceError(
            f"source path exceeds {limits.max_path_characters} characters: {value}"
        )
    if len(relative.parts) > limits.max_path_depth:
        raise ImportReferenceError(
            f"source path exceeds depth {limits.max_path_depth}: {value}"
        )


def load_manifest(package_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            (package_dir / create_expert.MANIFEST_FILE).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImportReferenceError(f"cannot read expert.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ImportReferenceError("expert.json must contain an object")
    return value


def assignment_ids(
    manifest: dict[str, Any],
    *,
    requested: list[str],
    all_members: bool,
) -> list[str]:
    roles = manifest_contract.manifest_roles(manifest)
    role_ids = [
        role["id"]
        for _field, role in roles
        if isinstance(role.get("id"), str)
    ]
    if not role_ids:
        raise ImportReferenceError("expert.json does not declare valid roles")
    if manifest.get("type") == "expert":
        if requested or all_members:
            raise ImportReferenceError(
                "single experts receive the Reference automatically; "
                "do not pass --assign-to or --all-members"
            )
        return role_ids
    if manifest.get("type") != "team":
        raise ImportReferenceError("expert.json type must be expert or team")
    if all_members:
        return role_ids
    if not requested:
        raise ImportReferenceError(
            "expert teams require --assign-to <agent-id> or --all-members"
        )
    duplicate = package_contract.first_duplicate(requested)
    if duplicate is not None:
        raise ImportReferenceError(f"--assign-to duplicates {duplicate}")
    unknown = sorted(set(requested) - set(role_ids))
    if unknown:
        raise ImportReferenceError(
            "--assign-to references unknown Agent IDs: " + ", ".join(unknown)
        )
    return requested


def _docx_text(path: PurePosixPath, data: bytes) -> str:
    display_path = Path(path.as_posix())
    inspection = archive_inspector.inspect_archive(
        data,
        display_path=display_path,
        require_single_root=False,
    )
    if inspection.errors:
        codes = ", ".join(sorted({item.code for item in inspection.errors}))
        raise ImportReferenceError(f"DOCX preflight failed for {path.name}: {codes}")
    members = set(inspection.members)
    active = sorted(
        member
        for member in members
        if member == "word/vbaProject.bin"
        or member.startswith("word/embeddings/")
        or member.startswith("word/externalLinks/")
    )
    if active:
        raise ImportReferenceError(
            f"DOCX contains active or external content and was not imported: {path.name}"
        )
    if "word/document.xml" not in members:
        raise ImportReferenceError(f"DOCX is missing word/document.xml: {path.name}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ImportReferenceError(f"DOCX CRC failed for {path.name}: {bad}")
        for member in sorted(members):
            if not member.endswith(".rels"):
                continue
            try:
                relationships = ElementTree.fromstring(archive.read(member))
            except ElementTree.ParseError as exc:
                raise ImportReferenceError(
                    f"DOCX relationships XML is invalid for {path.name}: {member}"
                ) from exc
            if any(
                node.attrib.get("TargetMode", "").lower() == "external"
                for node in relationships.iter()
            ):
                raise ImportReferenceError(
                    f"DOCX contains an external relationship and was not imported: {path.name}"
                )
        data = archive.read("word/document.xml")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ImportReferenceError(f"DOCX XML is invalid for {path.name}: {exc}") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{WORD_NAMESPACE}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(f"{WORD_NAMESPACE}t")
        ).strip()
        if text:
            paragraphs.append(text)
    if not paragraphs:
        raise ImportReferenceError(f"DOCX contains no readable paragraph text: {path.name}")
    return f"# {path.stem}\n\n" + "\n\n".join(paragraphs) + "\n"


def _text_content(path: PurePosixPath, data: bytes) -> tuple[str, str]:
    if path.suffix.lower() == ".docx":
        return f"{path.stem}.md", _docx_text(path, data)
    detected = _binary_magic(data[:16])
    if detected is not None:
        raise ImportReferenceError(
            f"conversion-required: {path.name} contains {detected} data"
        )
    if b"\x00" in data or path.suffix.lower() in BINARY_SUFFIXES:
        raise ImportReferenceError(
            f"conversion-required: {path.name} is binary; provide trusted Markdown/text "
            "or a supported DOCX"
        )
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportReferenceError(
            f"conversion-required: {path.name} is not UTF-8 text"
        ) from exc
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise ImportReferenceError(f"source file is empty: {path.name}")
    return path.name, content


def _inspection_failure(
    error: safe_input.InputInspectionError,
    limits: archive_inspector.ArchiveLimits,
) -> ImportReferenceError:
    path = error.path
    messages = {
        "INPUT_NOT_FOUND": "source must be an existing local file or directory",
        "INPUT_ENTRY_COUNT_LIMIT": f"source exceeds {limits.max_entries} entries",
        "INPUT_FILE_SIZE_LIMIT": (
            f"source file exceeds {limits.max_entry_uncompressed_bytes} bytes: {path}"
        ),
        "INPUT_TOTAL_SIZE_LIMIT": (
            "source files exceed total size limit "
            f"{limits.max_total_uncompressed_bytes} bytes"
        ),
        "INPUT_PATH_LENGTH_LIMIT": (
            f"source path exceeds {limits.max_path_characters} characters: {path}"
        ),
        "INPUT_PATH_DEPTH_LIMIT": (
            f"source path exceeds depth {limits.max_path_depth}: {path}"
        ),
        "INPUT_SYMLINK_FORBIDDEN": f"source contains symlink: {path}",
        "INPUT_REPARSE_POINT_FORBIDDEN": (
            f"source contains a Windows reparse point: {path}"
        ),
        "INPUT_SPECIAL_FILE_FORBIDDEN": (
            f"source contains a non-regular file: {path}"
        ),
    }
    return ImportReferenceError(messages.get(error.code, str(error)))


def _inspect_source(
    source: Path,
    limits: archive_inspector.ArchiveLimits,
) -> safe_input.InputSnapshot:
    try:
        return safe_input.inspect(source, limits)
    except safe_input.InputInspectionError as exc:
        raise _inspection_failure(exc, limits) from exc


def collect_source(
    source: Path | safe_input.InputSnapshot,
    *,
    limits: archive_inspector.ArchiveLimits | None = None,
) -> list[tuple[PurePosixPath, str]]:
    active_limits = archive_inspector.default_limits() if limits is None else limits
    snapshot = (
        source
        if isinstance(source, safe_input.InputSnapshot)
        else _inspect_source(source, active_limits)
    )
    collected: list[tuple[PurePosixPath, str]] = []
    targets: set[str] = set()
    output_bytes = 0
    for directory in snapshot.directories:
        relative = PurePosixPath(directory)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            raise ImportReferenceError(
                f"source contains forbidden directory: {relative.as_posix()}"
            )
    for item in snapshot.files:
        relative = PurePosixPath(item.relative_path)
        if any(part in IGNORED_NAMES for part in relative.parts):
            continue
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            raise ImportReferenceError(f"unsafe source path: {relative.as_posix()}")
        output_name, content = _text_content(relative, item.content)
        target = relative.with_name(output_name)
        _check_path_limits(target, active_limits)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > active_limits.max_entry_uncompressed_bytes:
            raise ImportReferenceError(
                "converted source exceeds "
                f"{active_limits.max_entry_uncompressed_bytes} bytes: "
                f"{target.as_posix()}"
            )
        output_bytes += content_bytes
        if output_bytes > active_limits.max_total_uncompressed_bytes:
            raise ImportReferenceError(
                "converted sources exceed total size limit "
                f"{active_limits.max_total_uncompressed_bytes} bytes"
            )
        key = target.as_posix()
        if key in targets:
            raise ImportReferenceError(f"converted source path collides: {key}")
        targets.add(key)
        collected.append((target, content))
    if not collected:
        raise ImportReferenceError("source contains no importable files")
    return collected


def _update_role_bindings(
    manifest: dict[str, Any],
    *,
    alias: str,
    assigned_to: list[str],
    had_references: bool,
) -> None:
    roles = manifest_contract.manifest_roles(manifest)
    explicit = ["references" in role for _field, role in roles]
    if had_references and not any(explicit):
        raise ImportReferenceError(
            "existing References have no role bindings; generate a migration preview "
            "and confirm every Reference consumer before importing"
        )
    if any(explicit) and not all(explicit):
        raise ImportReferenceError("existing role Reference bindings are incomplete")
    selected = set(assigned_to)
    for field, role in roles:
        try:
            values = package_contract.normalize_role_aliases(
                role.get("references", []), f"{field}.references"
            )
        except package_contract.ContractError as exc:
            raise ImportReferenceError(str(exc)) from exc
        values = [item for item in values if item != alias]
        if role.get("id") in selected:
            values.append(alias)
        role["references"] = values


def import_reference(
    package_dir: Path,
    source: Path,
    *,
    alias: str,
    description: str,
    assign_to: list[str],
    all_members: bool,
    hidden: bool,
    replace: bool,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ImportReferenceError(
            "--confirm is required after the user approves the source, role scope, and copy"
        )
    if not package_contract.NAME_RE.fullmatch(alias):
        raise ImportReferenceError("--alias must use lowercase kebab-case")
    if not description.strip():
        raise ImportReferenceError("--description must explain when the Reference is used")
    package_dir = execution_context.canonical_path(package_dir)
    source = source.expanduser().absolute()
    resolved_source = source.resolve(strict=False)
    source_limits = archive_inspector.default_limits()
    source_snapshot = _inspect_source(source, source_limits)
    if execution_context.is_within(resolved_source, package_dir):
        raise ImportReferenceError("source must remain outside the target expert package")
    validation = validate_expert.validate_package(package_dir)
    if not validation.ok:
        raise ImportReferenceError(
            "target expert package is invalid: " + "; ".join(validation.errors[:8])
        )
    initial_revision = create_expert.calculate_package_revision(package_dir)
    collected = collect_source(source_snapshot, limits=source_limits)

    with tempfile.TemporaryDirectory(prefix="mobilework-reference-import-") as temp:
        temp_package = Path(temp) / package_dir.name
        shutil.copytree(package_dir, temp_package, ignore=shutil.ignore_patterns(".git"))
        manifest = load_manifest(temp_package)
        assigned = assignment_ids(
            manifest,
            requested=assign_to,
            all_members=all_members,
        )
        runtime = manifest.setdefault("runtime_extensions", {})
        if not isinstance(runtime, dict):
            raise ImportReferenceError("runtime_extensions must be an object")
        references = runtime.setdefault("references", {})
        reference_files = runtime.setdefault("reference_files", [])
        if not isinstance(references, dict) or not isinstance(reference_files, list):
            raise ImportReferenceError("existing Reference declarations are invalid")
        existed = alias in references
        if existed and not replace:
            raise ImportReferenceError(
                f"Reference {alias} already exists; use --replace after explicit confirmation"
            )
        prefix = f".opencode/references/{manifest.get('slug')}/{alias}/"
        reference_files[:] = [
            item
            for item in reference_files
            if not (isinstance(item, dict) and str(item.get("path", "")).startswith(prefix))
        ]
        for relative, content in collected:
            reference_files.append(
                {"path": prefix + relative.as_posix(), "content": content}
            )
        references[alias] = {
            "path": prefix.rstrip("/"),
            "description": description.strip(),
            "hidden": hidden,
        }
        _update_role_bindings(
            manifest,
            alias=alias,
            assigned_to=assigned,
            had_references=bool(set(references) - {alias}),
        )
        (temp_package / create_expert.MANIFEST_FILE).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            normalized = create_expert.normalize_manifest(
                manifest,
                manifest_dir=temp_package,
            )
            create_expert.prepare_avatar_assets(normalized, temp_package)
            with create_expert.package_lock(package_dir.parent, manifest["slug"]):
                if not package_dir.is_dir():
                    raise ImportReferenceError(
                        "target expert package disappeared during import"
                    )
                if create_expert.calculate_package_revision(package_dir) != initial_revision:
                    raise ImportReferenceError(
                        "target expert package changed during import; retry from the new revision"
                    )
                written = create_expert._write_project_locked(
                    normalized,
                    package_dir.parent,
                    force=True,
                )
        except SystemExit as exc:
            raise ImportReferenceError(str(exc)) from exc
    final = validate_expert.validate_package(written)
    if not final.ok:
        raise ImportReferenceError(
            "committed package failed validation: " + "; ".join(final.errors[:8])
        )
    return {
        "status": "package-valid",
        "runtimeStatus": "runtime-not-tested",
        "action": "replaced" if existed else "imported",
        "package": str(written),
        "reference": alias,
        "assignedTo": assigned,
        "files": [relative.as_posix() for relative, _content in collected],
        "sourceExecution": "not-attempted",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--description", required=True)
    assignment = parser.add_mutually_exclusive_group()
    assignment.add_argument("--assign-to", action="append", default=[])
    assignment.add_argument("--all-members", action="store_true")
    parser.add_argument("--hidden", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def _legacy_main() -> int:
    args = parse_args()
    try:
        result = import_reference(
            args.package_dir,
            args.source,
            alias=args.alias,
            description=args.description,
            assign_to=args.assign_to,
            all_members=args.all_members,
            hidden=args.hidden,
            replace=args.replace,
            confirmed=args.confirm,
        )
    except (ImportReferenceError, package_contract.ContractError) as exc:
        print(f"error: {output_sanitizer.sanitize_exception(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "error: internal manager failure: "
            + output_sanitizer.sanitize_exception(exc),
            file=sys.stderr,
        )
        return 3
    print(output_sanitizer.json_dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return cli_contract.run_legacy_entrypoint(
        "import-reference", _legacy_main, argv=argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
