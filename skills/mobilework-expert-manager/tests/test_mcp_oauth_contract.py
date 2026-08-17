from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class McpOAuthContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads(load_spec_text("legacy-expert-json"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate(
        self,
        data: dict[str, object],
        *,
        name: str,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        source = self.root / name / "expert.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output = self.root / f"{name}-out"
        result = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(source),
                "--output-dir",
                str(output),
            ],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output / str(data["slug"])

    def validate(self, package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATE), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_projects_local_header_and_oauth_mcp_without_loss(self) -> None:
        data = copy.deepcopy(self.base)
        data["mcp_servers"] = [
            {
                "name": "local-canary",
                "type": "local",
                "command": ["node", "mock-stdio.mjs"],
                "environment": {"CANARY_TOKEN": "{env:LOCAL_MCP_TOKEN}"},
                "enabled": True,
                "timeout": 2500,
            },
            {
                "name": "header-canary",
                "type": "remote",
                "url": "https://example.com/{env:TENANT_ID}/mcp",
                "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
                "oauth": False,
                "enabled": True,
                "timeout": 3000,
            },
            {
                "name": "oauth-canary",
                "type": "remote",
                "url": "http://127.0.0.1:43123/mcp",
                "oauth": {
                    "clientId": "{env:OAUTH_CLIENT_ID}",
                    "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
                    "scope": "tools.read tools.call",
                    "callbackPort": 19876,
                    "redirectUri": "http://127.0.0.1:19876/mcp/oauth/callback",
                },
                "enabled": False,
            },
        ]
        data["agent"]["mcp"] = ["local-canary", "header-canary", "oauth-canary"]

        created, package = self.generate(data, name="oauth-full")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["mcp"]["local-canary"]["timeout"], 2500)
        self.assertIs(runtime["mcp"]["header-canary"]["oauth"], False)
        self.assertEqual(runtime["mcp"]["oauth-canary"]["oauth"], data["mcp_servers"][2]["oauth"])
        self.assertEqual(
            (package / ".env.example").read_text(encoding="utf-8"),
            "API_TOKEN=<required>\n"
            "LOCAL_MCP_TOKEN=<required>\n"
            "OAUTH_CLIENT_ID=<required>\n"
            "OAUTH_CLIENT_SECRET=<required>\n"
            "TENANT_ID=<required>\n",
        )
        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)

        runtime["mcp"]["oauth-canary"]["oauth"]["scope"] = "silently-mutated"
        (package / "opencode.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rejected = self.validate(package)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("mcp must exactly match expert.json mcp_servers", rejected.stdout)

    def test_explicit_empty_oauth_uses_dynamic_client_registration(self) -> None:
        data = copy.deepcopy(self.base)
        data["mcp_servers"] = [
            {
                "name": "oauth-dynamic",
                "type": "remote",
                "url": "https://example.com/mcp",
                "oauth": {},
            }
        ]
        data["agent"]["mcp"] = ["oauth-dynamic"]
        created, package = self.generate(data, name="oauth-dynamic")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["mcp"]["oauth-dynamic"]["oauth"], {})
        self.assertFalse((package / ".env.example").exists())
        self.assertEqual(self.validate(package).returncode, 0)

    def test_rejects_invalid_or_ambiguous_mcp_shapes(self) -> None:
        cases = [
            (
                "duplicate",
                [
                    {"name": "same", "type": "local", "command": ["one"]},
                    {"name": "same", "type": "local", "command": ["two"]},
                ],
                "duplicates same",
            ),
            ("local-empty", [{"name": "local", "type": "local", "command": []}], "non-empty list"),
            ("remote-url", [{"name": "remote", "type": "remote"}], "must be a non-empty string"),
            (
                "local-oauth",
                [{"name": "local", "type": "local", "command": ["demo"], "oauth": {}}],
                "unsupported fields oauth",
            ),
            (
                "remote-environment",
                [{"name": "remote", "type": "remote", "url": "https://example.com/mcp", "environment": {}}],
                "unsupported fields environment",
            ),
            (
                "oauth-true",
                [{"name": "remote", "type": "remote", "url": "https://example.com/mcp", "oauth": True}],
                "must be false or a mapping",
            ),
            (
                "oauth-unknown",
                [{"name": "remote", "type": "remote", "url": "https://example.com/mcp", "oauth": {"pkce": True}}],
                "unsupported fields pkce",
            ),
            (
                "oauth-port",
                [{"name": "remote", "type": "remote", "url": "https://example.com/mcp", "oauth": {"callbackPort": 0}}],
                "integer from 1 to 65535",
            ),
            (
                "oauth-redirect",
                [{"name": "remote", "type": "remote", "url": "https://example.com/mcp", "oauth": {"redirectUri": "file:///tmp/callback"}}],
                "must use http:// or https://",
            ),
            (
                "timeout",
                [{"name": "local", "type": "local", "command": ["demo"], "timeout": 0}],
                "positive integer",
            ),
            (
                "timeout-float",
                [{"name": "local", "type": "local", "command": ["demo"], "timeout": 1.5}],
                "positive integer",
            ),
            (
                "empty-url-host",
                [{"name": "remote", "type": "remote", "url": "https://"}],
                "must use http:// or https://",
            ),
            (
                "unknown",
                [{"name": "local", "type": "local", "command": ["demo"], "cwd": "/tmp"}],
                "unsupported fields cwd",
            ),
        ]
        for name, servers, expected in cases:
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                data["mcp_servers"] = servers
                created, _ = self.generate(data, name=f"invalid-{name}")
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)

    def test_minimal_role_mode_is_derived_from_package_shape(self) -> None:
        data = copy.deepcopy(self.base)
        data["agent"].pop("mode", None)
        created, package = self.generate(data, name="mode-omitted")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["agent"][data["agent"]["id"]]["mode"], "all")
        self.assertEqual(self.validate(package).returncode, 0)

    def test_rejects_unknown_manifest_and_runtime_extension_fields(self) -> None:
        mutations = [
            ("top-level", lambda data: data.update({"mystery": True}), "expert.json: unknown fields mystery"),
            (
                "runtime",
                lambda data: data.setdefault("runtime_extensions", {}).update({"mystery": True}),
                "runtime_extensions contains unsupported fields: mystery",
            ),
            (
                "command",
                lambda data: data.setdefault("runtime_extensions", {}).update(
                    {"commands": [{"name": "demo", "template": "demo", "mystery": True}]}
                ),
                "commands[0] contains unsupported fields: mystery",
            ),
            (
                "plugins",
                lambda data: data.setdefault("runtime_extensions", {}).update(
                    {"plugins": {"mystery": []}}
                ),
                "plugins contains unsupported fields: mystery",
            ),
            (
                "lsp",
                lambda data: data.setdefault("runtime_extensions", {}).update(
                    {"lsp": {"demo": {"disabled": True, "mystery": True}}}
                ),
                "lsp.demo contains unsupported fields: mystery",
            ),
        ]
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                mutate(data)
                created, _ = self.generate(data, name=f"unknown-{name}")
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)


if __name__ == "__main__":
    unittest.main()
