from __future__ import annotations

import copy
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
TEAM_EXAMPLE = SKILL_ROOT / "evals" / "files" / "software-dev-team.expert.json"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import validate_expert
import workflow_autonomy
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class WorkflowAutonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def single_manifest(self) -> dict[str, object]:
        return json.loads(load_spec_text("expert-json"))

    def team_manifest(self) -> dict[str, object]:
        return json.loads(TEAM_EXAMPLE.read_text(encoding="utf-8"))

    def add_single_contract(self, data: dict[str, object]) -> None:
        agent = data["agent"]
        assert isinstance(agent, dict)
        agent["permission"] = {}
        agent.pop("tools", None)
        runtime = data.setdefault("runtime_extensions", {})
        assert isinstance(runtime, dict)
        runtime.setdefault("custom_tools", []).append(
            {
                "path": "validate.ts",
                "content": "export default async () => ({ ok: true })\n",
            }
        )
        data["workflows"] = [
            {
                "name": "合同审查",
                "trigger": "用户需要完整审查合同时触发。",
                "autonomy": "bounded",
                "command": {
                    "name": "review-contract",
                    "description": "按照合同审查 workflow 完成校验和风险判断",
                },
                "phases": [
                    {
                        "name": "确定性校验",
                        "mode": "serial",
                        "agents": ["contract-reviewer"],
                        "autonomy": "scripted",
                        "input": "合同文本",
                        "expected_output": "机器可读校验结果",
                        "execution": {
                            "executors": [
                                {"kind": "custom-tool", "ref": "validate.ts"}
                            ],
                            "standards": ["只调用 validate.ts，不得临时编写替代代码"],
                        },
                        "agent_overrides": {
                            "contract-reviewer": {
                                "autonomy": "fixed",
                                "reason": "允许按已确认规则处理工具返回的已知错误码。",
                            }
                        },
                        "acceptance": ["校验结果包含输入摘要和工具证据"],
                    },
                    {
                        "name": "风险判断",
                        "mode": "serial",
                        "agents": ["contract-reviewer"],
                        "autonomy": "guided",
                        "autonomy_reason": "非标准条款需要结合上下文分析。",
                        "input": "已通过校验的合同条款",
                        "expected_output": "风险分级和修改建议",
                        "execution": {
                            "executors": [
                                {"kind": "agent", "ref": "contract-reviewer"}
                            ],
                            "standards": ["高影响修改建议必须先请求用户确认"],
                        },
                        "acceptance": ["每个高风险结论引用条款位置"],
                    },
                ],
            }
        ]

    def add_team_contract(self, data: dict[str, object]) -> None:
        primary = data["primary_agent"]
        subagents = data["subagents"]
        assert isinstance(primary, dict) and isinstance(subagents, list)
        for item in [primary, *subagents]:
            assert isinstance(item, dict)
            item["permission"] = {}
            item.pop("tools", None)
        data["workflows"] = [
            {
                "name": "方案评审",
                "trigger": "用户要求完整评审方案时触发。",
                "autonomy": "bounded",
                "command": {
                    "name": "review-solution",
                    "description": "按照方案评审 workflow 汇总产品与架构意见",
                },
                "phases": [
                    {
                        "name": "并行评审",
                        "mode": "parallel",
                        "agents": ["product-strategist", "architect", "qa-reviewer"],
                        "autonomy": "guided",
                        "autonomy_reason": "需要探索非标准方案，但关键选择必须确认。",
                        "input": "用户目标和方案附件",
                        "expected_output": "产品与架构评审结果",
                        "execution": {
                            "executors": [
                                {"kind": "agent", "ref": "product-strategist"},
                                {"kind": "agent", "ref": "architect"},
                                {"kind": "agent", "ref": "qa-reviewer"},
                            ],
                            "standards": ["高影响范围和架构决定必须先确认"],
                        },
                        "agent_overrides": {
                            "architect": {
                                "autonomy": "fixed",
                                "execution": {
                                    "executors": [
                                        {"kind": "agent", "ref": "architect"}
                                    ],
                                    "standards": ["只按已批准架构检查表评审"],
                                },
                            },
                            "qa-reviewer": {
                                "execution": {
                                    "executors": [
                                        {"kind": "agent", "ref": "qa-reviewer"}
                                    ],
                                    "standards": ["只按已批准质量清单评审"],
                                },
                            }
                        },
                        "acceptance": ["三个角色分别返回可验收证据"],
                    }
                ],
            }
        ]

    def generate(
        self,
        name: str,
        data: dict[str, object],
        *,
        resource_files: dict[str, str] | None = None,
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        source = self.root / name
        source.mkdir()
        for relative, content in (resource_files or {}).items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        manifest = source / "expert.json"
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = source / "out"
        result = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        return output / str(data["slug"]), result

    def normalize(self, data: dict[str, object]) -> list[dict[str, object]]:
        if data["type"] == "expert":
            role = data["agent"]
            assert isinstance(role, dict)
            role_ids = {str(role["id"])}
            primary_id = str(role["id"])
        else:
            primary = data["primary_agent"]
            subagents = data["subagents"]
            assert isinstance(primary, dict) and isinstance(subagents, list)
            primary_id = str(primary["id"])
            role_ids = {primary_id, *(str(item["id"]) for item in subagents if isinstance(item, dict))}
        return workflow_autonomy.normalize_workflows(
            data,
            role_ids=role_ids,
            primary_id=primary_id,
        )

    def test_five_levels_and_three_layer_inheritance(self) -> None:
        data = self.single_manifest()
        runner = (
            ".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/"
            "scripts/verify.py"
        )
        data["package_resources"] = [{"path": runner, "kind": "text"}]
        phases: list[dict[str, object]] = []
        for level in workflow_autonomy.AUTONOMY_LEVELS:
            phase: dict[str, object] = {
                "name": level,
                "mode": "serial",
                "agents": ["contract-reviewer"],
                "autonomy": level,
                "acceptance": [f"{level} result is verified"],
            }
            if workflow_autonomy.AUTONOMY_RANK[level] > workflow_autonomy.AUTONOMY_RANK["bounded"]:
                phase["autonomy_reason"] = "该阶段需要更高自主度处理非标准情况。"
            if level != "adaptive":
                phase["execution"] = {
                    "executors": (
                        []
                        if level == "guided"
                        else [{"kind": "programming-tool", "ref": f"python3 {runner} *"}]
                    ),
                    "standards": [
                        "关键决定先确认" if level == "guided" else "按已确认输入输出合同执行"
                    ],
                }
            phases.append(phase)
        phases[2]["agent_overrides"] = {
            "contract-reviewer": {
                "autonomy": "guided",
                "reason": "该角色需要处理非标准例外。",
                "execution": {
                    "executors": [],
                    "standards": ["例外处理前请求确认"],
                },
            }
        }
        data["workflows"] = [
            {
                "name": "五档测试",
                "autonomy": "bounded",
                "phases": phases,
            }
        ]

        normalized = self.normalize(data)
        actual = [phase["effective_autonomy"] for phase in normalized[0]["phases"]]
        self.assertEqual(actual, list(workflow_autonomy.AUTONOMY_LEVELS))
        self.assertEqual(
            [workflow_autonomy.autonomy_prefix(level) for level in workflow_autonomy.AUTONOMY_LEVELS],
            [
                "【自主度：极低】",
                "【自主度：低】",
                "【自主度：中】",
                "【自主度：高】",
                "【自主度：极高】",
            ],
        )
        override = normalized[0]["phases"][2]["agent_overrides"]["contract-reviewer"]
        self.assertEqual(override["effective_autonomy"], "guided")

    def test_single_expert_generates_complete_command_and_all_projections(self) -> None:
        data = self.single_manifest()
        self.add_single_contract(data)
        package, result = self.generate("single", data)
        self.assertEqual(result.returncode, 0, result.stderr)
        validation = validate_expert.validate_package(package)
        self.assertTrue(validation.ok, validation.errors)

        command = (package / ".opencode/commands/review-contract.md").read_text(encoding="utf-8")
        for expected in (
            "agent: contract-reviewer",
            "【自主度：中】 按照合同审查 workflow 完成校验和风险判断",
            "用户要求：$ARGUMENTS",
            "Workflow 默认自主度：中：可在明确边界内选择方法 (`bounded`)",
            "**【自主度：极低】 确定性校验**",
            "Phase 生效自主度：极低：全程照脚本执行，不能自行换方法 (`scripted`)",
            "Agent `contract-reviewer`：【自主度：低】",
            "生效自主度：低：按固定步骤执行，只能处理预设分支 (`fixed`)",
            "自主度来源：Agent override",
            "execution 来源：Phase",
            "custom-tool",
            "validate.ts",
            "**【自主度：高】 风险判断**",
            "Agent `contract-reviewer`：【自主度：高】",
            "自主度来源：Phase",
            "高：可根据目标灵活安排，但关键决定需确认 (`guided`)",
            "验收标准",
            "停止、升级与确认",
        ):
            self.assertIn(expected, command)
        self.assertNotIn("Agent override `contract-reviewer`", command)

        primary = (package / ".opencode/agents/contract-reviewer.md").read_text(encoding="utf-8")
        readme = (package / "README.md").read_text(encoding="utf-8")
        common = (
            package
            / ".opencode/skills/contract-review-expert-common-delivery-quality/SKILL.md"
        ).read_text(encoding="utf-8")
        role_skill = (
            package
            / ".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/SKILL.md"
        ).read_text(encoding="utf-8")
        for text in (primary, readme, common):
            self.assertIn("中：可在明确边界内选择方法", text)
            self.assertIn("确定性校验", text)
            self.assertNotIn("【自主度：", text)
        self.assertIn("低：按固定步骤执行，只能处理预设分支", role_skill)
        self.assertIn("确定性校验", role_skill)
        self.assertNotIn("【自主度：", role_skill)
        config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("autonomy", config)
        self.assertNotIn("command", config)

    def test_team_projects_effective_autonomy_to_primary_and_members(self) -> None:
        data = self.team_manifest()
        self.add_team_contract(data)
        package, result = self.generate("team", data)
        self.assertEqual(result.returncode, 0, result.stderr)
        validation = validate_expert.validate_package(package)
        self.assertTrue(validation.ok, validation.errors)
        primary = (package / ".opencode/agents/delivery-director.md").read_text(encoding="utf-8")
        product = (package / ".opencode/agents/product-strategist.md").read_text(encoding="utf-8")
        architect = (package / ".opencode/agents/architect.md").read_text(encoding="utf-8")
        qa = (package / ".opencode/agents/qa-reviewer.md").read_text(encoding="utf-8")
        command = (package / ".opencode/commands/review-solution.md").read_text(encoding="utf-8")
        self.assertIn("Agent override `architect`", primary)
        self.assertIn("prompt 必须包含该 Agent 的生效自主度", primary)
        self.assertIn("高：可根据目标灵活安排，但关键决定需确认 (`guided`)", product)
        self.assertIn("低：按固定步骤执行，只能处理预设分支 (`fixed`)", architect)
        self.assertIn("高：可根据目标灵活安排，但关键决定需确认 (`guided`)", qa)
        self.assertIn("**【自主度：高】 并行评审**", command)
        for agent_id, prefix in (
            ("product-strategist", "【自主度：高】"),
            ("architect", "【自主度：低】"),
            ("qa-reviewer", "【自主度：高】"),
        ):
            marker = f"Agent `{agent_id}`：{prefix}"
            self.assertEqual(command.count(marker), 1, command)
        product_section = command.split("Agent `product-strategist`：", 1)[1].split(
            "Agent `architect`：", 1
        )[0]
        architect_section = command.split("Agent `architect`：", 1)[1].split(
            "Agent `qa-reviewer`：", 1
        )[0]
        qa_section = command.split("Agent `qa-reviewer`：", 1)[1].split("   - 输入：", 1)[0]
        self.assertIn("自主度来源：Phase", product_section)
        self.assertIn("execution 来源：Phase", product_section)
        self.assertIn("自主度来源：Agent override", architect_section)
        self.assertIn("execution 来源：Agent override", architect_section)
        self.assertIn("自主度来源：Phase", qa_section)
        self.assertIn("execution 来源：Agent override", qa_section)
        self.assertNotIn("Agent override `architect`", command)

    def test_invalid_contracts_are_rejected(self) -> None:
        cases: list[tuple[str, callable, str]] = []

        def case(name: str, mutate: callable, expected: str) -> None:
            cases.append((name, mutate, expected))

        case("invalid level", lambda d: d["workflows"][0].__setitem__("autonomy", "free"), "must be one of")
        case(
            "unknown workflow field",
            lambda d: d["workflows"][0].__setitem__("mystery", True),
            "workflows\\[0\\]: unsupported fields: mystery",
        )
        case(
            "unknown phase field",
            lambda d: d["workflows"][0]["phases"][0].__setitem__("mystery", True),
            "phases\\[0\\]: unsupported fields: mystery",
        )
        case(
            "unknown execution field",
            lambda d: d["workflows"][0]["phases"][0]["execution"].__setitem__(
                "mystery", True
            ),
            "execution: unsupported fields: mystery",
        )
        case(
            "unknown Agent override field",
            lambda d: d["workflows"][0]["phases"][0]["agent_overrides"][
                "contract-reviewer"
            ].__setitem__("mystery", True),
            "agent_overrides.contract-reviewer: unsupported fields: mystery",
        )
        case(
            "reserved command prefix",
            lambda d: d["workflows"][0]["command"].__setitem__(
                "description", "  【自主度：中】 不应手写生成前缀"
            ),
            "reserved generated prefix",
        )
        case(
            "phase raise without reason",
            lambda d: d["workflows"][0]["phases"][1].pop("autonomy_reason"),
            "autonomy_reason",
        )
        case(
            "override raise without reason",
            lambda d: d["workflows"][0]["phases"][0]["agent_overrides"]["contract-reviewer"].pop("reason"),
            "reason",
        )
        case(
            "unknown override",
            lambda d: d["workflows"][0]["phases"][0].__setitem__(
                "agent_overrides", {"unknown-agent": {"autonomy": "fixed"}}
            ),
            "must participate",
        )
        case(
            "scripted agent",
            lambda d: d["workflows"][0]["phases"][0]["execution"].__setitem__(
                "executors", [{"kind": "agent", "ref": "contract-reviewer"}]
            ),
            "scripted must not use agent",
        )
        case(
            "fixed missing standards",
            lambda d: d["workflows"][0]["phases"][0]["agent_overrides"]["contract-reviewer"].__setitem__(
                "execution", {"executors": [{"kind": "custom-tool", "ref": "validate.ts"}]}
            ),
            "requires process standards",
        )
        case(
            "empty acceptance",
            lambda d: d["workflows"][0]["phases"][0].__setitem__("acceptance", []),
            "acceptance",
        )
        case(
            "missing custom tool",
            lambda d: d["workflows"][0]["phases"][0]["execution"]["executors"][0].__setitem__(
                "ref", "missing.ts"
            ),
            "runtime_extensions.custom_tools",
        )
        case(
            "missing skill script",
            lambda d: d["workflows"][0]["phases"][0]["execution"].__setitem__(
                "executors",
                [
                    {
                        "kind": "skill-script",
                        "ref": "contract-review-expert-common-delivery-quality:scripts/missing.py",
                    }
                ],
            ),
            "declared supplemental skill script",
        )
        case(
            "permission denied",
            lambda d: d["agent"]["permission"].__setitem__("validate", "deny"),
            "permission denies custom tool",
        )
        case(
            "command collision",
            lambda d: d["runtime_extensions"].__setitem__(
                "commands", [{"name": "review-contract", "template": "duplicate"}]
            ),
            "conflicts with command",
        )
        case(
            "workflow command collision",
            lambda d: d["workflows"].append(
                {
                    "name": "重复 command",
                    "autonomy": "adaptive",
                    "command": {
                        "name": "review-contract",
                        "description": "重复 command",
                    },
                    "phases": [
                        {
                            "name": "重复阶段",
                            "agents": ["contract-reviewer"],
                            "acceptance": ["重复阶段完成"],
                        }
                    ],
                }
            ),
            "conflicts with command",
        )
        case(
            "phase fields without workflow level",
            lambda d: d["workflows"][0].pop("autonomy"),
            "required when workflow command is declared",
        )

        for name, mutate, expected in cases:
            with self.subTest(name=name):
                data = self.single_manifest()
                self.add_single_contract(data)
                mutate(data)
                with self.assertRaisesRegex(
                    workflow_autonomy.WorkflowContractError,
                    expected,
                ):
                    self.normalize(data)

    def test_mcp_executor_requires_agent_ownership(self) -> None:
        data = self.single_manifest()
        self.add_single_contract(data)
        data["mcp_servers"] = [
            {
                "name": "secure-docs",
                "type": "remote",
                "url": "https://example.com/mcp",
                "enabled": True,
            }
        ]
        phase = data["workflows"][0]["phases"][0]
        phase["autonomy"] = "fixed"
        phase["execution"] = {
            "executors": [{"kind": "mcp-tool", "ref": "secure-docs/read"}],
            "standards": ["只读取已授权文档"],
        }
        phase.pop("agent_overrides")
        with self.assertRaisesRegex(
            workflow_autonomy.WorkflowContractError,
            "MCP owned by Agent",
        ):
            self.normalize(data)
        data["agent"]["mcp"] = ["secure-docs"]
        self.assertEqual(self.normalize(data)[0]["phases"][0]["effective_autonomy"], "fixed")

    def test_skill_script_and_programming_tool_permissions_are_enforced(self) -> None:
        data = self.single_manifest()
        self.add_single_contract(data)
        script_path = (
            ".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/"
            "scripts/check_contract.py"
        )
        data["package_resources"] = [{"path": script_path, "kind": "text"}]
        phase = data["workflows"][0]["phases"][0]
        phase["execution"] = {
            "executors": [
                {
                    "kind": "skill-script",
                    "ref": (
                        "contract-review-expert-contract-reviewer-role-guidelines:"
                        "scripts/check_contract.py"
                    ),
                }
            ],
            "standards": ["只按脚本输入输出合同执行"],
        }
        phase.pop("agent_overrides")
        package, result = self.generate(
            "skill-script",
            data,
            resource_files={script_path: "print('verified')\n"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(validate_expert.validate_package(package).ok)

        phase["autonomy"] = "fixed"
        phase["execution"] = {
            "executors": [{"kind": "programming-tool", "ref": f"python3 {script_path} *"}],
            "standards": ["只运行批准的只读检查命令"],
        }
        data["agent"]["permission"]["bash"] = "deny"
        with self.assertRaisesRegex(
            workflow_autonomy.WorkflowContractError,
            "permission denies programming tool",
        ):
            self.normalize(data)
        data["agent"]["permission"]["bash"] = {f"python3 {script_path} *": "allow"}
        self.assertEqual(self.normalize(data)[0]["phases"][0]["effective_autonomy"], "fixed")

    def test_programming_tool_requires_owned_resource_and_rejects_shell_controls(self) -> None:
        data = self.single_manifest()
        self.add_single_contract(data)
        phase = data["workflows"][0]["phases"][0]
        phase["execution"]["executors"] = [
            {"kind": "programming-tool", "ref": "python3 missing.py *"}
        ]
        with self.assertRaisesRegex(workflow_autonomy.WorkflowContractError, "declared package resource"):
            self.normalize(data)
        resource = (
            ".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/"
            "scripts/check.py"
        )
        data["package_resources"] = [{"path": resource, "kind": "text"}]
        phase["execution"]["executors"] = [
            {"kind": "programming-tool", "ref": f"python3 {resource} *; touch escaped"}
        ]
        with self.assertRaisesRegex(workflow_autonomy.WorkflowContractError, "unsafe programming-tool"):
            self.normalize(data)

    def test_validator_rejects_tampered_workflow_projections(self) -> None:
        targets = {
            "command description": (
                ".opencode/commands/review-contract.md",
                "【自主度：中】 按照合同审查 workflow 完成校验和风险判断",
            ),
            "command phase": (
                ".opencode/commands/review-contract.md",
                "【自主度：极低】 确定性校验",
            ),
            "command Agent": (
                ".opencode/commands/review-contract.md",
                "Agent `contract-reviewer`：【自主度：低】",
            ),
            "agent": (".opencode/agents/contract-reviewer.md", "中：可在明确边界内选择方法"),
            "common skill": (".opencode/skills/contract-review-expert-common-delivery-quality/SKILL.md", "中：可在明确边界内选择方法"),
            "role skill": (".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/SKILL.md", "低：按固定步骤执行，只能处理预设分支"),
            "readme": ("README.md", "中：可在明确边界内选择方法"),
        }
        for index, (name, (relative, marker)) in enumerate(targets.items()):
            with self.subTest(name=name):
                data = self.single_manifest()
                self.add_single_contract(data)
                package, result = self.generate(f"tamper-{index}", data)
                self.assertEqual(result.returncode, 0, result.stderr)
                path = package / relative
                text = path.read_text(encoding="utf-8")
                self.assertIn(marker, text)
                path.write_text(text.replace(marker, "不受约束", 1), encoding="utf-8")
                validation = validate_expert.validate_package(package)
                self.assertFalse(validation.ok)
                self.assertTrue(
                    any("projection differs" in error for error in validation.errors),
                    validation.errors,
                )

    def test_runtime_extension_command_keeps_original_description(self) -> None:
        data = self.single_manifest()
        runtime = data["runtime_extensions"]
        assert isinstance(runtime, dict)
        runtime["commands"] = [
            {
                "name": "manual-check",
                "description": "普通 command 说明",
                "template": "# 普通 command\n\n用户要求：$ARGUMENTS",
            }
        ]
        package, result = self.generate("runtime-command", data)
        self.assertEqual(result.returncode, 0, result.stderr)
        command = (package / ".opencode/commands/manual-check.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("普通 command 说明", command)
        self.assertNotIn("【自主度：", command)
        self.assertTrue(validate_expert.validate_package(package).ok)

    def test_legacy_workflow_keeps_legacy_projection_and_no_generated_command(self) -> None:
        data = self.single_manifest()
        data["workflows"] = [
            {
                "name": "旧流程",
                "trigger": "旧专家包触发。",
                "phases": [
                    {
                        "name": "旧阶段",
                        "mode": "primary",
                        "agents": [],
                        "acceptance": ["旧验收"],
                    }
                ],
            }
        ]
        package, result = self.generate("legacy", data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((package / ".opencode/commands").exists())
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(encoding="utf-8")
        self.assertNotIn("Workflow 默认自主度", agent)
        validation = validate_expert.validate_package(package)
        self.assertTrue(validation.ok, validation.errors)


if __name__ == "__main__":
    unittest.main()
