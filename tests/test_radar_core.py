import unittest
from datetime import datetime, timedelta, timezone

from radar_core import (
    attach_outlier_metrics_v2,
    brief_quality,
    build_brief,
    build_reflow_candidates,
    build_search_terms,
    build_transcript_evidence,
    compress_comments,
    derive_rising_signals,
    merge_search_results,
    parse_youtube_video_ids,
    pick_evidence_ready_videos,
    pick_videos_diverse,
    validate_reflow_selection,
)


class BriefTests(unittest.TestCase):
    def test_required_context_increases_quality(self):
        minimal = build_brief("口紅", "", "請選擇")
        complete = build_brief(
            "敏感唇的平價口紅",
            "20–30 歲敏感唇上班族",
            "建立專業信任（接案／接業配）",
            who="彩妝師",
            form_pref="實測／挑戰",
            strengths="能做八小時實測",
        )
        minimal_score, minimal_missing = brief_quality(minimal)
        complete_score, complete_missing = brief_quality(complete)
        self.assertLess(minimal_score, complete_score)
        self.assertIn("目標觀眾", minimal_missing)
        self.assertNotIn("目標觀眾", complete_missing)


class EvidenceTests(unittest.TestCase):
    def test_timed_evidence_keeps_hook_and_ending(self):
        segments = [
            {"start": index * 10, "duration": 5, "text": f"第 {index} 段內容 " * 8}
            for index in range(31)
        ]
        evidence = build_transcript_evidence(segments, max_chars=1_500)
        self.assertIn("[00:00]", evidence)
        self.assertTrue("[04:50]" in evidence or "[05:00]" in evidence)
        self.assertLessEqual(len(evidence), 1_500)

    def test_comments_are_ranked_and_deduplicated(self):
        comments = [
            {"text": "希望可以補上價格比較", "likes": 20, "replies": 4},
            {"text": "希望可以補上價格比較！", "likes": 18, "replies": 3},
            {"text": "想看台灣品牌版本", "likes": 10, "replies": 8},
        ]
        compact = compress_comments(comments, limit=8)
        self.assertEqual(len(compact), 2)
        self.assertEqual(compact[0]["text"], "想看台灣品牌版本")


class KeywordTests(unittest.TestCase):
    def test_reference_urls_parse_only_real_video_ids(self):
        ids = parse_youtube_video_ids(
            "https://youtu.be/abcdefghijk 和 "
            "https://www.youtube.com/shorts/ABCDEFGHIJK?feature=share 不是普通文字"
        )
        self.assertEqual(ids, ["abcdefghijk", "ABCDEFGHIJK"])

    def test_duplicate_video_keeps_every_keyword_hit(self):
        pool = {}
        video = {"id": "video-1", "title": "同一支", "tags": ["接案"]}
        merge_search_results(
            pool,
            [video],
            research_keyword="命理創業",
            market="zh",
            order="relevance",
        )
        merge_search_results(
            pool,
            [{**video, "tags": ["第一次收費"]}],
            research_keyword="命理師收費",
            market="zh",
            order="date",
        )
        self.assertEqual(len(pool["video-1"]["keyword_hits"]), 2)
        self.assertEqual(pool["video-1"]["tags"], ["接案", "第一次收費"])

    def test_reflow_is_locked_to_sourced_candidate_and_deduplicated(self):
        videos = [
            {
                "id": "video-1",
                "title": "命理師第一次收費怎麼做",
                "market": "zh",
                "tags": ["命理師收費", "第一批客戶"],
            }
        ]
        comments = {
            "video-1": [
                {
                    "comment_id": "c1",
                    "ref": "video-1:c1",
                    "text": "免費練習到什麼時候才能收費？",
                    "likes": 12,
                    "replies": 3,
                }
            ]
        }
        candidates = build_reflow_candidates(videos, comments, ["命理創業"])
        comment_candidate = next(
            item for item in candidates if item["source_kind"] == "comment"
        )
        response = {
            "terms": [
                {
                    "candidate_id": comment_candidate["candidate_id"],
                    "query": "命理師什麼時候開始收費",
                    "reason": "補上收費時機",
                },
                {
                    "candidate_id": "R999",
                    "query": "完全無關",
                    "reason": "假的",
                },
            ]
        }
        selected = validate_reflow_selection(response, candidates, ["命理創業"], 4)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_video_ids"], ["video-1"])
        self.assertEqual(selected[0]["market"], "zh")

    def test_evidence_selection_refills_missing_transcript_video(self):
        candidates = [
            {"id": "empty", "evidence_score": 100},
            {"id": "ready-1", "evidence_score": 80},
            {"id": "ready-2", "evidence_score": 70},
        ]
        selected = pick_evidence_ready_videos(
            candidates,
            {"ready-1": [{"text": "字幕"}]},
            {"ready-2": [{"text": str(index)} for index in range(4)]},
            2,
        )
        self.assertEqual({item["id"] for item in selected}, {"ready-1", "ready-2"})

    def test_one_plan_combines_core_question_and_one_round_related_terms(self):
        plan = {
            "core_terms": ["算命", "算命創業"],
            "question_terms": ["如何開始", "怎麼收費", "沒客戶怎麼辦"],
            "en_core_terms": ["astrology business", "tarot business"],
            "en_question_terms": ["how to start", "get first clients"],
        }
        selected = build_search_terms(
            plan,
            [("算命師如何開始接案", 90), ("命理創業收入", 80)],
            [("spiritual business mistakes", 90)],
            zh_limit=4,
            en_limit=3,
        )
        zh_terms = [item["kw"] for item in selected["zh"]]
        self.assertIn("算命", zh_terms)
        self.assertIn("算命 如何開始", zh_terms)
        self.assertIn("算命創業 怎麼收費", zh_terms)
        self.assertIn("算命師如何開始接案", zh_terms)
        self.assertEqual(len(zh_terms), 4)
        self.assertIn(
            "spiritual business mistakes", [item["kw"] for item in selected["en"]]
        )
        self.assertEqual(len(selected["en"]), 3)

    def test_default_search_budget_is_four_zh_and_two_en(self):
        selected = build_search_terms(
            {
                "core_terms": ["廣告代操", "數位行銷"],
                "question_terms": ["如何選", "怎麼買", "踩雷怎麼辦", "值得嗎"],
                "en_core_terms": ["digital marketing agency"],
                "en_question_terms": ["how to choose", "common mistakes"],
            }
        )
        self.assertEqual(len(selected["zh"]), 4)
        self.assertEqual(len(selected["en"]), 2)
        self.assertTrue(
            all(
                item["intent"] in {"核心字", "核心字 × 問題字"}
                for market in selected.values()
                for item in market
            )
        )


class ScoringTests(unittest.TestCase):
    def test_outlier_uses_recent_same_format_baseline(self):
        target = {
            "id": "target",
            "title": "測試影片",
            "market": "en",
            "channel_id": "channel",
            "publish_time": "2099-01-01T00:00:00Z",
            "view_count": 7_000,
            "like_count": 300,
            "comment_count": 40,
            "duration_min": 8,
        }
        recent = {
            "channel": [
                {
                    "id": f"ref-{index}",
                    "view_count": views,
                    "publish_time": "2099-01-01T00:00:00Z",
                    "duration_min": 6,
                }
                for index, views in enumerate([700, 1_400, 2_100, 1_400, 1_400])
            ]
        }
        attach_outlier_metrics_v2(
            [target],
            {"channel": {"subs": 5_000, "country": "US"}},
            recent,
        )
        self.assertEqual(target["baseline_sample_size"], 5)
        self.assertEqual(target["outlier_ratio"], 5.0)
        self.assertEqual(target["outlier_confidence"], "medium")

    def test_selection_limits_channel_and_near_duplicate_titles(self):
        videos = []
        for index in range(8):
            videos.append(
                {
                    "id": str(index),
                    "title": "同一種一週挑戰" if index < 2 else f"不同企劃 {index}",
                    "market": "en" if index % 2 == 0 else "zh",
                    "channel_id": "same" if index < 2 else f"channel-{index}",
                    "source_keyword": f"keyword-{index % 3}",
                    "view_count": 20_000,
                    "duration_min": 8,
                    "evidence_score": 100 - index,
                    "outlier_ratio": 4,
                    "views_per_day": 1_000,
                }
            )
        selected = pick_videos_diverse(videos, k=5)
        channels = [video["channel_id"] for video in selected]
        self.assertEqual(len(channels), len(set(channels)))

    def test_rising_signal_requires_multiple_channels(self):
        published = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        videos = [
            {
                "id": str(index),
                "market": "en",
                "research_keyword": "walking pad desk",
                "channel_id": "same-channel",
                "publish_time": published,
                "views_per_day": 2_000,
                "outlier_ratio": 4,
                "evidence_score": 80,
            }
            for index in range(3)
        ]
        self.assertEqual(derive_rising_signals(videos), [])

    def test_rising_signal_surfaces_cross_channel_topic(self):
        published = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        videos = [
            {
                "id": str(index),
                "market": "en",
                "research_keyword": "walking pad desk",
                "channel_id": f"channel-{index}",
                "publish_time": published,
                "views_per_day": 2_000 + index * 500,
                "outlier_ratio": 3 + index,
                "evidence_score": 80 + index,
            }
            for index in range(3)
        ]
        signals = derive_rising_signals(videos)
        self.assertEqual(signals[0]["signal_key"], "en:walking pad desk")
        self.assertEqual(signals[0]["recent_channel_count"], 3)
        self.assertEqual(signals[0]["confidence"], "medium")


if __name__ == "__main__":
    unittest.main()
