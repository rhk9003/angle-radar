import unittest

from youtube_data import YouTubeData


class FakeYouTubeData(YouTubeData):
    def __init__(self):
        super().__init__("test-key")
        self.orders_seen = []

    def _comments_for_order(self, video_id, order, max_results):
        self.orders_seen.append((video_id, order, max_results))
        shared = {
            "comment_id": "shared",
            "ref": f"{video_id}:shared",
            "text": "共同留言",
            "likes": 5,
            "replies": 1,
            "reply_samples": [f"{order} reply"],
            "source_orders": [order],
        }
        unique = {
            "comment_id": order,
            "ref": f"{video_id}:{order}",
            "text": f"{order} only",
            "likes": 1,
            "replies": 0,
            "reply_samples": [],
            "source_orders": [order],
        }
        return [shared, unique]


class YouTubeCommentSamplingTests(unittest.TestCase):
    def test_relevance_and_time_are_both_sampled_and_deduplicated(self):
        youtube = FakeYouTubeData()
        comments = youtube.sample_comments("video-1", 40)
        self.assertEqual(
            {order for _video_id, order, _limit in youtube.orders_seen},
            {"relevance", "time"},
        )
        self.assertEqual(len(comments), 3)
        shared = next(item for item in comments if item["comment_id"] == "shared")
        self.assertEqual(set(shared["source_orders"]), {"relevance", "time"})
        self.assertEqual(len(shared["reply_samples"]), 2)

    def test_usage_estimates_search_quota_separately(self):
        youtube = FakeYouTubeData()
        youtube._used("search_calls")
        youtube._used("data_calls")
        self.assertEqual(youtube.usage()["estimated_quota_units"], 101)


if __name__ == "__main__":
    unittest.main()
