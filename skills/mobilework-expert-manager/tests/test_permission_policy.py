from __future__ import annotations

import copy
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
        "autonomy": "bounded",
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

    def test_five_level_matrix_and_skill_actions(self) -> None:
        expected = {
            "scripted": ("deny", "deny", "deny", "deny", "deny", "deny"),
            "fixed": ("ask", "ask", "ask", "ask", "ask", "deny"),
            "bounded": ("ask", "allow", "ask", "allow", "ask", "deny"),
            "guided": ("ask", "allow", "ask", "allow", "ask", "ask"),
            "adaptive": ("ask", "allow", "ask", "allow", "allow", "allow"),
        }
        for level, actions in expected.items():
            with self.subTest(level=level):
                permission, audit = self.build(
                    role(autonomy=level),
                    [workflow("adaptive")],
                )
                self.assertEqual(
                    (
                        permission["*"],
                        permission["edit"],
                        permission["bash"]["*"],
                        permission["webfetch"],
                        permission["doom_loop"],
                        permission["skill"]["*"],
                    ),
                    actions,
                )
                self.assertEqual(
                    permission["external_directory"]["*"],
                    "deny" if level == "scripted" else "ask",
                )
                self.assertEqual(permission["skill"]["review-common"], "allow")
                self.assertEqual(audit["effective"], level)
                self.assertEqual(audit["label"], permission_policy.ROLE_AUTONOMY_LABELS[level])
                self.assertEqual(permission["todowrite"], "allow")

    def test_workflow_and_execution_cannot_change_static_permission(self) -> None:
        variants = [
            [],
            [workflow("scripted")],
            [workflow("adaptive")],
            [workflow("fixed", "other")],
            [
                workflow(
                    "guided",
                    execution={
                        "executors": [
                            {"kind": "programming-tool", "ref": "python check.py"},
                            {"kind": "mcp-tool", "ref": "records/query"},
                        ]
                    },
                )
            ],
        ]
        outputs = [self.build(role(autonomy="guided", mcp=["records"]), item)[0] for item in variants]
        for output in outputs[1:]:
            self.assertEqual(output, outputs[0])
        self.assertNotIn("python check.py", outputs[0]["bash"])
        self.assertNotIn("records_query", outputs[0])

    def test_changing_role_autonomy_changes_only_the_matrix(self) -> None:
        bounded, _audit = self.build(role(autonomy="bounded"), [workflow("fixed")])
        adaptive, _audit = self.build(role(autonomy="adaptive"), [workflow("fixed")])
        self.assertEqual(bounded["task"], adaptive["task"])
        self.assertEqual(bounded["read"], adaptive["read"])
        self.assertEqual(bounded["skill"]["review-common"], "allow")
        self.assertEqual(adaptive["skill"]["review-common"], "allow")
        self.assertEqual(bounded["skill"]["*"], "deny")
        self.assertEqual(adaptive["skill"]["*"], "allow")
        self.assertEqual(bounded["doom_loop"], "ask")
        self.assertEqual(adaptive["doom_loop"], "allow")

    def test_todo_is_system_managed_for_primary_members_and_legacy_roles(self) -> None:
        self.assertNotIn("todowrite", permission_policy.BUILTIN_PERMISSION_KEYS)
        self.assertEqual(
            permission_policy.SYSTEM_MANAGED_PERMISSION_KEYS,
            frozenset({"todowrite", "todoread"}),
        )
        for is_primary in (True, False):
            for manifest_mode in ("unified", "legacy"):
                with self.subTest(is_primary=is_primary, manifest_mode=manifest_mode):
                    permission, _audit = permission_policy.build_role_permission(
                        role(),
                        workflows=[workflow("adaptive")],
                        manifest_mode=manifest_mode,
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
                        self.build(role(permission={key: action}), [])
            for enabled in (True, False):
                with self.subTest(section="tools", key=key, enabled=enabled):
                    with self.assertRaisesRegex(
                        permission_policy.PermissionPolicyError,
                        "Todo 由系统托管，请删除该声明",
                    ):
                        permission_policy.tools_to_permission({key: enabled}, "agent.tools")

    def test_todo_custom_tool_names_are_reserved(self) -> None:
        for path in ("todowrite.ts", "nested/todoread.js"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    permission_policy.PermissionPolicyError,
                    "Todo 由系统托管，请删除该声明",
                ):
                    permission_policy.validate_custom_tool_ownership(
                        [path], [], "agent.custom_tools"
                    )

    def test_common_inspection_and_secret_rules(self) -> None:
        permission, _audit = self.build(role(autonomy="scripted"), [])
        self.assertEqual(permission["read"]["*"], "allow")
        self.assertEqual(permission["read"][".env"], "deny")
        self.assertEqual(permission["read"][".env.example"], "allow")
        for key in ("glob", "grep", "list", "lsp"):
            self.assertEqual(permission[key], "allow")

    def test_owned_resources_obey_role_matrix(self) -> None:
        for level in permission_policy.AUTONOMY_ORDER:
            with self.subTest(level=level):
                permission, _audit = self.build(
                    role(
                        autonomy=level,
                        custom_tools=["validate.ts"],
                        mcp=["records"],
                    ),
                    [],
                )
                self.assertEqual(permission["validate"], "allow")
                self.assertEqual(
                    permission["records_*"],
                    "deny" if level in {"scripted", "fixed"} else "allow",
                )
                self.assertEqual(
                    permission_policy._action_for_path(permission, "future_tool"),
                    "deny" if level == "scripted" else "ask",
                )

    def test_unowned_mcp_and_custom_tools_stay_unavailable(self) -> None:
        permission, _audit = self.build(role(autonomy="adaptive"), [])
        self.assertEqual(permission["records_*"], "deny")
        self.assertNotIn("validate", permission)
        cases = (
            ({"records_query": "allow"}, "undeclared MCP"),
            ({"evil_query": "allow"}, "undeclared tool or MCP"),
            ({"future_tool": "allow"}, "undeclared tool or MCP"),
        )
        for explicit, message in cases:
            with self.subTest(explicit=explicit):
                with self.assertRaisesRegex(permission_policy.PermissionPolicyError, message):
                    self.build(
                        role(
                            autonomy="adaptive",
                            permission=explicit,
                            permission_reason="理由不能提权",
                        ),
                        [],
                    )

    def test_explicit_permission_can_tighten_but_never_raise(self) -> None:
        permission, audit = self.build(
            role(
                autonomy="adaptive",
                mcp=["records"],
                custom_tools=["validate.ts"],
                permission={
                    "edit": "deny",
                    "skill": {"*": "deny"},
                    "records_*": "ask",
                    "validate": "deny",
                },
                permission_reason="保留说明但不构成提权授权",
            ),
            [],
        )
        self.assertEqual(permission["edit"], "deny")
        self.assertEqual(permission["skill"]["*"], "deny")
        self.assertEqual(permission["records_*"], "ask")
        self.assertEqual(permission["validate"], "deny")
        self.assertEqual(audit["permission_reason"], "保留说明但不构成提权授权")
        for reason in ("", "业务理由"):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(
                    permission_policy.PermissionPolicyError,
                    "cannot raise role autonomy baseline",
                ):
                    self.build(
                        role(
                            autonomy="scripted",
                            permission={"edit": "allow"},
                            permission_reason=reason,
                        ),
                        [],
                    )

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
                        role(
                            autonomy="adaptive",
                            permission=explicit,
                            permission_reason="不能绕过硬边界",
                        ),
                        [],
                    )

    def test_missing_legacy_role_autonomy_defaults_to_bounded_projection(self) -> None:
        legacy = role()
        legacy.pop("autonomy")
        legacy["permission"] = {"bash": {"*": "allow"}, "edit": "deny"}
        permission, audit = self.build(legacy, [workflow("adaptive")], manifest_mode="legacy")
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(permission["bash"]["*"], "ask")
        self.assertEqual(permission["skill"]["*"], "deny")
        self.assertEqual(audit["source"], "legacy-role-autonomy-defaulted")
        self.assertEqual(audit["effective"], "bounded")
        self.assertEqual(audit["warning"], "legacy-role-autonomy-defaulted")

    def test_invalid_role_autonomy_is_rejected(self) -> None:
        with self.assertRaisesRegex(permission_policy.PermissionPolicyError, "autonomy must"):
            self.build(role(autonomy="extreme"), [])

    def test_declared_tool_name_collisions_are_rejected(self) -> None:
        with self.assertRaisesRegex(permission_policy.PermissionPolicyError, "tool name collision"):
            permission_policy.validate_custom_tool_ownership(
                ["one/validate.ts", "two/validate.ts"],
                ["one/validate.ts"],
                "agent.custom_tools",
            )


if __name__ == "__main__":
    unittest.main()
