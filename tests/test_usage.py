import unittest
from unittest.mock import Mock

from llm_client import GeminiClient, UsageLedger, _parse_json_object


class FakeUsage:
    prompt_token_count = 40_000
    candidates_token_count = 5_000
    thoughts_token_count = 2_000
    cached_content_token_count = 0
    total_token_count = 47_000


class UsageTests(unittest.TestCase):
    def test_generate_retries_generic_invalid_argument_without_thinking(self):
        response = type("Response", (), {"usage_metadata": {}})()
        models = Mock()
        models.generate_content = Mock(
            side_effect=[ValueError("400 INVALID_ARGUMENT"), response]
        )
        client = object.__new__(GeminiClient)
        client._client = type("Client", (), {"models": models})()
        client._ledger = Mock()
        client._config = Mock(return_value={})

        result = client._generate(
            stage="evidence",
            model="test-model",
            contents="test",
            schema={"type": "object"},
            max_output_tokens=1_000,
            temperature=0.2,
            thinking_level="low",
        )

        self.assertIs(result, response)
        self.assertEqual(
            client._config.call_args_list[0].kwargs["thinking_level"], "low"
        )
        self.assertIsNone(client._config.call_args_list[1].kwargs["thinking_level"])

    def test_cost_includes_thinking_tokens(self):
        ledger = UsageLedger()
        ledger.record("test", "gemini-3.1-flash-lite", FakeUsage())
        summary = ledger.summary(usd_to_twd=32)
        expected_usd = 40_000 * 0.25 / 1_000_000 + 7_000 * 1.5 / 1_000_000
        self.assertAlmostEqual(summary["estimated_usd"], expected_usd, places=6)
        self.assertAlmostEqual(summary["estimated_twd"], expected_usd * 32, places=2)

    def test_json_parser_accepts_code_fence(self):
        self.assertEqual(_parse_json_object('```json\n{"ok": true}\n```'), {"ok": True})

    def test_generate_json_retries_truncated_response(self):
        client = object.__new__(GeminiClient)
        client._generate = Mock(
            side_effect=[
                type(
                    "Response",
                    (),
                    {"parsed": None, "text": '{"cards":[', "candidates": []},
                )(),
                type(
                    "Response",
                    (),
                    {"parsed": None, "text": '{"cards":[]}', "candidates": []},
                )(),
            ]
        )
        result = client.generate_json(
            stage="menu",
            model="test-model",
            prompt="make menu",
            schema={"type": "object"},
            max_output_tokens=1_000,
            thinking_level="medium",
        )
        self.assertEqual(result, {"cards": []})
        self.assertEqual(client._generate.call_count, 2)
        retry = client._generate.call_args_list[1].kwargs
        self.assertGreater(retry["max_output_tokens"], 1_000)
        self.assertEqual(retry["thinking_level"], "low")


if __name__ == "__main__":
    unittest.main()
