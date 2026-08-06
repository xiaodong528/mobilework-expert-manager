from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import windows_file_ops


class FakeApi:
    def __init__(self) -> None:
        self.attributes = 0
        self.attributes_by_handle: dict[int, int] = {}
        self.file_type_value = windows_file_ops.FILE_TYPE_DISK
        self.identity_value = windows_file_ops.WindowsFileIdentity(7, b"i" * 16)
        self.closed: list[int] = []
        self.renames: list[tuple[int, Path]] = []
        self.delete_marked: list[int] = []
        self.payload = b""
        self.next_handle = 100
        self.create_calls: list[dict[str, object]] = []
        self.create_error_at: int | None = None
        self.reparse_at: set[int] = set()
        self.close_errors: set[int] = set()
        self.identities_by_handle: dict[
            int, windows_file_ops.WindowsFileIdentity
        ] = {}

    def create_file(self, path: Path, **options: object) -> int:
        call_index = len(self.create_calls)
        self.create_calls.append({"path": path, **options})
        if self.create_error_at == call_index:
            raise windows_file_ops.WindowsFileOpsError(
                "WINDOWS_FILE_OPERATION_FAILED", "injected create failure"
            )
        handle = self.next_handle
        self.next_handle += 1
        operation = str(options.get("operation", ""))
        self.attributes_by_handle[handle] = (
            windows_file_ops.FILE_ATTRIBUTE_DIRECTORY
            if operation == "CreateFileW(directory-anchor)"
            else 0
        )
        if call_index in self.reparse_at:
            self.attributes_by_handle[handle] |= (
                windows_file_ops.FILE_ATTRIBUTE_REPARSE_POINT
            )
        self.identities_by_handle[handle] = self.identity_value
        return handle

    def attribute_tag(self, handle: int) -> tuple[int, int]:
        return self.attributes_by_handle.get(handle, self.attributes), 0

    def file_type(self, _handle: int) -> int:
        return self.file_type_value

    def identity(self, handle: int) -> windows_file_ops.WindowsFileIdentity:
        return self.identities_by_handle.get(handle, self.identity_value)

    def close(self, handle: int) -> None:
        self.closed.append(handle)
        if handle in self.close_errors:
            raise windows_file_ops.WindowsFileOpsError(
                "WINDOWS_FILE_OPERATION_FAILED", "injected close failure"
            )

    def read(self, _handle: int, max_bytes: int) -> bytes:
        if len(self.payload) > max_bytes:
            raise windows_file_ops.WindowsFileOpsError(
                "WINDOWS_FILE_TOO_LARGE", "too large"
            )
        return self.payload

    def write(self, _handle: int, payload: bytes) -> None:
        self.payload = payload

    def flush(self, _handle: int) -> None:
        return None

    def rename_no_replace(self, handle: int, target_path: Path) -> None:
        self.renames.append((handle, target_path))

    def mark_delete_on_close(self, handle: int) -> None:
        self.delete_marked.append(handle)


def fake_anchor(api: FakeApi) -> windows_file_ops.WindowsDirectoryAnchor:
    directory_identity = windows_file_ops.WindowsFileIdentity(7, b"i" * 16)
    api.attributes_by_handle[41] = windows_file_ops.FILE_ATTRIBUTE_DIRECTORY
    return windows_file_ops.WindowsDirectoryAnchor(
        path=Path("C:/workspace"),
        api=api,
        handles=[
            windows_file_ops._AnchoredDirectoryHandle(
                Path("C:/workspace"), 41, directory_identity
            )
        ],
    )


class WindowsFileOpsPortableTests(unittest.TestCase):
    def test_file_identity_requires_exactly_128_bits(self) -> None:
        with self.assertRaisesRegex(ValueError, "128-bit"):
            windows_file_ops.WindowsFileIdentity(1, b"short")

    def test_fixed_width_structures_match_win32_abi(self) -> None:
        self.assertEqual(ctypes.sizeof(windows_file_ops._FILE_ATTRIBUTE_TAG_INFO), 8)
        self.assertEqual(ctypes.sizeof(windows_file_ops._FILE_ID_INFO), 24)
        self.assertEqual(ctypes.sizeof(windows_file_ops._FILE_DISPOSITION_INFO), 1)
        self.assertEqual(windows_file_ops._FILE_RENAME_INFO.ReplaceIfExists.offset, 0)
        self.assertEqual(windows_file_ops._FILE_RENAME_INFO.RootDirectory.offset, 8)
        self.assertEqual(windows_file_ops._FILE_RENAME_INFO.FileNameLength.offset, 16)
        self.assertEqual(windows_file_ops._FILE_RENAME_INFO.FileName.offset, 20)

    def test_rename_buffer_uses_exact_utf16_byte_length_and_no_replace(self) -> None:
        name = "release-锁-\U0001f512"
        encoded = name.encode("utf-16-le")
        buffer = windows_file_ops._rename_info_buffer(123, name)
        header = ctypes.cast(
            buffer, ctypes.POINTER(windows_file_ops._FILE_RENAME_INFO)
        ).contents

        self.assertEqual(header.ReplaceIfExists, 0)
        self.assertEqual(header.RootDirectory, 123)
        self.assertEqual(header.FileNameLength, len(encoded))
        offset = windows_file_ops._FILE_RENAME_INFO.FileName.offset
        self.assertEqual(buffer.raw[offset : offset + len(encoded)], encoded)
        self.assertEqual(
            ctypes.sizeof(buffer),
            ctypes.sizeof(windows_file_ops._FILE_RENAME_INFO) + len(encoded),
        )

    def test_native_path_and_absolute_rename_buffer_are_exact(self) -> None:
        self.assertEqual(
            windows_file_ops._native_path(Path(r"\\?\C:\workspace\lock")),
            r"\\?\C:\workspace\lock",
        )
        self.assertEqual(
            windows_file_ops._native_path(Path(r"\\server\share\lock")),
            r"\\?\UNC\server\share\lock",
        )
        self.assertEqual(
            windows_file_ops._native_path(Path(r"C:\workspace\lock")),
            r"\\?\C:\workspace\lock",
        )

        target = Path(r"C:\workspace\release-锁")
        encoded = windows_file_ops._native_path(target).encode("utf-16-le")
        buffer = windows_file_ops._absolute_rename_info_buffer(target)
        header = ctypes.cast(
            buffer, ctypes.POINTER(windows_file_ops._FILE_RENAME_INFO)
        ).contents
        self.assertFalse(header.ReplaceIfExists)
        self.assertIsNone(header.RootDirectory)
        self.assertEqual(header.FileNameLength, len(encoded))
        offset = windows_file_ops._FILE_RENAME_INFO.FileName.offset
        self.assertEqual(buffer.raw[offset : offset + len(encoded)], encoded)

    def test_rename_buffers_reject_empty_encoded_targets(self) -> None:
        class EmptyEncoded:
            def encode(self, *_args: object, **_kwargs: object) -> bytes:
                return b""

        with mock.patch.object(
            windows_file_ops, "_safe_component", return_value=EmptyEncoded()
        ):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                windows_file_ops._rename_info_buffer(1, "ignored")
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_INVALID_COMPONENT")

        with mock.patch.object(
            windows_file_ops, "_native_path", return_value=EmptyEncoded()
        ):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                windows_file_ops._absolute_rename_info_buffer(Path("ignored"))
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_INVALID_PATH")

    def test_safe_component_rejects_escape_reserved_and_ambiguous_names(self) -> None:
        invalid = (
            "",
            ".",
            "..",
            "../lock",
            "child/lock",
            "child\\lock",
            "stream:name",
            "trailing.",
            "trailing ",
            "NUL",
            "con.txt",
            "LPT9.json",
            "bad\x00name",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                    windows_file_ops._safe_component(value)
                self.assertEqual(
                    caught.exception.code, "WINDOWS_FILE_INVALID_COMPONENT"
                )

        self.assertEqual(
            windows_file_ops._safe_component(".manager.release-deadbeef"),
            ".manager.release-deadbeef",
        )

    def test_winerror_mapping_is_stable(self) -> None:
        expected = {
            windows_file_ops.ERROR_FILE_EXISTS: "WINDOWS_FILE_EXISTS",
            windows_file_ops.ERROR_ALREADY_EXISTS: "WINDOWS_FILE_EXISTS",
            windows_file_ops.ERROR_FILE_NOT_FOUND: "WINDOWS_FILE_NOT_FOUND",
            windows_file_ops.ERROR_PATH_NOT_FOUND: "WINDOWS_FILE_NOT_FOUND",
            windows_file_ops.ERROR_SHARING_VIOLATION: "WINDOWS_FILE_BUSY",
            windows_file_ops.ERROR_ACCESS_DENIED: "WINDOWS_FILE_ACCESS_DENIED",
            windows_file_ops.ERROR_NOT_SUPPORTED: "WINDOWS_FILE_OPERATION_UNSUPPORTED",
            9999: "WINDOWS_FILE_OPERATION_FAILED",
        }
        for winerror, code in expected.items():
            with self.subTest(winerror=winerror):
                error = windows_file_ops._error_from_winerror("test", winerror)
                self.assertEqual(error.code, code)
                self.assertEqual(error.winerror, winerror)

    @unittest.skipIf(os.name == "nt", "non-Windows contract only")
    def test_non_windows_calls_fail_with_stable_unavailable(self) -> None:
        windows_file_ops._api.cache_clear()
        self.assertFalse(windows_file_ops.available())
        with self.assertRaises(windows_file_ops.WindowsFileOpsUnavailable) as caught:
            windows_file_ops.open_directory_chain_no_reparse(Path("/tmp"))
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_OPS_UNAVAILABLE")

    def test_available_and_require_api_cover_supported_and_unavailable_branches(
        self,
    ) -> None:
        sentinel = object()
        with mock.patch.object(windows_file_ops.os, "name", "nt"), mock.patch.object(
            windows_file_ops, "_api", return_value=sentinel
        ):
            self.assertTrue(windows_file_ops.available())
            self.assertIs(windows_file_ops._require_api(), sentinel)

        unavailable = windows_file_ops.WindowsFileOpsUnavailable("missing")
        with mock.patch.object(windows_file_ops.os, "name", "nt"), mock.patch.object(
            windows_file_ops, "_api", side_effect=unavailable
        ):
            self.assertFalse(windows_file_ops.available())

        cached_api = windows_file_ops._api
        cached_api.cache_clear()
        try:
            with mock.patch.object(
                windows_file_ops, "_Kernel32Api", return_value=sentinel
            ):
                self.assertIs(cached_api(), sentinel)
        finally:
            cached_api.cache_clear()

    def test_regular_validation_rejects_reparse_directory_and_non_disk(self) -> None:
        api = FakeApi()
        api.attributes = windows_file_ops.FILE_ATTRIBUTE_REPARSE_POINT
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_regular(api, 9)
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN"
        )

        api.attributes = windows_file_ops.FILE_ATTRIBUTE_DIRECTORY
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_regular(api, 9)
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_NOT_REGULAR")

        api.attributes = 0
        api.file_type_value = windows_file_ops.FILE_TYPE_DISK
        self.assertEqual(
            windows_file_ops._validate_regular(api, 9), api.identity_value
        )

    def test_directory_validation_covers_reparse_shape_type_and_success(self) -> None:
        api = FakeApi()
        api.attributes = windows_file_ops.FILE_ATTRIBUTE_REPARSE_POINT
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_directory(api, 1)
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN"
        )

        api.attributes = 0
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_directory(api, 1)
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_NOT_DIRECTORY")

        api.attributes = windows_file_ops.FILE_ATTRIBUTE_DIRECTORY
        api.file_type_value = 3
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_directory(api, 1)
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_NOT_DIRECTORY")

        api.file_type_value = windows_file_ops.FILE_TYPE_DISK
        self.assertEqual(
            windows_file_ops._validate_directory(api, 1), api.identity_value
        )

        api.attributes = 0
        api.file_type_value = 3
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            windows_file_ops._validate_regular(api, 9)
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_NOT_REGULAR")

    def test_handle_binds_identity_rename_and_delete_to_same_object(self) -> None:
        api = FakeApi()
        anchor = fake_anchor(api)
        handle = windows_file_ops.WindowsFileHandle(
            path=anchor.child_path(".manager.lock"),
            api=api,
            raw_handle=77,
            identity=api.identity_value,
            delete_access=True,
        )

        handle.write(b'{"ownerToken":"owner"}\n')
        handle.flush()
        self.assertEqual(handle.read(), b'{"ownerToken":"owner"}\n')
        target = handle.rename_no_replace(anchor, ".manager.lock.release-owner")
        self.assertEqual(target, Path("C:/workspace/.manager.lock.release-owner"))
        self.assertEqual(
            api.renames,
            [(77, Path("C:/workspace/.manager.lock.release-owner"))],
        )
        handle.mark_delete_on_close()
        self.assertEqual(api.delete_marked, [77])
        handle.close()
        self.assertEqual(api.closed, [77])

    def test_handle_refuses_identity_change_and_delete_without_access(self) -> None:
        api = FakeApi()
        original = api.identity_value
        handle = windows_file_ops.WindowsFileHandle(
            path=Path("C:/workspace/.manager.lock"),
            api=api,
            raw_handle=77,
            identity=original,
            delete_access=False,
        )
        api.identity_value = windows_file_ops.WindowsFileIdentity(7, b"x" * 16)
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            handle.identity()
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_IDENTITY_CHANGED")
        api.identity_value = original
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            handle.mark_delete_on_close()
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_DELETE_ACCESS_REQUIRED"
        )
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            handle.rename_no_replace(fake_anchor(api), ".release")
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_DELETE_ACCESS_REQUIRED"
        )

    def test_handle_argument_limits_context_and_closed_state(self) -> None:
        api = FakeApi()
        handle = windows_file_ops.WindowsFileHandle(
            path=Path("C:/workspace/.manager.lock"),
            api=api,
            raw_handle=77,
            identity=api.identity_value,
            delete_access=True,
        )
        self.assertEqual(handle.identity(), api.identity_value)
        for invalid in (True, 0, -1, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    handle.read(invalid)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            handle.write("not-bytes")  # type: ignore[arg-type]
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            handle.write(b"x" * (windows_file_ops._MAX_IO_BYTES + 1))
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_TOO_LARGE")

        with handle as entered:
            self.assertIs(entered, handle)
        self.assertEqual(api.closed, [77])
        handle.close()
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            _ = handle.raw_handle
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_HANDLE_CLOSED")

    def test_anchor_rejects_reparse_or_changed_identity_and_closes_all_handles(self) -> None:
        api = FakeApi()
        anchor = fake_anchor(api)
        api.attributes_by_handle[41] = windows_file_ops.FILE_ATTRIBUTE_REPARSE_POINT
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            anchor.assert_safe()
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN"
        )
        anchor.close()
        self.assertEqual(api.closed, [41])

    def test_anchor_properties_context_identity_change_and_close_errors(self) -> None:
        api = FakeApi()
        anchor = fake_anchor(api)
        self.assertEqual(anchor.raw_handle, 41)
        self.assertEqual(anchor.identity, api.identity_value)
        self.assertEqual(
            anchor.child("safe.lock"),
            (Path("C:/workspace/safe.lock"), "safe.lock"),
        )
        api.identities_by_handle[41] = windows_file_ops.WindowsFileIdentity(
            7, b"z" * 16
        )
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            anchor.assert_safe()
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_IDENTITY_CHANGED")

        api.identities_by_handle.pop(41)
        api.close_errors.add(41)
        with self.assertRaises(windows_file_ops.WindowsFileOpsError):
            anchor.close()
        anchor.close()
        for attribute in ("raw_handle", "identity"):
            with self.subTest(attribute=attribute):
                with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                    getattr(anchor, attribute)
                self.assertEqual(caught.exception.code, "WINDOWS_FILE_ANCHOR_CLOSED")
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            anchor.assert_safe()
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_ANCHOR_CLOSED")

        clean_api = FakeApi()
        with fake_anchor(clean_api) as entered:
            self.assertEqual(entered.raw_handle, 41)
        self.assertEqual(clean_api.closed, [41])

    def test_anchor_close_attempts_every_handle_and_reports_first_error(self) -> None:
        api = FakeApi()
        identity = api.identity_value
        anchor = windows_file_ops.WindowsDirectoryAnchor(
            path=Path("C:/workspace"),
            api=api,
            handles=[
                windows_file_ops._AnchoredDirectoryHandle(Path("C:/"), 40, identity),
                windows_file_ops._AnchoredDirectoryHandle(
                    Path("C:/workspace"), 41, identity
                ),
            ],
        )
        api.close_errors.update({40, 41})
        with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
            anchor.close()
        self.assertEqual(str(caught.exception), "injected close failure")
        self.assertEqual(api.closed, [41, 40])

    def test_directory_prefixes_and_invalid_absolute_result(self) -> None:
        requested = Path("/tmp/portable-workspace")
        expected = Path(os.path.abspath(os.fspath(requested)))
        absolute, prefixes = windows_file_ops._directory_prefixes(
            requested
        )
        self.assertEqual(absolute, expected)
        current = Path(expected.anchor)
        expected_prefixes = [current]
        for component in expected.parts[1:]:
            current = current / component
            expected_prefixes.append(current)
        self.assertEqual(
            prefixes,
            expected_prefixes,
        )
        with mock.patch.object(
            windows_file_ops.os.path, "abspath", return_value="relative"
        ):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                windows_file_ops._directory_prefixes(Path("anything"))
        self.assertEqual(caught.exception.code, "WINDOWS_FILE_INVALID_PATH")

    def test_open_directory_chain_with_fake_api_and_cleanup_failures(self) -> None:
        api = FakeApi()
        requested = Path("/tmp/portable-workspace")
        expected = Path(os.path.abspath(os.fspath(requested)))
        with mock.patch.object(windows_file_ops, "_require_api", return_value=api):
            with windows_file_ops.open_directory_chain_no_reparse(
                requested
            ) as anchor:
                self.assertEqual(anchor.path, expected)
                self.assertEqual(anchor.raw_handle, 102)
                self.assertEqual(len(api.create_calls), 3)
                for call in api.create_calls:
                    self.assertEqual(
                        call["operation"], "CreateFileW(directory-anchor)"
                    )
                    self.assertEqual(
                        call["creation_disposition"], windows_file_ops.OPEN_EXISTING
                    )
        self.assertEqual(api.closed, [102, 101, 100])

        api = FakeApi()
        api.create_error_at = 1
        with mock.patch.object(windows_file_ops, "_require_api", return_value=api):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError):
                windows_file_ops.open_directory_chain_no_reparse(
                    Path("/tmp/portable-workspace")
                )
        self.assertEqual(api.closed, [100])

        api = FakeApi()
        api.reparse_at.add(1)
        api.close_errors.add(100)
        with mock.patch.object(windows_file_ops, "_require_api", return_value=api):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                windows_file_ops.open_directory_chain_no_reparse(
                    Path("/tmp/portable-workspace")
                )
        self.assertEqual(
            caught.exception.code, "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN"
        )
        self.assertEqual(api.closed, [101, 100])

        api = FakeApi()
        with mock.patch.object(
            windows_file_ops, "_require_api", return_value=api
        ), mock.patch.object(
            windows_file_ops.WindowsDirectoryAnchor,
            "assert_safe",
            side_effect=windows_file_ops.WindowsFileOpsError(
                "WINDOWS_FILE_IDENTITY_CHANGED", "changed"
            ),
        ):
            with self.assertRaises(windows_file_ops.WindowsFileOpsError):
                windows_file_ops.open_directory_chain_no_reparse(
                    Path("/tmp/portable-workspace")
                )
        self.assertEqual(api.closed, [102, 101, 100])

    def test_create_and_open_regular_with_fake_api_cover_flags_and_cleanup(self) -> None:
        api = FakeApi()
        anchor = fake_anchor(api)
        created = windows_file_ops.create_exclusive_regular(anchor, ".manager.lock")
        self.assertEqual(created.raw_handle, 100)
        create_call = api.create_calls[-1]
        self.assertEqual(create_call["creation_disposition"], windows_file_ops.CREATE_NEW)
        self.assertEqual(create_call["share_mode"], windows_file_ops.FILE_SHARE_READ)
        created.close()

        opened = windows_file_ops.open_existing_regular_no_reparse(
            anchor, ".manager.lock"
        )
        open_call = api.create_calls[-1]
        self.assertEqual(open_call["creation_disposition"], windows_file_ops.OPEN_EXISTING)
        self.assertEqual(
            open_call["share_mode"],
            windows_file_ops.FILE_SHARE_READ
            | windows_file_ops.FILE_SHARE_WRITE
            | windows_file_ops.FILE_SHARE_DELETE,
        )
        opened.close()

        delete_opened = windows_file_ops.open_existing_regular_no_reparse(
            anchor, ".manager.lock", delete_access=True
        )
        delete_call = api.create_calls[-1]
        self.assertEqual(delete_call["share_mode"], windows_file_ops.FILE_SHARE_READ)
        self.assertTrue(int(delete_call["desired_access"]) & windows_file_ops.DELETE)
        delete_opened.close()

        with self.assertRaises(TypeError):
            windows_file_ops.open_existing_regular_no_reparse(
                anchor, ".manager.lock", delete_access=1  # type: ignore[arg-type]
            )

        failing_api = FakeApi()
        failing_anchor = fake_anchor(failing_api)
        failing_api.reparse_at.add(0)
        with self.assertRaises(windows_file_ops.WindowsFileOpsError):
            windows_file_ops.create_exclusive_regular(
                failing_anchor, ".manager.lock"
            )
        self.assertEqual(failing_api.closed, [100])

        failing_api = FakeApi()
        failing_anchor = fake_anchor(failing_api)
        failing_api.reparse_at.add(0)
        with self.assertRaises(windows_file_ops.WindowsFileOpsError):
            windows_file_ops.open_existing_regular_no_reparse(
                failing_anchor, ".manager.lock"
            )
        self.assertEqual(failing_api.closed, [100])

    def test_api_availability_does_not_swallow_non_unavailable_errors(self) -> None:
        if os.name == "nt":
            self.skipTest("portable mock contract only")
        with mock.patch.object(windows_file_ops.os, "name", "nt"), mock.patch.object(
            windows_file_ops, "_api", side_effect=RuntimeError("unexpected")
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                windows_file_ops.available()


@unittest.skipUnless(os.name == "nt", "real Win32 filesystem test")
class WindowsFileOpsIntegrationTests(unittest.TestCase):
    def test_directory_junction_is_rejected_before_child_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            junction = root / "junction"
            target.mkdir()
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                windows_file_ops.open_directory_chain_no_reparse(junction)
            self.assertEqual(
                caught.exception.code,
                "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN",
            )

    def test_create_read_rename_and_owner_handle_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            with windows_file_ops.open_directory_chain_no_reparse(root) as anchor:
                handle = windows_file_ops.create_exclusive_regular(
                    anchor, ".manager.lock"
                )
                identity = handle.identity()
                payload = b'{"ownerToken":"owner"}\n'
                handle.write(payload)
                handle.flush()
                self.assertEqual(handle.read(), payload)
                quarantine = handle.rename_no_replace(
                    anchor, ".manager.lock.release-owner"
                )
                self.assertFalse((root / ".manager.lock").exists())
                self.assertTrue(quarantine.exists())
                self.assertEqual(handle.identity(), identity)
                handle.mark_delete_on_close()
                handle.close()
                self.assertFalse(quarantine.exists())

    def test_create_new_collision_does_not_open_existing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            (root / ".manager.lock").write_bytes(b"existing")
            with windows_file_ops.open_directory_chain_no_reparse(root) as anchor:
                with self.assertRaises(windows_file_ops.WindowsFileOpsError) as caught:
                    windows_file_ops.create_exclusive_regular(anchor, ".manager.lock")
            self.assertEqual(caught.exception.code, "WINDOWS_FILE_EXISTS")
            self.assertEqual((root / ".manager.lock").read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
