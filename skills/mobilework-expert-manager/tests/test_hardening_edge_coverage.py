from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bundle_contract
import check_environment
import config_loader
import diagnose_expert
import manager_contract
import migration_planner
import ooxml_inspector
import package_expert
import scan_portable_artifacts
import validate_expert
from validation_result import ValidationResult


class BundleEdgeContractTests(unittest.TestCase):
    def test_validation_reports_independent_manifest_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = (
                "hash.zip", "invalid.zip", "identity.zip", "mismatch.zip",
                "slug-one.zip", "slug-two.zip",
            )
            for name in files:
                (root / name).write_bytes(b"fixture")
            manifest = {
                "schemaVersion": 9,
                "contractVersion": "old",
                "generatorVersion": "",
                "packages": [
                    "not-an-object",
                    {"file": "../bad.zip"},
                    {"file": "missing.zip", "slug": "missing", "version": "1.0.0", "sha256": "actual"},
                    {"file": "hash.zip", "slug": "", "version": "1.0.0", "sha256": "wrong"},
                    {"file": "hash.zip", "slug": "duplicate-file", "version": "1.0.0", "sha256": "actual"},
                    {"file": "invalid.zip", "slug": "invalid", "version": "1.0.0", "sha256": "actual"},
                    {"file": "identity.zip", "slug": "identity", "version": "1.0.0", "sha256": "actual"},
                    {"file": "mismatch.zip", "slug": "declared", "version": "1.0.0", "sha256": "actual"},
                    {"file": "slug-one.zip", "slug": "same", "version": "1.0.0", "sha256": "actual"},
                    {"file": "slug-two.zip", "slug": "same", "version": "1.0.0", "sha256": "actual"},
                ],
                "tests": {"collected": -1, "passed": 0, "failed": 0, "skipped": 0},
                "documents": "invalid",
            }
            (root / bundle_contract.MANIFEST_NAME).write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            def fake_diagnose(path: Path):
                return SimpleNamespace(ok=path.name != "invalid.zip")

            def fake_identity(path: Path):
                if path.name == "identity.zip":
                    raise bundle_contract.BundleContractError("identity unavailable")
                if path.name == "mismatch.zip":
                    return "actual", "2.0.0"
                return path.stem, "1.0.0"

            with patch.object(bundle_contract, "_hash", return_value="actual"), patch.object(
                bundle_contract.diagnose_expert, "diagnose", side_effect=fake_diagnose
            ), patch.object(bundle_contract, "_read_package_identity", side_effect=fake_identity):
                result = bundle_contract.validate_bundle(root)

            codes = {item["code"] for item in result["findings"]}
            self.assertTrue(
                {
                    "BUNDLE_SCHEMA_VERSION_INVALID",
                    "BUNDLE_CONTRACT_VERSION_MISMATCH",
                    "BUNDLE_GENERATOR_VERSION_MISSING",
                    "BUNDLE_PACKAGE_ENTRY_INVALID",
                    "BUNDLE_PACKAGE_PATH_INVALID",
                    "BUNDLE_PACKAGE_MISSING",
                    "BUNDLE_PACKAGE_SLUG_INVALID",
                    "BUNDLE_PACKAGE_HASH_MISMATCH",
                    "BUNDLE_PACKAGE_FILE_DUPLICATE",
                    "BUNDLE_PACKAGE_INVALID",
                    "BUNDLE_PACKAGE_IDENTITY_UNREADABLE",
                    "BUNDLE_PACKAGE_SLUG_MISMATCH",
                    "BUNDLE_PACKAGE_VERSION_MISMATCH",
                    "BUNDLE_PACKAGE_SLUG_DUPLICATE",
                    "BUNDLE_DOCUMENTS_INVALID",
                    "BUNDLE_TEST_SUMMARY_INVALID",
                }.issubset(codes)
            )

    def test_document_paths_and_docx_errors_are_static(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {
                "schemaVersion": 1,
                "contractVersion": "2.0.0",
                "packages": [],
                "tests": {"collected": 0, "passed": 0, "failed": 0, "skipped": 0},
                "documents": {"markdown": "../escape.md", "docx": "broken.docx"},
            }
            (root / "broken.docx").write_bytes(b"not-a-zip")
            codes = {
                item["code"]
                for item in bundle_contract._document_findings(root, manifest)
            }
            self.assertEqual(
                codes,
                {"BUNDLE_DOCUMENT_PATH_INVALID", "BUNDLE_DOCUMENT_UNREADABLE"},
            )

            missing_xml = root / "missing.docx"
            with zipfile.ZipFile(missing_xml, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
            with self.assertRaisesRegex(bundle_contract.BundleContractError, "word/document.xml"):
                bundle_contract._docx_text(missing_xml)

            malformed = root / "malformed.docx"
            with zipfile.ZipFile(malformed, "w") as archive:
                archive.writestr("word/document.xml", "<broken>")
            with self.assertRaisesRegex(bundle_contract.BundleContractError, "invalid"):
                bundle_contract._docx_text(malformed)

    def test_create_manifest_rejects_duplicate_sources_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "one" / "expert.zip"
            second = root / "two" / "expert.zip"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            with patch.object(bundle_contract, "_read_package_identity", return_value=("one", "1.0.0")):
                with self.assertRaisesRegex(bundle_contract.BundleContractError, "filename"):
                    bundle_contract.create_manifest(root / "bundle", [first, second])

            third = root / "three.zip"
            fourth = root / "four.zip"
            third.write_bytes(b"three")
            fourth.write_bytes(b"four")
            with patch.object(
                bundle_contract, "_read_package_identity", return_value=("same", "1.0.0")
            ):
                with self.assertRaisesRegex(bundle_contract.BundleContractError, "slug"):
                    bundle_contract.create_manifest(root / "slug-bundle", [third, fourth])

            target_bundle = root / "target-bundle"
            target_bundle.mkdir()
            (target_bundle / third.name).write_bytes(b"existing")
            with patch.object(
                bundle_contract, "_read_package_identity", return_value=("three", "1.0.0")
            ):
                with self.assertRaisesRegex(bundle_contract.BundleContractError, "already exists"):
                    bundle_contract.create_manifest(target_bundle, [third])


class DiagnosticEnvironmentAndConfigEdges(unittest.TestCase):
    def test_diagnose_missing_unsupported_and_corrupt_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = diagnose_expert.diagnose(root / "missing.zip")
            self.assertEqual(missing.findings[0].code, "DIAGNOSTIC_SOURCE_MISSING")
            self.assertEqual(missing.gates["install"], "blocked")
            self.assertEqual(missing.gates["configLoad"], "blocked")
            text = root / "expert.txt"
            text.write_text("fixture", encoding="utf-8")
            unsupported = diagnose_expert.diagnose(text)
            self.assertEqual(unsupported.findings[0].code, "DIAGNOSTIC_SOURCE_UNSUPPORTED")
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(b"not-a-zip")
            damaged = diagnose_expert.diagnose(corrupt)
            self.assertFalse(damaged.ok)
            self.assertIn(damaged.gates["contract"], {"blocked", "failed"})
            self.assertEqual(damaged.gates["install"], "blocked")
            self.assertEqual(damaged.gates["configLoad"], "blocked")

    def test_diagnose_main_reports_contract_error_and_runtime_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            with patch.object(
                sys, "argv", ["diagnose_expert.py", str(source), "--format", "json"]
            ), patch.object(
                diagnose_expert.manager_contract,
                "resolve_target",
                side_effect=manager_contract.ManagerContractError("bad target"),
            ), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(diagnose_expert.main(), 2)
            self.assertEqual(
                json.loads(output.getvalue())["findings"][0]["code"],
                "MANAGER_VERSION_CONTRACT_ERROR",
            )

            valid = ValidationResult(execution_reason="static", input_path=source)
            valid.set_gate("contract", "passed")
            with patch.object(
                sys,
                "argv",
                ["diagnose_expert.py", str(source), "--format", "json", "--runtime"],
            ), patch.object(diagnose_expert, "diagnose", return_value=valid), contextlib.redirect_stdout(
                io.StringIO()
            ):
                self.assertEqual(diagnose_expert.main(), 4)

    def test_validate_main_reports_contract_error_without_recursive_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            with patch.object(
                sys,
                "argv",
                ["validate_expert.py", str(source), "--format", "json"],
            ), patch.object(
                validate_expert.manager_contract,
                "resolve_target",
                side_effect=manager_contract.ManagerContractError("bad target"),
            ), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(validate_expert.main(), 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["findings"][0]["code"], "MANAGER_VERSION_CONTRACT_ERROR")
            self.assertEqual(
                payload["provenance"]["targetOpenCode"]["source"],
                "version-contract-error",
            )

    def test_environment_all_features_and_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sidecar = root / "opencode"
            sidecar.write_text("fixture", encoding="utf-8")
            sidecar.chmod(0o700)
            with patch.object(check_environment.importlib.util, "find_spec", return_value=object()), patch.object(
                check_environment.shutil, "which", return_value="/trusted/tool"
            ):
                result = check_environment.check_environment(
                    check_environment.selected_features(["all"]),
                    env={},
                    workspace_root=root,
                    sidecar=sidecar,
                )
            self.assertTrue(result["ok"])

            with patch.object(
                check_environment.manager_contract,
                "resolve_target",
                side_effect=manager_contract.ManagerContractError("missing target"),
            ):
                failed = check_environment.check_environment(
                    ["config-load"], env={}, workspace_root=root, sidecar=sidecar
                )
            self.assertFalse(failed["ok"])
            self.assertIn("target-opencode-contract", failed["missing"])

            link = root / "linked-sidecar"
            link.symlink_to(sidecar)
            self.assertFalse(check_environment.explicit_sidecar_status(link)["available"])
            self.assertFalse(
                check_environment.explicit_sidecar_status(root / "missing")["available"]
            )

    def test_environment_main_emits_structured_json(self) -> None:
        with patch.object(sys, "argv", ["check_environment.py", "--feature", "core"]), contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            self.assertEqual(check_environment.main(), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["operation"], "check-environment")
        self.assertEqual(payload["data"]["features"], ["core"])

    def test_config_loader_rejects_unsafe_sidecars_and_invocation_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing"
            with self.assertRaisesRegex(config_loader.ConfigLoadError, "executable"):
                config_loader._sidecar(missing)
            executable = root / "sidecar"
            executable.write_text("fixture", encoding="utf-8")
            executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            link = root / "link"
            link.symlink_to(executable)
            with self.assertRaisesRegex(config_loader.ConfigLoadError, "symlink"):
                config_loader._sidecar(link)

            with patch.object(config_loader.subprocess, "run", side_effect=OSError("boom")):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "invocation"):
                    config_loader._run(executable, ["--version"], cwd=root, env={})
            failed = SimpleNamespace(returncode=1, stderr="failure", stdout="")
            with patch.object(config_loader.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "failure"):
                    config_loader._run(executable, ["--version"], cwd=root, env={})

    def test_config_loader_rejects_missing_and_malformed_config_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            sidecar = root / "opencode"
            sidecar.write_text("fixture", encoding="utf-8")
            sidecar.chmod(0o700)
            target = manager_contract.resolve_target(cli_version="unknown", env={})
            with self.assertRaisesRegex(config_loader.ConfigLoadError, "config is missing"):
                config_loader._verify_sidecar(workspace, sidecar, target=target)

            runtime = workspace / ".opencode"
            runtime.mkdir()
            (runtime / "opencode.jsonc").write_text("{}", encoding="utf-8")
            no_version = SimpleNamespace(returncode=0, stdout="no version", stderr="")
            with patch.object(config_loader, "_run", return_value=no_version):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "parseable version"):
                    config_loader._verify_sidecar(workspace, sidecar, target=target)

            version = SimpleNamespace(returncode=0, stdout="1.2.3", stderr="")
            bad_json = SimpleNamespace(returncode=0, stdout="not-json", stderr="")
            with patch.object(config_loader, "_run", side_effect=[version, bad_json]):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "non-JSON"):
                    config_loader._verify_sidecar(workspace, sidecar, target=target)

            list_json = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            with patch.object(config_loader, "_run", side_effect=[version, list_json]):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "JSON object"):
                    config_loader._verify_sidecar(workspace, sidecar, target=target)


class MigrationPortableAndValidatorEdges(unittest.TestCase):
    def test_migration_handles_team_roles_zip_and_empty_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "legacy"
            package.mkdir()
            manifest = {
                "slug": "legacy-team",
                "type": "team",
                "primary_agent": {"id": "lead", "skills": []},
                "subagents": [
                    {"id": "worker", "skills": ["legacy"], "maxSteps": 7},
                    "ignored",
                ],
            }
            (package / "expert.json").write_text(json.dumps(manifest), encoding="utf-8")
            archive = root / "legacy.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.write(package / "expert.json", "legacy/expert.json")
            plan = migration_planner.plan(archive)
            self.assertEqual(plan["slug"], "legacy-team")
            self.assertIn("MIGRATE_LEGACY_STEPS", {item["code"] for item in plan["automaticActions"]})
            self.assertIn("- None", migration_planner.render_markdown(plan))

            with self.assertRaisesRegex(migration_planner.MigrationPlanError, "directory or ZIP"):
                migration_planner.plan(root / "legacy.txt")
            invalid = root / "invalid"
            invalid.mkdir()
            (invalid / "expert.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(migration_planner.MigrationPlanError, "JSON object"):
                migration_planner.plan(invalid)
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(b"invalid")
            with self.assertRaisesRegex(migration_planner.MigrationPlanError, "preflight"):
                migration_planner.plan(corrupt)

    def test_portable_scanner_main_and_binary_text_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "binary.md"
            binary.write_bytes(b"\xff\xfe")
            findings: list[dict[str, str]] = []
            scan_portable_artifacts.scan_text_file(binary, root, findings)
            self.assertEqual(findings, [])
            outside = root.parent / "outside-mobilework-fixture.txt"
            try:
                outside.write_text("portable", encoding="utf-8")
                with patch.object(
                    sys,
                    "argv",
                    [
                        "scan_portable_artifacts.py",
                        "--workspace-root",
                        str(root),
                        str(outside),
                        str(root / "missing"),
                    ],
                ), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(scan_portable_artifacts.main(), 1)
                payload = json.loads(output.getvalue())
                self.assertEqual(
                    {item["type"] for item in payload["findings"]},
                    {"artifact outside workspace", "missing path"},
                )
            finally:
                outside.unlink(missing_ok=True)

            with patch.object(
                ooxml_inspector,
                "inspect_workbook",
                return_value=SimpleNamespace(
                    issues=[SimpleNamespace(path="xl", code="OOXML_LIMIT", message="blocked", severity="error")],
                    errors=["blocked"],
                ),
            ):
                workbook_findings: list[dict[str, str]] = []
                scan_portable_artifacts.scan_workbook(root / "fixture.xlsx", root, workbook_findings)
            self.assertEqual(workbook_findings[0]["type"], "OOXML_LIMIT")

    def test_validator_helper_error_matrix(self) -> None:
        result = ValidationResult()
        self.assertIsNone(validate_expert.validate_name("Bad Name", "name", result))
        self.assertIsNone(validate_expert.validate_name("a" * 65, "name", result))
        self.assertEqual(validate_expert.validate_text(None, "required", result, required=True), "")
        self.assertEqual(validate_expert.validate_text(3, "text", result), "")
        self.assertEqual(
            validate_expert.validate_string_list(None, "recommended", result, recommended_count=3),
            [],
        )
        self.assertEqual(validate_expert.validate_string_list("bad", "strings", result), [])
        self.assertEqual(
            validate_expert.validate_string_list(["one"], "count", result, recommended_count=3),
            ["one"],
        )
        validate_expert.check_single_expert_public_name(
            "demo", slug="demo", agent_id="agent", result=result
        )
        validate_expert.check_single_expert_public_name(
            "internalQ", slug="demo", agent_id="agent", result=result
        )
        validate_expert.validate_avatar("ftp://avatar", "avatar", result)
        validate_expert.validate_avatar("../avatar.png", "avatar", result)
        validate_expert.validate_avatar("avatar.txt", "avatar", result)
        self.assertEqual(
            validate_expert.validate_package_file_path(
                "wrong/file.bin",
                "resource",
                result,
                allowed_suffixes={".md"},
                required_prefix="expected",
            ),
            "wrong/file.bin",
        )
        self.assertEqual(
            validate_expert.validate_package_file_path("../escape", "escape", result), ""
        )

        text_resources = [
            "bad",
            {"path": ".opencode/references/demo/a.md", "content": "", "extra": True},
            {"path": ".opencode/references/demo/a.md", "content": "duplicate"},
        ]
        paths = validate_expert.validate_text_resource_list(
            text_resources,
            "references",
            result,
            required_prefix=".opencode/references/demo",
        )
        self.assertEqual(paths.count(".opencode/references/demo/a.md"), 2)
        self.assertEqual(
            validate_expert.validate_text_resource_list("bad", "references", result, required_prefix="x"),
            [],
        )

        embedded = [
            1,
            {"path": "tool.py", "content": "", "unexpected": True},
            {"path": "tool.py", "content": "duplicate"},
        ]
        validate_expert.validate_embedded_files(
            embedded, "tools", result, allowed_suffixes={".ts"}
        )
        self.assertEqual(
            validate_expert.validate_embedded_files("bad", "tools", result, allowed_suffixes={".ts"}),
            [],
        )
        self.assertGreater(len(result.errors), 15)

    def test_validator_frontmatter_and_role_error_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = ValidationResult()
            self.assertIsNone(validate_expert.parse_frontmatter(root / "missing.md", result))
            plain = root / "plain.md"
            plain.write_text("# no frontmatter\n", encoding="utf-8")
            self.assertIsNone(validate_expert.parse_frontmatter(plain, result))
            malformed = root / "malformed.md"
            malformed.write_text("---\n[\n---\n", encoding="utf-8")
            self.assertIsNone(validate_expert.parse_frontmatter(malformed, result))
            sequence = root / "sequence.md"
            sequence.write_text("---\n[]\n---\n", encoding="utf-8")
            self.assertIsNone(validate_expert.parse_frontmatter(sequence, result))

            self.assertIsNone(
                validate_expert.validate_role(
                    "bad", "agent", result, expected_mode="primary"
                )
            )
            validate_expert.validate_role(
                {
                    "id": "Bad Id",
                    "mode": "subagent",
                    "name": 1,
                    "description": None,
                    "steps": 0,
                    "skills": "bad",
                    "mcp": ["duplicate", "duplicate", "Bad Name"],
                    "route_triggers": "bad",
                    "handoff_contract": "bad",
                },
                "agent",
                result,
                expected_mode="primary",
            )
            self.assertEqual(validate_expert.list_role_ids({"type": "bad"}, result), (None, []))
            validate_expert.list_role_ids(
                {"type": "expert", "primary_agent": {}, "subagents": [], "agent": None},
                result,
            )
            validate_expert.list_role_ids(
                {"type": "team", "agent": {}, "primary_agent": {}, "subagents": []},
                result,
            )
            duplicate_role = {
                "id": "same",
                "name": "Same",
                "description": "fixture",
                "skills": [],
            }
            validate_expert.list_role_ids(
                {
                    "type": "team",
                    "primary_agent": dict(duplicate_role),
                    "subagents": [dict(duplicate_role)],
                },
                result,
            )
            self.assertGreater(len(result.errors), 20)

    def test_packager_skips_forbidden_metadata_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / ".DS_Store"
            metadata.write_bytes(b"metadata")
            self.assertTrue(package_expert.should_skip(metadata, root))

    def test_iter_package_paths_ignores_only_root_git(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("root metadata", encoding="utf-8")
            (root / "nested/.git").mkdir(parents=True)
            (root / "nested/.git/config").write_text("invalid nested metadata", encoding="utf-8")
            relative = {path.relative_to(root).as_posix() for path in validate_expert.iter_package_paths(root)}
            self.assertNotIn(".git", relative)
            self.assertIn("nested/.git", relative)


if __name__ == "__main__":
    unittest.main()
