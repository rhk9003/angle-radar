import unittest

from radar_core import build_brief
from reporting import generic_ai_comparison_prompt, render_menu, validate_menu_evidence


class ReportingTests(unittest.TestCase):
    def test_comparison_prompt_does_not_reveal_internal_method(self):
        prompt = generic_ai_comparison_prompt(
            build_brief("增肌飲食", "外食上班族", "建立專業信任（接案／接業配）")
        )
        for secret_term in ("autocomplete", "outlier", "探勘輪數", "評分公式"):
            self.assertNotIn(secret_term, prompt)
        self.assertIn("沒有即時市場資料", prompt)

    def test_invalid_evidence_id_is_not_rendered_as_fake_source(self):
        menu = {
            "opportunity_summary": "這是一個機會摘要。",
            "cards": [
                {
                    "plan_name": "外食挑戰",
                    "what_to_shoot": "拍一週外食增肌",
                    "format": "七天紀錄",
                    "opening_line": "七天都不煮飯，能增肌嗎？",
                    "why_attractive": "結果有懸念",
                    "evidence_video_id": "not-real",
                    "evidence_reason": "假的",
                    "leverage": "承接外食需求",
                    "surpass": "列出完整花費",
                    "zh_market_status": "unknown",
                    "zh_market_reason": "樣本不足",
                    "recommendation_score": 80,
                    "evidence_confidence": "low",
                    "production_difficulty": "medium",
                    "creator_fit": "可實拍",
                }
            ],
            "recommended_card": 1,
            "recommendation_reason": "容易執行",
            "watchlist_ids": [],
        }
        rendered = render_menu(menu, "增肌飲食", [])
        self.assertIn("沒有足夠直接證據", rendered)
        self.assertNotIn("假的", rendered)

    def test_rising_topics_must_match_computed_signal(self):
        menu = {
            "cards": [],
            "watchlist_ids": [],
            "rising_topics": [
                {
                    "signal_key": "en:invented",
                    "topic": "憑空出現",
                    "confidence": "high",
                    "source_ids": ["video-1"],
                },
                {
                    "signal_key": "en:walking pad desk",
                    "topic": "走路辦公",
                    "confidence": "high",
                    "source_ids": ["video-1", "fake"],
                },
            ],
        }
        videos = [{"id": "video-1", "market": "en"}]
        signals = [
            {
                "signal_key": "en:walking pad desk",
                "confidence": "low",
                "source_ids": ["video-1"],
            }
        ]
        validated = validate_menu_evidence(menu, videos, [], signals)
        self.assertEqual(len(validated["rising_topics"]), 1)
        self.assertEqual(validated["rising_topics"][0]["confidence"], "low")
        self.assertEqual(validated["rising_topics"][0]["source_ids"], ["video-1"])


if __name__ == "__main__":
    unittest.main()
