from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import manager_contract


class ManagerContractTests(unittest.TestCase):
    def test_policy_has_no_fixed_opencode_version(self) -> None:
        policy = manager_contract.load_policy()
        self.assertEqual(policy["contractVersion"], "2.1.0")
        self.assertNotIn("targetOpenCodeVersion", policy)
        text = (SCRIPT_DIR / "manager-contract.json").read_text(encoding="utf-8")
        self.assertNotIn("1.18.3", text)
        self.assertNotIn("1.16.2", text)

    def test_target_version_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contract = Path(temp) / "host.json"
            contract.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "opencodeVersion": "v3.2.1",
                        "capabilities": {"references": True},
                    }
                ),
                encoding="utf-8",
            )
            resolved = manager_contract.resolve_target(
                cli_version="4.0.0",
                env={manager_contract.TARGET_VERSION_ENV: "3.9.0"},
                host_contract=contract,
            )
            self.assertEqual(resolved.version, "4.0.0")
            self.assertEqual(resolved.source, "cli")
            self.assertEqual(resolved.capabilities, {"references": True})

            resolved = manager_contract.resolve_target(
                env={manager_contract.TARGET_VERSION_ENV: "3.9.0"},
                host_contract=contract,
            )
            self.assertEqual(resolved.version, "3.9.0")
            self.assertEqual(resolved.source, "environment")

            resolved = manager_contract.resolve_target(env={}, host_contract=contract)
            self.assertEqual(resolved.version, "3.2.1")
            self.assertEqual(resolved.source, "host-contract")

    def test_unknown_target_does_not_claim_capabilities(self) -> None:
        resolved = manager_contract.resolve_target(env={})
        self.assertEqual(resolved.version, "unknown")
        self.assertEqual(resolved.source, "unknown")
        self.assertEqual(resolved.capabilities, {})
        self.assertFalse(resolved.capability_verified)

    def test_version_string_alone_does_not_verify_capabilities(self) -> None:
        resolved = manager_contract.resolve_target(cli_version="9.9.9", env={})
        self.assertEqual(resolved.capabilities, {})
        self.assertFalse(resolved.capability_verified)

    def test_host_contract_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contract = Path(temp) / "host.json"
            contract.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "opencodeVersion": "2.0.0",
                        "unexpected": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(manager_contract.ManagerContractError, "unknown fields"):
                manager_contract.resolve_target(env={}, host_contract=contract)

    def test_validator_cli_records_explicit_target_and_contract_errors(self) -> None:
        broken = SCRIPT_DIR.parent / "evals/files/broken-package"
        explicit = subprocess.run(
            [
                sys.executable, str(SCRIPT_DIR / "validate_expert.py"), str(broken),
                "--format", "json", "--target-opencode-version", "7.8.9",
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(explicit.returncode, 1)
        payload = json.loads(explicit.stdout)
        self.assertEqual(payload["provenance"]["targetOpenCode"]["version"], "7.8.9")
        self.assertEqual(payload["provenance"]["targetOpenCode"]["source"], "cli")
        self.assertFalse(payload["provenance"]["targetOpenCode"]["capability_verified"])

        with tempfile.TemporaryDirectory() as temp:
            host = Path(temp) / "host.json"
            host.write_text('{"schemaVersion": 99, "opencodeVersion": "1.0.0"}', encoding="utf-8")
            invalid = subprocess.run(
                [
                    sys.executable, str(SCRIPT_DIR / "validate_expert.py"), str(broken),
                    "--format", "json", "--host-contract", str(host),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(json.loads(invalid.stdout)["findings"][0]["code"], "MANAGER_VERSION_CONTRACT_ERROR")


if __name__ == "__main__":
    unittest.main()
