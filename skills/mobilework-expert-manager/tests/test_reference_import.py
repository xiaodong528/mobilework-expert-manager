from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
IMPORT = SCRIPTS / "import_reference.py"
INSTALL = SCRIPTS / "install_expert.py"
sys.path.insert(0, str(SCRIPTS))

import create_expert
import archive_inspector
import import_reference as reference_importer
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class ReferenceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "expert.json"
        source.write_text(load_spec_text("expert-json") + "\n", encoding="utf-8")
        self.packages = self.root / "packages"
        created = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(source),
                "--output-dir",
                str(self.packages),
            ],
            env=managed_generator_env(self.packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        self.package = self.packages / "contract-review-expert"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_import(self, source: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(IMPORT),
                "--package-dir",
                str(self.package),
                "--source",
                str(source),
                "--alias",
                "company-rules",
                "--description",
                "审查公司合同时查阅",
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_docx(self, path: Path, text: str) -> None:
        document = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", document)

    def write_docx_with_external_relationship(self, path: Path) -> None:
        self.write_docx(path, "External source")
        relationships = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="https://example.com/source" '
            'TargetMode="External" Type="example"/></Relationships>'
        )
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("word/_rels/document.xml.rels", relationships)

    def test_confirmed_directory_import_is_zero_execution_and_converts_docx(self) -> None:
        source = self.root / "source-material"
        source.mkdir()
        (source / "rules.md").write_text("# Company rules\n", encoding="utf-8")
        sentinel = self.root / "must-not-exist"
        (source / "attempt.sh").write_text(
            f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8"
        )
        self.write_docx(source / "supplement.docx", "Supplement rule")

        result = self.run_import(source, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["sourceExecution"], "not-attempted")
        self.assertFalse(sentinel.exists())

        manifest = json.loads((self.package / "expert.json").read_text(encoding="utf-8"))
        entry = manifest["runtime_extensions"]["references"]["company-rules"]
        self.assertEqual(
            entry["path"],
            ".opencode/references/contract-review-expert/company-rules",
        )
        self.assertIn("company-rules", manifest["agent"]["references"])
        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn(str(source), encoded)
        converted = (
            self.package
            / ".opencode/references/contract-review-expert/company-rules/supplement.md"
        )
        self.assertIn("Supplement rule", converted.read_text(encoding="utf-8"))

    def test_missing_confirmation_binary_and_symlink_leave_package_unchanged(self) -> None:
        text = self.root / "rules.md"
        text.write_text("rules\n", encoding="utf-8")
        before = create_expert.calculate_package_revision(self.package)
        unconfirmed = self.run_import(text)
        self.assertEqual(unconfirmed.returncode, 2)
        self.assertIn("--confirm is required", unconfirmed.stderr)
        self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        binary = self.root / "rules.pdf"
        binary.write_bytes(b"%PDF-1.4\x00fixture")
        rejected = self.run_import(binary, "--confirm")
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("conversion-required", rejected.stderr)
        self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        disguised = self.root / "disguised-rules.txt"
        disguised.write_bytes(b"%PDF-1.4\ntext-compatible-fixture")
        rejected_disguised = self.run_import(disguised, "--confirm")
        self.assertEqual(rejected_disguised.returncode, 2)
        self.assertIn("contains PDF data", rejected_disguised.stderr)
        self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        external_docx = self.root / "external.docx"
        self.write_docx_with_external_relationship(external_docx)
        rejected_docx = self.run_import(external_docx, "--confirm")
        self.assertEqual(rejected_docx.returncode, 2)
        self.assertIn("external relationship", rejected_docx.stderr)
        self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        package_source = (
            self.package
            / ".opencode/references/contract-review-expert/playbook/overview.md"
        )
        escaped = self.run_import(package_source, "--confirm")
        self.assertEqual(escaped.returncode, 2)
        self.assertIn("outside the target expert package", escaped.stderr)
        self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        if hasattr(os, "symlink"):
            link = self.root / "linked-rules.md"
            link.symlink_to(text)
            linked = self.run_import(link, "--confirm")
            self.assertEqual(linked.returncode, 2)
            self.assertIn("symlink", linked.stderr)
            self.assertEqual(create_expert.calculate_package_revision(self.package), before)

        if hasattr(os, "mkfifo"):
            fifo_source = self.root / "fifo-source"
            fifo_source.mkdir()
            os.mkfifo(fifo_source / "rules.txt")
            fifo = self.run_import(fifo_source, "--confirm")
            self.assertEqual(fifo.returncode, 2)
            self.assertIn("non-regular file", fifo.stderr)
            self.assertEqual(create_expert.calculate_package_revision(self.package), before)

    def test_local_directory_import_enforces_shared_resource_limits(self) -> None:
        def limits(**overrides: int) -> archive_inspector.ArchiveLimits:
            values = {
                "max_entries": 10,
                "max_total_uncompressed_bytes": 20,
                "max_entry_uncompressed_bytes": 10,
                "max_compression_ratio": 200.0,
                "max_path_characters": 64,
                "max_path_depth": 4,
            }
            values.update(overrides)
            return archive_inspector.ArchiveLimits(**values)

        entries = self.root / "too-many-entries"
        entries.mkdir()
        for index in range(3):
            (entries / f"{index}.txt").write_text("x", encoding="utf-8")
        oversized = self.root / "oversized.txt"
        oversized.write_text("12345", encoding="utf-8")
        total = self.root / "too-large-total"
        total.mkdir()
        (total / "first.txt").write_text("1234", encoding="utf-8")
        (total / "second.txt").write_text("5678", encoding="utf-8")
        long_path = self.root / "long-name.txt"
        long_path.write_text("x", encoding="utf-8")
        deep = self.root / "deep"
        (deep / "nested").mkdir(parents=True)
        (deep / "nested/rules.txt").write_text("x", encoding="utf-8")

        cases = (
            (entries, limits(max_entries=2), "exceeds 2 entries"),
            (oversized, limits(max_entry_uncompressed_bytes=4), "exceeds 4 bytes"),
            (total, limits(max_total_uncompressed_bytes=6), "total size limit 6 bytes"),
            (long_path, limits(max_path_characters=8), "exceeds 8 characters"),
            (deep, limits(max_path_depth=1), "exceeds depth 1"),
        )
        for source, active_limits, expected in cases:
            with self.subTest(source=source.name):
                with patch.object(
                    reference_importer.archive_inspector,
                    "default_limits",
                    return_value=active_limits,
                ):
                    with self.assertRaisesRegex(
                        reference_importer.ImportReferenceError,
                        expected,
                    ):
                        reference_importer.collect_source(source)

    def test_imported_package_json_reference_is_installed_and_receipted(self) -> None:
        source = self.root / "package.json"
        source.write_text('{"kind":"reference-material"}\n', encoding="utf-8")
        imported = self.run_import(source, "--confirm")
        self.assertEqual(imported.returncode, 0, imported.stderr)

        host = self.root / "host-references.json"
        host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )
        workspace = self.root / "workspace"
        workspace.mkdir()
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(self.package),
                "--workspace-dir",
                str(workspace),
                "--host-contract",
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        relative = "references/contract-review-expert/company-rules/package.json"
        installed_reference = workspace / ".opencode" / relative
        self.assertEqual(
            installed_reference.read_text(encoding="utf-8"),
            source.read_text(encoding="utf-8"),
        )
        receipt = json.loads(
            (
                workspace
                / ".opencode/.expert-installs/contract-review-expert.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn(relative, receipt["files"])


if __name__ == "__main__":
    unittest.main()
