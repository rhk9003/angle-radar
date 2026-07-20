import unittest
from datetime import datetime, timedelta, timezone

from radar_core import (
    attach_outlier_metrics_v2,
    brief_quality,
    build_brief,
    build_transcript_evidence,
    compress_comments,
    derive_rising_signals,
    pick_videos_diverse,
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
