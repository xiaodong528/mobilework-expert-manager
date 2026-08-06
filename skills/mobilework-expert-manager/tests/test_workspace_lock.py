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


if __name__ == "__main__":
    unittest.main()
