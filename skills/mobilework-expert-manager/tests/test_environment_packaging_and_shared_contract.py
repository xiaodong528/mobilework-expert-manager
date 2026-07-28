from __future__ import annotations

import builtins
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_environment
import create_expert
import execution_context
import manifest_contract
import package_expert
import renderers
import scan_portable_artifacts
import validate_expert
from spec_templates import load_spec_text


class EnvironmentPackagingAndSharedContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_package(self) -> Path:
        manifest_path = self.root / "expert.json"
        manifest_path.write_text(
            load_spec_text("legacy-expert-json"), encoding="utf-8"
        )
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        normalized = create_expert.normalize_manifest(data, manifest_dir=manifest_path.parent)
        create_expert.prepare_avatar_assets(normalized, manifest_path.parent)
        output = self.root / "generated"
        output.mkdir()
        return create_expert.write_project(normalized, output, force=False)

    def test_environment_features_expand_deterministically(self) -> None:
        self.assertEqual(check_environment.selected_features([]), ["core"])
        self.assertEqual(
            check_environment.selected_features(["package", "core"]),
            ["core", "package"],
        )
        self.assertEqual(
            check_environment.selected_features(["all"]),
            [
                "core",
                "excel",
                "package",
                "bundle-docx",
                "git",
                "config-load",
                "coverage",
            ],
        )

    def test_coverage_and_config_load_features_report_without_execution(self) -> None:
        sidecar = self.root / "opencode"
        sidecar.write_text("fixture", encoding="utf-8")
        sidecar.chmod(0o700)
        host = self.root / "host.json"
        host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "9.8.7",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )
        with patch.object(check_environment.importlib.util, "find_spec", return_value=None):
            result = check_environment.check_environment(
                ["config-load", "coverage"],
                env={},
                workspace_root=self.root,
                sidecar=sidecar,
                host_contract=host,
            )
        by_name = {item["name"]: item for item in result["checks"]}
        self.assertTrue(by_name["trusted-opencode-sidecar"]["available"])
        self.assertEqual(by_name["target-opencode-contract"]["version"], "9.8.7")
        self.assertTrue(by_name["target-opencode-contract"]["capabilityVerified"])
        self.assertFalse(by_name["coverage"]["available"])
        self.assertEqual(result["missing"], ["coverage"])
        self.assertFalse(result["ok"])

        missing = check_environment.check_environment(
            ["config-load"], env={}, workspace_root=self.root
        )
        self.assertEqual(missing["missing"], ["trusted-opencode-sidecar"])

    def test_core_environment_does_not_require_pyyaml(self) -> None:
        original = importlib.util.find_spec

        def fake_find_spec(name: str):
            if name == "yaml":
                return None
            return original(name)

        with patch.object(check_environment.importlib.util, "find_spec", side_effect=fake_find_spec):
            result = check_environment.check_environment(["core"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["hostMode"], "workspace")

    def test_environment_reports_mobilework_managed_output(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        managed = self.root / "real-home" / ".mobilework" / "my-experts"
        result = check_environment.check_environment(
            ["core"],
            env={
                execution_context.HOST_ENV: "mobilework",
                execution_context.MY_EXPERTS_ENV: str(managed),
            },
            workspace_root=workspace,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["hostMode"], "mobilework")
        self.assertEqual(Path(result["outputRoot"]), managed.resolve())
        self.assertEqual(result["pathSource"], "mobilework-main-process")

    def test_environment_fails_closed_for_partial_or_workspace_managed_contract(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        cases = (
            ({execution_context.HOST_ENV: "mobilework"}, "HOST_CONTRACT_INCOMPLETE"),
            (
                {
                    execution_context.HOST_ENV: "mobilework",
                    execution_context.MY_EXPERTS_ENV: str(workspace / "my-experts"),
                },
                "TARGET_OUTSIDE_ROOT",
            ),
        )
        for env, code in cases:
            with self.subTest(code=code):
                result = check_environment.check_environment(
                    ["core"],
                    env=env,
                    workspace_root=workspace,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["errors"][0]["code"], code)

    def test_generator_and_validator_fall_back_without_pyyaml(self) -> None:
        blocked_modules = self.root / "blocked-modules"
        blocked_modules.mkdir()
        (blocked_modules / "yaml.py").write_text(
            'raise ImportError("simulated missing PyYAML")\n',
            encoding="utf-8",
        )
        manifest = self.root / "expert.json"
        manifest.write_text(
            load_spec_text("legacy-expert-json"), encoding="utf-8"
        )
        output = self.root / "generated"
        output.mkdir()
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(blocked_modules), env.get("PYTHONPATH", "")) if part
        )

        created = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "create_expert.py"),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(output),
            ],
            env=env,
            cwd=output,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        package = output / "contract-review-expert"
        agent = package / ".opencode" / "agents" / "contract-reviewer.md"
        frontmatter = json.loads(agent.read_text(encoding="utf-8").split("---", 2)[1])
        self.assertEqual(frontmatter["mode"], "primary")

        validated = subprocess.run(
            [sys.executable, str(SCRIPTS / "validate_expert.py"), str(package)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_text_scan_does_not_import_openpyxl(self) -> None:
        artifact = self.root / "summary.md"
        artifact.write_text("portable summary\n", encoding="utf-8")
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise AssertionError("text scan must not import openpyxl")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            self.assertEqual(scan_portable_artifacts.scan_root(artifact), [])

    def test_excel_scan_reports_optional_dependency_only_when_needed(self) -> None:
        workbook = self.root / "report.xlsx"
        with zipfile.ZipFile(workbook, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("simulated missing openpyxl")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(SystemExit, "openpyxl is required"):
                scan_portable_artifacts.scan_root(workbook)

    def test_packager_requires_force_and_keeps_verified_old_zip_on_failure(self) -> None:
        package = self.make_package()
        dist = self.root / "dist"
        zip_path = package_expert.make_zip(package, dist, run_external_test=False)
        original_bytes = zip_path.read_bytes()

        with self.assertRaisesRegex(SystemExit, "--force"):
            package_expert.make_zip(package, dist, run_external_test=False)

        with patch.object(
            package_expert,
            "verify_extracted_package",
            side_effect=SystemExit("simulated extracted validation failure"),
        ):
            with self.assertRaisesRegex(SystemExit, "simulated extracted"):
                package_expert.make_zip(package, dist, force=True, run_external_test=False)
        self.assertEqual(zip_path.read_bytes(), original_bytes)
        self.assertEqual(sorted(path.name for path in dist.glob("*.zip")), [zip_path.name])

    def test_skip_unzip_still_runs_python_and_extracted_verification(self) -> None:
        package = self.make_package()
        dist = self.root / "dist"
        with patch.object(package_expert, "test_zip_external") as external, patch.object(
            package_expert,
            "verify_extracted_package",
            wraps=package_expert.verify_extracted_package,
        ) as extracted:
            package_expert.make_zip(package, dist, run_external_test=False)
        external.assert_not_called()
        extracted.assert_called_once()

    def test_shared_contract_and_renderer_compatibility_wrappers(self) -> None:
        data = json.loads(load_spec_text("legacy-expert-json"))
        self.assertEqual(manifest_contract.collect_manifest_issues(data), [])
        invalid = dict(data)
        invalid["primary_agent"] = data["agent"]
        self.assertTrue(manifest_contract.collect_manifest_issues(invalid))
        rendered = renderers.render_frontmatter({"name": "demo"}, "# Body\n")
        raw_frontmatter = rendered.split("---", 2)[1]
        rendered_frontmatter = (
            renderers.yaml.safe_load(raw_frontmatter)
            if renderers.yaml is not None
            else json.loads(raw_frontmatter)
        )
        self.assertEqual(rendered_frontmatter, {"name": "demo"})
        self.assertTrue(rendered.endswith("# Body\n"))

    def test_skill_contract_is_compact_portable_and_autonomous(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(skill_text.splitlines()), 240)
        frontmatter = skill_text.split("---", 2)[1]
        frontmatter_keys = {
            line.split(":", 1)[0].strip()
            for line in frontmatter.splitlines()
            if line and not line.startswith(" ") and ":" in line
        }
        self.assertEqual(frontmatter_keys, {"name", "description"})
        self.assertTrue((SKILL / "agents" / "openai.yaml").is_file())
        self.assertIn("<skill-root>/scripts/create_expert.py", skill_text)
        self.assertNotIn("~/.agents/skills/mobilework-expert-manager/scripts", skill_text)
        self.assertIn("references/requirements-discovery.md", skill_text)
        self.assertIn("取得明确确认后才能", skill_text)
        self.assertIn("只读诊断、校验、安装和打包可直接执行", skill_text)

    def test_requirements_discovery_contract_has_design_gate_and_direct_lane(self) -> None:
        discovery = (SKILL / "references" / "requirements-discovery.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "单专家",
            "专家团",
            "用户事实",
            "候选设计",
            "未确认项",
            "每轮最多组织 3 个",
            "是否确认按此设计开始生成",
            "结构性修改",
            "直接执行",
            "`--force`",
        ):
            self.assertIn(required, discovery)

    def test_workflow_command_authoring_contract_is_explicit_and_optional(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        discovery = (SKILL / "references" / "requirements-discovery.md").read_text(
            encoding="utf-8"
        )
        runtime = (SKILL / "references" / "runtime-extensions-spec.md").read_text(
            encoding="utf-8"
        )
        authoring = (
            SKILL / "references" / "opencode-authoring-best-practices.md"
        ).read_text(encoding="utf-8")

        for text in (discovery, runtime, authoring):
            self.assertIn("可由用户直接触发", text)
            self.assertIn("$ARGUMENTS", text)
        self.assertIn("references/opencode-authoring-best-practices.md", skill_text)
        self.assertIn("不要让 validator 或生成器自动补建", discovery)
        self.assertIn("属于宿主消息层", runtime)
        self.assertIn("不新增附件", runtime)
        self.assertIn("不生成根级 `command`", runtime)
        self.assertIn("专家团默认路由到团长", authoring)

    def test_workflow_autonomy_reference_is_direct_and_complete(self) -> None:
        autonomy = (SKILL / "references" / "workflow-autonomy-spec.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "极低：全程照脚本执行，不能自行换方法",
            "低：按固定步骤执行，只能处理预设分支",
            "中：可在明确边界内选择方法",
            "高：可根据目标灵活安排，但关键决定需确认",
            "极高：可自主规划、调整和返工，仍受安全与验收标准约束",
            "Agent override > phase.autonomy > workflow.autonomy",
            "口算、目测或纯文字替代执行",
            "每个用户可直接触发、会重复使用的稳定 workflow",
        ):
            self.assertIn(required, autonomy)

    def test_runtime_resource_recommendations_and_no_agents_contract_are_explicit(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        discovery = (SKILL / "references" / "requirements-discovery.md").read_text(
            encoding="utf-8"
        )
        runtime = (SKILL / "references" / "runtime-extensions-spec.md").read_text(
            encoding="utf-8"
        )
        authoring = (
            SKILL / "references" / "opencode-authoring-best-practices.md"
        ).read_text(encoding="utf-8")

        for text in (discovery, runtime, authoring):
            self.assertIn("领域资料", text)
            self.assertIn("`.opencode/references", text)
            self.assertIn("`.opencode/plugins/`", text)
            self.assertIn("`.opencode/tools/`", text)
            self.assertIn("`.opencode/instructions/", text)
            self.assertIn("AGENTS.md", text)
        self.assertIn("references/runtime-extensions-spec.md", skill_text)
        self.assertIn("PDF、DOCX、图片等先转换", discovery)
        self.assertIn("不要用 plugin 代替", runtime)
        self.assertIn("不开发根级 `AGENTS.md`", authoring)
        for text in (runtime, authoring):
            self.assertIn("跨包冲突审计", text)
        self.assertIn("<slug>-<name>", runtime)
        self.assertIn("同一干净 workspace 顺序安装", runtime)

    def test_reference_contract_is_version_independent(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        runtime = (SKILL / "references/runtime-extensions-spec.md").read_text(encoding="utf-8")
        opencode = (SKILL / "references/opencode-json-spec.md").read_text(encoding="utf-8")
        manager = (SKILL / "references/manager-contract.md").read_text(encoding="utf-8")
        for text in (skill_text, runtime, opencode):
            self.assertNotIn("1.18.3", text)
            self.assertNotIn("1.16.2", text)
        self.assertIn("--target-opencode-version", skill_text)
        self.assertIn("MOBILEWORK_TARGET_OPENCODE_VERSION", skill_text)
        self.assertIn("--target-opencode-version", manager)
        self.assertIn("MOBILEWORK_TARGET_OPENCODE_VERSION", manager)
        self.assertIn("--host-contract", manager)
        self.assertIn("opencode.json.references", runtime)
        self.assertIn("opencode.json.references", opencode)
        self.assertIn("`repository`", runtime)
        self.assertIn("`branch`", runtime)
        self.assertIn("`hidden`", runtime)
        self.assertIn("恰好声明一个", runtime)
        self.assertIn("不再承载 local reference 文件", opencode)

    def test_single_expert_does_not_generate_root_agents_md(self) -> None:
        package = self.make_package()
        self.assertFalse((package / "AGENTS.md").exists())
        self.assertTrue((package / "README.md").is_file())

    def test_validator_rejects_root_agents_md_with_migration_guidance(self) -> None:
        package = self.make_package()
        (package / "AGENTS.md").write_text("# Legacy rules\n", encoding="utf-8")

        result = validate_expert.validate_package(package)

        self.assertFalse(result.ok)
        matching = [error for error in result.errors if "AGENTS.md" in error]
        self.assertEqual(len(matching), 1)
        self.assertIn("supported by official OpenCode", matching[0])
        self.assertIn("runtime_extensions.instruction_files", matching[0])
        self.assertIn("runtime_extensions.instructions", matching[0])

    def test_eval_fixtures_and_trigger_balance_are_complete(self) -> None:
        evals = json.loads((SKILL / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(len(evals["evals"]), 35)
        names = {item["name"] for item in evals["evals"]}
        self.assertEqual(
            names,
            {
                "clarify-underspecified-single-expert",
                "clarify-underspecified-expert-team",
                "clarify-only-missing-sop-information",
                "generate-explicitly-confirmed-complete-design",
                "clarify-structural-team-modification",
                "delegated-design-still-requires-confirmation",
                "repair-derived-portability-defect-directly",
                "diagnose-and-validate-directly",
                "install-confirmed-runtime-package-directly",
                "package-existing-expert-directly",
                "recommend-commands-for-user-workflows",
                "recommend-runtime-resources-and-no-agents",
                "recommend-low-autonomy-for-repeatable-financial-reconciliation",
                "ask-only-for-missing-execution-standard",
                "confirm-workflow-phase-and-agent-override-autonomy",
                "propose-deterministic-executor-when-none-exists",
                "generate-command-with-explicit-autonomy-at-three-levels",
                "generate-declared-agent-runtime-options",
                "reject-invalid-agent-runtime-options",
                "audit-cross-package-workspace-collisions",
                "verify-five-autonomy-permission-baselines",
                "verify-mixed-autonomy-team-permissions",
                "diagnose-legacy-contract-zip-statically",
                "diagnose-malicious-help-package-without-execution",
                "prove-opencode-workspace-install-projection",
                "verify-version-independent-manager-contract",
                "block-hostile-zip-and-ooxml-before-loading",
                "plan-legacy-migration-read-only",
                "audit-supply-chain-warning-first",
                "create-and-validate-manifest-driven-bundle",
                "version-trusted-expert-with-local-git-semver",
                "verify-owned-custom-tool-across-autonomy",
                "upload-and-auto-assign-single-expert-skill",
                "upload-and-assign-skill-to-all-team-members",
                "migrate-legacy-skills-during-upload",
            },
        )
        for item in evals["evals"]:
            self.assertTrue(item["expectations"])
            for file_name in item["files"]:
                self.assertTrue((SKILL / file_name).is_file(), file_name)
        triggers = json.loads((SKILL / "evals" / "trigger-evals.json").read_text(encoding="utf-8"))
        self.assertEqual(len(triggers), 21)
        self.assertEqual(sum(1 for item in triggers if item["should_trigger"]), 11)
        self.assertEqual(sum(1 for item in triggers if not item["should_trigger"]), 10)

    def test_broken_fixture_reports_unique_validator_roots(self) -> None:
        fixture = SKILL / "evals" / "files" / "broken-package"
        result = validate_expert.validate_package(fixture)
        self.assertEqual(len(result.errors), 25)
        self.assertLess(result.as_dict()["rootCauseCount"], result.as_dict()["rawFindingCount"])
        self.assertIn("README_SECTION_MISSING", {item.code for item in result.findings})
        self.assertTrue(
            {
                "LEGACY_README_PERMISSION_SECTION_MISSING",
                "LEGACY_README_PERMISSION_PROJECTION_MISMATCH",
            }.issubset({item.code for item in result.findings if item.severity == "warning"})
        )
        self.assertFalse(any(error.startswith("subagents: must contain") for error in result.errors))


if __name__ == "__main__":
    unittest.main()
