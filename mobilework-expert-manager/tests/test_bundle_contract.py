from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bundle_contract


class BundleContractTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {
            "schemaVersion": 1, "contractVersion": "2.0.0",
            "generatorVersion": "fixture-generator",
            "packages": [{"file": "a.zip", "slug": "a", "version": "1.0.0", "sha256": "x"}],
            "tests": {"collected": 3, "passed": 2, "failed": 0, "skipped": 1},
            "documents": {"markdown": "summary.md", "docx": "summary.docx"},
        }

    def test_markdown_and_docx_controlled_fields(self) -> None:
        manifest = self.manifest()
        expected = bundle_contract.controlled_fields(manifest)
        text = bundle_contract.render_summary(manifest)
        self.assertEqual(bundle_contract._parse_controlled_text(text), expected)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "summary.docx"
            lines = "".join(f"<w:p><w:r><w:t>{bundle_contract.CONTROL_PREFIX}{key}={value}</w:t></w:r></w:p>" for key, value in expected.items())
            document = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{lines}</w:body></w:document>'
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document)
            extracted = bundle_contract._parse_controlled_text(bundle_contract._docx_text(path))
            self.assertEqual(extracted, expected)

    def test_document_drift_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.manifest()
            manifest["documents"]["docx"] = None
            (root / "summary.md").write_text("MOBILEWORK_BUNDLE_FIELD packageCount=99\n", encoding="utf-8")
            findings = bundle_contract._document_findings(root, manifest)
            self.assertIn("BUNDLE_DOCUMENT_FIELD_DRIFT", {item["code"] for item in findings})

    def test_manifest_shape_extra_zip_and_test_summary_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.manifest()
            manifest["packages"] = []
            manifest["tests"] = {"collected": 2, "passed": 2, "failed": 1, "skipped": 0}
            manifest["documents"] = {"markdown": None, "docx": None}
            (root / bundle_contract.MANIFEST_NAME).write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (root / "extra.zip").write_bytes(b"not-opened-because-undeclared")
            result = bundle_contract.validate_bundle(root)
            codes = {item["code"] for item in result["findings"]}
            self.assertIn("BUNDLE_UNDECLARED_PACKAGE_ZIP", codes)
            self.assertIn("BUNDLE_TEST_SUMMARY_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
