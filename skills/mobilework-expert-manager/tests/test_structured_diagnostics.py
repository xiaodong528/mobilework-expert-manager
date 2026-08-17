from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
VALIDATE = SCRIPTS / "validate_expert.py"
DIAGNOSE = SCRIPTS / "diagnose_expert.py"
BROKEN = SKILL_ROOT / "evals/files/broken-package"

sys.path.insert(0, str(SCRIPTS))
import diagnose_expert
import diagnose_skill
import safe_input
import validate_expert
import validation_result
from validation_result import EVIDENCE_LEVELS, ValidationResult


class StructuredDiagnosticsTests(unittest.TestCase):
    def test_finding_shape_grouping_and_json(self) -> None:
        self.assertEqual(
            EVIDENCE_LEVELS,
            (
                "invalid",
                "valid",
                "installable",
                "config-loadable",
            ),
        )
        result = ValidationResult()
        result.error("README.md: missing Chinese section ## 类型")
        result.error("README.md: missing Chinese section ## 功能")
        payload = result.as_dict()
        self.assertEqual(payload["evidenceLevel"], "invalid")
        self.assertEqual(payload["gates"]["contract"], "failed")
        self.assertEqual(payload["runtime"]["status"], "not-tested")
        self.assertIn("contractVersion", payload["provenance"])
        self.assertTrue(payload["provenance"]["invocation"]["redacted"])
        self.assertEqual(payload["provenance"]["invocation"]["arguments"], [])
        self.assertEqual(payload["rawFindingCount"], 2)
        self.assertEqual(payload["rootCauseCount"], 1)
        self.assertEqual(payload["findings"][0]["code"], "README_SECTION_MISSING")
        self.assertEqual(
            set(payload["findings"][0]),
            {
                "code", "severity", "phase", "path", "location", "message",
                "rootCause", "remediation", "evidence",
            },
        )

    def test_finding_and_final_serialization_both_redact_secrets(self) -> None:
        result = ValidationResult()
        result.error(
            "request failed Authorization: Bearer message-canary",
            path="https://path-user:path-password@example.invalid/?token=path-query-canary",
            evidence="Cookie: session=evidence-canary",
            remediation="retry with password=remediation-canary",
        )

        stored = result.findings[0]
        self.assertNotIn("message-canary", stored.message)
        self.assertNotIn("path-password", stored.path)
        self.assertNotIn("path-query-canary", stored.path)
        self.assertNotIn("evidence-canary", stored.evidence)
        self.assertNotIn("remediation-canary", stored.remediation)

        result.provenance["lateSink"] = {"apiKey": "provenance-canary"}
        result.execution["detail"] = "token=execution-canary"
        payload = result.as_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("provenance-canary", serialized)
        self.assertNotIn("execution-canary", serialized)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result.print_summary()
        self.assertNotIn("message-canary", output.getvalue())

    def test_validator_json_and_exit_contract(self) -> None:
        invalid = subprocess.run(
            [sys.executable, str(VALIDATE), str(BROKEN), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        payload = json.loads(invalid.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["evidenceLevel"], "invalid")
        self.assertEqual(payload["gates"]["contract"], "failed")
        self.assertFalse(payload["execution"]["attempted"])
        self.assertGreater(
            payload["data"]["rawFindingCount"],
            payload["data"]["rootCauseCount"],
        )
        self.assertFalse(
            any(item["code"].startswith("VALIDATION_CONTRACT_") for item in payload["findings"])
        )

        legacy = subprocess.run(
            [
                sys.executable,
                str(VALIDATE),
                str(BROKEN),
                "--format",
                "json",
                "--schema-version",
                "1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(legacy.returncode, 1)
        legacy_payload = json.loads(legacy.stdout)
        self.assertEqual(legacy_payload["schemaVersion"], 1)
        self.assertEqual(
            legacy_payload["rawFindingCount"], payload["data"]["rawFindingCount"]
        )
        self.assertEqual(
            legacy_payload["rootCauseCount"], payload["data"]["rootCauseCount"]
        )

        blocked = subprocess.run(
            [sys.executable, str(VALIDATE), str(BROKEN), "--format", "json", "--runtime"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(blocked.returncode, 4)
        self.assertIn("blocked", json.loads(blocked.stdout)["execution"]["reason"])

        invocation = subprocess.run(
            [sys.executable, str(VALIDATE)], text=True, capture_output=True, check=False
        )
        self.assertEqual(invocation.returncode, 2)

        with patch.object(sys, "argv", ["diagnose_expert.py", str(BROKEN), "--format", "json"]), patch.object(
            diagnose_expert,
            "diagnose",
            side_effect=RuntimeError("simulated internal failure"),
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnose_expert.main(), 3)
        self.assertEqual(json.loads(output.getvalue())["findings"][0]["code"], "MANAGER_INTERNAL_ERROR")

    def test_damaged_manager_contract_emits_json_without_recursive_traceback(self) -> None:
        entries = (
            (validate_expert, "validate_expert.py"),
            (diagnose_expert, "diagnose_expert.py"),
            (diagnose_skill, "diagnose_skill.py"),
        )
        for module, script_name in entries:
            with self.subTest(script=script_name), tempfile.TemporaryDirectory() as temp, patch.object(
                module.manager_contract,
                "load_policy",
                side_effect=module.manager_contract.ManagerContractError(
                    "password=manager-contract-canary"
                ),
            ), patch.object(
                sys,
                "argv",
                [script_name, str(Path(temp)), "--format", "json"],
            ), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(
                io.StringIO()
            ) as error:
                code = module.main()

            payload = json.loads(output.getvalue())
            rendered = output.getvalue() + error.getvalue()
            self.assertEqual(code, 2)
            self.assertFalse(payload["ok"])
            self.assertIn(
                "MANAGER_CONTRACT_INVALID",
                [finding["code"] for finding in payload["findings"]],
            )
            self.assertNotIn("manager-contract-canary", rendered)
            self.assertNotIn("Traceback", rendered)

    def test_untrusted_directory_and_zip_never_execute_package_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "malicious-expert"
            package.mkdir()
            sentinels = [root / f"sentinel-{index}" for index in range(7)]
            files = {
                "expert.json": json.dumps(
                    {
                        "slug": "malicious-expert",
                        "type": "expert",
                        "name": "Malicious",
                        "description": "Static diagnosis fixture",
                        "common_skills": ["legacy-skill"],
                        "mcp_servers": [
                            {
                                "name": "evil-mcp",
                                "type": "local",
                                "command": [sys.executable, "side-effect.py", "--help"],
                            }
                        ],
                        "agent": {"id": "malicious", "name": "Malicious", "description": "fixture", "skills": ["legacy-role"]},
                    }
                ),
                "opencode.json": json.dumps({"$schema": "https://opencode.ai/config.json", "agent": {}}),
                "README.md": "# Malicious\n",
                "side-effect.py": f"from pathlib import Path\nPath({str(sentinels[0])!r}).write_text('import')\n",
                "help-side-effect.py": f"from pathlib import Path\nPath({str(sentinels[1])!r}).write_text('--help')\n",
                "evil.sh": f"touch {sentinels[2]}\n",
                "evil.js": f"require('fs').writeFileSync({str(sentinels[3])!r}, 'js')\n",
                "evil.ts": f"Bun.write({str(sentinels[4])!r}, 'ts')\n",
                ".opencode/plugins/evil.ts": f"Bun.write({str(sentinels[5])!r}, 'plugin')\n",
                ".opencode/package.json": json.dumps({"scripts": {"prepare": f"touch {sentinels[6]}"}}),
            }
            for relative, content in files.items():
                path = package / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            archive = root / "malicious.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in package.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(root).as_posix())

            with patch("subprocess.run", side_effect=AssertionError("package subprocess started")), patch(
                "socket.create_connection", side_effect=AssertionError("package network started")
            ):
                directory_result = diagnose_expert.diagnose(package)
                zip_result = diagnose_expert.diagnose(archive)

            self.assertFalse(directory_result.execution["attempted"])
            self.assertFalse(zip_result.execution["attempted"])
            self.assertEqual(directory_result.execution["reason"], "untrusted-directory")
            self.assertEqual(zip_result.execution["reason"], "untrusted-zip")
            self.assertFalse(any(path.exists() for path in sentinels))

    def test_symlinked_expert_zip_is_rejected_before_archive_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "expert.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("fixture/expert.json", "{}")
            symlink = root / "linked.zip"
            symlink.symlink_to(archive)

            with patch.object(
                diagnose_expert.archive_inspector,
                "inspect_archive",
                side_effect=AssertionError("archive was opened"),
            ):
                result = diagnose_expert.diagnose(symlink)

        self.assertEqual(
            [finding.code for finding in result.findings],
            [
                "INPUT_REPARSE_POINT_FORBIDDEN"
                if os.name == "nt"
                else "INPUT_SYMLINK_FORBIDDEN"
            ],
        )
        self.assertEqual(result.gates["contract"], "failed")

    def test_directory_diagnosis_validates_snapshot_bytes_after_source_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "snapshot-expert"
            package.mkdir()
            manifest = package / "expert.json"
            manifest.write_text('{"source":"before"}', encoding="utf-8")
            original_inspect = validation_result.safe_input.inspect
            validated_contents: list[str] = []
            validated_snapshots: list[safe_input.InputSnapshot] = []

            def inspect_then_mutate(path, *args, **kwargs):
                snapshot = original_inspect(path, *args, **kwargs)
                if Path(path).absolute() == package.absolute():
                    manifest.write_text('{"source":"after"}', encoding="utf-8")
                return snapshot

            def validate_staged(path, *, target=None, input_snapshot=None):
                self.assertNotEqual(Path(path).absolute(), package.absolute())
                self.assertIsNotNone(input_snapshot)
                validated_snapshots.append(input_snapshot)
                validated_contents.append(
                    (Path(path) / "expert.json").read_text(encoding="utf-8")
                )
                return ValidationResult(input_snapshot=input_snapshot, target=target)

            with patch.object(
                validation_result.safe_input,
                "inspect",
                side_effect=inspect_then_mutate,
            ), patch.object(
                diagnose_expert.validate_expert,
                "validate_package",
                side_effect=validate_staged,
            ):
                result = diagnose_expert.diagnose(package)

        self.assertEqual(validated_contents, ['{"source":"before"}'])
        self.assertEqual(len(validated_snapshots), 1)
        self.assertEqual(result.execution["reason"], "untrusted-directory")
        self.assertEqual(
            result.provenance["inputSha256"],
            result.provenance["validatedPackageInputSha256"],
        )


if __name__ == "__main__":
    unittest.main()
