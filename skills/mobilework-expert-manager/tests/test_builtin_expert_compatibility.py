from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
EXPERTS = SKILL_ROOT.parents[1] / "experts"
VALIDATE = SCRIPTS / "validate_expert.py"
INSTALL = SCRIPTS / "install_expert.py"


class BuiltinExpertCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = {
            EXPERTS / "anysign-signing-expert",
            EXPERTS / "windows-sop-record-replay-expert",
            EXPERTS / "mermaid-diagram-expert",
        }
        missing = sorted(path.name for path in required if not path.is_dir())
        if missing:
            raise unittest.SkipTest(
                "repository built-in expert fixtures are unavailable: "
                + ", ".join(missing)
            )

    def run_json(self, command: list[str]) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
        return completed, payload

    def test_repository_builtin_validation_matrix(self) -> None:
        expected = {
            "anysign-signing-expert": True,
            "windows-sop-record-replay-expert": True,
            "mermaid-diagram-expert": False,
        }
        for slug, should_be_valid in expected.items():
            with self.subTest(slug=slug):
                completed, payload = self.run_json(
                    [
                        sys.executable,
                        str(VALIDATE),
                        str(EXPERTS / slug),
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(payload["ok"], should_be_valid, payload)
                self.assertEqual(completed.returncode == 0, should_be_valid, completed.stderr)

    def test_valid_legacy_builtins_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for slug in (
                "anysign-signing-expert",
                "windows-sop-record-replay-expert",
            ):
                with self.subTest(slug=slug):
                    workspace = root / slug
                    workspace.mkdir()
                    completed, payload = self.run_json(
                        [
                            sys.executable,
                            str(INSTALL),
                            "--package-dir",
                            str(EXPERTS / slug),
                            "--workspace-dir",
                            str(workspace),
                        ]
                    )
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    self.assertEqual(payload["status"], "installable")
                    self.assertEqual(
                        payload["data"]["runtime_status"],
                        "runtime-not-tested",
                    )
                    runtime = workspace / ".opencode"
                    self.assertTrue((runtime / "opencode.jsonc").is_file())
                    self.assertTrue((runtime / f".expert-installs/{slug}.json").is_file())

    def test_autonomy_enabled_package_keeps_strict_readme_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "strict-autonomy-expert"
            shutil.copytree(EXPERTS / "anysign-signing-expert", package)
            manifest_path = package / "expert.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["workflows"] = [
                {
                    "name": "strict documentation projection",
                    "autonomy": "adaptive",
                    "phases": [
                        {
                            "name": "execute",
                            "mode": "primary",
                            "agents": [],
                            "input": "confirmed input",
                            "expected_output": "verified output",
                            "acceptance": ["output is verified"],
                        }
                    ],
                }
            ]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            completed, payload = self.run_json(
                [
                    sys.executable,
                    str(VALIDATE),
                    str(package),
                    "--format",
                    "json",
                ]
            )
            self.assertNotEqual(completed.returncode, 0)
            permission_readme_findings = [
                item
                for item in payload["findings"]
                if "permission baseline" in item["message"]
            ]
            self.assertTrue(permission_readme_findings, payload)
            self.assertTrue(
                all(item["severity"] == "error" for item in permission_readme_findings),
                permission_readme_findings,
            )


if __name__ == "__main__":
    unittest.main()
