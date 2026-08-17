from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import create_expert


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


class PackageLockProtocolTests(unittest.TestCase):
    def test_publishes_exact_v2_owner_with_heartbeat_and_restricted_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            with create_expert.package_lock(
                root,
                "contract-review",
                heartbeat_seconds=0.005,
                stale_seconds=0.05,
            ):
                owner_path = lock_path / create_expert.PACKAGE_LOCK_OWNER
                first = json.loads(owner_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    set(first),
                    {"ownerToken", "pid", "createdAt", "heartbeatAt", "protocolVersion"},
                )
                self.assertEqual(first["protocolVersion"], 2)
                self.assertEqual(first["pid"], os.getpid())
                self.assertRegex(first["ownerToken"], r"^[a-f0-9]{32}$")
                self.assertEqual(first["createdAt"], first["heartbeatAt"])
                marker_path = lock_path / create_expert.PACKAGE_LOCK_UNPUBLISHED_OWNER
                self.assertEqual(
                    marker_path.read_text(encoding="ascii"),
                    f"{first['ownerToken']}\n",
                )
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode) & 0o077, 0)
                    self.assertEqual(stat.S_IMODE(owner_path.stat().st_mode) & 0o077, 0)
                    self.assertEqual(stat.S_IMODE(marker_path.stat().st_mode) & 0o077, 0)
                deadline = time.monotonic() + 2.0
                current = first
                while (
                    current["heartbeatAt"] == first["heartbeatAt"]
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                    current = json.loads(owner_path.read_text(encoding="utf-8"))
                self.assertNotEqual(current["heartbeatAt"], first["heartbeatAt"])
            self.assertFalse(lock_path.exists())

    def test_retries_transient_windows_sharing_conflicts_for_lock_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            target = root / "target"
            source.write_text("new", encoding="utf-8")
            target.write_text("old", encoding="utf-8")
            real_replace = os.replace
            attempts = 0

            def flaky_replace(source_path: Path, target_path: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 4:
                    raise OSError(errno.EACCES, "sharing conflict")
                real_replace(source_path, target_path)

            with (
                patch.object(create_expert.os, "replace", side_effect=flaky_replace),
                patch.object(create_expert.time, "sleep") as sleep_mock,
            ):
                create_expert.replace_lock_entry(source, target, platform_name="nt")

            self.assertEqual(attempts, 4)
            self.assertEqual(sleep_mock.call_count, 3)
            sleep_mock.assert_called_with(0.002)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertFalse(source.exists())

    def test_retries_transient_windows_sharing_conflicts_for_quarantine_cleanup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            quarantine = Path(temp) / "quarantine"
            quarantine.mkdir()
            (quarantine / create_expert.PACKAGE_LOCK_UNPUBLISHED_OWNER).write_text(
                "owner\n",
                encoding="ascii",
            )
            real_rmtree = shutil.rmtree
            attempts = 0

            def flaky_rmtree(target: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 4:
                    raise OSError(errno.EBUSY, "sharing conflict")
                real_rmtree(target)

            with (
                patch.object(create_expert.shutil, "rmtree", side_effect=flaky_rmtree),
                patch.object(create_expert.time, "sleep") as sleep_mock,
            ):
                create_expert.remove_lock_quarantine(
                    quarantine,
                    platform_name="nt",
                )

            self.assertEqual(attempts, 4)
            self.assertEqual(sleep_mock.call_count, 3)
            sleep_mock.assert_called_with(0.002)
            self.assertFalse(quarantine.exists())

    def test_unknown_and_active_owners_are_never_stolen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            lock_path.mkdir()
            with self.assertRaisesRegex(SystemExit, "timed out waiting"):
                with create_expert.package_lock(
                    root,
                    "contract-review",
                    timeout_seconds=0.015,
                    heartbeat_seconds=0.005,
                    stale_seconds=0.01,
                    poll_seconds=0.002,
                ):
                    pass
            self.assertTrue(lock_path.exists())

            write_json(
                lock_path / create_expert.PACKAGE_LOCK_OWNER,
                {
                    "ownerToken": "active-owner",
                    "pid": os.getpid(),
                    "createdAt": "2000-01-01T00:00:00.000Z",
                    "heartbeatAt": "2000-01-01T00:00:00.000Z",
                    "protocolVersion": 2,
                },
            )
            with self.assertRaisesRegex(SystemExit, "timed out waiting"):
                with create_expert.package_lock(
                    root,
                    "contract-review",
                    timeout_seconds=0.015,
                    heartbeat_seconds=0.005,
                    stale_seconds=0.01,
                    poll_seconds=0.002,
                ):
                    pass
            self.assertEqual(
                create_expert.read_lock_owner(lock_path)["ownerToken"],
                "active-owner",
            )

    def test_quarantines_stale_v2_and_legacy_owners(self) -> None:
        for owner in (
            {
                "ownerToken": "stale-v2",
                "pid": 999_999_999,
                "createdAt": "2000-01-01T00:00:00.000Z",
                "heartbeatAt": "2000-01-01T00:00:00.000Z",
                "protocolVersion": 2,
            },
            {
                "pid": 999_999_999,
                "token": "stale-legacy",
                "startedAt": "2000-01-01T00:00:00.000Z",
            },
        ):
            with self.subTest(owner=owner):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    lock_path = create_expert.package_lock_path(root, "contract-review")
                    lock_path.mkdir()
                    write_json(lock_path / create_expert.PACKAGE_LOCK_OWNER, owner)
                    with create_expert.package_lock(
                        root,
                        "contract-review",
                        timeout_seconds=0.2,
                        heartbeat_seconds=0.005,
                        stale_seconds=0.01,
                        poll_seconds=0.002,
                    ):
                        self.assertEqual(
                            create_expert.read_lock_owner(lock_path)["kind"],
                            "v2",
                        )
                    self.assertFalse(lock_path.exists())
                    self.assertEqual(list(root.iterdir()), [])

    def test_release_preserves_a_replacement_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                with create_expert.package_lock(
                    root,
                    "contract-review",
                    heartbeat_seconds=0.005,
                    stale_seconds=0.05,
                ):
                    shutil.rmtree(lock_path)
                    lock_path.mkdir()
                    timestamp = create_expert.utc_now()
                    write_json(
                        lock_path / create_expert.PACKAGE_LOCK_OWNER,
                        {
                            "ownerToken": "replacement-owner",
                            "pid": os.getpid(),
                            "createdAt": timestamp,
                            "heartbeatAt": timestamp,
                            "protocolVersion": 2,
                        },
                    )
            self.assertEqual(
                create_expert.read_lock_owner(lock_path)["ownerToken"],
                "replacement-owner",
            )

    def test_owner_publication_failure_removes_only_the_unpublished_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            with patch.object(create_expert, "write_lock_owner", side_effect=OSError("injected")):
                with self.assertRaisesRegex(OSError, "injected"):
                    with create_expert.package_lock(root, "contract-review"):
                        pass
            self.assertFalse(lock_path.exists())

    def test_owner_publication_failure_never_deletes_a_replacement_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")

            def replace_then_fail(path: Path, owner: dict[str, object]) -> None:
                del owner
                shutil.rmtree(path)
                path.mkdir()
                timestamp = create_expert.utc_now()
                write_json(
                    path / create_expert.PACKAGE_LOCK_OWNER,
                    {
                        "ownerToken": "replacement-owner",
                        "pid": os.getpid(),
                        "createdAt": timestamp,
                        "heartbeatAt": timestamp,
                        "protocolVersion": 2,
                    },
                )
                raise OSError("injected publication failure")

            real_stat = Path.stat

            def force_reused_directory_identity(
                candidate: Path,
                *args: object,
                **kwargs: object,
            ) -> object:
                metadata = real_stat(candidate, *args, **kwargs)
                if candidate == lock_path or candidate.name.startswith(
                    f"{lock_path.name}.unpublished-"
                ):
                    return SimpleNamespace(st_dev=1, st_ino=1)
                return metadata

            with (
                patch.object(Path, "stat", force_reused_directory_identity),
                patch.object(
                    create_expert,
                    "write_lock_owner",
                    side_effect=replace_then_fail,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "changed before owner publication"):
                    with create_expert.package_lock(root, "contract-review"):
                        pass
            self.assertEqual(
                create_expert.read_lock_owner(lock_path)["ownerToken"],
                "replacement-owner",
            )
            self.assertEqual(
                list(root.glob(f"{lock_path.name}.unpublished-*")),
                [],
            )

    def test_release_cleanup_failure_keeps_owner_named_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            with patch.object(create_expert.shutil, "rmtree", side_effect=OSError("injected cleanup")):
                with self.assertRaisesRegex(OSError, "injected cleanup"):
                    with create_expert.package_lock(root, "contract-review"):
                        pass
            self.assertFalse(lock_path.exists())
            quarantines = list(root.glob(f"{lock_path.name}.release-*"))
            self.assertEqual(len(quarantines), 1)
            owner = create_expert.read_lock_owner(quarantines[0])
            self.assertIsNotNone(owner)
            self.assertIn(owner["ownerToken"], quarantines[0].name)

    def test_process_crash_after_owner_publish_is_recovered_only_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = create_expert.package_lock_path(root, "contract-review")
            program = "\n".join(
                [
                    "import importlib.util",
                    "import os",
                    "import pathlib",
                    "import sys",
                    "script = pathlib.Path(sys.argv[1])",
                    "sys.path.insert(0, str(script.parent))",
                    "spec = importlib.util.spec_from_file_location('crashing_generator', script)",
                    "module = importlib.util.module_from_spec(spec)",
                    "spec.loader.exec_module(module)",
                    "with module.package_lock(pathlib.Path(sys.argv[2]), sys.argv[3], heartbeat_seconds=0.005, stale_seconds=0.05):",
                    "    print('owner-published', flush=True)",
                    "    os._exit(91)",
                ]
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    program,
                    str(SCRIPTS / "create_expert.py"),
                    str(root),
                    "contract-review",
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(completed.returncode, 91)
            self.assertEqual(completed.stdout.strip(), "owner-published")
            crashed_owner = create_expert.read_lock_owner(lock_path)
            self.assertIsNotNone(crashed_owner)
            heartbeat_at = create_expert.valid_lock_timestamp(
                crashed_owner["heartbeatAt"]
            )
            self.assertIsNotNone(heartbeat_at)
            observed_at = time.time()
            stale_margin = 0.1
            stale_seconds = max(0.0, observed_at - heartbeat_at) + stale_margin
            self.assertFalse(
                create_expert.lock_owner_is_stale(
                    crashed_owner,
                    stale_seconds,
                    now=observed_at,
                )
            )

            time.sleep(stale_margin + 0.02)
            with create_expert.package_lock(
                root,
                "contract-review",
                timeout_seconds=0.3,
                heartbeat_seconds=0.005,
                stale_seconds=stale_seconds,
                poll_seconds=0.002,
            ):
                self.assertNotEqual(
                    create_expert.read_lock_owner(lock_path)["ownerToken"],
                    crashed_owner["ownerToken"],
                )
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
