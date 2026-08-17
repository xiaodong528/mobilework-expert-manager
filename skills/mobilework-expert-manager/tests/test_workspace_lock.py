from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workspace_lock


LOCK_NAME = ".test-mobilework-expert-manager.lock"
LOCK_FIELDS = [
    "ownerToken",
    "pid",
    "createdAt",
    "heartbeatAt",
    "protocolVersion",
]


def lock_policy(
    *,
    file_name: str = LOCK_NAME,
    file_mode: int = 0o600,
    protocol_version: int = 2,
    fields: list[str] | None = None,
) -> dict[str, object]:
    return {
        "workspaceLock": {
            "fileName": file_name,
            "fileMode": file_mode,
            "protocolVersion": protocol_version,
            "fields": LOCK_FIELDS if fields is None else fields,
        }
    }


class FakeWindowsAnchor:
    def __init__(
        self,
        path: Path,
        *,
        identity: object = "workspace-identity",
        assert_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.path = path
        self.identity = identity
        self.assert_error = assert_error
        self.close_error = close_error
        self.close_calls = 0

    def assert_safe(self) -> None:
        if self.assert_error is not None:
            raise self.assert_error

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeWindowsHandle:
    def __init__(
        self,
        *,
        identity: object = "lock-identity",
        payload: bytes = b"",
        operation_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.identity_value = identity
        self.payload = payload
        self.operation_error = operation_error
        self.close_error = close_error
        self.write_calls = 0
        self.flush_calls = 0
        self.close_calls = 0
        self.rename_targets: list[str] = []
        self.delete_marked = False

    def identity(self) -> object:
        if self.operation_error is not None:
            raise self.operation_error
        return self.identity_value

    def write(self, payload: bytes) -> None:
        if self.operation_error is not None:
            raise self.operation_error
        self.write_calls += 1
        self.payload = payload

    def flush(self) -> None:
        if self.operation_error is not None:
            raise self.operation_error
        self.flush_calls += 1

    def read(self) -> bytes:
        if self.operation_error is not None:
            raise self.operation_error
        return self.payload

    def rename_no_replace(self, anchor: FakeWindowsAnchor, target: str) -> None:
        del anchor
        if self.operation_error is not None:
            raise self.operation_error
        self.rename_targets.append(target)

    def mark_delete_on_close(self) -> None:
        if self.operation_error is not None:
            raise self.operation_error
        self.delete_marked = True

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class WorkspaceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self.policy_patch = patch.object(
            workspace_lock.manager_contract,
            "load_policy",
            return_value=lock_policy(),
        )
        self.load_policy = self.policy_patch.start()

    def tearDown(self) -> None:
        self.policy_patch.stop()
        self.temp.cleanup()

    @property
    def lock_path(self) -> Path:
        return self.workspace / LOCK_NAME

    def test_acquire_writes_strict_protocol_document_and_releases(self) -> None:
        with workspace_lock.acquire(self.workspace) as acquired:
            document = acquired.document
            self.assertEqual(set(document), set(LOCK_FIELDS))
            self.assertEqual(document["ownerToken"], acquired.owner_token)
            self.assertEqual(document["pid"], os.getpid())
            self.assertEqual(document["protocolVersion"], 2)
            self.assertEqual(document["createdAt"], document["heartbeatAt"])
            self.assertEqual(acquired.path, self.lock_path)
            self.assertEqual(acquired.document, document)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(self.lock_path.stat().st_mode), 0o600)
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(self.load_policy.call_count, 1)

    def test_policy_filename_and_protocol_are_loaded_lazily(self) -> None:
        alternate_name = ".policy-selected.lock"
        self.load_policy.return_value = lock_policy(
            file_name=alternate_name,
            protocol_version=9,
        )
        alternate_path = self.workspace / alternate_name
        with workspace_lock.acquire(self.workspace) as acquired:
            document = acquired.document
            self.assertEqual(document["protocolVersion"], 9)
            self.assertFalse(self.lock_path.exists())
        self.assertFalse(alternate_path.exists())

    def test_existing_regular_lock_is_not_reclaimed(self) -> None:
        stale = {
            "ownerToken": "stale-owner",
            "pid": 1,
            "createdAt": "2000-01-01T00:00:00Z",
            "heartbeatAt": "2000-01-01T00:00:00Z",
            "protocolVersion": 2,
        }
        original = json.dumps(stale, sort_keys=True) + "\n"
        self.lock_path.write_text(original, encoding="utf-8")
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock.acquire(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_HELD")
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), original)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_existing_symlink_fails_closed_without_touching_target(self) -> None:
        target = Path(self.temp.name) / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        self.lock_path.symlink_to(target)
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock.acquire(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        self.assertTrue(self.lock_path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "outside")

    @unittest.skipUnless(os.name == "posix", "POSIX name-race injection")
    def test_release_refuses_a_changed_owner_token(self) -> None:
        acquired = workspace_lock.acquire(self.workspace)
        document = json.loads(self.lock_path.read_text(encoding="utf-8"))
        document["ownerToken"] = "different-owner"
        self.lock_path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            acquired.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_NOT_OWNER")
        self.assertTrue(self.lock_path.exists())
        self.lock_path.unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX name-race injection")
    def test_release_refuses_replacement_even_with_same_document(self) -> None:
        acquired = workspace_lock.acquire(self.workspace)
        original_identity = self.lock_path.stat().st_ino
        document = self.lock_path.read_bytes()
        replacement = self.workspace / ".replacement.lock"
        for _ in range(8):
            replacement.write_bytes(document)
            if replacement.stat().st_ino != original_identity:
                break
            replacement.unlink()
        self.assertNotEqual(replacement.stat().st_ino, original_identity)
        os.replace(replacement, self.lock_path)
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            acquired.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")
        self.assertTrue(self.lock_path.exists())
        self.lock_path.unlink()

    def test_context_exception_still_releases_owned_lock(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with workspace_lock.acquire(self.workspace):
                raise RuntimeError("boom")
        self.assertFalse(self.lock_path.exists())

    def test_active_owner_rejects_wrong_workspace_and_released_handle(self) -> None:
        other = Path(self.temp.name) / "other"
        other.mkdir()
        acquired = workspace_lock.acquire(self.workspace)
        acquired.assert_active_owner(self.workspace)
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            acquired.assert_active_owner(other)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_WRONG_WORKSPACE")
        acquired.release()
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            acquired.assert_active_owner(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_NOT_OWNER")

    @unittest.skipUnless(os.name == "posix", "POSIX write-race injection")
    def test_write_failure_never_unlinks_a_replacement_lock(self) -> None:
        replacement = self.workspace / ".replacement.lock"
        replacement.write_text("replacement-owner\n", encoding="utf-8")

        def replace_then_fail(descriptor: int, payload: bytes) -> None:
            del descriptor, payload
            os.replace(replacement, self.lock_path)
            raise OSError("injected write failure")

        with patch.object(workspace_lock, "_write_all", side_effect=replace_then_fail):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock.acquire(self.workspace)

        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_WRITE_FAILED")
        self.assertEqual(
            self.lock_path.read_text(encoding="utf-8"),
            "replacement-owner\n",
        )

    @unittest.skipUnless(os.name == "posix", "POSIX rename-race injection")
    def test_release_race_preserves_replacement_in_quarantine(self) -> None:
        acquired = workspace_lock.acquire(self.workspace)
        replacement = self.workspace / ".replacement.lock"
        replacement_bytes = self.lock_path.read_bytes()
        replacement.write_bytes(replacement_bytes)
        original_rename = workspace_lock.posix_noreplace.rename
        injected = False

        def replace_before_rename(
            source_fd: int,
            source: str,
            target_fd: int,
            target: str,
        ) -> None:
            nonlocal injected
            if not injected:
                injected = True
                os.replace(replacement, self.lock_path)
            original_rename(
                source_fd,
                source,
                target_fd,
                target,
            )

        with patch.object(
            workspace_lock.posix_noreplace,
            "rename",
            side_effect=replace_before_rename,
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.release()

        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")
        quarantines = list(self.workspace.glob(f"{LOCK_NAME}.release-*"))
        self.assertEqual(len(quarantines), 1)
        self.assertEqual(quarantines[0].read_bytes(), replacement_bytes)
        quarantines[0].unlink()

    def test_invalid_policy_and_workspace_have_stable_codes(self) -> None:
        invalid_policies = (
            lock_policy(file_name="../escape.lock"),
            lock_policy(file_mode=0),
            lock_policy(protocol_version=0),
            lock_policy(fields=["ownerToken"]),
        )
        if os.name == "posix":
            invalid_policies += (lock_policy(file_mode=0o644),)
        for policy in invalid_policies:
            with self.subTest(policy=policy):
                self.load_policy.return_value = policy
                with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                    workspace_lock.acquire(self.workspace)
                self.assertEqual(
                    caught.exception.code,
                    "WORKSPACE_LOCK_POLICY_INVALID",
                )

        self.load_policy.return_value = lock_policy()
        missing = Path(self.temp.name) / "missing"
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock.acquire(missing)
        self.assertEqual(
            caught.exception.code,
            "WORKSPACE_LOCK_WORKSPACE_INVALID",
        )

    def test_contract_and_low_level_failures_have_stable_codes(self) -> None:
        self.load_policy.side_effect = OSError("contract unavailable")
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._load_policy()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_POLICY_INVALID")

        self.load_policy.side_effect = None
        self.load_policy.return_value = {"workspaceLock": []}
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._load_policy()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_POLICY_INVALID")

        self.load_policy.return_value = lock_policy()
        policy = workspace_lock._load_policy()
        with patch.object(
            workspace_lock.os.path,
            "abspath",
            return_value=str(self.workspace),
        ):
            self.assertEqual(workspace_lock._workspace_path("relative"), self.workspace)

        regular_file = Path(self.temp.name) / "not-a-workspace"
        regular_file.write_text("file", encoding="utf-8")
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._workspace_path(regular_file)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_WORKSPACE_INVALID")

        with patch.object(workspace_lock.os, "open", side_effect=OSError("blocked")):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock._open_workspace(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_WORKSPACE_INVALID")

        with patch.object(workspace_lock.os, "write", return_value=0):
            with self.assertRaisesRegex(OSError, "short write"):
                workspace_lock._write_all(1, b"payload")

        with patch.object(workspace_lock.os, "stat", side_effect=FileNotFoundError):
            self.assertIsNone(workspace_lock._stat_lock(1, LOCK_NAME))
        with patch.object(workspace_lock.os, "stat", side_effect=OSError("blocked")):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock._stat_lock(1, LOCK_NAME)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        with patch.object(workspace_lock, "_stat_lock", return_value=None):
            error = workspace_lock._existing_lock_error(1, LOCK_NAME)
        self.assertEqual(error.code, "WORKSPACE_LOCK_CREATE_FAILED")

        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._parse_lock_document(b"\xff", policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_INVALID")
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._parse_lock_document(b"{}", policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_INVALID")

        document, _ = workspace_lock._lock_document(policy)
        wrong_protocol = dict(document)
        wrong_protocol["protocolVersion"] = policy.protocol_version + 1
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._parse_lock_document(
                json.dumps(wrong_protocol).encode("utf-8"),
                policy,
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_INVALID")
        invalid_owner = dict(document)
        invalid_owner["ownerToken"] = ""
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._parse_lock_document(
                json.dumps(invalid_owner).encode("utf-8"),
                policy,
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_INVALID")

        incompatible_policy = workspace_lock._WorkspaceLockPolicy(
            file_name=policy.file_name,
            file_mode=policy.file_mode,
            protocol_version=policy.protocol_version,
            fields=("ownerToken",),
        )
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            workspace_lock._lock_document(incompatible_policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_POLICY_INVALID")

    @unittest.skipUnless(os.name == "posix", "POSIX failure injection")
    def test_posix_backend_failures_remain_fail_closed(self) -> None:
        unavailable = workspace_lock.posix_noreplace.NoReplaceUnavailable("blocked")
        with patch.object(
            workspace_lock.posix_noreplace,
            "require_available",
            side_effect=unavailable,
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock.acquire(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_PLATFORM_BLOCKED")

        with (
            patch.object(workspace_lock, "_open_workspace", return_value=99),
            patch.object(workspace_lock.os, "open", side_effect=OSError("blocked")),
            patch.object(workspace_lock.os, "close") as close,
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock.acquire(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CREATE_FAILED")
        close.assert_called_once_with(99)

        with patch.object(workspace_lock.os, "fstat", return_value=self.workspace.stat()):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock.acquire(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        self.assertTrue(self.lock_path.exists())
        self.lock_path.unlink()

    def test_windows_backend_acquires_verifies_and_releases_by_pinned_owner(self) -> None:
        policy = workspace_lock._load_policy()
        primary = FakeWindowsAnchor(self.workspace)
        active = FakeWindowsHandle()
        requested: list[FakeWindowsAnchor] = []
        verifications: list[FakeWindowsHandle] = []
        open_calls = 0

        def open_anchor(path: Path | str) -> FakeWindowsAnchor:
            nonlocal open_calls
            open_calls += 1
            if open_calls == 1:
                return primary
            anchor = FakeWindowsAnchor(Path(path))
            requested.append(anchor)
            return anchor

        def open_existing(
            anchor: FakeWindowsAnchor,
            file_name: str,
        ) -> FakeWindowsHandle:
            del anchor
            self.assertEqual(file_name, LOCK_NAME)
            handle = FakeWindowsHandle(payload=active.payload)
            verifications.append(handle)
            return handle

        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                side_effect=open_anchor,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "create_exclusive_regular",
                return_value=active,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
                side_effect=open_existing,
            ),
        ):
            acquired = workspace_lock._acquire_windows(self.workspace, policy)
            self.assertEqual(active.write_calls, 1)
            self.assertEqual(active.flush_calls, 1)
            self.assertEqual(acquired.document["ownerToken"], acquired.owner_token)
            acquired.assert_active_owner(self.workspace)
            with acquired as entered:
                self.assertIs(entered, acquired)

        self.assertTrue(active.delete_marked)
        self.assertEqual(active.close_calls, 1)
        self.assertEqual(primary.close_calls, 1)
        self.assertTrue(active.rename_targets[0].startswith(f"{LOCK_NAME}.release-"))
        self.assertTrue(all(item.close_calls == 1 for item in requested))
        self.assertTrue(all(item.close_calls == 1 for item in verifications))
        acquired.release()
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            acquired.assert_active_owner(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_NOT_OWNER")

    def test_windows_owner_verification_rejects_every_mismatch(self) -> None:
        policy = workspace_lock._load_policy()
        document, payload = workspace_lock._lock_document(policy)

        def make_lock(
            *,
            anchor: FakeWindowsAnchor | None = None,
            handle: FakeWindowsHandle | None = None,
        ) -> workspace_lock.WindowsWorkspaceMutationLock:
            return workspace_lock.WindowsWorkspaceMutationLock(
                workspace=self.workspace,
                anchor=anchor or FakeWindowsAnchor(self.workspace),
                lock_handle=handle or FakeWindowsHandle(payload=payload),
                policy=policy,
                document=document,
            )

        cases = (
            (
                "wrong-workspace",
                FakeWindowsAnchor(self.workspace, identity="other-workspace"),
                FakeWindowsHandle(payload=payload),
                "WORKSPACE_LOCK_WRONG_WORKSPACE",
            ),
            (
                "unsafe-anchor",
                FakeWindowsAnchor(
                    self.workspace,
                    assert_error=workspace_lock.windows_file_ops.WindowsFileOpsError(
                        "WINDOWS_FILE_IDENTITY_CHANGED",
                        "changed",
                    ),
                ),
                FakeWindowsHandle(payload=payload),
                "WORKSPACE_LOCK_UNSAFE",
            ),
            (
                "changed-lock-handle",
                FakeWindowsAnchor(self.workspace),
                FakeWindowsHandle(identity="other-lock", payload=payload),
                "WORKSPACE_LOCK_CHANGED",
            ),
        )
        for name, requested, handle, expected_code in cases:
            with self.subTest(name=name):
                acquired = make_lock(
                    anchor=requested if name == "unsafe-anchor" else None,
                )
                requested_anchor = (
                    FakeWindowsAnchor(self.workspace)
                    if name == "unsafe-anchor"
                    else requested
                )
                with (
                    patch.object(
                        workspace_lock.windows_file_ops,
                        "open_directory_chain_no_reparse",
                        return_value=requested_anchor,
                    ),
                    patch.object(
                        workspace_lock.windows_file_ops,
                        "open_existing_regular_no_reparse",
                        return_value=handle,
                    ),
                ):
                    with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                        acquired.assert_active_owner(self.workspace)
                self.assertEqual(caught.exception.code, expected_code)
                acquired._close_handles()

        changed_verification = FakeWindowsHandle(
            identity="other-lock",
            payload=payload,
        )
        acquired = make_lock()
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=FakeWindowsAnchor(self.workspace),
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
                return_value=changed_verification,
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.assert_active_owner(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")
        acquired._close_handles()

        active = FakeWindowsHandle(payload=payload)
        acquired = make_lock(handle=active)
        active.identity_value = "replacement-lock"
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=FakeWindowsAnchor(self.workspace),
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.assert_active_owner(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")
        acquired._close_handles()

        replacement = dict(document)
        replacement["ownerToken"] = "replacement-owner"
        replacement_payload = (
            json.dumps(replacement, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        acquired = make_lock()
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=FakeWindowsAnchor(self.workspace),
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
                return_value=FakeWindowsHandle(payload=replacement_payload),
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.assert_active_owner(self.workspace)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_NOT_OWNER")
        acquired._close_handles()

    def test_windows_acquire_maps_backend_failures_and_closes_handles(self) -> None:
        policy = workspace_lock._load_policy()
        backend_error = workspace_lock.windows_file_ops.WindowsFileOpsError
        unavailable = workspace_lock.windows_file_ops.WindowsFileOpsUnavailable()

        for error, expected_code in (
            (unavailable, "WORKSPACE_LOCK_PLATFORM_BLOCKED"),
            (
                backend_error("WINDOWS_FILE_NOT_FOUND", "missing"),
                "WORKSPACE_LOCK_WORKSPACE_INVALID",
            ),
            (
                backend_error("WINDOWS_FILE_OPERATION_FAILED", "failed"),
                "WORKSPACE_LOCK_UNSAFE",
            ),
        ):
            with self.subTest(error=error.code):
                with patch.object(
                    workspace_lock.windows_file_ops,
                    "open_directory_chain_no_reparse",
                    side_effect=error,
                ):
                    with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                        workspace_lock._acquire_windows(self.workspace, policy)
                self.assertEqual(caught.exception.code, expected_code)

        for inspection_error in (
            None,
            backend_error("WINDOWS_FILE_BUSY", "busy"),
            backend_error("WINDOWS_FILE_ACCESS_DENIED", "denied"),
        ):
            with self.subTest(inspection_error=inspection_error):
                anchor = FakeWindowsAnchor(self.workspace)
                existing = FakeWindowsHandle()
                open_existing = (
                    {"return_value": existing}
                    if inspection_error is None
                    else {"side_effect": inspection_error}
                )
                with (
                    patch.object(
                        workspace_lock.windows_file_ops,
                        "open_directory_chain_no_reparse",
                        return_value=anchor,
                    ),
                    patch.object(
                        workspace_lock.windows_file_ops,
                        "create_exclusive_regular",
                        side_effect=backend_error("WINDOWS_FILE_EXISTS", "exists"),
                    ),
                    patch.object(
                        workspace_lock.windows_file_ops,
                        "open_existing_regular_no_reparse",
                        **open_existing,
                    ),
                ):
                    with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                        workspace_lock._acquire_windows(self.workspace, policy)
                self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_HELD")
                self.assertEqual(anchor.close_calls, 1)
                self.assertEqual(
                    existing.close_calls,
                    1 if inspection_error is None else 0,
                )

        anchor = FakeWindowsAnchor(self.workspace)
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=anchor,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "create_exclusive_regular",
                side_effect=backend_error("WINDOWS_FILE_EXISTS", "exists"),
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
                side_effect=backend_error("WINDOWS_FILE_NOT_REGULAR", "unsafe"),
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock._acquire_windows(self.workspace, policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        self.assertEqual(anchor.close_calls, 1)

        anchor = FakeWindowsAnchor(self.workspace)
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=anchor,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "create_exclusive_regular",
                side_effect=backend_error(
                    "WINDOWS_FILE_OPERATION_FAILED",
                    "create failed",
                ),
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock._acquire_windows(self.workspace, policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        self.assertEqual(anchor.close_calls, 1)

        anchor = FakeWindowsAnchor(self.workspace)
        handle = FakeWindowsHandle(
            operation_error=backend_error("WINDOWS_FILE_OPERATION_FAILED", "write failed")
        )
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=anchor,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "create_exclusive_regular",
                return_value=handle,
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                workspace_lock._acquire_windows(self.workspace, policy)
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")
        self.assertEqual(handle.close_calls, 1)
        self.assertEqual(anchor.close_calls, 1)

    def test_windows_release_maps_backend_and_close_failures(self) -> None:
        policy = workspace_lock._load_policy()
        document, payload = workspace_lock._lock_document(policy)
        backend_error = workspace_lock.windows_file_ops.WindowsFileOpsError

        close_error = backend_error("WINDOWS_FILE_OPERATION_FAILED", "close failed")
        anchor = FakeWindowsAnchor(self.workspace, close_error=close_error)
        handle = FakeWindowsHandle(payload=payload, close_error=close_error)
        acquired = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=anchor,
            lock_handle=handle,
            policy=policy,
            document=document,
        )
        with self.assertRaises(workspace_lock.windows_file_ops.WindowsFileOpsError):
            acquired._close_handles()
        self.assertEqual(handle.close_calls, 1)
        self.assertEqual(anchor.close_calls, 1)

        anchor = FakeWindowsAnchor(self.workspace, close_error=close_error)
        acquired = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=anchor,
            lock_handle=FakeWindowsHandle(payload=payload),
            policy=policy,
            document=document,
        )
        with self.assertRaises(workspace_lock.windows_file_ops.WindowsFileOpsError):
            acquired._close_handles()
        self.assertEqual(anchor.close_calls, 1)

        anchor = FakeWindowsAnchor(self.workspace)
        handle = FakeWindowsHandle(payload=payload)
        acquired = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=anchor,
            lock_handle=handle,
            policy=policy,
            document=document,
        )
        handle.operation_error = backend_error(
            "WINDOWS_FILE_OPERATION_FAILED",
            "rename failed",
        )
        requested = FakeWindowsAnchor(self.workspace)
        verification = FakeWindowsHandle(payload=payload)
        with (
            patch.object(
                workspace_lock.windows_file_ops,
                "open_directory_chain_no_reparse",
                return_value=requested,
            ),
            patch.object(
                workspace_lock.windows_file_ops,
                "open_existing_regular_no_reparse",
                return_value=verification,
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_UNSAFE")

        changed = FakeWindowsHandle(payload=payload)
        acquired = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=FakeWindowsAnchor(self.workspace),
            lock_handle=changed,
            policy=policy,
            document=document,
        )
        changed.identity_value = "replacement-lock"
        with patch.object(acquired, "assert_active_owner"):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                acquired.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")

        close_error_lock = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=FakeWindowsAnchor(self.workspace),
            lock_handle=FakeWindowsHandle(payload=payload, close_error=close_error),
            policy=policy,
            document=document,
        )
        with patch.object(
            close_error_lock,
            "assert_active_owner",
            side_effect=workspace_lock.WorkspaceLockError(
                "WORKSPACE_LOCK_CHANGED",
                "changed",
            ),
        ):
            with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
                close_error_lock.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_RELEASE_FAILED")

        unavailable_lock = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=FakeWindowsAnchor(self.workspace),
            lock_handle=FakeWindowsHandle(payload=payload),
            policy=policy,
            document=document,
        )
        unavailable_lock._anchor = None
        with self.assertRaises(workspace_lock.WorkspaceLockError) as caught:
            unavailable_lock.release()
        self.assertEqual(caught.exception.code, "WORKSPACE_LOCK_CHANGED")

        destructor_lock = workspace_lock.WindowsWorkspaceMutationLock(
            workspace=self.workspace,
            anchor=FakeWindowsAnchor(self.workspace, close_error=close_error),
            lock_handle=FakeWindowsHandle(payload=payload, close_error=close_error),
            policy=policy,
            document=document,
        )
        destructor_lock.__del__()

    def test_acquire_routes_to_windows_backend(self) -> None:
        sentinel = object()
        with (
            patch.object(workspace_lock.os, "name", "nt"),
            patch.object(
                workspace_lock,
                "_acquire_windows",
                return_value=sentinel,
            ) as acquire_windows,
        ):
            self.assertIs(workspace_lock.acquire(self.workspace), sentinel)
        acquire_windows.assert_called_once()


if __name__ == "__main__":
    unittest.main()
