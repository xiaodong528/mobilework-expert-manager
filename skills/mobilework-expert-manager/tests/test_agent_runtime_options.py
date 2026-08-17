from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


def read_frontmatter(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8").split("---", 2)[1]
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"invalid frontmatter in {path}")
    return value


def mutate_frontmatter(path: Path, mutate: Callable[[dict[str, object]], None]) -> None:
    source = path.read_text(encoding="utf-8")
    _, raw, body = source.split("---", 2)
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"invalid frontmatter in {path}")
    mutate(value)
    rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).rstrip()
    path.write_text(f"---\n{rendered}\n---{body}", encoding="utf-8")


class AgentRuntimeOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads(load_spec_text("legacy-expert-json"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate(
        self,
        data: dict[str, object],
        *,
        name: str,
        force: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        source = self.root / name / "expert.json"
        source.parent.mkdir(exist_ok=True)
        source.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / f"{name}-out"
        command = [
            sys.executable,
            str(CREATE),
            "--manifest",
            str(source),
            "--output-dir",
            str(output),
        ]
        if force:
            command.append("--force")
        result = subprocess.run(
            command,
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output / str(data["slug"])

    def validate(self, package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATE), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_single_expert_projects_supported_declared_options(self) -> None:
        data = copy.deepcopy(self.base)
        role = data["agent"]
        role.update(
            {
                "steps": 64,
                "model": "openai/gpt-5",
                "variant": "high",
                "options": {
                    "reasoningEffort": "high",
                    "textVerbosity": "low",
                    "providerLimits": {"threshold": 0.75},
                },
            }
        )
        role["permission"].pop("webfetch")
        role["tools"] = {"webfetch": False}

        created, package = self.generate(data, name="single-full")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        config_agent = runtime["agent"][role["id"]]
        markdown_agent = read_frontmatter(package / f".opencode/agents/{role['id']}.md")
        expected = {
            key: role[key]
            for key in ("steps", "model", "variant", "options")
        }
        for key, value in expected.items():
            self.assertEqual(config_agent[key], value)
            self.assertEqual(markdown_agent[key], value)
        self.assertNotIn("hidden", config_agent)
        self.assertNotIn("hidden", markdown_agent)
        self.assertNotIn("temperature", config_agent)
        self.assertNotIn("top_p", config_agent)
        self.assertNotIn("temperature", markdown_agent)
        self.assertNotIn("top_p", markdown_agent)
        self.assertNotIn("tools", config_agent)
        self.assertNotIn("tools", markdown_agent)
        self.assertEqual(config_agent["permission"]["webfetch"], "deny")
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Agent 运行参数", readme)
        self.assertIn("`reasoningEffort`", readme)
        self.assertIn("`textVerbosity`", readme)
        self.assertNotIn('"reasoningEffort": "high"', readme)
        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_team_allows_hidden_only_for_subagents(self) -> None:
        data = copy.deepcopy(self.base)
        source_role = data.pop("agent")
        data["type"] = "team"
        primary = copy.deepcopy(source_role)
        primary.update(
            {
                "id": "review-lead",
                "mode": "all",
                "steps": 120,
                "model": "openai/gpt-5",
            }
        )
        primary["permission"].pop("skill", None)
        subagent = copy.deepcopy(source_role)
        subagent.update(
            {
                "id": "silent-reviewer",
                "mode": "subagent",
                "steps": 45,
                "model": "anthropic/claude-sonnet-4",
                "hidden": True,
                "options": {"reasoningEffort": "medium"},
            }
        )
        subagent["permission"].pop("skill", None)
        data["primary_agent"] = primary
        data["subagents"] = [subagent]

        created, package = self.generate(data, name="team-hidden")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("hidden", runtime["agent"]["review-lead"])
        self.assertNotIn("temperature", runtime["agent"]["review-lead"])
        self.assertNotIn("top_p", runtime["agent"]["review-lead"])
        self.assertIs(runtime["agent"]["silent-reviewer"]["hidden"], True)
        self.assertNotIn("top_p", runtime["agent"]["silent-reviewer"])
        self.assertNotIn("temperature", runtime["agent"]["silent-reviewer"])
        member = read_frontmatter(package / ".opencode/agents/silent-reviewer.md")
        self.assertIs(member["hidden"], True)
        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_steps_aliases_and_default_are_compatible(self) -> None:
        for index, key in enumerate(("steps", "max_turns", "maxTurns")):
            with self.subTest(key=key):
                data = copy.deepcopy(self.base)
                role = data["agent"]
                for alias in ("steps", "max_turns", "maxTurns"):
                    role.pop(alias, None)
                role[key] = 72
                created, package = self.generate(data, name=f"steps-{index}")
                self.assertEqual(created.returncode, 0, created.stderr)
                runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
                config_agent = runtime["agent"][role["id"]]
                markdown_agent = read_frontmatter(package / f".opencode/agents/{role['id']}.md")
                self.assertEqual(config_agent["steps"], 72)
                self.assertEqual(markdown_agent["steps"], 72)
                for non_official_key in ("max_turns", "maxTurns", "maxSteps"):
                    self.assertNotIn(non_official_key, config_agent)
                    self.assertNotIn(non_official_key, markdown_agent)
                generated_manifest = json.loads(
                    (package / "expert.json").read_text(encoding="utf-8")
                )
                generated_role = generated_manifest["agent"]
                self.assertEqual(generated_role["steps"], 72)
                for legacy_key in ("max_turns", "maxTurns", "maxSteps"):
                    self.assertNotIn(legacy_key, generated_role)

        data = copy.deepcopy(self.base)
        for alias in ("steps", "max_turns", "maxTurns"):
            data["agent"].pop(alias, None)
        created, package = self.generate(data, name="steps-default")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["agent"][data["agent"]["id"]]["steps"], 80)

        data = copy.deepcopy(self.base)
        data["agent"].update({"steps": 70, "max_turns": 70, "maxTurns": 70})
        created, package = self.generate(data, name="steps-identical-aliases")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        config_agent = runtime["agent"][data["agent"]["id"]]
        markdown_agent = read_frontmatter(package / ".opencode/agents/contract-reviewer.md")
        self.assertEqual(config_agent["steps"], 70)
        self.assertEqual(markdown_agent["steps"], 70)
        for non_official_key in ("max_turns", "maxTurns", "maxSteps"):
            self.assertNotIn(non_official_key, config_agent)
            self.assertNotIn(non_official_key, markdown_agent)

    def test_docs_distinguish_official_steps_from_mobilework_legacy_inputs(self) -> None:
        expert_spec = (SKILL_ROOT / "references/expert-json-spec.md").read_text(encoding="utf-8")
        opencode_spec = (SKILL_ROOT / "references/opencode-json-spec.md").read_text(encoding="utf-8")
        authoring_spec = (
            SKILL_ROOT / "references/opencode-authoring-best-practices.md"
        ).read_text(encoding="utf-8")

        self.assertIn("不是 OpenCode Agent 选项", " ".join(expert_spec.split()))
        self.assertIn("正式步数字段只有 `steps`", " ".join(opencode_spec.split()))
        self.assertIn("不是 OpenCode Agent 选项", " ".join(authoring_spec.split()))

    def test_invalid_runtime_options_are_rejected(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], None], str]] = [
            (
                "step-conflict",
                lambda role: role.update({"steps": 80, "max_turns": 81}),
                "conflicting step aliases",
            ),
            ("step-type", lambda role: role.update({"steps": True}), "positive integer"),
            ("model", lambda role: role.update({"model": "missing-provider"}), "provider/model"),
            ("variant", lambda role: role.update({"variant": "high"}), "requires model"),
            (
                "temperature",
                lambda role: role.update({"temperature": 0.2}),
                "unsupported Agent fields temperature",
            ),
            (
                "top-p",
                lambda role: role.update({"top_p": 0.8}),
                "unsupported Agent fields top_p",
            ),
            (
                "nested-temperature",
                lambda role: role.update(
                    {"options": {"provider": {"temperature": 0.2}}}
                ),
                "sampling fields are unsupported",
            ),
            ("hidden", lambda role: role.update({"hidden": True}), "only allowed for subagents"),
            ("empty-options", lambda role: role.update({"options": {}}), "non-empty JSON object"),
            (
                "nan-options",
                lambda role: role.update({"options": {"nested": [float("nan")]}}),
                "numbers must be finite",
            ),
            (
                "provider-top-level",
                lambda role: role.update({"reasoningEffort": "high"}),
                "put provider-specific parameters under options",
            ),
            ("prompt", lambda role: role.update({"prompt": "override"}), "unsupported Agent fields"),
            ("disable", lambda role: role.update({"disable": True}), "unsupported Agent fields"),
            ("max-steps", lambda role: role.update({"maxSteps": 80}), "unsupported Agent fields"),
            (
                "secret-options",
                lambda role: role.update({"options": {"apiKey": "sk-123456789012345678901234"}}),
                "possible secret-like value",
            ),
            (
                "non-portable-options",
                lambda role: role.update({"options": {"cacheDir": "/Users/example/private"}}),
                "non-portable developer home absolute path",
            ),
        ]
        for index, (name, mutate, expected) in enumerate(cases):
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                mutate(data["agent"])
                created, _ = self.generate(data, name=f"invalid-{index}")
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)

    def test_validator_detects_markdown_and_runtime_drift(self) -> None:
        data = copy.deepcopy(self.base)
        data["agent"].update(
            {
                "model": "openai/gpt-5",
                "variant": "high",
                "options": {"reasoningEffort": "high"},
            }
        )
        created, package = self.generate(data, name="drift")
        self.assertEqual(created.returncode, 0, created.stderr)
        agent_path = package / ".opencode/agents/contract-reviewer.md"

        mutate_frontmatter(agent_path, lambda value: value.pop("model"))
        validated = self.validate(package)
        self.assertNotEqual(validated.returncode, 0)
        self.assertIn("model must match expert.json", validated.stdout)

        created, package = self.generate(data, name="drift", force=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        config_path = package / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent"]["contract-reviewer"]["options"]["reasoningEffort"] = "low"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validated = self.validate(package)
        self.assertNotEqual(validated.returncode, 0)
        self.assertIn("options must match expert.json", validated.stdout)

        created, package = self.generate(data, name="drift", force=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        agent_path = package / ".opencode/agents/contract-reviewer.md"
        mutate_frontmatter(agent_path, lambda value: value.update({"providerOption": True}))
        validated = self.validate(package)
        self.assertNotEqual(validated.returncode, 0)
        self.assertIn("unsupported frontmatter fields providerOption", validated.stdout)

    def test_legacy_sampling_fields_validate_with_warning_but_cannot_regenerate(self) -> None:
        data = copy.deepcopy(self.base)
        created, package = self.generate(data, name="legacy-sampling")
        self.assertEqual(created.returncode, 0, created.stderr)

        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["agent"].update({"temperature": 0.2, "top_p": 0.8})
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        runtime_path = package / "opencode.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["agent"]["contract-reviewer"].update(
            {"temperature": 0.2, "top_p": 0.8}
        )
        runtime_path.write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        agent_path = package / ".opencode/agents/contract-reviewer.md"
        mutate_frontmatter(
            agent_path,
            lambda value: value.update({"temperature": 0.2, "top_p": 0.8}),
        )

        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)
        self.assertIn("legacy sampling field remains readable", validated.stdout)

        regenerated, _ = self.generate(
            manifest,
            name="legacy-sampling-regenerate",
        )
        self.assertNotEqual(regenerated.returncode, 0)
        self.assertIn("unsupported Agent fields temperature, top_p", regenerated.stderr)

    def test_todo_permission_projects_to_runtime_and_agent_markdown(self) -> None:
        data = copy.deepcopy(self.base)
        created, package = self.generate(data, name="todo-projection")
        self.assertEqual(created.returncode, 0, created.stderr)

        role_id = data["agent"]["id"]
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        markdown_path = package / f".opencode/agents/{role_id}.md"
        markdown = markdown_path.read_text(encoding="utf-8")
        frontmatter = read_frontmatter(markdown_path)
        self.assertEqual(runtime["agent"][role_id]["permission"]["todowrite"], "allow")
        self.assertEqual(frontmatter["permission"]["todowrite"], "allow")
        self.assertNotIn("todoread", runtime["agent"][role_id]["permission"])
        for marker in (
            "## Todo 与 Phase 进度",
            "`pending`、`in_progress`、`completed`、`cancelled`",
            "通过该 Phase 的全部 acceptance",
            "阻塞不得标记为 `completed`",
            "Todo 不得反向修改 Workflow",
        ):
            self.assertIn(marker, markdown)

    def test_validator_rejects_todo_projection_tampering(self) -> None:
        data = copy.deepcopy(self.base)
        role_id = data["agent"]["id"]
        created, package = self.generate(data, name="todo-tamper")
        self.assertEqual(created.returncode, 0, created.stderr)

        markdown_path = package / f".opencode/agents/{role_id}.md"
        markdown_mutations = (
            lambda value: value["permission"].pop("todowrite"),
            lambda value: value["permission"].update({"todowrite": "ask"}),
            lambda value: value["permission"].update({"todoread": "allow"}),
        )
        for index, mutate in enumerate(markdown_mutations):
            with self.subTest(target="agent-markdown", index=index):
                if index:
                    created, package = self.generate(
                        data,
                        name="todo-tamper",
                        force=True,
                    )
                    self.assertEqual(created.returncode, 0, created.stderr)
                    markdown_path = package / f".opencode/agents/{role_id}.md"
                mutate_frontmatter(markdown_path, mutate)
                validated = self.validate(package)
                self.assertNotEqual(validated.returncode, 0)
                self.assertIn("permission", validated.stdout)

        runtime_mutations = (
            lambda value: value["agent"][role_id]["permission"].pop("todowrite"),
            lambda value: value["agent"][role_id]["permission"].update(
                {"todowrite": "deny"}
            ),
            lambda value: value["agent"][role_id]["permission"].update(
                {"todoread": "allow"}
            ),
        )
        for index, mutate in enumerate(runtime_mutations):
            with self.subTest(target="opencode-json", index=index):
                created, package = self.generate(
                    data,
                    name="todo-tamper",
                    force=True,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                runtime_path = package / "opencode.json"
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                mutate(runtime)
                runtime_path.write_text(
                    json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                validated = self.validate(package)
                self.assertNotEqual(validated.returncode, 0)
                self.assertIn("permission", validated.stdout)

    def test_generator_rejects_todo_manifest_declarations(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], None]]] = [
            (
                "permission",
                lambda data: data["agent"]["permission"].update(
                    {"todowrite": "deny"}
                ),
            ),
            (
                "tools",
                lambda data: data["agent"].setdefault("tools", {}).update(
                    {"todoread": False}
                ),
            ),
            (
                "custom-tool",
                lambda data: data["runtime_extensions"].setdefault(
                    "custom_tools",
                    [],
                ).append(
                    {
                        "path": "todowrite.ts",
                        "purpose": "故意冲突系统 Todo 工具的负向测试。",
                        "content": "export default {}",
                    }
                ),
            ),
        ]
        for index, (name, mutate) in enumerate(cases):
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                mutate(data)
                created, _package = self.generate(
                    data,
                    name=f"todo-declaration-{index}",
                )
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(
                    "Todo 由系统托管，请删除该声明",
                    created.stderr,
                )


if __name__ == "__main__":
    unittest.main()
