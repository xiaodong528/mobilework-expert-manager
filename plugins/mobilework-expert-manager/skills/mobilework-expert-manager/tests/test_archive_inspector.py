from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import archive_inspector
import diagnose_expert


def write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)


class ArchiveInspectorTests(unittest.TestCase):
    def test_resource_limit_blocks_before_crc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "large.zip"
            write_zip(source, [("expert/expert.json", b"x" * 4096)])
            limits = archive_inspector.ArchiveLimits(
                max_entries=10,
                max_total_uncompressed_bytes=1024,
                max_entry_uncompressed_bytes=1024,
                max_compression_ratio=10.0,
                max_path_characters=512,
                max_path_depth=32,
            )
            with mock.patch.object(
                diagnose_expert.archive_inspector,
                "default_limits",
                return_value=limits,
            ), mock.patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("CRC ran")):
                result = diagnose_expert.diagnose(source)
            self.assertFalse(result.ok)
            self.assertIn("ZIP_TOTAL_SIZE_LIMIT", {item.code for item in result.findings})
            payload = result.as_dict()
            self.assertEqual(payload["gates"]["archive"], "failed")
            self.assertEqual(payload["gates"]["contract"], "blocked")
            self.assertEqual(payload["gates"]["portability"], "blocked")
            self.assertEqual(payload["provenance"]["limits"], limits.as_dict())

    def test_path_collisions_and_metadata_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "collisions.zip"
            write_zip(
                source,
                [
                    ("expert/A.txt", b"a"),
                    ("expert/a.txt", b"b"),
                    ("expert/caf\u00e9.txt", b"c"),
                    ("expert/cafe\u0301.txt", b"d"),
                    ("expert/.git/config", b"x"),
                    ("__MACOSX/._expert", b"x"),
                ],
            )
            inspection = archive_inspector.inspect_archive(source)
            codes = {item.code for item in inspection.issues}
            self.assertIn("ZIP_CASE_COLLISION", codes)
            self.assertIn("ZIP_UNICODE_COLLISION", codes)
            self.assertIn("ZIP_GIT_METADATA_FORBIDDEN", codes)
            self.assertIn("ZIP_MACOS_METADATA_FORBIDDEN", codes)

    def test_safe_extract_enforces_actual_byte_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "valid.zip"
            target = Path(temp) / "extract"
            write_zip(source, [("expert/expert.json", b"{}")])
            inspection = archive_inspector.inspect_archive(source)
            self.assertFalse(inspection.errors)
            archive_inspector.safe_extract(source, target, inspection)
            self.assertEqual((target / "expert" / "expert.json").read_bytes(), b"{}")

    def test_mojibake_is_warning_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "mojibake.zip"
            write_zip(source, [("expert/璧勪骇.txt", b"x")])
            inspection = archive_inspector.inspect_archive(source)
            self.assertFalse(inspection.errors)
            self.assertIn("ZIP_FILENAME_MOJIBAKE", {item.code for item in inspection.warnings})

    def test_all_metadata_limits_and_path_guards_are_table_driven(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            limits = archive_inspector.ArchiveLimits(
                max_entries=1,
                max_total_uncompressed_bytes=32,
                max_entry_uncompressed_bytes=16,
                max_compression_ratio=2.0,
                max_path_characters=24,
                max_path_depth=3,
            )
            cases: list[tuple[str, list[tuple[str, bytes]], str]] = [
                (
                    "entry-count",
                    [("expert/a", b"a"), ("expert/b", b"b")],
                    "ZIP_ENTRY_COUNT_LIMIT",
                ),
                ("entry-size", [("expert/a", b"x" * 17)], "ZIP_ENTRY_SIZE_LIMIT"),
                ("total-size", [("expert/a", b"x" * 33)], "ZIP_TOTAL_SIZE_LIMIT"),
                (
                    "compression-ratio",
                    [("expert/a", b"x" * 4096)],
                    "ZIP_COMPRESSION_RATIO_LIMIT",
                ),
                (
                    "path-length",
                    [("expert/" + "a" * 32, b"x")],
                    "ZIP_PATH_LENGTH_LIMIT",
                ),
                (
                    "path-depth",
                    [("expert/a/b/c", b"x")],
                    "ZIP_PATH_DEPTH_LIMIT",
                ),
                ("traversal", [("expert/../escape", b"x")], "ZIP_PATH_ESCAPE"),
                ("windows-name", [("expert/CON.txt", b"x")], "ZIP_WINDOWS_RESERVED_NAME"),
            ]
            for name, entries, expected in cases:
                with self.subTest(name=name):
                    source = root / f"{name}.zip"
                    write_zip(source, entries)
                    codes = {
                        item.code
                        for item in archive_inspector.inspect_archive(
                            source, limits=limits
                        ).issues
                    }
                    self.assertIn(expected, codes)

            duplicate = root / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("expert/a", b"a")
                archive.writestr("expert/a", b"b")
            self.assertIn(
                "ZIP_DUPLICATE_PATH",
                {item.code for item in archive_inspector.inspect_archive(duplicate).issues},
            )

            symlink = root / "symlink.zip"
            info = zipfile.ZipInfo("expert/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr(info, "target")
            self.assertIn(
                "ZIP_SYMLINK_FORBIDDEN",
                {item.code for item in archive_inspector.inspect_archive(symlink).issues},
            )


if __name__ == "__main__":
    unittest.main()
