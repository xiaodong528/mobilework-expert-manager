from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from spec_templates import (
    SPEC_TEMPLATES,
    SpecTemplateError,
    load_spec_template,
    load_spec_text,
)


EXPECTED = {
    "common-skill": (
        "skill-md-spec.md",
        "markdown",
        "2f154bb0347c5597cc67b3e7beb9a8858bd9dca2f8b7fcea20d0be0cbe2d176b",
    ),
    "expert-agent": (
        "agent-md-spec.md",
        "markdown",
        "33aeecb2245b541a84bd8d0101be54c3fa39a8bf348961c98d1298f4b2099d00",
    ),
    "expert-json": (
        "expert-json-spec.md",
        "json",
        "bd3eef1ec322de6febff188d4081120abf49ca9113aeebd53e48cb34116ca621",
    ),
    "legacy-expert-json": (
        "expert-json-spec.md",
        "json",
        "e05cfb867b8d4df18de7dbb92579373babdde67b43ec2956bd92fea4af84dcc9",
    ),
    "primary-agent": (
        "agent-md-spec.md",
        "markdown",
        "fdc6fe683a018e9c7467beb0ff38673242afdc9d9e0f2f5ea3cf56a0e59c2bc3",
    ),
    "readme": (
        "package-docs-spec.md",
        "markdown",
        "d80ed2cf0d004ef220835eaa55f5a99bcdbee0a28d23604d6365ac49a38320d9",
    ),
    "role-skill": (
        "skill-md-spec.md",
        "markdown",
        "c595e297874446002261479b278c5566394519c05a2696fd7f3d6aa452dc4586",
    ),
    "subagent": (
        "agent-md-spec.md",
        "markdown",
        "d416cb8ed1a99eef10d0f11e133bd114aa7ff3c0f0d8722717ce418ba4972575",
    ),
}


class SpecTemplateTests(unittest.TestCase):
    def test_registry_is_complete_and_uses_expected_specs(self) -> None:
        actual = {
            template_id: (entry.file_name, entry.language)
            for template_id, entry in SPEC_TEMPLATES.items()
        }
        expected = {
            template_id: (file_name, language)
            for template_id, (file_name, language, _) in EXPECTED.items()
        }
        self.assertEqual(actual, expected)

    def test_extracted_templates_preserve_original_bytes(self) -> None:
        for template_id, (_, _, expected_sha256) in EXPECTED.items():
            with self.subTest(template_id=template_id):
                content = load_spec_text(template_id).encode("utf-8")
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected_sha256)

    def test_expert_json_template_is_valid_and_templates_directory_is_removed(self) -> None:
        data = json.loads(load_spec_text("expert-json"))
        self.assertEqual(data["slug"], "contract-review-expert")
        self.assertFalse((SKILL_ROOT / "templates").exists())

    def test_runtime_extension_json_examples_are_valid(self) -> None:
        source = (SKILL_ROOT / "references" / "runtime-extensions-spec.md").read_text(encoding="utf-8")
        blocks = re.findall(r"^```json\n(.*?)^```$", source, flags=re.MULTILINE | re.DOTALL)
        self.assertGreaterEqual(len(blocks), 5)
        for index, block in enumerate(blocks):
            with self.subTest(index=index):
                json.loads(block)

    def test_skill_routes_every_reference_and_drops_obsolete_names(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_names = {path.name for path in (SKILL_ROOT / "references").glob("*.md")}
        for name in sorted(reference_names):
            with self.subTest(name=name):
                self.assertIn(f"references/{name}", skill)
        self.assertNotIn("manifest-schema.md", skill)
        self.assertNotIn("opencode-runtime-extensions.md", skill)

    def test_skill_does_not_ship_examples_directory(self) -> None:
        self.assertFalse((SKILL_ROOT / "examples").exists())

    def test_load_spec_template_returns_string_template(self) -> None:
        rendered = load_spec_template("readme").safe_substitute(expert_name="示例专家")
        self.assertIn("# 示例专家", rendered)

    def test_loader_rejects_unknown_missing_duplicate_and_unfenced_sections(self) -> None:
        with self.assertRaisesRegex(SpecTemplateError, "unknown spec template"):
            load_spec_text("missing")

        with tempfile.TemporaryDirectory() as temp:
            references = Path(temp)
            path = references / "agent-md-spec.md"

            with self.assertRaisesRegex(SpecTemplateError, "cannot read spec template source"):
                load_spec_text("expert-agent", references_dir=references)

            start = "<!-- mobilework-template:expert-agent:start -->"
            end = "<!-- mobilework-template:expert-agent:end -->"
            path.write_text(f"{start}\nnot fenced\n{end}\n", encoding="utf-8")
            with self.assertRaisesRegex(SpecTemplateError, "four-backtick markdown fence"):
                load_spec_text("expert-agent", references_dir=references)

            valid = f"{start}\n````markdown\ntext\n````\n{end}\n"
            path.write_text(valid + valid, encoding="utf-8")
            with self.assertRaisesRegex(SpecTemplateError, "exactly one"):
                load_spec_text("expert-agent", references_dir=references)


if __name__ == "__main__":
    unittest.main()
