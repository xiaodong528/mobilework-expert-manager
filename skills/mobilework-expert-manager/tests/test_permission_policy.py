from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import permission_policy


def workflow(level: str, role_id: str = "reviewer", execution: dict | None = None) -> dict:
    return {
        "contract_enabled": True,
        "phases": [
            {
                "participants": [role_id],
                "effective_autonomy": level,
                "execution": execution,
                "agent_overrides": {},
            }
        ],
    }


def role(**updates: object) -> dict:
    value = {
        "id": "reviewer",
        "allowed_skills": ["review-common"],
        "mcp": [],
        "custom_tools": [],
        "permission": {},
        "permission_reason": "",
    }
    value.update(updates)
    return value


class PermissionPolicyTests(unittest.TestCase):
    def build(
        self,
        item: dict,
        workflows: list[dict],
        *,
        manifest_mode: str = "unified",
    ) -> tuple[dict, dict]:
        return permission_policy.build_role_permission(
            item,
            workflows=workflows,
            manifest_mode=manifest_mode,
            mcp_names=["records"],
            custom_tool_paths=["validate.ts"],
            subagent_ids=["worker"],
            is_primary=True,
        )

    def test_five_level_matrix(self) -> None:
        expected = {
            "scripted": ("deny", "deny", "deny", "deny", "deny"),
            "fixed": ("ask", "ask", "ask", "ask", "ask"),
            "bounded": ("ask", "allow", "ask", "allow", "ask"),
            "guided": ("ask", "allow", "ask", "allow", "ask"),
            "adaptive": ("ask", "allow", "ask", "allow", "allow"),
        }
        for level, actions in expected.items():
            with self.subTest(level=level):
                permission, audit = self.build(role(), [workflow(level)])
                self.assertEqual(
                    (
                        permission["*"],
                        permission["edit"],
                        permission["bash"]["*"],
                        permission["webfetch"],
                        permission["doom_loop"],
                    ),
                    actions,
                )
                self.assertNotEqual(permission["bash"]["*"], "allow")
                self.assertEqual(permission["external_directory"]["*"], "deny" if level == "scripted" else "ask")
                self.assertEqual(audit["effective"], level)
                self.assertEqual(permission["todowrite"], "allow")
                self.assertGreater(
                    list(permission).index("todowrite"),
                    list(permission).index("*"),
                )

    def test_todo_is_system_managed_for_primary_members_and_legacy_roles(self) -> None:
        self.assertNotIn("todowrite", permission_policy.BUILTIN_PERMISSION_KEYS)
        self.assertEqual(
            permission_policy.SYSTEM_MANAGED_PERMISSION_KEYS,
            frozenset({"todowrite", "todoread"}),
        )
        for is_primary in (True, False):
            for workflows in ([workflow("bounded")], []):
                with self.subTest(is_primary=is_primary, legacy=not workflows):
                    permission, _audit = permission_policy.build_role_permission(
                        role(),
                        workflows=workflows,
                        manifest_mode=(
                            "legacy"
                            if not workflows
                            else "unified"
                        ),
                        mcp_names=[],
                        custom_tool_paths=[],
                        subagent_ids=["worker"],
                        is_primary=is_primary,
                    )
                    self.assertEqual(permission["todowrite"], "allow")
                    self.assertNotIn("todoread", permission)

    def test_todo_manifest_declarations_are_rejected_consistently(self) -> None:
        for key in ("todowrite", "todoread"):
            for action in ("allow", "ask", "deny"):
                with self.subTest(section="permission", key=key, action=action):
                    with self.assertRaisesRegex(
                        permission_policy.PermissionPolicyError,
                        "Todo 由系统托管，请删除该声明",
                    ):
                        self.build(
                            role(permission={key: action}),
                            [workflow("bounded")],
                        )
            for enabled in (True, False):
                with self.subTest(section="tools", key=key, enabled=enabled):
                    with self.assertRaisesRegex(
                        permission_policy.PermissionPolicyError,
                        "Todo 由系统托管，请删除该声明",
                    ):
                        permission_policy.tools_to_permission(
                            {key: enabled},
                            "agent.tools",
                        )

    def test_todo_custom_tool_names_are_reserved(self) -> None:
        for path in ("todowrite.ts", "nested/todoread.js"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    permission_policy.PermissionPolicyError,
                    "Todo 由系统托管，请删除该声明",
                ):
                    permission_policy.validate_custom_tool_ownership(
                        [path],
                        [],
                        "agent.custom_tools",
                    )

    def test_common_inspection_and_secret_rules(self) -> None:
        permission, _audit = self.build(role(), [workflow("scripted")])
        self.assertEqual(permission["read"]["*"], "allow")
        self.assertEqual(permission["read"][".env"], "deny")
        self.assertEqual(permission["read"][".env.example"], "allow")
        for key in ("glob", "grep", "list", "lsp"):
            self.assertEqual(permission[key], "allow")

    def test_mixed_sensitive_conflicts_become_ask(self) -> None:
        permission, audit = self.build(
            role(), [workflow("scripted"), workflow("adaptive")]
        )
        self.assertEqual(permission["*"], "ask")
        for key in ("edit", "webfetch", "doom_loop"):
            self.assertEqual(permission[key], "ask")
        self.assertEqual(permission["bash"]["*"], "ask")
        self.assertEqual(audit["levels"], ["scripted", "adaptive"])

    def test_unused_role_falls_back_to_bounded(self) -> None:
        permission, audit = self.build(role(), [workflow("adaptive", "other")])
        self.assertEqual(permission["*"], "ask")
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(audit["warning"], "unused-role-bounded-fallback")

    def test_unified_manifest_without_workflow_uses_bounded_default(self) -> None:
        permission, audit = self.build(role(), [])
        self.assertEqual(permission["*"], "ask")
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(permission["bash"]["*"], "ask")
        self.assertEqual(permission["todowrite"], "allow")
        self.assertEqual(audit["source"], "no-workflow-bounded-default")
        self.assertEqual(audit["effective"], "bounded")
        self.assertEqual(audit["levels"], ["bounded"])
        self.assertEqual(audit["warning"], "")

    def test_exact_executor_allowlists(self) -> None:
        execution = {
            "executors": [
                {"kind": "programming-tool", "ref": "python3 scripts/check.py *"},
                {"kind": "custom-tool", "ref": "validate.ts"},
                {"kind": "mcp-tool", "ref": "records/query"},
            ]
        }
        permission, _audit = self.build(
            role(mcp=["records"]), [workflow("scripted", execution=execution)]
        )
        self.assertEqual(permission["bash"]["*"], "deny")
        self.assertEqual(permission["bash"]["python3 scripts/check.py *"], "allow")
        self.assertEqual(permission["validate"], "allow")
        self.assertEqual(permission["records_*"], "deny")
        self.assertEqual(permission["records_query"], "allow")

    def test_owned_custom_tools_are_allowed_at_all_levels(self) -> None:
        for level in permission_policy.AUTONOMY_ORDER:
            with self.subTest(level=level):
                permission, _audit = self.build(
                    role(custom_tools=["validate.ts"]), [workflow(level)]
                )
                self.assertEqual(permission["validate"], "allow")
                self.assertEqual(
                    permission_policy._action_for_path(permission, "future_tool"),
                    "deny" if level == "scripted" else "ask",
                )

    def test_explicit_undeclared_mcp_and_tool_are_rejected(self) -> None:
        cases = (
            ({"records_query": "allow"}, "undeclared MCP"),
            ({"evil_query": "allow"}, "undeclared tool or MCP"),
            ({"future_tool": "allow"}, "undeclared tool or MCP"),
        )
        for explicit, message in cases:
            with self.subTest(explicit=explicit):
                with self.assertRaisesRegex(permission_policy.PermissionPolicyError, message):
                    self.build(
                        role(permission=explicit, permission_reason="业务需要"),
                        [workflow("adaptive")],
                    )

    def test_mcp_executor_requires_declared_role_ownership(self) -> None:
        execution = {
            "executors": [{"kind": "mcp-tool", "ref": "records/query"}]
        }
        with self.assertRaisesRegex(permission_policy.PermissionPolicyError, "not owned"):
            self.build(role(), [workflow("fixed", execution=execution)])
        permission, _audit = self.build(
            role(mcp=["records"]), [workflow("fixed", execution=execution)]
        )
        self.assertEqual(permission["records_query"], "allow")

    def test_declared_tool_name_collisions_are_rejected(self) -> None:
        with self.assertRaisesRegex(permission_policy.PermissionPolicyError, "tool name collision"):
            permission_policy.validate_custom_tool_ownership(
                ["one/validate.ts", "two/validate.ts"],
                ["one/validate.ts"],
                "agent.custom_tools",
            )

    def test_explicit_escalation_requires_reason(self) -> None:
        with self.assertRaisesRegex(permission_policy.PermissionPolicyError, "permission_reason"):
            self.build(role(permission={"edit": "allow"}), [workflow("scripted")])
        permission, audit = self.build(
            role(permission={"edit": "allow"}, permission_reason="需要写入审查结果"),
            [workflow("scripted")],
        )
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(audit["permission_reason"], "需要写入审查结果")

    def test_hard_ownership_boundaries(self) -> None:
        cases = [
            ({"bash": {"*": "allow"}}, "unconditional"),
            ({"external_directory": {"*": "allow"}}, "wildcard"),
            ({"task": {"*": "allow"}}, "task topology"),
            ({"skill": {"unknown": "allow"}}, "undeclared skills"),
        ]
        for explicit, message in cases:
            with self.subTest(explicit=explicit):
                with self.assertRaisesRegex(permission_policy.PermissionPolicyError, message):
                    self.build(
                        role(permission=explicit, permission_reason="显式业务需要"),
                        [workflow("adaptive")],
                    )

    def test_legacy_behavior_is_preserved(self) -> None:
        permission, audit = self.build(
            role(),
            [],
            manifest_mode="legacy",
        )
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(permission["bash"]["*"], "allow")
        self.assertEqual(permission["todowrite"], "allow")
        self.assertEqual(audit["warning"], "legacy-permission-baseline")


if __name__ == "__main__":
    unittest.main()
