#!/usr/bin/env python3
"""Metadata-first, bounded snapshots for untrusted files and directories."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

import manager_contract


READ_CHUNK_BYTES = 1024 * 1024
REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InputInspectionError(ValueError):
    """Raised when an input cannot be snapshotted without crossing a safety gate."""

    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.message = message
        self.path = path
        suffix = f" [{path}]" if path else ""
        super().__init__(f"{code}: {message}{suffix}")


class InputLimitValues(Protocol):
    max_entries: int
    max_total_uncompressed_bytes: int
    max_entry_uncompressed_bytes: int
    max_path_characters: int
    max_path_depth: int


@dataclass(frozen=True)
class InputLimits:
    max_entries: int
    max_total_bytes: int
    max_file_bytes: int
    max_path_characters: int
    max_path_depth: int

    def as_dict(self) -> dict[str, int]:
        return {
            "maxEntries": self.max_entries,
            "maxTotalBytes": self.max_total_bytes,
            "maxFileBytes": self.max_file_bytes,
            "maxPathCharacters": self.max_path_characters,
            "maxPathDepth": self.max_path_depth,
        }


@dataclass(frozen=True)
class InputExclusions:
    root_directory_names: frozenset[str] = frozenset()
    directory_names: frozenset[str] = frozenset()
    file_names: frozenset[str] = frozenset()
    file_suffixes: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "rootDirectoryNames": sorted(self.root_directory_names),
            "directoryNames": sorted(self.directory_names),
            "fileNames": sorted(self.file_names),
            "fileSuffixes": sorted(self.file_suffixes),
        }


@dataclass(frozen=True)
class InputFile:
    relative_path: str
    size: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    file_attributes: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class ExcludedInputEntry:
    relative_path: str
    kind: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    file_attributes: int


@dataclass(frozen=True)
class InputSnapshot:
    source: Path
    kind: str
    files: tuple[InputFile, ...]
    directories: tuple[str, ...]
    excluded_entries: tuple[ExcludedInputEntry, ...]
    sha256: str
    total_bytes: int
    entry_count: int
    limits: InputLimits

    @property
    def file_count(self) -> int:
        return len(self.files)

    def file(self, relative_path: str) -> InputFile:
        for item in self.files:
            if item.relative_path == relative_path:
                return item
        raise KeyError(relative_path)

    def read_bytes(self, relative_path: str | None = None) -> bytes:
        if relative_path is None:
            if self.kind != "file" or len(self.files) != 1:
                raise ValueError("relative_path is required for a directory snapshot")
            return self.files[0].content
        return self.file(relative_path).content

    def read_text(
        self,
        relative_path: str | None = None,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
    ) -> str:
        return self.read_bytes(relative_path).decode(encoding, errors)

    def materialize(self, target: Path) -> Path:
        """Write this immutable snapshot to a new trusted staging path."""

        if target.exists() or target.is_symlink():
            raise FileExistsError(f"snapshot target already exists: {target}")
        if self.kind == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read_bytes())
            return target
        target.mkdir(parents=True)
        for relative_path in self.directories:
            target.joinpath(*Path(relative_path).parts).mkdir(
                parents=True,
                exist_ok=True,
            )
        for item in self.files:
            destination = target.joinpath(*Path(item.relative_path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.content)
        return target


@dataclass(frozen=True)
class _Entry:
    path: Path
    relative_path: str
    kind: str
    identity: tuple[int, int, int, int, int, int, int, int]

    @property
    def size(self) -> int:
        return self.identity[3]


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _mapping_value(
    values: Mapping[str, object],
    *names: str,
) -> object:
    for name in names:
        if name in values:
            return values[name]
    raise ValueError(f"input limits are missing {names[0]}")


def _coerce_limits(
    limits: InputLimits | Mapping[str, object] | InputLimitValues,
) -> InputLimits:
    if isinstance(limits, InputLimits):
        return limits
    if isinstance(limits, Mapping):
        return InputLimits(
            max_entries=_positive_integer(
                _mapping_value(limits, "maxEntries", "max_entries"), "maxEntries"
            ),
            max_total_bytes=_positive_integer(
                _mapping_value(
                    limits,
                    "maxTotalBytes",
                    "maxTotalUncompressedBytes",
                    "max_total_bytes",
                    "max_total_uncompressed_bytes",
                ),
                "maxTotalBytes",
            ),
            max_file_bytes=_positive_integer(
                _mapping_value(
                    limits,
                    "maxFileBytes",
                    "maxEntryUncompressedBytes",
                    "max_file_bytes",
                    "max_entry_uncompressed_bytes",
                ),
                "maxFileBytes",
            ),
            max_path_characters=_positive_integer(
                _mapping_value(
                    limits, "maxPathCharacters", "max_path_characters"
                ),
                "maxPathCharacters",
            ),
            max_path_depth=_positive_integer(
                _mapping_value(limits, "maxPathDepth", "max_path_depth"),
                "maxPathDepth",
            ),
        )
    return InputLimits(
        max_entries=_positive_integer(limits.max_entries, "maxEntries"),
        max_total_bytes=_positive_integer(
            limits.max_total_uncompressed_bytes, "maxTotalBytes"
        ),
        max_file_bytes=_positive_integer(
            limits.max_entry_uncompressed_bytes, "maxFileBytes"
        ),
        max_path_characters=_positive_integer(
            limits.max_path_characters, "maxPathCharacters"
        ),
        max_path_depth=_positive_integer(limits.max_path_depth, "maxPathDepth"),
    )


def default_limits() -> InputLimits:
    policy = manager_contract.load_policy()
    raw = policy.get("inputLimits", policy["archiveLimits"])
    if not isinstance(raw, dict):
        raise manager_contract.ManagerContractError(
            "manager contract input limits must be an object"
        )
    return _coerce_limits(raw)


def _contract_name_set(value: object, field: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise manager_contract.ManagerContractError(
            f"manager contract {field} must be an array"
        )
    names: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or item in {".", ".."}
            or "/" in item
            or "\\" in item
        ):
            raise manager_contract.ManagerContractError(
                f"manager contract {field} must contain filesystem names"
            )
        names.append(item)
    if len(names) != len(set(names)):
        raise manager_contract.ManagerContractError(
            f"manager contract {field} must not contain duplicates"
        )
    return frozenset(names)


def _contract_suffix_set(value: object, field: str) -> frozenset[str]:
    suffixes = _contract_name_set(value, field)
    if any(not suffix.startswith(".") for suffix in suffixes):
        raise manager_contract.ManagerContractError(
            f"manager contract {field} must contain dot-prefixed suffixes"
        )
    return suffixes


def default_exclusions() -> InputExclusions:
    policy = manager_contract.load_policy()
    raw = policy.get("packageSnapshotExclusions", {})
    if not isinstance(raw, dict):
        raise manager_contract.ManagerContractError(
            "manager contract package snapshot exclusions must be an object"
        )
    known_fields = {
        "rootDirectoryNames",
        "directoryNames",
        "fileNames",
        "fileSuffixes",
    }
    unknown_fields = sorted(set(raw) - known_fields)
    if unknown_fields:
        raise manager_contract.ManagerContractError(
            "manager contract package snapshot exclusions contain unknown fields: "
            + ", ".join(unknown_fields)
        )
    return InputExclusions(
        root_directory_names=_contract_name_set(
            raw.get("rootDirectoryNames", []),
            "packageSnapshotExclusions.rootDirectoryNames",
        ),
        directory_names=_contract_name_set(
            raw.get("directoryNames", []),
            "packageSnapshotExclusions.directoryNames",
        ),
        file_names=_contract_name_set(
            raw.get("fileNames", []),
            "packageSnapshotExclusions.fileNames",
        ),
        file_suffixes=_contract_suffix_set(
            raw.get("fileSuffixes", []),
            "packageSnapshotExclusions.fileSuffixes",
        ),
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _mtime_ns(info: os.stat_result) -> int:
    value = getattr(info, "st_mtime_ns", None)
    return int(value if value is not None else info.st_mtime * 1_000_000_000)


def _ctime_ns(info: os.stat_result) -> int:
    value = getattr(info, "st_ctime_ns", None)
    return int(value if value is not None else info.st_ctime * 1_000_000_000)


def _file_attributes(info: os.stat_result) -> int:
    return int(getattr(info, "st_file_attributes", 0) or 0)


def _security_file_attributes(value: int) -> int:
    return value & REPARSE_POINT_ATTRIBUTE


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        stat.S_IFMT(info.st_mode),
        int(info.st_size),
        _mtime_ns(info),
        _ctime_ns(info),
        _file_attributes(info),
        stat.S_IMODE(info.st_mode),
    )


def _identity_matches(
    expected: tuple[int, int, int, int, int, int, int, int],
    actual: tuple[int, int, int, int, int, int, int, int],
) -> bool:
    if os.name != "nt":
        return expected == actual
    # CPython on Windows reports creation time through lstat().st_ctime_ns,
    # but fstat().st_ctime_ns for the same handle may equal st_mtime_ns. Keep
    # checking identity, type, size, mtime, reparse attributes, and mode.
    return (
        expected[:5] == actual[:5]
        and _security_file_attributes(expected[6])
        == _security_file_attributes(actual[6])
        and expected[7] == actual[7]
    )


def _display_path(source: Path, relative_path: str) -> str:
    return relative_path or source.name or "."


def _lstat(path: Path, display_path: str, *, initial: bool = False) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        code = "INPUT_NOT_FOUND" if initial else "INPUT_CHANGED_DURING_SCAN"
        message = "input does not exist" if initial else "input disappeared during scan"
        raise InputInspectionError(code, message, display_path) from exc
    except OSError as exc:
        raise InputInspectionError(
            "INPUT_METADATA_UNAVAILABLE",
            f"cannot inspect input metadata: {exc}",
            display_path,
        ) from exc


def _kind(info: os.stat_result, display_path: str) -> str:
    if _file_attributes(info) & REPARSE_POINT_ATTRIBUTE:
        raise InputInspectionError(
            "INPUT_REPARSE_POINT_FORBIDDEN",
            "Windows reparse points are not allowed",
            display_path,
        )
    mode = info.st_mode
    if stat.S_ISLNK(mode):
        raise InputInspectionError(
            "INPUT_SYMLINK_FORBIDDEN", "symlink is not allowed", display_path
        )
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISFIFO(mode):
        special_type = "FIFO"
    elif stat.S_ISSOCK(mode):
        special_type = "socket"
    elif stat.S_ISCHR(mode):
        special_type = "character device"
    elif stat.S_ISBLK(mode):
        special_type = "block device"
    else:
        special_type = "unknown filesystem object"
    raise InputInspectionError(
        "INPUT_SPECIAL_FILE_FORBIDDEN",
        f"{special_type} inputs are not allowed",
        display_path,
    )


def _check_path(relative_path: str, limits: InputLimits) -> None:
    try:
        relative_path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputInspectionError(
            "INPUT_PATH_ENCODING_FORBIDDEN",
            "input path must be valid UTF-8",
            relative_path.encode("utf-8", "backslashreplace").decode("ascii"),
        ) from exc
    if len(relative_path) > limits.max_path_characters:
        raise InputInspectionError(
            "INPUT_PATH_LENGTH_LIMIT",
            f"input path exceeds {limits.max_path_characters} characters",
            relative_path,
        )
    depth = len(Path(relative_path).parts)
    if depth > limits.max_path_depth:
        raise InputInspectionError(
            "INPUT_PATH_DEPTH_LIMIT",
            f"input path exceeds depth {limits.max_path_depth}",
            relative_path,
        )


def check_relative_path(
    relative_path: str,
    limits: InputLimits | Mapping[str, object] | InputLimitValues | None = None,
) -> None:
    """Apply the canonical input path limits to one already-relative path."""

    if not isinstance(relative_path, str) or not relative_path:
        raise InputInspectionError(
            "INPUT_PATH_INVALID",
            "input path must be a non-empty string",
            str(relative_path),
        )
    active_limits = default_limits() if limits is None else _coerce_limits(limits)
    _check_path(relative_path, active_limits)


def _check_identity(entry: _Entry, actual: os.stat_result) -> None:
    display_path = _display_path(entry.path, entry.relative_path)
    try:
        actual_kind = _kind(actual, display_path)
    except InputInspectionError as exc:
        raise InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            f"input type changed during scan ({exc.code})",
            display_path,
        ) from exc
    if actual_kind != entry.kind or not _identity_matches(
        entry.identity,
        _identity(actual),
    ):
        raise InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            "input identity, size, or modification time changed during scan",
            display_path,
        )


def _check_excluded_identity(entry: _Entry, actual: os.stat_result) -> None:
    """Reject replacement/type races while allowing ignored content to change."""

    display_path = _display_path(entry.path, entry.relative_path)
    try:
        actual_kind = _kind(actual, display_path)
    except InputInspectionError as exc:
        raise InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            f"excluded input type changed during scan ({exc.code})",
            display_path,
        ) from exc
    actual_identity = _identity(actual)
    if (
        actual_kind != entry.kind
        or actual_identity[0] != entry.identity[0]
        or actual_identity[1] != entry.identity[1]
        or actual_identity[2] != entry.identity[2]
        or _security_file_attributes(actual_identity[6])
        != _security_file_attributes(entry.identity[6])
    ):
        raise InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            "excluded input identity or type changed during scan",
            display_path,
        )


def _inventory(
    source: Path,
    limits: InputLimits,
    exclusions: InputExclusions,
) -> tuple[str, list[_Entry], list[_Entry], int, int]:
    root_info = _lstat(source, source.name or ".", initial=True)
    root_kind = _kind(root_info, source.name or ".")
    if root_kind == "file":
        relative = source.name or "."
        _check_path(relative, limits)
        size = int(root_info.st_size)
        if size > limits.max_file_bytes:
            raise InputInspectionError(
                "INPUT_FILE_SIZE_LIMIT",
                f"input file exceeds {limits.max_file_bytes} bytes",
                relative,
            )
        if size > limits.max_total_bytes:
            raise InputInspectionError(
                "INPUT_TOTAL_SIZE_LIMIT",
                f"input exceeds total size limit {limits.max_total_bytes} bytes",
                relative,
            )
        return (
            root_kind,
            [_Entry(source, relative, root_kind, _identity(root_info))],
            [],
            1,
            size,
        )

    root = _Entry(source, "", root_kind, _identity(root_info))
    entries: list[_Entry] = [root]
    excluded_entries: list[_Entry] = []
    pending = [root]
    entry_count = 0
    raw_entry_count = 0
    total_bytes = 0
    while pending:
        directory = pending.pop()
        _check_identity(
            directory,
            _lstat(
                directory.path,
                _display_path(source, directory.relative_path),
            ),
        )
        try:
            with os.scandir(directory.path) as iterator:
                children = []
                for child in iterator:
                    raw_entry_count += 1
                    if raw_entry_count > limits.max_entries:
                        relative = (
                            f"{directory.relative_path}/{child.name}"
                            if directory.relative_path
                            else child.name
                        )
                        raise InputInspectionError(
                            "INPUT_ENTRY_COUNT_LIMIT",
                            (
                                "input exceeds "
                                f"{limits.max_entries} raw directory entries"
                            ),
                            relative,
                        )
                    children.append(child)
                children.sort(key=lambda item: item.name)
        except FileNotFoundError as exc:
            raise InputInspectionError(
                "INPUT_CHANGED_DURING_SCAN",
                "directory disappeared during scan",
                _display_path(source, directory.relative_path),
            ) from exc
        except OSError as exc:
            raise InputInspectionError(
                "INPUT_METADATA_UNAVAILABLE",
                f"cannot enumerate directory metadata: {exc}",
                _display_path(source, directory.relative_path),
            ) from exc
        child_directories: list[_Entry] = []
        for child in children:
            relative = (
                f"{directory.relative_path}/{child.name}"
                if directory.relative_path
                else child.name
            )
            child_path = Path(child.path)
            info = _lstat(child_path, relative)
            child_kind = _kind(info, relative)
            entry = _Entry(child_path, relative, child_kind, _identity(info))
            if child_kind == "directory":
                is_root_exclusion = (
                    not directory.relative_path
                    and child.name in exclusions.root_directory_names
                )
                if is_root_exclusion or child.name in exclusions.directory_names:
                    excluded_entries.append(entry)
                    continue
            if child_kind == "file" and (
                child.name in exclusions.file_names
                or child_path.suffix in exclusions.file_suffixes
            ):
                excluded_entries.append(entry)
                continue
            _check_path(relative, limits)
            entry_count += 1
            entries.append(entry)
            if child_kind == "directory":
                child_directories.append(entry)
                continue
            size = int(info.st_size)
            if size > limits.max_file_bytes:
                raise InputInspectionError(
                    "INPUT_FILE_SIZE_LIMIT",
                    f"input file exceeds {limits.max_file_bytes} bytes",
                    relative,
                )
            total_bytes += size
            if total_bytes > limits.max_total_bytes:
                raise InputInspectionError(
                    "INPUT_TOTAL_SIZE_LIMIT",
                    f"input exceeds total size limit {limits.max_total_bytes} bytes",
                    relative,
                )
        pending.extend(reversed(child_directories))

    for entry in entries:
        _check_identity(
            entry,
            _lstat(entry.path, _display_path(source, entry.relative_path)),
        )
    for entry in excluded_entries:
        _check_excluded_identity(
            entry,
            _lstat(entry.path, _display_path(source, entry.relative_path)),
        )
    return root_kind, entries, excluded_entries, entry_count, total_bytes


def _open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    return flags


def _read_file(entry: _Entry, limits: InputLimits) -> InputFile:
    display_path = entry.relative_path
    before = _lstat(entry.path, display_path)
    _check_identity(entry, before)
    try:
        descriptor = os.open(entry.path, _open_flags())
    except OSError as exc:
        try:
            current = _lstat(entry.path, display_path)
            _check_identity(entry, current)
        except InputInspectionError as changed:
            raise changed from exc
        raise InputInspectionError(
            "INPUT_READ_FAILED", f"cannot safely open input: {exc}", display_path
        ) from exc

    digest = hashlib.sha256()
    bytes_read = 0
    chunks: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        _check_identity(entry, opened)
        while True:
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except OSError as exc:
                raise InputInspectionError(
                    "INPUT_READ_FAILED", f"cannot read input: {exc}", display_path
                ) from exc
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > entry.size or bytes_read > limits.max_file_bytes:
                raise InputInspectionError(
                    "INPUT_CHANGED_DURING_SCAN",
                    "input size changed while it was read",
                    display_path,
                )
            digest.update(chunk)
            chunks.append(chunk)
        after_read = os.fstat(descriptor)
        _check_identity(entry, after_read)
        if bytes_read != entry.size:
            raise InputInspectionError(
                "INPUT_CHANGED_DURING_SCAN",
                "input size changed while it was read",
                display_path,
            )
    finally:
        os.close(descriptor)

    after_close = _lstat(entry.path, display_path)
    _check_identity(entry, after_close)
    return InputFile(
        relative_path=entry.relative_path,
        size=entry.size,
        sha256=digest.hexdigest(),
        device=entry.identity[0],
        inode=entry.identity[1],
        mtime_ns=entry.identity[4],
        ctime_ns=entry.identity[5],
        mode=entry.identity[7],
        file_attributes=entry.identity[6],
        content=b"".join(chunks),
    )


def digest_tree(
    files: tuple[InputFile, ...] | list[InputFile],
    directories: tuple[str, ...] | list[str] = (),
) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(directories):
        encoded = relative_path.encode("utf-8")
        digest.update(b"D")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    for item in sorted(files, key=lambda candidate: candidate.relative_path):
        encoded = item.relative_path.encode("utf-8")
        digest.update(b"F")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(bytes.fromhex(item.sha256))
    return digest.hexdigest()


def inspect(
    path: Path,
    limits: InputLimits | Mapping[str, object] | InputLimitValues | None = None,
    *,
    exclusions: InputExclusions | None = None,
) -> InputSnapshot:
    """Return a deterministic snapshot after metadata, type, and resource preflight."""

    source = _absolute_lexical(path)
    active_limits = default_limits() if limits is None else _coerce_limits(limits)
    active_exclusions = InputExclusions() if exclusions is None else exclusions
    kind, entries, excluded, entry_count, total_bytes = _inventory(
        source, active_limits, active_exclusions
    )
    file_entries = sorted(
        (entry for entry in entries if entry.kind == "file"),
        key=lambda entry: entry.relative_path,
    )
    files = tuple(_read_file(entry, active_limits) for entry in file_entries)
    directories = tuple(
        entry.relative_path
        for entry in entries
        if entry.kind == "directory" and entry.relative_path
    )
    for entry in entries:
        _check_identity(
            entry,
            _lstat(entry.path, _display_path(source, entry.relative_path)),
        )
    for entry in excluded:
        _check_excluded_identity(
            entry,
            _lstat(entry.path, _display_path(source, entry.relative_path)),
        )
    excluded_entries = tuple(
        ExcludedInputEntry(
            relative_path=entry.relative_path,
            kind=entry.kind,
            device=entry.identity[0],
            inode=entry.identity[1],
            size=entry.identity[3],
            mtime_ns=entry.identity[4],
            ctime_ns=entry.identity[5],
            file_attributes=entry.identity[6],
        )
        for entry in excluded
    )
    input_sha256 = (
        files[0].sha256
        if kind == "file"
        else digest_tree(files, directories)
    )
    return InputSnapshot(
        source=source,
        kind=kind,
        files=files,
        directories=directories,
        excluded_entries=excluded_entries,
        sha256=input_sha256,
        total_bytes=total_bytes,
        entry_count=entry_count,
        limits=active_limits,
    )


def inspect_package(
    path: Path,
    limits: InputLimits | Mapping[str, object] | InputLimitValues | None = None,
) -> InputSnapshot:
    """Snapshot package content with contract-owned root exclusions."""

    return inspect(path, limits, exclusions=default_exclusions())
