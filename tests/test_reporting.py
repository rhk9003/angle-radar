import unittest

from reporting import (
    angle_development_prompt,
    angle_report_prompt,
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
                "signal": "英文市場來源留言反覆追問如何開始收費。",
                "route_note": "不要照抄國外市場案例，改用自己的接案資料。",
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
            "國外市場",
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
                    "internal_signal_type": "momentum_extension",
                    "route_note": "沿著高關注的接案內容，接著回答第一位陌生客戶從哪裡來。",
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
                "views_per_day": 800,
                "baseline_sample_size": 5,
                "outlier_ratio": 3.2,
            }
        ]
        rendered = render_angle_report(
            report,
            "命理創業",
            videos,
            breakdowns=[
                {
                    "video_id": "video-1",
                    "breakout_reasons": ["以真實接案過程形成結果懸念"],
                    "reusable_angles": ["補上第一位陌生客戶的取得過程"],
                }
            ],
        )
        for text in (
            "🔥",
            "💡",
            "🚀",
            "foreign_adaptation",
            "momentum_extension",
            "國外移植",
        ):
            self.assertNotIn(text, rendered)
        self.assertIn("第一批客戶", rendered)
        self.assertIn("熱門內容的延伸", rendered)
        self.assertIn("沿著熱門話題可以接著談", rendered)
        self.assertIn("近期同頻道基準的 3.2 倍", rendered)
        self.assertIn("來源內容結論", rendered)
        self.assertIn("第一位陌生客戶的取得過程", rendered)
        self.assertIn("這個切角從哪裡挖到", rendered)
        self.assertIn("線索完整度", rendered)
        self.assertIn("深化前先確認", rendered)
        self.assertIn(
            "以下是值得探索的內容切角，不代表已證實的市場需求。",
            rendered,
        )
        self.assertNotIn("證據信心", rendered)

    def test_internal_route_is_downgraded_when_source_is_not_eligible(self):
        report = {
            "radar_summary": "摘要",
            "angles": [
                {
                    "angle_name": "改寫來源",
                    "opportunity": "把既有內容換成自己的案例。",
                    "signal": "來源表現強。",
                    "internal_signal_type": "cross_context_adaptation",
                    "route_note": "轉成自己的版本。",
                    "evidence_video_ids": ["zh-video"],
                    "comment_gap": "",
                    "confidence": "high",
                    "caution": "需要查證。",
                }
            ],
        }
        videos = [{"id": "zh-video", "market": "zh", "evidence_score": 90}]
        breakdowns = [{"video_id": "zh-video", "comment_gaps": []}]
        validated = validate_angle_evidence(report, videos, breakdowns)
        self.assertEqual(validated["angles"][0]["internal_signal_type"], "other")
        self.assertEqual(validated["angles"][0]["confidence"], "low")

    def test_report_prompt_forces_four_core_signal_families(self):
        prompt = angle_report_prompt(
            "命理創業",
            {"zh": [{"kw": "算命創業", "intent": "核心詞"}], "en": []},
            [
                {
                    "video_id": "en-video",
                    "comment_gaps": ["第一批客戶從哪裡來？"],
                }
            ],
            [
                {
                    "id": "en-video",
                    "title": "Start a spiritual business",
                    "market": "en",
                    "evidence_score": 95,
                    "views_per_day": 1000,
                }
            ],
            [{"source_ids": ["en-video"], "recent_video_count": 3}],
            8,
        )
        self.assertIn("2 個 cross_context_adaptation", prompt)
        self.assertIn("2 個 audience_gap", prompt)
        self.assertIn("2 個 momentum_extension", prompt)
        self.assertIn("1 個 rising_topic", prompt)
        self.assertIn("沿著哪個熱門內容", prompt)

    def test_momentum_route_requires_real_performance_signal(self):
        report = {
            "radar_summary": "摘要",
            "angles": [
                {
                    "angle_name": "接著談第一批客戶",
                    "opportunity": "回答熱門接案題材留下的下一題。",
                    "signal": "來源近期觀看速度高。",
                    "internal_signal_type": "momentum_extension",
                    "route_note": "沿著熱門內容接著談第一位陌生客戶。",
                    "evidence_video_ids": ["hot-video"],
                    "comment_gap": "",
                    "confidence": "high",
                    "caution": "仍需確認題材適配。",
                }
            ],
        }
        videos = [
            {
                "id": "hot-video",
                "market": "zh",
                "view_count": 50_000,
                "views_per_day": 900,
                "evidence_score": 95,
            }
        ]
        validated = validate_angle_evidence(report, videos, [])
        self.assertEqual(
            validated["angles"][0]["internal_signal_type"], "momentum_extension"
        )


if __name__ == "__main__":
    unittest.main()
