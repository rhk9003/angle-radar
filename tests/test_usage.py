import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from llm_client import (
    MODEL_PRICING,
    GeminiClient,
    UsageLedger,
    _parse_json_object,
    _retry_thinking_level,
    classify_youtube_input_route,
    estimate_probe_cost,
    usage_metadata_snapshot,
)


class FakeUsage:
    prompt_token_count = 40_000
    candidates_token_count = 5_000
    thoughts_token_count = 2_000
    cached_content_token_count = 0
    total_token_count = 47_000


class FakeModalityDetail:
    def __init__(self, modality: str, token_count: int) -> None:
        self.modality = modality
        self.token_count = token_count


class FakeVideoUsage:
    prompt_token_count = 13_400
    candidates_token_count = 400
    thoughts_token_count = 0
    cached_content_token_count = 0
    total_token_count = 13_800
    prompt_tokens_details = [
        FakeModalityDetail("VIDEO", 10_000),
        FakeModalityDetail("AUDIO", 3_200),
        FakeModalityDetail("TEXT", 200),
    ]


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

    def test_probe_cost_uses_audio_price_and_modality_details(self):
        snapshot = usage_metadata_snapshot(FakeVideoUsage())
        cost = estimate_probe_cost(
            "gemini-3.1-flash-lite", snapshot, usd_to_twd=32
        )
        expected_usd = (
            10_200 * 0.25 + 3_200 * 0.50 + 400 * 1.50
        ) / 1_000_000

        self.assertEqual(
            snapshot["prompt_tokens_details"],
            [
                {"modality": "video", "tokens": 10_000},
                {"modality": "audio", "tokens": 3_200},
                {"modality": "text", "tokens": 200},
            ],
        )
        self.assertAlmostEqual(cost["total_usd"], expected_usd, places=6)
        self.assertAlmostEqual(cost["total_twd"], expected_usd * 32, places=2)

    def test_route_classifier_distinguishes_reported_media_from_cc_like_input(self):
        media = classify_youtube_input_route(
            prompt_tokens=13_400,
            token_details=usage_metadata_snapshot(FakeVideoUsage())[
                "prompt_tokens_details"
            ],
            duration_seconds=600,
        )
        cc_like = classify_youtube_input_route(
            prompt_tokens=2_000,
            token_details=[],
            duration_seconds=600,
        )

        self.assertEqual(media["route"], "media_tokens_reported")
        self.assertEqual(cc_like["route"], "cc_or_text_likely")
        self.assertEqual(cc_like["expected_low_media_tokens"], 58_800)

    def test_youtube_probe_counts_and_generates_exactly_once(self):
        models = Mock()
        models.count_tokens.return_value = SimpleNamespace(total_tokens=12_345)
        models.generate_content.return_value = SimpleNamespace(
            parsed={"spoken_summary": "摘要"},
            usage_metadata=FakeVideoUsage(),
            text="",
        )
        client = object.__new__(GeminiClient)
        client._client = SimpleNamespace(models=models)
        client._types = SimpleNamespace(
            FileData=lambda **kwargs: {"file_data": kwargs},
            Part=lambda **kwargs: {"part": kwargs},
            Content=lambda **kwargs: {"content": kwargs},
        )
        client._ledger = UsageLedger()
        client._config = Mock(return_value={"media_resolution": "low"})

        result = client.probe_youtube_url(
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            model="gemini-3.1-flash-lite",
            prompt="只回摘要",
            schema={"type": "object"},
        )

        self.assertEqual(result["count_tokens"], 12_345)
        self.assertEqual(result["analysis"], {"spoken_summary": "摘要"})
        models.count_tokens.assert_called_once()
        models.generate_content.assert_called_once()
        self.assertTrue(client._config.call_args.kwargs["media_resolution_low"])
        self.assertEqual(len(client._ledger.records()), 1)

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
