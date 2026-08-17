from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drift_backup
import manager_contract


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
BACKUP_ID_PATTERN = r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z(?:-[0-9]{3})?$"


class DriftBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name) / ".opencode"
        self.runtime.mkdir()
        original = manager_contract.load_policy()
        self.policy = {
            **original,
            "driftRecovery": {
                "rootName": ".expert-drift-backups",
                "manifestName": "manifest.json",
                "payloadDirectory": "payload",
                "publishProtocol": "posix-exclusive-directory-v1",
                "schemaVersion": 1,
                "dirMode": 448,
                "fileMode": 384,
                "backupIdPattern": BACKUP_ID_PATTERN,
            },
        }
        patcher = mock.patch.object(
            manager_contract,
            "load_policy",
            side_effect=lambda *args, **kwargs: self.policy,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self) -> drift_backup.BackupRecord:
        return drift_backup.create_backup(
            self.runtime,
            "demo-expert",
            HASH_A,
            {
                "agents/demo.md": drift_backup.TargetState(
                    "agents/demo.md", True, b"sensitive-pre-image\n", 0o640
                ),
                "opencode.jsonc": drift_backup.TargetState(
                    "opencode.jsonc", False, None, None
                ),
            },
            HASH_B,
            HASH_C,
        )

    def assert_error(self, code: str, callback) -> None:
        with self.assertRaises(drift_backup.DriftBackupError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_create_load_and_stage_restore_round_trip(self) -> None:
        record = self.create()

        self.assertRegex(record.backup_id, BACKUP_ID_PATTERN)
        self.assertEqual(stat.S_IMODE(record.path.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((record.path / "payload").stat().st_mode), 0o700
        )
        manifest_path = record.path / "manifest.json"
        payload_path = record.path / "payload/000001.bin"
        self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(payload_path.stat().st_mode), 0o600)
        manifest_bytes = manifest_path.read_bytes()
        self.assertNotIn(b"sensitive-pre-image", manifest_bytes)
        self.assertEqual(manifest_bytes[-1:], b"\n")

        snapshot = drift_backup.load_and_verify_backup(
            self.runtime,
            "demo-expert",
            record.backup_id,
            record.backup_sha256,
        )
        self.assertEqual(snapshot.record, record)
        self.assertEqual(
            snapshot.targets_by_path["agents/demo.md"].content,
            b"sensitive-pre-image\n",
        )
        staging = Path(self.temporary.name) / "staging"
        staging.mkdir(mode=0o700)
        staged, stale = drift_backup.stage_restore(snapshot, staging)
        self.assertEqual(stale, ["opencode.jsonc"])
        self.assertEqual(staged["agents/demo.md"].read_bytes(), b"sensitive-pre-image\n")
        self.assertEqual(
            stat.S_IMODE(staged["agents/demo.md"].stat().st_mode), 0o640
        )

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_allocates_collision_suffix_without_overwriting_first_backup(self) -> None:
        fixed = datetime(2026, 8, 4, 1, 2, 3, 456789, tzinfo=timezone.utc)
        with mock.patch.object(drift_backup, "_utc_now", return_value=fixed):
            first = self.create()
            second = self.create()

        self.assertEqual(first.backup_id, "20260804T010203.456789Z")
        self.assertEqual(second.backup_id, "20260804T010203.456789Z-001")
        self.assertTrue(first.path.is_dir())
        self.assertTrue(second.path.is_dir())
        self.assertNotEqual(first.path, second.path)

    @unittest.skipUnless(os.name == "posix", "exclusive directory publish is POSIX-only")
    def test_publish_never_replaces_late_empty_directory_collision(self) -> None:
        policy = drift_backup._policy()
        slug_root = drift_backup._prepare_roots(
            self.runtime,
            "demo-expert",
            policy,
        )
        backup_id = "20260804T010203.456789Z"
        collision = slug_root / backup_id
        collision.mkdir(mode=0o700)
        os.chmod(collision, 0o700)
        identity = (collision.stat().st_dev, collision.stat().st_ino)

        with mock.patch.object(
            drift_backup,
            "_choose_backup_id",
            return_value=backup_id,
        ):
            self.assert_error("DRIFT_BACKUP_ID_COLLISION", self.create)

        self.assertEqual((collision.stat().st_dev, collision.stat().st_ino), identity)
        self.assertEqual(list(collision.iterdir()), [])
        self.assertEqual(list(slug_root.glob(".tmp-*")), [])

    @unittest.skipUnless(os.name == "posix", "exclusive directory publish is POSIX-only")
    def test_publish_backend_loss_with_verified_cleanup_records_attempt(self) -> None:
        real_rename = drift_backup._rename_no_replace
        calls = 0

        def reject_publish_once(parent_fd: int, source: str, target: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise drift_backup.DriftBackupError(
                    "DRIFT_RECOVERY_PLATFORM_BLOCKED",
                    "filesystem rejected no-replace",
                )
            real_rename(parent_fd, source, target)

        with mock.patch.object(
            drift_backup,
            "_rename_no_replace",
            side_effect=reject_publish_once,
        ):
            with self.assertRaises(drift_backup.DriftBackupError) as raised:
                self.create()

        self.assertEqual(raised.exception.code, "DRIFT_RECOVERY_PLATFORM_BLOCKED")
        self.assertTrue(raised.exception.attempted)
        self.assertFalse(raised.exception.committed)
        self.assertTrue(raised.exception.rollback_verified)
        self.assertEqual(raised.exception.recovery_paths, [])
        slug_root = self.runtime / ".expert-drift-backups" / "demo-expert"
        self.assertEqual(list(slug_root.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "exclusive directory publish is POSIX-only")
    def test_persistent_publish_backend_loss_preserves_private_recovery_path(self) -> None:
        blocked = drift_backup.DriftBackupError(
            "DRIFT_RECOVERY_PLATFORM_BLOCKED",
            "filesystem rejected no-replace",
        )
        with mock.patch.object(
            drift_backup,
            "_rename_no_replace",
            side_effect=blocked,
        ):
            with self.assertRaises(drift_backup.DriftBackupError) as raised:
                self.create()

        self.assertEqual(raised.exception.code, "DRIFT_BACKUP_CLEANUP_FAILED")
        self.assertTrue(raised.exception.attempted)
        self.assertFalse(raised.exception.committed)
        self.assertFalse(raised.exception.rollback_verified)
        self.assertEqual(len(raised.exception.recovery_paths), 1)
        recovery_path = Path(raised.exception.recovery_paths[0])
        self.assertTrue(recovery_path.is_dir())
        self.assertTrue(recovery_path.name.startswith(".tmp-"))
        self.assertEqual(stat.S_IMODE(recovery_path.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "exclusive directory cleanup is POSIX-only")
    def test_post_quarantine_cleanup_failure_reports_current_directory(self) -> None:
        parent = self.runtime / "cleanup-parent"
        parent.mkdir(mode=0o700)
        published = parent / "published"
        published.mkdir(mode=0o700)
        (published / "payload.bin").write_bytes(b"preserve")
        identity = drift_backup._directory_identity(published.stat())

        with mock.patch.object(
            drift_backup,
            "_clear_directory_fd",
            side_effect=OSError("injected cleanup failure"),
        ):
            with self.assertRaises(drift_backup.DriftBackupError) as raised:
                drift_backup._remove_failed_publish(published, parent, identity)

        self.assertEqual(raised.exception.code, "DRIFT_BACKUP_CLEANUP_FAILED")
        self.assertEqual(len(raised.exception.recovery_paths), 1)
        recovery_path = Path(raised.exception.recovery_paths[0])
        self.assertTrue(recovery_path.is_dir())
        self.assertTrue(recovery_path.name.startswith(".published.failed-"))
        self.assertFalse(published.exists())
        self.assertFalse(raised.exception.durability_unverified)

    @unittest.skipUnless(os.name == "posix", "exclusive directory cleanup is POSIX-only")
    def test_cleanup_fsync_failure_reports_parent_durability_boundary(self) -> None:
        parent = self.runtime / "durability-parent"
        parent.mkdir(mode=0o700)
        published = parent / "published"
        published.mkdir(mode=0o700)
        identity = drift_backup._directory_identity(published.stat())

        with mock.patch.object(
            drift_backup.os,
            "fsync",
            side_effect=OSError("injected fsync failure"),
        ):
            with self.assertRaises(drift_backup.DriftBackupError) as raised:
                drift_backup._remove_failed_publish(published, parent, identity)

        self.assertEqual(raised.exception.code, "DRIFT_BACKUP_CLEANUP_FAILED")
        self.assertEqual(raised.exception.recovery_paths, [str(parent)])
        self.assertTrue(raised.exception.durability_unverified)
        self.assertTrue(parent.is_dir())
        self.assertFalse(published.exists())
        self.assertEqual(list(parent.glob(".published.failed-*")), [])

    @unittest.skipUnless(os.name == "posix", "exclusive directory publish is POSIX-only")
    def test_failed_publish_readback_removes_exact_new_backup(self) -> None:
        injected = drift_backup.DriftBackupError(
            "DRIFT_BACKUP_HASH_MISMATCH",
            "injected readback failure",
        )
        with mock.patch.object(
            drift_backup,
            "load_and_verify_backup",
            side_effect=injected,
        ):
            self.assert_error("DRIFT_BACKUP_HASH_MISMATCH", self.create)

        slug_root = self.runtime / ".expert-drift-backups" / "demo-expert"
        self.assertTrue(slug_root.is_dir())
        self.assertEqual(list(slug_root.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "exclusive directory publish is POSIX-only")
    def test_failed_readback_never_deletes_replacement_directory(self) -> None:
        injected = drift_backup.DriftBackupError(
            "DRIFT_BACKUP_HASH_MISMATCH",
            "injected readback failure",
        )

        def replace_then_fail(
            runtime_dir: Path,
            slug: str,
            backup_id: str,
            expected_sha256: str,
        ) -> drift_backup.BackupSnapshot:
            del runtime_dir, expected_sha256
            slug_root = self.runtime / ".expert-drift-backups" / slug
            published = slug_root / backup_id
            published.rename(slug_root / f"preserved-{backup_id}")
            published.mkdir(mode=0o700)
            os.chmod(published, 0o700)
            marker = published / "replacement-sentinel"
            marker.write_text("preserve\n", encoding="utf-8")
            raise injected

        with mock.patch.object(
            drift_backup,
            "load_and_verify_backup",
            side_effect=replace_then_fail,
        ):
            self.assert_error("DRIFT_BACKUP_CLEANUP_FAILED", self.create)

        slug_root = self.runtime / ".expert-drift-backups" / "demo-expert"
        replacements = [
            path
            for path in slug_root.iterdir()
            if (path / "replacement-sentinel").is_file()
        ]
        self.assertEqual(len(replacements), 1)
        self.assertEqual(
            (replacements[0] / "replacement-sentinel").read_text(encoding="utf-8"),
            "preserve\n",
        )

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_rejects_wrong_hash_and_target_traversal(self) -> None:
        record = self.create()
        self.assert_error(
            "DRIFT_BACKUP_HASH_MISMATCH",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                record.backup_id,
                "d" * 64,
            ),
        )
        self.assert_error(
            "DRIFT_BACKUP_ARGUMENT_INVALID",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                "../" + "a" * 20,
                record.backup_sha256,
            ),
        )
        self.assert_error(
            "DRIFT_BACKUP_TARGET_INVALID",
            lambda: drift_backup.create_backup(
                self.runtime,
                "demo-expert",
                HASH_A,
                {
                    "../outside": drift_backup.TargetState(
                        "../outside", True, b"no", 0o600
                    )
                },
                HASH_B,
                HASH_C,
            ),
        )

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_rejects_tampered_payload_and_manifest_fields(self) -> None:
        record = self.create()
        payload = record.path / "payload/000001.bin"
        payload.write_bytes(b"tampered\n")
        os.chmod(payload, 0o600)
        self.assert_error(
            "DRIFT_BACKUP_PAYLOAD_MISMATCH",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                record.backup_id,
                record.backup_sha256,
            ),
        )

        second = self.create()
        manifest_path = second.path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["unknown"] = True
        manifest_path.write_bytes(manager_contract.canonical_json_bytes(manifest) + b"\n")
        os.chmod(manifest_path, 0o600)
        self.assert_error(
            "DRIFT_BACKUP_MANIFEST_INVALID",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                second.backup_id,
                second.backup_sha256,
            ),
        )

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_rejects_extra_missing_symlink_and_unsafe_permissions(self) -> None:
        record = self.create()
        extra = record.path / "payload/extra.bin"
        extra.write_bytes(b"extra")
        os.chmod(extra, 0o600)
        self.assert_error(
            "DRIFT_BACKUP_CONTENTS_INVALID",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                record.backup_id,
                record.backup_sha256,
            ),
        )

        second = self.create()
        (second.path / "payload/000001.bin").unlink()
        self.assert_error(
            "DRIFT_BACKUP_MANIFEST_INVALID",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                second.backup_id,
                second.backup_sha256,
            ),
        )

        third = self.create()
        os.chmod(third.path / "manifest.json", 0o644)
        self.assert_error(
            "DRIFT_BACKUP_PERMISSION_INVALID",
            lambda: drift_backup.load_and_verify_backup(
                self.runtime,
                "demo-expert",
                third.backup_id,
                third.backup_sha256,
            ),
        )

        if hasattr(os, "symlink"):
            fourth = self.create()
            payload = fourth.path / "payload/000001.bin"
            payload.unlink()
            os.symlink(Path(self.temporary.name) / "outside", payload)
            self.assert_error(
                "DRIFT_BACKUP_UNSAFE",
                lambda: drift_backup.load_and_verify_backup(
                    self.runtime,
                    "demo-expert",
                    fourth.backup_id,
                    fourth.backup_sha256,
                ),
            )

    @unittest.skipUnless(os.name == "posix", "drift recovery is POSIX-only")
    def test_restore_staging_rejects_existing_target_and_symlink_parent(self) -> None:
        record = self.create()
        snapshot = drift_backup.load_and_verify_backup(
            self.runtime,
            "demo-expert",
            record.backup_id,
            record.backup_sha256,
        )
        staging = Path(self.temporary.name) / "staging-existing"
        (staging / "agents").mkdir(parents=True)
        (staging / "agents/demo.md").write_text("existing", encoding="utf-8")
        self.assert_error(
            "DRIFT_RESTORE_STAGING_INVALID",
            lambda: drift_backup.stage_restore(snapshot, staging),
        )

        if hasattr(os, "symlink"):
            symlink_staging = Path(self.temporary.name) / "staging-symlink"
            symlink_staging.mkdir()
            outside = Path(self.temporary.name) / "outside-parent"
            outside.mkdir()
            os.symlink(outside, symlink_staging / "agents")
            self.assert_error(
                "DRIFT_RESTORE_STAGING_INVALID",
                lambda: drift_backup.stage_restore(snapshot, symlink_staging),
            )
            self.assertFalse((outside / "demo.md").exists())

    def test_windows_backend_is_policy_blocked_before_writes(self) -> None:
        with mock.patch.object(drift_backup, "_platform_supported", return_value=False):
            self.assert_error("DRIFT_RECOVERY_PLATFORM_BLOCKED", self.create)
        self.assertFalse((self.runtime / ".expert-drift-backups").exists())

    def test_policy_is_loaded_lazily_and_validated(self) -> None:
        self.policy["driftRecovery"]["fileMode"] = 0o644
        callback = self.create if os.name == "posix" else drift_backup._policy
        self.assert_error("DRIFT_BACKUP_POLICY_INVALID", callback)
        self.assertFalse((self.runtime / ".expert-drift-backups").exists())

    def test_target_state_hash_is_canonical_and_excludes_original_bytes(self) -> None:
        secret = b"do-not-serialize-this-secret"
        targets = {
            "opencode.jsonc": drift_backup.TargetState(
                "opencode.jsonc", False, None, None
            ),
            "agents/demo.md": drift_backup.TargetState(
                "agents/demo.md", True, secret, 0o640
            ),
        }
        expected = manager_contract.canonical_json_sha256(
            {
                "targets": [
                    {
                        "relativePath": "agents/demo.md",
                        "present": True,
                        "size": len(secret),
                        "sha256": hashlib.sha256(secret).hexdigest(),
                        "mode": 0o640,
                    },
                    {
                        "relativePath": "opencode.jsonc",
                        "present": False,
                        "size": None,
                        "sha256": None,
                        "mode": None,
                    },
                ]
            },
            domain="mobilework-drift-target-state-v1",
        )

        self.assertEqual(drift_backup.target_state_sha256(targets), expected)
        self.assertEqual(
            drift_backup.target_state_sha256(dict(reversed(list(targets.items())))),
            expected,
        )
        self.assertNotEqual(
            drift_backup.target_state_sha256(
                {
                    **targets,
                    "agents/demo.md": drift_backup.TargetState(
                        "agents/demo.md", True, secret + b"!", 0o640
                    ),
                }
            ),
            expected,
        )
        self.assertEqual(
            drift_backup.target_state_sha256({}),
            manager_contract.canonical_json_sha256(
                {"targets": []}, domain="mobilework-drift-target-state-v1"
            ),
        )

    def test_receipt_set_hash_is_canonical_and_strict(self) -> None:
        files = {
            "second-expert.json": b'{"secret":"redacted-by-hash"}\n',
            "first-expert.json": b'{"contract":3}\n',
        }
        expected = manager_contract.canonical_json_sha256(
            {
                "files": [
                    {
                        "filename": "first-expert.json",
                        "size": len(files["first-expert.json"]),
                        "sha256": hashlib.sha256(
                            files["first-expert.json"]
                        ).hexdigest(),
                    },
                    {
                        "filename": "second-expert.json",
                        "size": len(files["second-expert.json"]),
                        "sha256": hashlib.sha256(
                            files["second-expert.json"]
                        ).hexdigest(),
                    },
                ]
            },
            domain="mobilework-drift-receipt-set-v1",
        )
        self.assertEqual(drift_backup.receipt_set_sha256(files), expected)
        self.assertEqual(
            drift_backup.receipt_set_sha256(dict(reversed(list(files.items())))),
            expected,
        )
        self.assertEqual(
            drift_backup.receipt_set_sha256({}),
            manager_contract.canonical_json_sha256(
                {"files": []}, domain="mobilework-drift-receipt-set-v1"
            ),
        )
        for invalid in (
            "../first-expert.json",
            "nested/first-expert.json",
            "first_expert.json",
            ".json",
            "first-expert.JSON",
        ):
            with self.subTest(invalid=invalid):
                self.assert_error(
                    "DRIFT_RECEIPT_SET_INVALID",
                    lambda invalid=invalid: drift_backup.receipt_set_sha256(
                        {invalid: b"{}"}
                    ),
                )
        self.assert_error(
            "DRIFT_RECEIPT_SET_INVALID",
            lambda: drift_backup.receipt_set_sha256(
                {"first-expert.json": bytearray(b"{}")}
            ),
        )


if __name__ == "__main__":
    unittest.main()
