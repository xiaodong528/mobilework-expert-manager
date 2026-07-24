from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gitignore_contract
import validate_expert


class GitignoreContractTests(unittest.TestCase):
    def test_managed_block_and_user_rules_are_preserved(self) -> None:
        content = gitignore_contract.merge_content("custom-cache/\n")
        self.assertIn(gitignore_contract.BLOCK_START, content)
        self.assertIn("custom-cache/", content)
        refreshed = gitignore_contract.merge_content(content.replace("*.zip", "old-rule"))
        self.assertIn("*.zip", refreshed)
        self.assertIn("custom-cache/", refreshed)

    def test_owned_file_cannot_be_ignored(self) -> None:
        content = gitignore_contract.required_content() + "expert.json\n"
        issues = gitignore_contract.validate_content(content, {"expert.json", ".gitignore"})
        self.assertIn("GITIGNORE_OWNED_FILE_IGNORED", {code for code, _ in issues})

    def test_only_root_git_metadata_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git/objects").mkdir(parents=True)
            (root / ".git/config").write_text("[core]", encoding="utf-8")
            self.assertFalse(any(".git" in path.relative_to(root).parts for path in validate_expert.iter_package_paths(root)))
            (root / ".opencode/.git/objects").mkdir(parents=True)
            nested = [path.relative_to(root).as_posix() for path in validate_expert.iter_package_paths(root)]
            self.assertIn(".opencode/.git", nested)
            self.assertFalse(any(path.startswith(".opencode/.git/") for path in nested))


if __name__ == "__main__":
    unittest.main()
