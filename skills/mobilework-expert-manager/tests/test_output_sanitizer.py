from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import output_sanitizer


class OutputSanitizerTests(unittest.TestCase):
    def test_recursively_sanitizes_headers_keys_and_urls_without_mutation(self) -> None:
        payload = {
            "Authorization": "Bearer authorization-canary",
            "Cookie": "session=cookie-canary",
            "nested": [
                {
                    "accessToken": "token-canary",
                    "password": "password-canary",
                    "apiKey": "{env:API_TOKEN}",
                    "tokenName": "API_TOKEN",
                }
            ],
            "text": (
                "Authorization: Bearer header-canary\n"
                "Cookie: session=cookie-text-canary\n"
                "serialized={\"Cookie\": \"session=json-cookie-canary\"}\n"
                "tool --api-key cli-text-canary\n"
                "request https://url-user:url-password@example.invalid/path"
                "?token=query-canary&safe=value#fragment-secret-canary "
                "password=inline-canary\n"
                "standalone sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz123456 "
                "sk-svcacct-ZyXwVuTsRqPoNmLkJiHgFeDcBa654321 "
                "ghp_abcdefghijklmnopqrstuvwxyz123456 "
                "xoxb-1234567890-standalone-slack-canary"
            ),
            "command": ["tool", "--token", "argv-canary", "--safe", "visible"],
        }

        sanitized = output_sanitizer.sanitize_mapping(payload)
        serialized = json.dumps(sanitized, sort_keys=True)

        for canary in (
            "authorization-canary",
            "cookie-canary",
            "token-canary",
            "password-canary",
            "header-canary",
            "cookie-text-canary",
            "json-cookie-canary",
            "cli-text-canary",
            "argv-canary",
            "url-user",
            "url-password",
            "query-canary",
            "fragment-secret-canary",
            "inline-canary",
            "AbCdEfGhIjKlMnOpQrStUvWxYz123456",
            "ZyXwVuTsRqPoNmLkJiHgFeDcBa654321",
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "standalone-slack-canary",
        ):
            self.assertNotIn(canary, serialized)
        self.assertEqual(sanitized["nested"][0]["apiKey"], "{env:API_TOKEN}")
        self.assertEqual(sanitized["nested"][0]["tokenName"], "API_TOKEN")
        self.assertIn("example.invalid/path", sanitized["text"])
        self.assertNotIn("safe=value", sanitized["text"])
        self.assertIn("safe=<redacted>", sanitized["text"])
        self.assertEqual(sanitized["command"][-1], "visible")
        self.assertEqual(payload["Authorization"], "Bearer authorization-canary")

    def test_preserves_only_the_contract_environment_reference_form(self) -> None:
        payload = {
            "token": "{env:API_TOKEN}",
            "password": "$PASSWORD_ENV",
            "clientSecret": "${CLIENT_SECRET_ENV}",
            "apiKey": "API_KEY_ENV",
            "Authorization": "Bearer {env:API_TOKEN}",
            "url": "https://example.invalid/?token={env:API_TOKEN}",
            "userinfoUrl": (
                "https://{env:API_USER}:{env:API_PASSWORD}@example.invalid/path"
            ),
            "fragmentUrl": "https://example.invalid/#{env:FRAGMENT_TOKEN}",
        }

        sanitized = output_sanitizer.sanitize_mapping(payload)

        self.assertEqual(sanitized["token"], "{env:API_TOKEN}")
        self.assertEqual(sanitized["password"], output_sanitizer.REDACTED)
        self.assertEqual(sanitized["clientSecret"], output_sanitizer.REDACTED)
        self.assertEqual(sanitized["apiKey"], output_sanitizer.REDACTED)
        self.assertEqual(
            sanitized["Authorization"],
            "Bearer {env:API_TOKEN}",
        )
        self.assertEqual(
            sanitized["url"],
            "https://example.invalid/?token={env:API_TOKEN}",
        )
        self.assertEqual(sanitized["userinfoUrl"], payload["userinfoUrl"])

    def test_uppercase_credentials_and_all_url_parameters_are_redacted(self) -> None:
        payload = {
            "apiKey": "ACTUALSUPERSECRET",
            "url": (
                "https://ADMIN:TOPSECRETPASSWORD@example.invalid/"
                "?safe=ALSOSECRET&page=2"
            ),
        }

        rendered = output_sanitizer.json_dumps(payload)

        for secret in (
            "ACTUALSUPERSECRET",
            "ADMIN",
            "TOPSECRETPASSWORD",
            "ALSOSECRET",
            "page=2",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("safe=<redacted>", rendered)
        self.assertIn("page=<redacted>", rendered)

    def test_sanitizes_mapping_keys_and_numeric_sensitive_values(self) -> None:
        payload = {
            "Authorization: Bearer mapping-key-canary": "visible",
            "token=first-key-canary": False,
            "token=second-key-canary": 0,
            "nested": {"apiKey": False, "password": None},
        }

        sanitized = output_sanitizer.sanitize_mapping(payload)
        rendered = json.dumps(sanitized, sort_keys=True)

        for canary in (
            "mapping-key-canary",
            "first-key-canary",
            "second-key-canary",
        ):
            self.assertNotIn(canary, rendered)
        self.assertEqual(len(sanitized), len(payload))
        self.assertIn("token=<redacted>", sanitized)
        self.assertIn("token=<redacted>#2", sanitized)
        self.assertIs(sanitized["token=<redacted>"], False)
        self.assertEqual(sanitized["token=<redacted>#2"], 0)
        self.assertIs(sanitized["nested"]["apiKey"], False)
        self.assertIsNone(sanitized["nested"]["password"])

    def test_sanitizes_nested_sensitive_suffixes_and_numeric_credentials(self) -> None:
        payload = {
            "targetOpenCode": {
                "capabilities": {
                    "githubToken": "capability-secret-canary",
                    "secretKey": "secret-key-canary",
                    "token": 8675309123456789,
                    "password": 123456,
                }
            }
        }

        rendered = output_sanitizer.json_dumps(payload)

        self.assertNotIn("capability-secret-canary", rendered)
        self.assertNotIn("secret-key-canary", rendered)
        self.assertNotIn("8675309123456789", rendered)
        self.assertNotIn("123456", rendered)

    def test_sanitizes_tuple_arguments_and_nested_sensitive_containers(self) -> None:
        payload = {
            "credentials": [
                {"label": "nested-secret-canary"},
                ("tuple-secret-canary", 123456),
            ],
            "command": ("tool", "--token", "argv-tuple-canary"),
        }

        sanitized = output_sanitizer.sanitize_mapping(payload)
        rendered = json.dumps(sanitized, sort_keys=True)

        self.assertIsInstance(sanitized["credentials"], list)
        self.assertIsInstance(sanitized["credentials"][1], tuple)
        self.assertIsInstance(sanitized["command"], tuple)
        for secret in (
            "nested-secret-canary",
            "tuple-secret-canary",
            "123456",
            "argv-tuple-canary",
        ):
            self.assertNotIn(secret, rendered)

    def test_sanitizes_relative_and_scheme_relative_url_parameters(self) -> None:
        value = (
            "//example.invalid/cb?code=scheme-relative-canary "
            "/cb?code=relative-canary&sig=relative-signature-canary#fragment-canary "
            "example.invalid/cb?code=bare-canary "
            "example.invalid?code=bare-root-canary "
            "localhost?code=localhost-root-canary"
        )

        rendered = output_sanitizer.sanitize_text(value)

        for canary in (
            "scheme-relative-canary",
            "relative-canary",
            "relative-signature-canary",
            "fragment-canary",
            "bare-canary",
            "bare-root-canary",
            "localhost-root-canary",
        ):
            self.assertNotIn(canary, rendered)
        self.assertEqual(rendered.count("code=<redacted>"), 5)
        self.assertEqual(output_sanitizer.sanitize_text(rendered), rendered)

    def test_preserves_non_secret_sk_prefixed_slugs_and_paths(self) -> None:
        value = "/tmp/sk-design-review/sk-package-path-canary-12345.zip"

        self.assertEqual(output_sanitizer.sanitize_text(value), value)

    def test_sanitizes_exception_text(self) -> None:
        error = RuntimeError(
            "request failed: api_key=exception-canary "
            "https://user:password@example.invalid/?secret=query-canary"
        )

        rendered = output_sanitizer.sanitize_exception(error)

        self.assertNotIn("exception-canary", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("query-canary", rendered)
        self.assertIn("example.invalid", rendered)

    def test_json_dumps_emits_one_sanitized_document(self) -> None:
        rendered = output_sanitizer.json_dumps(
            {"ok": True, "token": "json-dumps-canary"},
            indent=2,
        )

        self.assertEqual(json.loads(rendered), {"ok": True, "token": "<redacted>"})
        self.assertNotIn("json-dumps-canary", rendered)


if __name__ == "__main__":
    unittest.main()
