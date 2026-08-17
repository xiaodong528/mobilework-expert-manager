from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"
INSTALL = SCRIPTS / "install_expert.py"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))
from generator_test_support import managed_generator_env


class MinimalManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate_and_validate(
        self,
        manifest: dict[str, object],
    ) -> Path:
        source = self.root / str(manifest["slug"]) / "expert.json"
        source.parent.mkdir()
        source.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "out"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(source), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = output / str(manifest["slug"])
        validated = subprocess.run(
            [sys.executable, str(VALIDATE), str(package), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        payload = json.loads(validated.stdout)
        legacy_defaults = [
            item for item in payload["findings"]
            if item["code"] == "LEGACY_ROLE_AUTONOMY_DEFAULTED"
        ]
        self.assertFalse(legacy_defaults)
        return package

    def assert_minimal_projection(self, package: Path, expected_agents: set[str]) -> None:
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(set(runtime), {"$schema", "agent"})
        self.assertEqual(set(runtime["agent"]), expected_agents)
        for agent in runtime["agent"].values():
            self.assertEqual(set(agent), {"mode", "description", "steps", "permission"})

        source_manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        for optional in (
            "runtime_extensions",
            "mcp_servers",
            "package_resources",
            "workflows",
            "tags",
            "quick_prompts",
            "default_prompt",
        ):
            self.assertNotIn(optional, source_manifest)
        self.assertFalse((package / ".env.example").exists())
        for relative in (
            ".opencode/commands",
            ".opencode/tools",
            ".opencode/plugins",
            ".opencode/references",
            ".opencode/instructions",
            ".opencode/package.json",
        ):
            self.assertFalse((package / relative).exists(), relative)
        for directory in (
            path for path in package.rglob("*")
            if path.is_dir() and ".git" not in path.relative_to(package).parts
        ):
            if directory.relative_to(package).as_posix() == ".opencode/skills":
                continue
            self.assertTrue(any(directory.iterdir()), f"orphan empty directory: {directory}")

    def assert_zero_capability_resources(
        self,
        package: Path,
        expected_role_ids: set[str],
    ) -> None:
        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        roles = (
            [manifest["agent"]]
            if manifest["type"] == "expert"
            else [manifest["primary_agent"], *manifest["subagents"]]
        )
        self.assertEqual(manifest.get("skills", []), [])
        self.assertEqual({role["id"] for role in roles}, expected_role_ids)
        for role in roles:
            self.assertEqual(role.get("skills", []), [])
            self.assertEqual(role.get("custom_tools", []), [])

        skills_dir = package / ".opencode/skills"
        self.assertTrue(skills_dir.is_dir())
        self.assertEqual(list(skills_dir.rglob("SKILL.md")), [])
        self.assertFalse((package / ".opencode/tools").exists())
        self.assertFalse((package / ".opencode/plugins").exists())

        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("plugin", runtime)
        for role_id in expected_role_ids:
            self.assertEqual(
                runtime["agent"][role_id]["permission"]["skill"],
                {"*": "deny"},
            )
            agent = (package / f".opencode/agents/{role_id}.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("当前角色未分配包内业务 Skill", agent)
            self.assertIn("普通任务运行只消费已生成的专家包资源", agent)
            for protected_path in (
                "expert.json",
                "opencode.json",
                ".opencode/skills/",
                ".opencode/tools/",
                ".opencode/plugins/",
            ):
                self.assertIn(protected_path, agent)

    def test_minimal_expert_manifest_omits_every_optional_projection(self) -> None:
        package = self.generate_and_validate(
            {
                "slug": "minimal-expert",
                "type": "expert",
                "name": "最小专家",
                "description": "验证规范必选字段足以生成单专家。",
                "common_skills": [{"purpose": "delivery"}],
                "agent": {
                    "id": "minimal-agent",
                    "mode": "all",
                    "autonomy": "bounded",
                    "description": "直接完成最小专家任务。",
                    "skills": [{"purpose": "method"}],
                },
            }
        )
        self.assert_minimal_projection(package, {"minimal-agent"})
        workspace = self.root / "legacy-workspace"
        workspace.mkdir()
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(package),
                "--workspace-dir",
                str(workspace),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.assertTrue((workspace / ".opencode/opencode.jsonc").is_file())

    def test_new_expert_requires_explicit_role_autonomy(self) -> None:
        manifest = {
            "slug": "missing-role-autonomy",
            "type": "expert",
            "name": "缺自主度专家",
            "description": "验证新建角色必须显式选择自主度。",
            "skills": [],
            "agent": {
                "id": "missing-role-autonomy-agent",
                "mode": "all",
                "description": "缺少角色自主度。",
                "skills": [],
            },
        }
        source = self.root / "missing-role-autonomy" / "expert.json"
        source.parent.mkdir()
        source.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "missing-output"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(source), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(generated.returncode, 0)
        self.assertIn("autonomy is required", generated.stderr)
        self.assertFalse((output / "missing-role-autonomy").exists())

    def test_minimal_team_manifest_omits_every_optional_projection(self) -> None:
        package = self.generate_and_validate(
            {
                "slug": "minimal-team",
                "type": "team",
                "name": "最小专家团",
                "description": "验证规范必选字段足以生成专家团。",
                "common_skills": [{"purpose": "delivery"}],
                "primary_agent": {
                    "id": "team-lead",
                    "mode": "all",
                    "autonomy": "bounded",
                    "name": "团长",
                    "description": "分派、验收并整合团员结果。",
                    "skills": [{"purpose": "routing"}],
                },
                "subagents": [
                    {
                        "id": "team-member",
                        "mode": "subagent",
                        "autonomy": "bounded",
                        "name": "团员",
                        "description": "完成被分派的专业子任务。",
                        "skills": [{"purpose": "execution"}],
                    }
                ],
            }
        )
        self.assert_minimal_projection(package, {"team-lead", "team-member"})

    def test_unified_expert_without_workflow_uses_bounded_default(self) -> None:
        package = self.generate_and_validate(
            {
                "slug": "minimal-unified-expert",
                "type": "expert",
                "name": "最小统一专家",
                "description": "验证统一专家可以不声明顶层 Workflow。",
                "skills": [],
                "agent": {
                    "id": "minimal-unified-agent",
                    "mode": "all",
                    "autonomy": "bounded",
                    "description": "直接完成开放式专家任务。",
                    "skills": [],
                },
            },
        )
        self.assert_minimal_projection(package, {"minimal-unified-agent"})
        runtime = json.loads(
            (package / "opencode.json").read_text(encoding="utf-8")
        )
        permission = runtime["agent"]["minimal-unified-agent"]["permission"]
        self.assertEqual(permission["*"], "ask")
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(permission["bash"]["*"], "ask")
        self.assertEqual(permission["todowrite"], "allow")
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("role-autonomy", readme)
        self.assertIn("`bounded`", readme)
        agent = (
            package / ".opencode/agents/minimal-unified-agent.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Todo 只跟踪普通执行步骤；不得把临时步骤称为 manifest Phase",
            agent,
        )
        self.assert_zero_capability_resources(
            package,
            {"minimal-unified-agent"},
        )

    def test_unified_team_without_workflow_uses_bounded_default_and_task_topology(
        self,
    ) -> None:
        package = self.generate_and_validate(
            {
                "slug": "minimal-unified-team",
                "type": "team",
                "name": "最小统一专家团",
                "description": "验证统一专家团可以不声明顶层 Workflow。",
                "skills": [],
                "primary_agent": {
                    "id": "unified-lead",
                    "mode": "all",
                    "autonomy": "bounded",
                    "name": "统一团长",
                    "description": "动态分派、验收并整合团员结果。",
                    "skills": [],
                },
                "subagents": [
                    {
                        "id": "unified-member",
                        "mode": "subagent",
                        "autonomy": "bounded",
                        "name": "统一团员",
                        "description": "完成被分派的专业子任务。",
                        "skills": [],
                    }
                ],
            },
        )
        self.assert_minimal_projection(
            package,
            {"unified-lead", "unified-member"},
        )
        runtime = json.loads(
            (package / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            runtime["agent"]["unified-lead"]["permission"]["task"],
            {"*": "deny", "unified-member": "allow"},
        )
        self.assertEqual(
            runtime["agent"]["unified-member"]["permission"]["task"],
            {"*": "deny"},
        )
        self.assert_zero_capability_resources(
            package,
            {"unified-lead", "unified-member"},
        )


if __name__ == "__main__":
    unittest.main()
