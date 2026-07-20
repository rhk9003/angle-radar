import unittest

from llm_client import UsageLedger


class FakeUsage:
    prompt_token_count = 40_000
    candidates_token_count = 5_000
    thoughts_token_count = 2_000
    cached_content_token_count = 0
    total_token_count = 47_000


class UsageTests(unittest.TestCase):
    def test_cost_includes_thinking_tokens(self):
        ledger = UsageLedger()
        ledger.record("test", "gemini-3.1-flash-lite", FakeUsage())
        summary = ledger.summary(usd_to_twd=32)
        expected_usd = 40_000 * 0.25 / 1_000_000 + 7_000 * 1.5 / 1_000_000
        self.assertAlmostEqual(summary["estimated_usd"], expected_usd, places=6)
        self.assertAlmostEqual(summary["estimated_twd"], expected_usd * 32, places=2)


if __name__ == "__main__":
    unittest.main()
