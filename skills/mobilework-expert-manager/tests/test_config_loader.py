from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import config_loader
import manager_contract


class ConfigLoaderTests(unittest.TestCase):
    def test_pure_config_uses_explicit_sidecar_and_version_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            runtime = workspace / ".opencode"
            runtime.mkdir(parents=True)
            (runtime / "opencode.jsonc").write_text('{"$schema":"https://opencode.ai/config.json"}', encoding="utf-8")
            sidecar = root / "opencode"
            sidecar.write_text("fixture", encoding="utf-8")
            sidecar.chmod(0o700)

            def fake_run(command, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = "2.3.4\n" if command[-1] == "--version" else json.dumps({"agent": {}})
                self.assertEqual(command[0], str(sidecar.resolve()))
                if command[-1] != "--version":
                    self.assertEqual(command[-3:], ["debug", "config", "--pure"])
                return Result()

            target = manager_contract.resolve_target(cli_version="2.3.4", env={})
            with patch("subprocess.run", side_effect=fake_run):
                result = config_loader.verify(workspace, sidecar, target=target)
            self.assertEqual(result["evidenceLevel"], "config-loadable")
            self.assertEqual(result["runtime"]["status"], "not-tested")
            self.assertEqual(result["provenance"]["sidecarActualVersion"], "2.3.4")

    def test_version_conflict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            (workspace / ".opencode").mkdir(parents=True)
            (workspace / ".opencode/opencode.jsonc").write_text("{}", encoding="utf-8")
            sidecar = root / "opencode"
            sidecar.write_text("fixture", encoding="utf-8")
            sidecar.chmod(0o700)
            class Result:
                returncode = 0
                stdout = "9.9.9\n"
                stderr = ""
            with patch("subprocess.run", return_value=Result()):
                with self.assertRaisesRegex(config_loader.ConfigLoadError, "conflicts"):
                    config_loader.verify(
                        workspace, sidecar,
                        target=manager_contract.resolve_target(cli_version="1.0.0", env={}),
                    )


if __name__ == "__main__":
    unittest.main()
