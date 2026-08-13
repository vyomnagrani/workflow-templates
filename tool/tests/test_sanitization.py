from __future__ import annotations

import unittest

from n8n_to_laa.sanitization import sanitize_secrets


class SanitizationTests(unittest.TestCase):
    def test_sanitizes_sensitive_header_literals(self) -> None:
        value = {
            "headers": {
                "Authorization": "Bearer live-token-value",
                "x-api-key": "actual-api-key-value",
            }
        }
        result = sanitize_secrets(value)
        self.assertEqual(
            result["headers"]["Authorization"],
            "REPLACE_WITH_SECRET",
        )
        self.assertEqual(
            result["headers"]["x-api-key"],
            "REPLACE_WITH_SECRET",
        )

    def test_sanitizes_name_value_header_shape(self) -> None:
        value = {
            "headerParameters": {
                "parameters": [
                    {"name": "x-api-key", "value": "actual-api-key-value"}
                ]
            }
        }
        result = sanitize_secrets(value)
        self.assertEqual(
            result["headerParameters"]["parameters"][0]["value"],
            "REPLACE_WITH_SECRET",
        )

    def test_preserves_expressions_and_placeholders(self) -> None:
        value = {
            "Authorization": "={{ $json.token }}",
            "apiKey": "REPLACE_WITH_API_KEY",
        }
        self.assertEqual(sanitize_secrets(value), value)


if __name__ == "__main__":
    unittest.main()
