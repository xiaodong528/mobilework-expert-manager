#!/usr/bin/env python3
"""Metadata-first ZIP inspection and bounded extraction for untrusted archives."""

from __future__ import annotations

import io
import math
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import manager_contract


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MOJIBAKE_MARKERS = ("\ufffd", "锟斤拷", "璧勪", "涓氬", "鏍￠", "寮傚")
MACOS_NAMES = {"__MACOSX", ".DS_Store"}


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int
    max_total_uncompressed_bytes: int
    max_entry_uncompressed_bytes: int
    max_compression_ratio: float
    max_path_characters: int
    max_path_depth: int

    def as_dict(self) -> dict[str, int | float]:
        return {
            "maxEntries": self.max_entries,
            "maxTotalUncompressedBytes": self.max_total_uncompressed_bytes,
            "maxEntryUncompressedBytes": self.max_entry_uncompressed_bytes,
            "maxCompressionRatio": self.max_compression_ratio,
            "maxPathCharacters": self.max_path_characters,
            "maxPathDepth": self.max_path_depth,
        }


@dataclass(frozen=True)
class ArchiveIssue:
    code: str
    severity: str
    message: str
    path: str
    root_cause: str


@dataclass(frozen=True)
class ArchiveInspection:
    source: Path
    limits: ArchiveLimits
    issues: tuple[ArchiveIssue, ...]
    members: tuple[str, ...]
    roots: tuple[str, ...]
    total_uncompressed_bytes: int

    @property
    def errors(self) -> tuple[ArchiveIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "error")

    @property
    def warnings(self) -> tuple[ArchiveIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "warning")


def default_limits(section: str = "archiveLimits") -> ArchiveLimits:
    raw = manager_contract.load_policy()[section]
    return ArchiveLimits(
        max_entries=int(raw["maxEntries"]),
        max_total_uncompressed_bytes=int(raw["maxTotalUncompressedBytes"]),
        max_entry_uncompressed_bytes=int(raw["maxEntryUncompressedBytes"]),
        max_compression_ratio=float(raw["maxCompressionRatio"]),
        max_path_characters=int(raw.get("maxPathCharacters", 512)),
        max_path_depth=int(raw.get("maxPathDepth", 32)),
    )


def _issue(
    code: str,
    message: str,
    path: str,
    *,
    severity: str = "error",
    root_cause: str = "unsafe-archive",
) -> ArchiveIssue:
    return ArchiveIssue(code, severity, message, path, root_cause)


def _windows_reserved(parts: tuple[str, ...]) -> str | None:
    for part in parts:
        stem = part.rstrip(". ").split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            return part
    return None


def inspect_archive(
    source: Path | bytes,
    *,
    limits: ArchiveLimits | None = None,
    require_single_root: bool = True,
    display_path: Path | None = None,
) -> ArchiveInspection:
    source_path = (
        source.expanduser().resolve()
        if isinstance(source, Path)
        else (display_path or Path("snapshot.zip"))
    )
    active_limits = limits or default_limits()
    issues: list[ArchiveIssue] = []
    members: list[str] = []
    roots: set[str] = set()
    exact: set[str] = set()
    casefolded: dict[str, str] = {}
    normalized: dict[str, str] = {}
    total = 0

    try:
        archive = zipfile.ZipFile(
            source if isinstance(source, Path) else io.BytesIO(source)
        )
    except (OSError, zipfile.BadZipFile) as exc:
        return ArchiveInspection(
            source_path,
            active_limits,
            (
                _issue(
                    "ZIP_INVALID",
                    f"cannot read ZIP metadata: {exc}",
                    source_path.name,
                    root_cause="corrupt-archive",
                ),
            ),
            (),
            (),
            0,
        )

    with archive:
        infos = archive.infolist()
        if len(infos) > active_limits.max_entries:
            issues.append(
                _issue(
                    "ZIP_ENTRY_COUNT_LIMIT",
                    f"ZIP has {len(infos)} entries; limit is {active_limits.max_entries}",
                    source_path.name,
                    root_cause="archive-resource-limit",
                )
            )
        for info in infos:
            name = info.filename
            members.append(name)
            total += info.file_size
            if len(name) > active_limits.max_path_characters:
                issues.append(_issue("ZIP_PATH_LENGTH_LIMIT", "ZIP path exceeds character limit", name, root_cause="archive-resource-limit"))
            if "\\" in name or WINDOWS_DRIVE_RE.match(name):
                issues.append(_issue("ZIP_PATH_ESCAPE", "ZIP path uses a backslash or drive prefix", name))
            path = PurePosixPath(name)
            parts = path.parts
            if path.is_absolute() or not parts or ".." in parts or "." in parts:
                issues.append(_issue("ZIP_PATH_ESCAPE", "ZIP path is absolute or traverses directories", name))
                continue
            if len(parts) > active_limits.max_path_depth:
                issues.append(_issue("ZIP_PATH_DEPTH_LIMIT", "ZIP path exceeds depth limit", name, root_cause="archive-resource-limit"))
            reserved = _windows_reserved(parts)
            if reserved is not None:
                issues.append(_issue("ZIP_WINDOWS_RESERVED_NAME", f"ZIP path contains reserved name {reserved}", name))
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                issues.append(_issue("ZIP_SYMLINK_FORBIDDEN", "ZIP symlink entry is forbidden", name))
            if info.flag_bits & 0x1:
                issues.append(_issue("ZIP_ENCRYPTED_ENTRY", "encrypted ZIP entries are unsupported", name))
            if any(part == ".git" for part in parts):
                issues.append(_issue("ZIP_GIT_METADATA_FORBIDDEN", ".git metadata must not be distributed", name, root_cause="git-metadata-leak"))
            if parts[0] in MACOS_NAMES or any(part.startswith("._") for part in parts):
                issues.append(_issue("ZIP_MACOS_METADATA_FORBIDDEN", "macOS metadata is not distributable", name, root_cause="macos-metadata"))
            elif require_single_root:
                roots.add(parts[0])
            if any(marker in name for marker in MOJIBAKE_MARKERS):
                summary = name.encode("utf-8", errors="backslashreplace").hex()[:128]
                issues.append(_issue("ZIP_FILENAME_MOJIBAKE", f"ZIP filename appears to contain mojibake; utf8Hex={summary}", name, severity="warning", root_cause="filename-encoding"))

            if name in exact:
                issues.append(_issue("ZIP_DUPLICATE_PATH", "ZIP contains duplicate paths", name))
            exact.add(name)
            folded = name.casefold()
            previous_case = casefolded.get(folded)
            if previous_case is not None and previous_case != name:
                issues.append(_issue("ZIP_CASE_COLLISION", f"ZIP path collides with {previous_case} after case folding", name))
            casefolded[folded] = name
            nfc = unicodedata.normalize("NFC", name)
            previous_nfc = normalized.get(nfc)
            if previous_nfc is not None and previous_nfc != name:
                issues.append(_issue("ZIP_UNICODE_COLLISION", f"ZIP path collides with {previous_nfc} after NFC normalization", name))
            normalized[nfc] = name

            if info.file_size > active_limits.max_entry_uncompressed_bytes:
                issues.append(_issue("ZIP_ENTRY_SIZE_LIMIT", "ZIP entry exceeds uncompressed size limit", name, root_cause="archive-resource-limit"))
            ratio = math.inf if info.compress_size == 0 and info.file_size else (
                info.file_size / info.compress_size if info.compress_size else 0.0
            )
            if ratio > active_limits.max_compression_ratio:
                issues.append(_issue("ZIP_COMPRESSION_RATIO_LIMIT", f"ZIP entry compression ratio {ratio:.1f}:1 exceeds limit", name, root_cause="archive-resource-limit"))

    if total > active_limits.max_total_uncompressed_bytes:
        issues.append(_issue("ZIP_TOTAL_SIZE_LIMIT", f"ZIP expands to {total} bytes; limit is {active_limits.max_total_uncompressed_bytes}", source_path.name, root_cause="archive-resource-limit"))
    if require_single_root and len(roots) != 1:
        issues.append(_issue("ZIP_ROOT_COUNT_INVALID", f"ZIP must contain exactly one package root; found {sorted(roots)}", source_path.name, root_cause="invalid-archive-layout"))
    return ArchiveInspection(
        source_path,
        active_limits,
        tuple(issues),
        tuple(members),
        tuple(sorted(roots)),
        total,
    )


def safe_extract(
    source: Path | bytes,
    target: Path,
    inspection: ArchiveInspection,
) -> None:
    """Extract an already-inspected archive while enforcing actual byte counts."""

    if inspection.errors:
        raise ValueError("cannot extract an archive with inspection errors")
    current = inspect_archive(
        source,
        limits=inspection.limits,
        require_single_root=bool(inspection.roots),
        display_path=inspection.source,
    )
    if current.errors or current.members != inspection.members:
        raise ValueError("archive changed or failed reinspection before extraction")
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(
        source if isinstance(source, Path) else io.BytesIO(source)
    ) as archive:
        for info in archive.infolist():
            destination = target.joinpath(*PurePosixPath(info.filename).parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as source_stream, destination.open("wb") as output:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > inspection.limits.max_entry_uncompressed_bytes:
                        raise ValueError(f"actual ZIP entry size exceeds limit: {info.filename}")
                    if total > inspection.limits.max_total_uncompressed_bytes:
                        raise ValueError("actual ZIP total size exceeds limit")
                    output.write(chunk)
