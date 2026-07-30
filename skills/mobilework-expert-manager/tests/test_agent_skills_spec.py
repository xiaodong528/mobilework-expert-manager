from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import diagnose_skill
import skill_contract
from validation_result import ValidationResult


def valid_frontmatter(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "contract-review",
        "description": (
            "Reviews contract clauses and produces evidence-linked findings. "
            "Use when a user requests contract review."
        ),
    }
    value.update(updates)
    return value


def issue_codes(
    frontmatter: object,
    *,
    directory_name: str = "contract-review",
    expected_compatibility: str | None = None,
) -> set[str]:
    return {
        issue.code
        for issue in skill_contract.validate_skill_frontmatter(
            frontmatter,
            directory_name=directory_name,
            expected_compatibility=expected_compatibility,
        )
    }


class AgentSkillsFrontmatterContractTests(unittest.TestCase):
    def test_complete_official_frontmatter_is_valid(self) -> None:
        frontmatter = valid_frontmatter(
            license="Apache-2.0",
            compatibility="Requires Python 3.11+ and network access",
            metadata={"author": "mobilework", "version": "1.0"},
            **{"allowed-tools": "Bash(git:*) Read"},
        )
        self.assertEqual(issue_codes(frontmatter), set())

    def test_name_contract_and_directory_match(self) -> None:
        valid_64 = "a" * 64
        self.assertEqual(
            issue_codes(
                valid_frontmatter(name=valid_64),
                directory_name=valid_64,
            ),
            set(),
        )
        cases = [
            ("a" * 65, "a" * 65, {"SKILL_NAME_INVALID"}),
            ("Contract-Review", "Contract-Review", {"SKILL_NAME_INVALID"}),
            ("contract--review", "contract--review", {"SKILL_NAME_INVALID"}),
            ("-contract-review", "-contract-review", {"SKILL_NAME_INVALID"}),
            ("contract-review-", "contract-review-", {"SKILL_NAME_INVALID"}),
            (
                "contract-review",
                "different-directory",
                {"SKILL_NAME_MISMATCH"},
            ),
        ]
        for name, directory_name, expected in cases:
            with self.subTest(name=name, directory_name=directory_name):
                self.assertEqual(
                    issue_codes(
                        valid_frontmatter(name=name),
                        directory_name=directory_name,
                    ),
                    expected,
                )

    def test_required_description_boundaries(self) -> None:
        self.assertEqual(
            issue_codes(valid_frontmatter(description="x" * 1024)),
            set(),
        )
        for value in (None, "", "   ", "x" * 1025):
            with self.subTest(value_type=type(value).__name__):
                self.assertIn(
                    "SKILL_DESCRIPTION_INVALID",
                    issue_codes(valid_frontmatter(description=value)),
                )
        self.assertIn(
            "SKILL_NAME_INVALID",
            issue_codes({"description": "Use for contract review."}),
        )

    def test_optional_field_types_and_boundaries(self) -> None:
        self.assertEqual(
            issue_codes(valid_frontmatter(compatibility="x" * 500)),
            set(),
        )
        cases = [
            (
                valid_frontmatter(compatibility="x" * 501),
                "SKILL_COMPATIBILITY_INVALID",
            ),
            (
                valid_frontmatter(compatibility=""),
                "SKILL_COMPATIBILITY_INVALID",
            ),
            (
                valid_frontmatter(compatibility=["opencode"]),
                "SKILL_COMPATIBILITY_INVALID",
            ),
            (
                valid_frontmatter(license=123),
                "SKILL_LICENSE_INVALID",
            ),
            (
                valid_frontmatter(license=""),
                "SKILL_LICENSE_INVALID",
            ),
            (
                valid_frontmatter(metadata={"version": 1}),
                "SKILL_METADATA_INVALID",
            ),
            (
                valid_frontmatter(metadata={1: "version"}),
                "SKILL_METADATA_INVALID",
            ),
            (
                valid_frontmatter(metadata={"nested": {"version": "1"}}),
                "SKILL_METADATA_INVALID",
            ),
            (
                valid_frontmatter(metadata=["version", "1"]),
                "SKILL_METADATA_INVALID",
            ),
            (
                valid_frontmatter(**{"allowed-tools": ["Read"]}),
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
            (
                valid_frontmatter(**{"allowed-tools": {"Read": True}}),
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
            (
                valid_frontmatter(**{"allowed-tools": 1}),
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
            (
                valid_frontmatter(**{"allowed-tools": "   "}),
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
        ]
        for frontmatter, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, issue_codes(frontmatter))

    def test_unknown_fields_and_mobilework_legacy_constraint(self) -> None:
        self.assertEqual(
            issue_codes(
                valid_frontmatter(compatibility="opencode"),
                expected_compatibility="opencode",
            ),
            set(),
        )
        self.assertIn(
            "SKILL_FRONTMATTER_FIELD_UNSUPPORTED",
            issue_codes(valid_frontmatter(mobilework_extra=True)),
        )
        self.assertIn(
            "SKILL_COMPATIBILITY_INVALID",
            issue_codes(
                valid_frontmatter(compatibility="another-host"),
                expected_compatibility="opencode",
            ),
        )

    def test_recommendations_are_non_blocking_structured_warnings(self) -> None:
        issues = skill_contract.skill_markdown_recommendations(501)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        result = ValidationResult()
        skill_contract.add_skill_markdown_issues(
            result,
            issues,
            path="SKILL.md",
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            result.findings[0].code,
            "SKILL_MARKDOWN_LENGTH_RECOMMENDED",
        )


class AgentSkillsStaticDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_skill(
        self,
        frontmatter: dict[str, object],
        *,
        directory_name: str = "contract-review",
    ) -> Path:
        skill_root = self.root / directory_name
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\n"
            + yaml.safe_dump(
                frontmatter,
                sort_keys=False,
                allow_unicode=True,
            )
            + "---\n\n# Contract review\n",
            encoding="utf-8",
        )
        return skill_root

    def diagnose_zip(self, skill_root: Path) -> set[str]:
        archive = self.root / f"{skill_root.name}.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(
                skill_root / "SKILL.md",
                f"{skill_root.name}/SKILL.md",
            )
        return {finding.code for finding in diagnose_skill.diagnose(archive).findings}

    def test_directory_and_zip_apply_the_same_normative_rules(self) -> None:
        cases = [
            (
                valid_frontmatter(compatibility="x" * 501),
                "SKILL_COMPATIBILITY_INVALID",
            ),
            (
                valid_frontmatter(metadata={"version": 1}),
                "SKILL_METADATA_INVALID",
            ),
            (
                valid_frontmatter(**{"allowed-tools": ["Read"]}),
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
            (
                valid_frontmatter(license=1),
                "SKILL_LICENSE_INVALID",
            ),
            (
                valid_frontmatter(mobilework_extra=True),
                "SKILL_FRONTMATTER_FIELD_UNSUPPORTED",
            ),
        ]
        for index, (frontmatter, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                skill_root = self.write_skill(
                    frontmatter,
                    directory_name=f"contract-review-{index}",
                )
                frontmatter["name"] = skill_root.name
                (skill_root / "SKILL.md").write_text(
                    "---\n"
                    + yaml.safe_dump(
                        frontmatter,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    + "---\n\n# Contract review\n",
                    encoding="utf-8",
                )
                directory_codes = {
                    finding.code
                    for finding in diagnose_skill.diagnose(skill_root).findings
                }
                zip_codes = self.diagnose_zip(skill_root)
                self.assertIn(expected, directory_codes)
                self.assertEqual(directory_codes, zip_codes)

    def test_json_flow_frontmatter_fails_closed(self) -> None:
        skill_root = self.root / "json-flow"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            '---\n{"name":"json-flow","description":"Use for JSON flow tests."}\n'
            "---\n\n# Fixture\n",
            encoding="utf-8",
        )
        result = diagnose_skill.diagnose(skill_root)
        self.assertFalse(result.ok)
        self.assertTrue(
            any("block-style YAML" in message for message in result.errors),
            result.errors,
        )

    def test_duplicate_yaml_keys_fail_closed(self) -> None:
        skill_root = self.root / "duplicate-key"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\n"
            "name: duplicate-key\n"
            "description: Use for duplicate-key tests.\n"
            "description: Duplicate values must not be accepted.\n"
            "---\n\n# Fixture\n",
            encoding="utf-8",
        )
        result = diagnose_skill.diagnose(skill_root)
        self.assertFalse(result.ok)
        self.assertTrue(
            any("duplicate key" in message for message in result.errors),
            result.errors,
        )


if __name__ == "__main__":
    unittest.main()
