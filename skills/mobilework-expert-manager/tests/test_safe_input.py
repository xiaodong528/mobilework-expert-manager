from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import provenance
import safe_input
import validate_expert
import validation_result


def limits(**overrides: int) -> safe_input.InputLimits:
    values = {
        "max_entries": 32,
        "max_total_bytes": 4096,
        "max_file_bytes": 2048,
        "max_path_characters": 128,
        "max_path_depth": 8,
    }
    values.update(overrides)
    return safe_input.InputLimits(**values)


def expected_tree(
    files: dict[str, bytes],
    directories: tuple[str, ...] = (),
) -> str:
    digest = hashlib.sha256()
    for relative in sorted(directories):
        encoded = relative.encode("utf-8")
        digest.update(b"D")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    for relative, content in sorted(files.items()):
        encoded = relative.encode("utf-8")
        digest.update(b"F")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


class SafeInputTests(unittest.TestCase):
    def test_directory_snapshot_contains_deterministic_file_and_tree_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            (root / "nested").mkdir(parents=True)
            (root / "a.txt").write_bytes(b"alpha")
            (root / "nested/b.txt").write_bytes(b"beta")

            snapshot = safe_input.inspect(root, limits())

            self.assertEqual(snapshot.kind, "directory")
            self.assertEqual(snapshot.entry_count, 3)
            self.assertEqual(snapshot.file_count, 2)
            self.assertEqual(snapshot.total_bytes, 9)
            self.assertEqual(
                snapshot.sha256,
                expected_tree(
                    {"a.txt": b"alpha", "nested/b.txt": b"beta"},
                    ("nested",),
                ),
            )
            self.assertEqual(
                snapshot.file("nested/b.txt").sha256,
                hashlib.sha256(b"beta").hexdigest(),
            )

    def test_materialize_preserves_empty_directories_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            (root / ".opencode/skills").mkdir(parents=True)
            (root / "expert.json").write_bytes(b"{}")
            snapshot = safe_input.inspect(root, limits())
            target = Path(temp) / "materialized"

            snapshot.materialize(target)

            self.assertTrue((target / ".opencode/skills").is_dir())
            self.assertEqual((target / "expert.json").read_bytes(), b"{}")

    def test_tree_hash_includes_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            root.mkdir()
            (root / "expert.json").write_bytes(b"{}")
            without_empty = safe_input.inspect(root, limits())
            (root / ".opencode/skills").mkdir(parents=True)
            with_empty = safe_input.inspect(root, limits())

            self.assertNotEqual(without_empty.sha256, with_empty.sha256)

    def test_file_snapshot_uses_no_follow_and_nonblocking_open_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input.txt"
            source.write_bytes(b"content")
            original_open = os.open
            with mock.patch.object(safe_input.os, "open", wraps=original_open) as opened:
                snapshot = safe_input.inspect(source, limits())

            self.assertEqual(snapshot.kind, "file")
            self.assertEqual(snapshot.sha256, hashlib.sha256(b"content").hexdigest())
            flags = opened.call_args.args[1]
            self.assertEqual(flags & getattr(os, "O_NONBLOCK", 0), getattr(os, "O_NONBLOCK", 0))
            self.assertEqual(flags & getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_NOFOLLOW", 0))

    def test_open_flags_include_binary_mode_when_the_platform_defines_it(self) -> None:
        binary_flag = 1 << 29
        with mock.patch.object(
            safe_input.os, "O_BINARY", binary_flag, create=True
        ):
            flags = safe_input._open_flags()

        self.assertEqual(flags & binary_flag, binary_flag)

    def test_non_utf8_posix_name_returns_a_stable_input_finding(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX surrogateescape filenames are unavailable on Windows")
        with self.assertRaises(safe_input.InputInspectionError) as caught:
            safe_input._check_path("invalid-\udcff-name", limits())

        self.assertEqual(
            caught.exception.code,
            "INPUT_PATH_ENCODING_FORBIDDEN",
        )

    def test_raw_entry_limit_stops_scandir_before_sort_or_exclusions(self) -> None:
        class GuardedScandir:
            def __init__(self, root: Path, names: list[str]) -> None:
                self.root = root
                self.names = names
                self.consumed = 0

            def __enter__(self) -> GuardedScandir:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self) -> GuardedScandir:
                return self

            def __next__(self) -> SimpleNamespace:
                if self.consumed >= 3:
                    raise AssertionError("scandir consumed an entry after maxEntries + 1")
                name = self.names[self.consumed]
                self.consumed += 1
                return SimpleNamespace(name=name, path=str(self.root / name))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            root.mkdir()
            scans = (
                ("ordinary", ["a", "b", "c"]),
                ("package-root-with-git", [".git", "a", "b"]),
            )
            for name, entry_names in scans:
                guarded = GuardedScandir(root, entry_names)
                with self.subTest(name=name), mock.patch.object(
                    safe_input.os, "scandir", return_value=guarded
                ), mock.patch.object(
                    safe_input.os,
                    "open",
                    side_effect=AssertionError("content was opened"),
                ):
                    with self.assertRaises(safe_input.InputInspectionError) as caught:
                        safe_input.inspect(
                            root,
                            limits(max_entries=2),
                            exclusions=safe_input.default_exclusions(),
                        )

                self.assertEqual(caught.exception.code, "INPUT_ENTRY_COUNT_LIMIT")
                self.assertEqual(guarded.consumed, 3)

    def test_snapshot_content_survives_source_change_without_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            root.mkdir()
            source = root / "message.txt"
            source.write_bytes("原始内容".encode())
            snapshot = safe_input.inspect(root, limits())

            source.write_bytes("已被替换".encode())
            source.unlink()
            with mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("source was reopened")
            ):
                self.assertEqual(
                    snapshot.read_bytes("message.txt"), "原始内容".encode()
                )
                self.assertEqual(snapshot.read_text("message.txt"), "原始内容")
                self.assertEqual(
                    snapshot.file("message.txt").content, "原始内容".encode()
                )

    def test_single_file_snapshot_read_helpers_do_not_require_a_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "message.txt"
            source.write_text("snapshot", encoding="utf-8")
            snapshot = safe_input.inspect(source, limits())
            source.write_text("changed", encoding="utf-8")

            with mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("source was reopened")
            ):
                self.assertEqual(snapshot.read_bytes(), b"snapshot")
                self.assertEqual(snapshot.read_text(), "snapshot")

    def test_symlink_and_fifo_are_rejected_before_any_content_open(self) -> None:
        if not hasattr(os, "symlink") or not hasattr(os, "mkfifo"):
            self.skipTest("symlink or FIFO support is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            root.mkdir()
            (root / "a-regular.txt").write_bytes(b"must not be opened")
            (root / "z-link.txt").symlink_to(root / "a-regular.txt")
            with mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("content was opened")
            ):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect(root, limits())
            self.assertEqual(caught.exception.code, "INPUT_SYMLINK_FORBIDDEN")

            (root / "z-link.txt").unlink()
            os.mkfifo(root / "z-pipe")
            with mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("content was opened")
            ):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect(root, limits())
            self.assertEqual(caught.exception.code, "INPUT_SPECIAL_FILE_FORBIDDEN")

    def test_socket_and_device_modes_are_rejected_before_content_open(self) -> None:
        for name, mode in (
            ("socket", stat.S_IFSOCK),
            ("character-device", stat.S_IFCHR),
            ("block-device", stat.S_IFBLK),
        ):
            special = os.stat_result((mode | 0o600, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            with self.subTest(name=name), mock.patch.object(
                safe_input.os, "lstat", return_value=special
            ), mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("content was opened")
            ):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect(Path(name), limits())
                self.assertEqual(
                    caught.exception.code, "INPUT_SPECIAL_FILE_FORBIDDEN"
                )

    def test_reparse_point_attribute_is_rejected_before_mode_is_read(self) -> None:
        reparse = mock.Mock()
        reparse.st_file_attributes = safe_input.REPARSE_POINT_ATTRIBUTE
        with mock.patch.object(safe_input.os, "lstat", return_value=reparse):
            with self.assertRaises(safe_input.InputInspectionError) as caught:
                safe_input.inspect(Path("reparse-root"), limits())
        self.assertEqual(caught.exception.code, "INPUT_REPARSE_POINT_FORBIDDEN")

    def test_resource_limits_fail_during_metadata_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cases: list[tuple[str, Path, safe_input.InputLimits, str]] = []

            count = base / "count"
            count.mkdir()
            (count / "a").write_bytes(b"a")
            (count / "b").write_bytes(b"b")
            cases.append(("count", count, limits(max_entries=1), "INPUT_ENTRY_COUNT_LIMIT"))

            file_size = base / "file-size"
            file_size.mkdir()
            (file_size / "large").write_bytes(b"12345")
            cases.append(("file-size", file_size, limits(max_file_bytes=4), "INPUT_FILE_SIZE_LIMIT"))

            total = base / "total"
            total.mkdir()
            (total / "a").write_bytes(b"123")
            (total / "b").write_bytes(b"456")
            cases.append(("total", total, limits(max_total_bytes=5), "INPUT_TOTAL_SIZE_LIMIT"))

            path_length = base / "path-length"
            path_length.mkdir()
            (path_length / "long-name").write_bytes(b"x")
            cases.append(("path-length", path_length, limits(max_path_characters=4), "INPUT_PATH_LENGTH_LIMIT"))

            depth = base / "depth"
            (depth / "a" / "b").mkdir(parents=True)
            cases.append(("depth", depth, limits(max_path_depth=1), "INPUT_PATH_DEPTH_LIMIT"))

            for name, source, active_limits, expected_code in cases:
                with self.subTest(name=name), mock.patch.object(
                    safe_input.os, "open", side_effect=AssertionError("content was opened")
                ):
                    with self.assertRaises(safe_input.InputInspectionError) as caught:
                        safe_input.inspect(source, active_limits)
                    self.assertEqual(caught.exception.code, expected_code)

    def test_change_before_open_is_reported_with_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "changing.txt"
            source.write_bytes(b"before")
            original_lstat = os.lstat
            calls = 0

            def mutate_before_second_lstat(path: os.PathLike[str] | str) -> os.stat_result:
                nonlocal calls
                if Path(path) == source:
                    calls += 1
                    if calls == 2:
                        source.write_bytes(b"after-is-longer")
                return original_lstat(path)

            with mock.patch.object(
                safe_input.os, "lstat", side_effect=mutate_before_second_lstat
            ):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect(source, limits())
            self.assertEqual(caught.exception.code, "INPUT_CHANGED_DURING_SCAN")

    def test_change_while_reading_is_reported_with_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "changing.txt"
            source.write_bytes(b"before")
            original_read = os.read
            mutated = False

            def mutate_after_read(descriptor: int, count: int) -> bytes:
                nonlocal mutated
                content = original_read(descriptor, count)
                if content and not mutated:
                    mutated = True
                    source.write_bytes(b"after-is-longer")
                return content

            with mock.patch.object(safe_input.os, "read", side_effect=mutate_after_read):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect(source, limits())
            self.assertEqual(caught.exception.code, "INPUT_CHANGED_DURING_SCAN")

    def test_provenance_consumes_snapshot_without_rescanning_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input.txt"
            source.write_bytes(b"trusted snapshot")
            snapshot = safe_input.inspect(source, limits())
            with mock.patch.object(
                provenance,
                "tree_sha256",
                return_value="skill-tree",
            ), mock.patch.object(
                provenance.safe_input,
                "inspect",
                side_effect=AssertionError("input was rescanned"),
            ):
                payload = provenance.collect(input_snapshot=snapshot)

            self.assertEqual(payload["inputSha256"], snapshot.sha256)
            self.assertEqual(payload["invocation"]["inputKind"], "file")
            self.assertEqual(payload["inputInspection"]["status"], "passed")
            self.assertEqual(payload["inputInspection"]["fileCount"], 1)

    def test_validation_result_passes_the_same_snapshot_to_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input.txt"
            source.write_bytes(b"one protected read")
            with mock.patch.object(
                validation_result.provenance,
                "collect",
                return_value={"inputInspection": {"status": "passed"}},
            ) as collected:
                result = validation_result.ValidationResult(input_path=source)

            self.assertIsNotNone(result.input_snapshot)
            self.assertIsNone(result.input_inspection_error)
            self.assertEqual(result.input_snapshot.read_bytes(), b"one protected read")
            self.assertIs(
                collected.call_args.kwargs["input_snapshot"], result.input_snapshot
            )
            self.assertIsNone(collected.call_args.kwargs["input_error"])

    def test_validator_blocks_all_preflight_failures_before_contract_reads(self) -> None:
        policy = {
            "contractVersion": "test",
            "findingCatalogVersion": 2,
        }
        cases = (
            ("symlink", "INPUT_SYMLINK_FORBIDDEN"),
            ("reparse", "INPUT_REPARSE_POINT_FORBIDDEN"),
            ("fifo", "INPUT_SPECIAL_FILE_FORBIDDEN"),
            ("socket", "INPUT_SPECIAL_FILE_FORBIDDEN"),
            ("device", "INPUT_SPECIAL_FILE_FORBIDDEN"),
            ("entries", "INPUT_ENTRY_COUNT_LIMIT"),
            ("file-size", "INPUT_FILE_SIZE_LIMIT"),
            ("total-size", "INPUT_TOTAL_SIZE_LIMIT"),
            ("path-length", "INPUT_PATH_LENGTH_LIMIT"),
            ("path-depth", "INPUT_PATH_DEPTH_LIMIT"),
            ("changed", "INPUT_CHANGED_DURING_SCAN"),
        )
        for name, code in cases:
            failure = safe_input.InputInspectionError(
                code, f"rejected {name} during metadata preflight", name
            )
            with self.subTest(name=name), mock.patch.object(
                validation_result.safe_input, "inspect", side_effect=failure
            ), mock.patch.object(
                provenance.manager_contract, "load_policy", return_value=policy
            ), mock.patch.object(
                provenance, "tree_sha256", return_value="skill-tree"
            ), mock.patch.object(
                validate_expert.contract, "assert_no_symlinks"
            ) as asserted, mock.patch.object(
                validate_expert, "iter_package_paths"
            ) as iterated, mock.patch.object(
                validate_expert, "read_json"
            ) as read_json, mock.patch.object(
                Path, "read_text", side_effect=AssertionError("Path.read_text ran")
            ) as read_text, mock.patch.object(
                Path, "read_bytes", side_effect=AssertionError("Path.read_bytes ran")
            ) as read_bytes:
                result = validate_expert.validate_package(Path(name))

            self.assertEqual([item.code for item in result.findings], [code])
            self.assertEqual(result.gates["contract"], "failed")
            self.assertEqual(result.gates["portability"], "blocked")
            self.assertEqual(result.gates["install"], "blocked")
            self.assertEqual(result.gates["configLoad"], "blocked")
            asserted.assert_not_called()
            iterated.assert_not_called()
            read_json.assert_not_called()
            read_text.assert_not_called()
            read_bytes.assert_not_called()

    def test_validator_distinguishes_a_regular_file_from_a_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "expert.json"
            source.write_text("{}", encoding="utf-8")

            result = validate_expert.validate_package(source)

        self.assertEqual(
            [finding.code for finding in result.findings],
            ["PACKAGE_INPUT_NOT_DIRECTORY"],
        )

    def test_provenance_compatibility_path_records_rejected_input(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO support is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "blocked.pipe"
            os.mkfifo(source)
            with mock.patch.object(
                provenance,
                "tree_sha256",
                return_value="skill-tree",
            ), mock.patch.object(
                safe_input.os, "open", side_effect=AssertionError("FIFO was opened")
            ):
                payload = provenance.collect(input_path=source, limits=limits().as_dict())

            self.assertEqual(payload["inputSha256"], "")
            self.assertEqual(payload["invocation"]["inputKind"], "rejected")
            self.assertEqual(payload["inputInspection"]["status"], "rejected")
            self.assertEqual(
                payload["inputInspection"]["code"], "INPUT_SPECIAL_FILE_FORBIDDEN"
            )

    def test_legacy_tree_hash_exclusions_reuse_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            (root / ".git").mkdir(parents=True)
            (root / "__pycache__").mkdir()
            (root / "kept.txt").write_bytes(b"kept")
            (root / ".git/config").write_bytes(b"ignored")
            (root / "__pycache__/module.pyc").write_bytes(b"ignored")

            original_open = os.open
            with mock.patch.object(
                safe_input.os, "open", wraps=original_open
            ) as opened:
                tree_hash = provenance.tree_sha256(root, limits=limits())
            self.assertEqual(tree_hash, expected_tree({"kept.txt": b"kept"}))
            opened_paths = [Path(call.args[0]) for call in opened.call_args_list]
            self.assertEqual(opened_paths, [root / "kept.txt"])

    def test_default_package_hash_excludes_root_git_and_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            (root / ".git").mkdir(parents=True)
            (root / "__pycache__").mkdir()
            (root / ".cache").mkdir()
            (root / "nested/.git").mkdir(parents=True)
            (root / "kept.txt").write_bytes(b"kept")
            (root / ".git/config").write_bytes(b"git-one")
            (root / "__pycache__/module.pyc").write_bytes(b"cache-one")
            (root / ".cache/state").write_bytes(b"cache-one")
            (root / ".DS_Store").write_bytes(b"finder-one")
            (root / "nested/.git/config").write_bytes(b"nested-one")

            unfiltered = safe_input.inspect(root, limits())
            self.assertEqual(
                [item.relative_path for item in unfiltered.files],
                [
                    ".DS_Store",
                    ".cache/state",
                    ".git/config",
                    "__pycache__/module.pyc",
                    "kept.txt",
                    "nested/.git/config",
                ],
            )

            first = safe_input.inspect_package(root, limits())
            (root / ".git/config").write_bytes(b"git-two-and-longer")
            second = safe_input.inspect_package(root, limits())

            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(
                [item.relative_path for item in second.files],
                [
                    ".DS_Store",
                    ".cache/state",
                    "__pycache__/module.pyc",
                    "kept.txt",
                    "nested/.git/config",
                ],
            )
            self.assertEqual(
                [
                    (item.relative_path, item.kind)
                    for item in second.excluded_entries
                ],
                [(".git", "directory")],
            )
            self.assertGreater(second.excluded_entries[0].inode, 0)

            (root / ".cache/state").write_bytes(b"cache-two")
            cache_changed = safe_input.inspect_package(root, limits())
            self.assertNotEqual(second.sha256, cache_changed.sha256)

            (root / "nested/.git/config").write_bytes(b"nested-two")
            third = safe_input.inspect_package(root, limits())
            self.assertNotEqual(cache_changed.sha256, third.sha256)

    def test_package_snapshot_does_not_enumerate_root_git_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            git = root / ".git"
            git.mkdir(parents=True)
            for index in range(100):
                (git / f"object-{index}").write_bytes(b"ignored")
            (root / "kept.txt").write_bytes(b"kept")

            original_scandir = os.scandir
            with mock.patch.object(
                safe_input.os, "scandir", wraps=original_scandir
            ) as scanned:
                snapshot = safe_input.inspect_package(
                    root,
                    limits(max_entries=2),
                )

            scanned_paths = [Path(call.args[0]) for call in scanned.call_args_list]
            self.assertEqual(scanned_paths, [root])
            self.assertEqual(
                [item.relative_path for item in snapshot.files],
                ["kept.txt"],
            )
            self.assertEqual(
                [item.relative_path for item in snapshot.excluded_entries],
                [".git"],
            )

    def test_excluded_directory_content_can_change_without_hiding_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            cache = root / "__pycache__"
            cache.mkdir(parents=True)
            (root / "kept.txt").write_bytes(b"kept")
            original_inventory = safe_input._inventory

            def inventory_then_update(*args, **kwargs):
                result = original_inventory(*args, **kwargs)
                (cache / "late.pyc").write_bytes(b"ignored cache")
                return result

            with mock.patch.object(
                safe_input,
                "_inventory",
                side_effect=inventory_then_update,
            ):
                snapshot = safe_input.inspect(
                    root,
                    limits(),
                    exclusions=safe_input.InputExclusions(
                        directory_names=frozenset({"__pycache__"}),
                    ),
                )

            self.assertEqual(
                [item.relative_path for item in snapshot.files],
                ["kept.txt"],
            )

    def test_package_root_git_symlink_and_reparse_point_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            target = Path(temp) / "git-target"
            root.mkdir()
            target.mkdir()
            git = root / ".git"

            if hasattr(os, "symlink"):
                git.symlink_to(target, target_is_directory=True)
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect_package(root, limits())
                self.assertEqual(
                    caught.exception.code,
                    "INPUT_REPARSE_POINT_FORBIDDEN"
                    if os.name == "nt"
                    else "INPUT_SYMLINK_FORBIDDEN",
                )
                git.unlink()

            git.mkdir()
            original_lstat = os.lstat
            reparse = mock.Mock(
                st_file_attributes=safe_input.REPARSE_POINT_ATTRIBUTE
            )

            def lstat_with_reparse(path: os.PathLike[str] | str) -> os.stat_result:
                if Path(path) == git:
                    return reparse
                return original_lstat(path)

            with mock.patch.object(
                safe_input.os, "lstat", side_effect=lstat_with_reparse
            ):
                with self.assertRaises(safe_input.InputInspectionError) as caught:
                    safe_input.inspect_package(root, limits())
            self.assertEqual(
                caught.exception.code,
                "INPUT_REPARSE_POINT_FORBIDDEN",
            )


if __name__ == "__main__":
    unittest.main()
