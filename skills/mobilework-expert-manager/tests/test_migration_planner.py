from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import migration_planner
import provenance


class MigrationPlannerTests(unittest.TestCase):
    def test_plan_is_read_only_and_covers_legacy_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "references").mkdir()
            (root / "references/guide.md").write_text("guide", encoding="utf-8")
            (root / "references/璧勪骇.md").write_text("mojibake", encoding="utf-8")
            (root / "AGENTS.md").write_text("legacy", encoding="utf-8")
            (root / "expert.json").write_text(json.dumps({
                "slug": "legacy-expert", "type": "expert",
                "common_skills": ["legacy-common"],
                "agent": {
                    "id": "legacy", "skills": ["legacy-role"], "maxTurns": 80,
                    "permission": {"bash": {"*": "allow"}},
                },
                "references": [{"path": "references/guide.md"}],
                "runtime_extensions": {
                    "references": {
                        "policy": {
                            "repository": "example-org/policy",
                            "description": "Legacy policy source"
                        }
                    },
                    "instructions": [
                        ".opencode/instructions/legacy-expert/*.md"
                    ]
                },
            }), encoding="utf-8")
            before = provenance.tree_sha256(root)
            result = migration_planner.plan(root)
            after = provenance.tree_sha256(root)
            self.assertEqual(before, after)
            self.assertEqual(result["mode"], "read-only")
            self.assertFalse(result["execution"]["attempted"])
            paths = {item["path"] for item in result["jsonPatchCandidates"]}
            self.assertIn("/common_skills", paths)
            self.assertIn("/agent/steps", paths)
            self.assertTrue(result["resourceMoves"])
            self.assertTrue(result["permissionChanges"])
            self.assertIn("MIGRATION_FILENAME_MOJIBAKE", {item["code"] for item in result["sourceWarnings"]})
            self.assertGreaterEqual(result["unconfirmedCount"], 3)
            decision_codes = {item["code"] for item in result["businessDecisions"]}
            self.assertIn("CONFIRM_REFERENCE_CONSUMERS", decision_codes)
            self.assertIn("CONFIRM_INSTRUCTION_SCOPE", decision_codes)
            markdown = migration_planner.render_markdown(result)
            for heading in (
                "## Automatic actions",
                "## Candidate JSON Patch",
                "## Resource moves",
                "## Permission changes",
                "## Source warnings",
                "## Decisions",
                "## Regenerate after migration",
            ):
                self.assertIn(heading, markdown)


if __name__ == "__main__":
    unittest.main()
