import unittest

from reporting import (
    ANGLE_REPORT_SCHEMA,
    BREAKDOWN_BATCH_SCHEMA,
    RESEARCH_SYNTHESIS_SCHEMA,
    angle_development_prompt,
    angle_report_prompt,
    keyword_reflow_prompt,
    render_angle_report,
    research_synthesis_prompt,
    validate_angle_evidence,
    validate_research_synthesis,
)


def action_card(**overrides):
    value = {
        "angle_name": "第一批客戶從哪裡來",
        "you_can_make": "你可以拍第一次向陌生人收費前，要先完成哪三個驗證。",
        "core_message": "把免費練習、試收費與正式收費拆成三個判斷點。",
        "how_to_make": "用三個常見情境做判斷題，不假設你已有個案資料。",
        "opening_line": "真正難的不是學會算，而是第一次開口收錢。",
        "why_worth_making": "多支內容都談學習，但留言反覆追問何時能收費。",
        "differentiation": "現有內容多談收入，這支改談第一次收費前的判斷。",
        "avoid": "不要拍成沒有條件與案例的收入大公開。",
        "internal_signal_type": "audience_gap",
        "evidence_insight_ids": ["I1"],
        "evidence_video_ids": ["video-1"],
        "comment_refs": ["video-1:c1"],
        "confidence": "medium",
        "caution": "留言來自少量樣本，仍要用自己的服務流程校正。",
    }
    value.update(overrides)
    return value


def validated_synthesis():
    return {
        "demand_patterns": [],
        "supply_patterns": [],
        "audience_patterns": [],
        "cross_layer_insights": [
            {
                "insight_id": "I1",
                "finding": "學習內容很多，但收費時機仍被反覆追問。",
                "implication": "可把收費前的判斷做成具體選題。",
                "layers": ["supply", "audience"],
                "support_pattern_ids": ["S1", "A1"],
                "source_video_ids": ["video-1", "video-2"],
                "comment_refs": ["video-1:c1"],
                "confidence": "medium",
            }
        ],
    }


class ReportingTests(unittest.TestCase):
    def test_breakdown_schema_stays_small_and_flat(self):
        videos = BREAKDOWN_BATCH_SCHEMA["properties"]["videos"]
        self.assertEqual(videos["maxItems"], 4)
        self.assertNotIn("audience_questions", videos["items"]["properties"])

    def test_research_schema_uses_one_flat_findings_array(self):
        self.assertEqual(list(RESEARCH_SYNTHESIS_SCHEMA["properties"]), ["findings"])
        finding = RESEARCH_SYNTHESIS_SCHEMA["properties"]["findings"]
        self.assertEqual(finding["maxItems"], 14)
        self.assertNotIn("layers", finding["items"]["properties"])

    def test_development_prompt_is_actionable_without_internal_method(self):
        prompt = angle_development_prompt(
            "命理創業",
            action_card(),
            [{"id": "video-1", "title": "命理師接案", "url": "https://youtu.be/1"}],
        )
        for secret_term in (
            "autocomplete",
            "outlier",
            "探勘輪數",
            "評分公式",
            "英文市場",
            "cross_layer",
        ):
            self.assertNotIn(secret_term, prompt)
        self.assertIn("第一次向陌生人收費", prompt)
        self.assertIn("我的專業、經驗或觀點", prompt)
        self.assertIn("不要虛構數據", prompt)

    def test_keyword_reflow_can_only_choose_sourced_candidates(self):
        prompt = keyword_reflow_prompt(
            "命理創業",
            [
                {
                    "candidate_id": "R001",
                    "query": "tarot first paying client",
                    "market": "en",
                    "source_kind": "comment",
                    "source_video_ids": ["video-1"],
                }
            ],
            ["命理創業"],
        )
        self.assertIn("R001", prompt)
        self.assertIn("最多選 4 個", prompt)
        self.assertIn("不可自創", prompt)

    def test_synthesis_validation_requires_real_cross_layer_support(self):
        raw = {
            "demand_patterns": [
                {
                    "finding": "搜尋詞出現收費問題",
                    "detail": "不同詞都指向第一次收費",
                    "evidence_keywords": ["第一次收費"],
                    "source_video_ids": ["video-1"],
                    "comment_refs": [],
                    "confidence": "medium",
                }
            ],
            "supply_patterns": [
                {
                    "finding": "影片多談收入，少談判斷",
                    "detail": "兩支影片都略過第一次收費前的條件",
                    "evidence_keywords": [],
                    "source_video_ids": ["video-1", "video-2"],
                    "comment_refs": [],
                    "confidence": "medium",
                }
            ],
            "audience_patterns": [
                {
                    "finding": "觀眾追問何時能收費",
                    "detail": "留言留下具體問題",
                    "evidence_keywords": [],
                    "source_video_ids": ["video-1"],
                    "comment_refs": ["video-1:c1", "fake:comment"],
                    "confidence": "medium",
                }
            ],
            "cross_layer_insights": [
                {
                    "finding": "第一次收費是供給缺口",
                    "implication": "可做成判斷型影片",
                    "layers": ["demand", "supply"],
                    "support_pattern_ids": ["D1", "S1"],
                    "source_video_ids": ["video-1", "video-2", "fake-video"],
                    "comment_refs": [],
                    "confidence": "medium",
                },
                {
                    "finding": "只有單層的結論",
                    "implication": "不應留下",
                    "layers": ["demand"],
                    "support_pattern_ids": ["D1"],
                    "source_video_ids": ["video-1"],
                    "comment_refs": [],
                    "confidence": "high",
                },
            ],
        }
        videos = [{"id": "video-1"}, {"id": "video-2"}]
        comments = {"video-1": [{"ref": "video-1:c1", "text": "何時能收費？"}]}
        checked = validate_research_synthesis(raw, videos, comments)
        self.assertEqual(checked["demand_patterns"][0]["insight_id"], "D1")
        self.assertEqual(checked["supply_patterns"][0]["insight_id"], "S1")
        self.assertEqual(len(checked["cross_layer_insights"]), 1)
        self.assertEqual(
            checked["cross_layer_insights"][0]["source_video_ids"],
            ["video-1", "video-2"],
        )
        self.assertEqual(checked["cross_layer_insights"][0]["insight_id"], "I1")

    def test_flat_synthesis_is_expanded_and_source_validated(self):
        raw = {
            "findings": [
                {
                    "item_id": "D7",
                    "item_type": "demand",
                    "finding": "搜尋詞指向收費時機",
                    "detail": "不同問句都在問第一次收費",
                    "evidence_keywords": ["第一次收費"],
                    "support_ids": [],
                    "source_video_ids": ["video-1"],
                    "comment_refs": [],
                    "confidence": "medium",
                },
                {
                    "item_id": "S9",
                    "item_type": "supply",
                    "finding": "供給少談判斷點",
                    "detail": "兩支片都只談收入",
                    "evidence_keywords": [],
                    "support_ids": [],
                    "source_video_ids": ["video-1", "video-2"],
                    "comment_refs": [],
                    "confidence": "medium",
                },
                {
                    "item_id": "I4",
                    "item_type": "cross",
                    "finding": "收費時機是可用缺口",
                    "detail": "可拍成具體判斷題",
                    "evidence_keywords": [],
                    "support_ids": ["D7", "S9"],
                    "source_video_ids": ["video-1", "video-2", "fake"],
                    "comment_refs": [],
                    "confidence": "medium",
                },
            ]
        }
        checked = validate_research_synthesis(
            raw,
            [{"id": "video-1"}, {"id": "video-2"}],
            {},
        )
        self.assertEqual(checked["demand_patterns"][0]["insight_id"], "D1")
        self.assertEqual(checked["supply_patterns"][0]["insight_id"], "S1")
        self.assertEqual(
            checked["cross_layer_insights"][0]["support_pattern_ids"],
            ["D1", "S1"],
        )
        self.assertEqual(
            checked["cross_layer_insights"][0]["source_video_ids"],
            ["video-1", "video-2"],
        )

    def test_final_angle_sources_are_locked_to_validated_insights(self):
        report = {
            "radar_summary": "可以優先回答第一次收費。",
            "angles": [
                action_card(
                    evidence_video_ids=["fake-video"],
                    comment_refs=["fake:comment"],
                )
            ],
        }
        videos = [{"id": "video-1"}, {"id": "video-2"}]
        checked = validate_angle_evidence(report, videos, validated_synthesis())
        angle = checked["angles"][0]
        self.assertEqual(angle["evidence_video_ids"], ["video-1", "video-2"])
        self.assertEqual(angle["comment_refs"], ["video-1:c1"])
        self.assertEqual(angle["internal_signal_type"], "audience_gap")
        self.assertEqual(angle["confidence"], "medium")

    def test_action_prompt_uses_strength_not_fixed_category_quotas(self):
        prompt = angle_report_prompt(
            "命理創業",
            validated_synthesis(),
            [
                {
                    "id": "video-1",
                    "title": "Start a tarot business",
                    "view_count": 20_000,
                    "views_per_day": 800,
                }
            ],
            6,
        )
        self.assertIn("請提出 4–6 張行動卡", prompt)
        self.assertIn("同一個已驗證洞察可以支撐多張卡", prompt)
        self.assertEqual(ANGLE_REPORT_SCHEMA["properties"]["angles"]["minItems"], 4)
        self.assertIn("不設配額", prompt)
        self.assertIn("opening_line", prompt)
        self.assertNotIn("2 個 cross_context_adaptation", prompt)

    def test_research_prompt_explicitly_compares_three_layers(self):
        prompt = research_synthesis_prompt(
            "命理創業",
            {"zh": [{"kw": "命理創業", "intent": "核心詞"}], "en": []},
            [],
            [{"video_id": "video-1", "topic": "如何接案"}],
            [
                {
                    "id": "video-1",
                    "title": "如何接案",
                    "keyword_hits": [{"keyword": "命理創業", "rank": 1}],
                }
            ],
            {"video-1": [{"ref": "video-1:c1", "text": "何時能收費？"}]},
            [],
        )
        self.assertIn("item_type=demand", prompt)
        self.assertIn("item_type=supply", prompt)
        self.assertIn("item_type=audience", prompt)
        self.assertIn("support_ids", prompt)
        self.assertIn("至少引用 2 支影片", prompt)

    def test_public_report_is_an_action_card_not_a_research_report(self):
        report = {
            "radar_summary": "🔥 先回答收費判斷，比再做一支收入介紹更有差異。",
            "angles": [action_card(angle_name="💡 第一次收費")],
        }
        videos = [
            {
                "id": "video-1",
                "title": "🚀 命理師接案",
                "url": "https://youtu.be/1",
                "view_count": 12_000,
                "views_per_day": 800,
                "baseline_sample_size": 5,
                "outlier_ratio": 3.2,
            }
        ]
        rendered = render_angle_report(report, "命理創業", videos)
        for value in ("🔥", "💡", "🚀", "audience_gap", "cross_layer"):
            self.assertNotIn(value, rendered)
        for value in (
            "你可以拍",
            "這支真正要講",
            "你可以這樣拍",
            "開場可以直接說",
            "和現有內容拉開差異",
            "不要拍成",
            "近期同頻道基準的 3.2 倍",
        ):
            self.assertIn(value, rendered)
        self.assertNotIn("來源內容結論", rendered)
        self.assertNotIn("這個切角從哪裡挖到", rendered)

    def test_missing_insight_downgrades_claim(self):
        report = {
            "radar_summary": "摘要",
            "angles": [action_card(evidence_insight_ids=["not-real"])],
        }
        checked = validate_angle_evidence(
            report, [{"id": "video-1"}], validated_synthesis()
        )
        angle = checked["angles"][0]
        self.assertEqual(angle["confidence"], "low")
        self.assertEqual(angle["evidence_video_ids"], [])
        self.assertIn("沒有足夠", angle["why_worth_making"])


if __name__ == "__main__":
    unittest.main()
