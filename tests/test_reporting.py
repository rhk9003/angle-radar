import unittest

from reporting import (
    angle_development_prompt,
    render_angle_report,
    validate_angle_evidence,
)


class ReportingTests(unittest.TestCase):
    def test_takeaway_prompt_does_not_reveal_internal_method_or_market_split(self):
        prompt = angle_development_prompt(
            "命理創業",
            {
                "angle_name": "第一批客戶如何出現",
                "opportunity": "從學會命理轉向取得第一批真實個案。",
                "signal": "來源留言反覆追問如何開始收費。",
                "comment_gap": "免費練習到什麼時候才能收費？",
                "evidence_video_ids": ["video-1"],
            },
            [{"id": "video-1", "title": "命理師接案", "url": "https://youtu.be/1"}],
        )
        for secret_term in (
            "autocomplete",
            "outlier",
            "探勘輪數",
            "評分公式",
            "國內外",
            "英文市場",
            "台灣主場",
        ):
            self.assertNotIn(secret_term, prompt)
        self.assertIn("我的專業、經驗或觀點", prompt)
        self.assertIn("不要虛構數據", prompt)

    def test_invalid_evidence_is_lowered_and_not_rendered_as_source(self):
        report = {
            "radar_summary": "有一個待驗證方向。",
            "angles": [
                {
                    "angle_name": "外食挑戰",
                    "opportunity": "測試一週外食增肌。",
                    "signal": "假的證據說法",
                    "evidence_video_ids": ["not-real"],
                    "comment_gap": "假的留言",
                    "confidence": "high",
                    "caution": "先確認可行性。",
                }
            ],
        }
        validated = validate_angle_evidence(report, [], [])
        self.assertEqual(validated["angles"][0]["confidence"], "low")
        self.assertEqual(validated["angles"][0]["comment_gap"], "")
        rendered = render_angle_report(validated, "增肌飲食", [])
        self.assertIn("本次樣本沒有足夠直接來源", rendered)
        self.assertNotIn("假的證據說法", rendered)

    def test_comment_gap_must_exactly_match_a_referenced_breakdown(self):
        base = {
            "radar_summary": "摘要",
            "angles": [
                {
                    "angle_name": "收費時機",
                    "opportunity": "討論何時開始收費。",
                    "signal": "留言出現追問。",
                    "evidence_video_ids": ["video-1"],
                    "comment_gap": "免費練習到什麼時候才能收費？",
                    "confidence": "medium",
                    "caution": "單一來源。",
                }
            ],
        }
        videos = [{"id": "video-1"}]
        breakdowns = [
            {
                "video_id": "video-1",
                "comment_gaps": ["免費練習到什麼時候才能收費？"],
            }
        ]
        validated = validate_angle_evidence(base, videos, breakdowns)
        self.assertEqual(
            validated["angles"][0]["comment_gap"],
            "免費練習到什麼時候才能收費？",
        )

        base["angles"][0]["comment_gap"] = "模型自己發明的留言缺口"
        validated = validate_angle_evidence(base, videos, breakdowns)
        self.assertEqual(validated["angles"][0]["comment_gap"], "")

    def test_public_report_removes_emoji_and_method_labels(self):
        report = {
            "radar_summary": "🔥 值得研究",
            "angles": [
                {
                    "angle_name": "💡 第一批客戶",
                    "opportunity": "找出第一位陌生客戶從哪裡來。",
                    "signal": "留言有明確追問。",
                    "evidence_video_ids": ["video-1"],
                    "comment_gap": "",
                    "confidence": "medium",
                    "caution": "先看完整來源。",
                }
            ],
        }
        videos = [
            {
                "id": "video-1",
                "title": "🚀 命理師接案",
                "url": "https://youtu.be/1",
                "view_count": 12000,
                "baseline_sample_size": 0,
            }
        ]
        rendered = render_angle_report(report, "命理創業", videos)
        for text in ("🔥", "💡", "🚀", "foreign_adaptation", "國外移植", "熱門延伸"):
            self.assertNotIn(text, rendered)
        self.assertIn("第一批客戶", rendered)


if __name__ == "__main__":
    unittest.main()
