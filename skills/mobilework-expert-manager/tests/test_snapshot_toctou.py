from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import create_expert
import install_expert
import manager_contract
import package_expert
import safe_input
import validate_expert
from spec_templates import load_spec_text


class SnapshotToctouTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = manager_contract.TargetContract(
            version="test-runtime",
            source="test",
            capabilities={"references": True},
            capability_verified=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_package(self) -> Path:
        manifest_path = self.root / "source" / "expert.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text(
            load_spec_text("legacy-expert-json"),
            encoding="utf-8",
        )
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        normalized = create_expert.normalize_manifest(
            data,
            manifest_dir=manifest_path.parent,
        )
        create_expert.prepare_avatar_assets(normalized, manifest_path.parent)
        output = self.root / "generated"
        output.mkdir()
        return create_expert.write_project(normalized, output, force=False)

    @staticmethod
    def mutable_runtime_file(snapshot: safe_input.InputSnapshot) -> safe_input.InputFile:
        return next(
            item
            for item in snapshot.files
            if item.relative_path.startswith(".opencode/instructions/")
        )

    def mutate_source_after_validation(
        self,
        module: Any,
        package: Path,
        captured: dict[str, Any],
    ):
        original_validate = module.package_snapshot.validate_snapshot

        def wrapped(
            snapshot: safe_input.InputSnapshot,
            *,
            target: manager_contract.TargetContract | None = None,
        ):
            result = original_validate(snapshot, target=target)
            item = self.mutable_runtime_file(snapshot)
            captured.update(
                {
                    "bytes": item.content,
                    "relative": item.relative_path,
                    "sha256": snapshot.sha256,
                }
            )
            (package / item.relative_path).write_bytes(b"race mutation after validation\n")
            return result

        return wrapped

    def test_package_zip_uses_snapshot_bytes_after_source_mutation(self) -> None:
        package = self.make_package()
        captured: dict[str, Any] = {}
        wrapped = self.mutate_source_after_validation(
            package_expert,
            package,
            captured,
        )

        with patch.object(
            package_expert.package_snapshot,
            "validate_snapshot",
            side_effect=wrapped,
        ):
            archive_path = package_expert.make_zip(
                package,
                self.root / "dist",
                run_external_test=False,
            )

        slug = json.loads((package / "expert.json").read_text(encoding="utf-8"))["slug"]
        with zipfile.ZipFile(archive_path) as archive:
            archived = archive.read(f"{slug}/{captured['relative']}")
        self.assertEqual(archived, captured["bytes"])
        self.assertNotEqual(
            (package / captured["relative"]).read_bytes(),
            captured["bytes"],
        )

    def test_package_preflight_change_writes_no_output(self) -> None:
        package = self.make_package()
        output = self.root / "dist-rejected"
        error = safe_input.InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            "fixture changed during scan",
            "expert.json",
        )

        with patch.object(
            package_expert.package_snapshot,
            "inspect_directory",
            side_effect=error,
        ), self.assertRaises(SystemExit) as raised:
            package_expert.make_zip(
                package,
                output,
                run_external_test=False,
            )

        self.assertIn("INPUT_CHANGED_DURING_SCAN", str(raised.exception))
        self.assertFalse(output.exists())

    def test_install_uses_snapshot_bytes_and_hashes_after_source_mutation(self) -> None:
        package = self.make_package()
        workspace = self.root / "workspace"
        workspace.mkdir()
        captured: dict[str, Any] = {}
        wrapped = self.mutate_source_after_validation(
            install_expert,
            package,
            captured,
        )

        with patch.object(
            install_expert.package_snapshot,
            "validate_snapshot",
            side_effect=wrapped,
        ):
            result = install_expert.install_package(
                package,
                workspace,
                force=False,
                target=self.target,
            )

        installed_relative = captured["relative"].removeprefix(".opencode/")
        installed = workspace / ".opencode" / installed_relative
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        expected_hash = hashlib.sha256(captured["bytes"]).hexdigest()
        self.assertEqual(installed.read_bytes(), captured["bytes"])
        self.assertEqual(receipt["files"][installed_relative], expected_hash)
        self.assertEqual(result["provenance"]["inputSha256"], captured["sha256"])
        self.assertNotEqual(
            (package / captured["relative"]).read_bytes(),
            captured["bytes"],
        )

    def test_install_preflight_change_writes_no_runtime_state(self) -> None:
        package = self.make_package()
        workspace = self.root / "workspace-rejected"
        workspace.mkdir()
        error = safe_input.InputInspectionError(
            "INPUT_CHANGED_DURING_SCAN",
            "fixture changed during scan",
            "opencode.json",
        )

        with patch.object(
            install_expert.package_snapshot,
            "inspect_directory",
            side_effect=error,
        ), self.assertRaises(SystemExit) as raised:
            install_expert.install_package(
                package,
                workspace,
                force=False,
                target=self.target,
            )

        self.assertIn("INPUT_CHANGED_DURING_SCAN", str(raised.exception))
        self.assertFalse((workspace / ".opencode").exists())

    def test_direct_validation_skips_only_large_root_git_metadata(self) -> None:
        package = self.make_package()
        git = package / ".git"
        git.mkdir()
        for index in range(5001):
            (git / f"object-{index}").write_bytes(b"")
        (package / ".cache").mkdir()
        (package / ".cache/entry").write_bytes(b"cache")
        (package / "cache.pyc").write_bytes(b"bytecode")

        first = validate_expert.validate_package(package, target=self.target)
        (git / "object-0").write_bytes(b"changed")
        second = validate_expert.validate_package(package, target=self.target)

        self.assertNotIn(
            "INPUT_ENTRY_COUNT_LIMIT",
            {finding.code for finding in second.findings},
        )
        errors = "\n".join(second.errors)
        self.assertIn("non-distributable directory", errors)
        self.assertIn("non-distributable file suffix", errors)
        self.assertEqual(
            first.provenance["inputSha256"],
            second.provenance["inputSha256"],
        )
        self.assertEqual(
            [item.relative_path for item in second.input_snapshot.excluded_entries],
            [".git"],
        )
        self.assertEqual(
            second.provenance["inputInspection"]["excludedEntryCount"],
            1,
        )
        self.assertEqual(
            second.provenance["inputInspection"]["excludedPaths"],
            [".git"],
        )


if __name__ == "__main__":
    unittest.main()
