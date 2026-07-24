from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import archive_inspector
import ooxml_inspector


def workbook(path: Path, *, macro: bool = False, payload: bytes = b"<workbook/>") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", payload)
        if macro:
            archive.writestr("xl/vbaProject.bin", b"macro")


class OoxmlInspectorTests(unittest.TestCase):
    def test_macro_is_reported_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "book.xlsm"
            workbook(path, macro=True)
            inspection = ooxml_inspector.inspect_workbook(path)
            self.assertFalse(inspection.errors)
            self.assertIn("OOXML_MACRO_PRESENT", {item.code for item in inspection.warnings})

    def test_limit_failure_blocks_workbook_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "book.xlsx"
            workbook(path, payload=b"x" * 4096)
            limits = archive_inspector.ArchiveLimits(
                max_entries=10,
                max_total_uncompressed_bytes=1024,
                max_entry_uncompressed_bytes=1024,
                max_compression_ratio=10.0,
                max_path_characters=512,
                max_path_depth=32,
            )
            inspection = ooxml_inspector.inspect_workbook(path, limits=limits)
            self.assertTrue(inspection.errors)
            self.assertIn("ZIP_TOTAL_SIZE_LIMIT", {item.code for item in inspection.errors})

    def test_external_links_and_embedded_objects_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "active-content.xlsx"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("[Content_Types].xml", b"<Types/>")
                archive.writestr("xl/workbook.xml", b"<workbook/>")
                archive.writestr("xl/externalLinks/externalLink1.xml", b"<externalLink/>")
                archive.writestr("xl/embeddings/object1.bin", b"embedded")
            codes = {item.code for item in ooxml_inspector.inspect_workbook(path).warnings}
            self.assertIn("OOXML_EXTERNAL_LINK_PRESENT", codes)
            self.assertIn("OOXML_EMBEDDED_OBJECT_PRESENT", codes)


if __name__ == "__main__":
    unittest.main()
