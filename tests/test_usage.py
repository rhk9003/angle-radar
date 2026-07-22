import unittest
from unittest.mock import Mock

from llm_client import (
    MODEL_PRICING,
    GeminiClient,
    UsageLedger,
    _parse_json_object,
    _retry_thinking_level,
)


class FakeUsage:
    prompt_token_count = 40_000
    candidates_token_count = 5_000
    thoughts_token_count = 2_000
    cached_content_token_count = 0
    total_token_count = 47_000


class UsageTests(unittest.TestCase):
    def test_mechanical_model_pricing_and_retry_thinking_are_tracked(self):
        pricing = MODEL_PRICING["gemini-3.1-flash-lite"]

        self.assertEqual(pricing["input"], 0.25)
        self.assertEqual(pricing["output"], 1.50)
        self.assertEqual(_retry_thinking_level("gemini-3.1-flash-lite"), "low")

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

    def test_max_tokens_retry_uses_minimal_thinking_and_full_budget(self):
        finish_reason = type("FinishReason", (), {"name": "MAX_TOKENS"})()
        candidate = type("Candidate", (), {"finish_reason": finish_reason})()
        client = object.__new__(GeminiClient)
        client._generate = Mock(
            side_effect=[
                type(
                    "Response",
                    (),
                    {
                        "parsed": None,
                        "text": '{"angles":[',
                        "candidates": [candidate],
                    },
                )(),
                type(
                    "Response",
                    (),
                    {
                        "parsed": None,
                        "text": '{"angles":[]}',
                        "candidates": [],
                    },
                )(),
            ]
        )

        result = client.generate_json(
            stage="切角雷達生成",
            model="gemini-3.5-flash",
            prompt="make cards",
            schema={"type": "object"},
            max_output_tokens=8_000,
            thinking_level="low",
        )

        self.assertEqual(result, {"angles": []})
        retry = client._generate.call_args_list[1].kwargs
        self.assertEqual(retry["max_output_tokens"], 12_000)
        self.assertEqual(retry["thinking_level"], "minimal")

    def test_generate_json_can_parse_prompt_only_json_without_schema(self):
        client = object.__new__(GeminiClient)
        client._generate = Mock(
            return_value=type(
                "Response",
                (),
                {
                    "parsed": None,
                    "text": '```json\n{"findings":[]}\n```',
                    "candidates": [],
                },
            )()
        )
        result = client.generate_json(
            stage="research",
            model="test-model",
            prompt="return JSON",
            schema=None,
            max_output_tokens=1_000,
            thinking_level=None,
        )
        self.assertEqual(result, {"findings": []})
        self.assertIsNone(client._generate.call_args.kwargs["schema"])


if __name__ == "__main__":
    unittest.main()
