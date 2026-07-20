import tempfile
import unittest
from pathlib import Path

from cache_store import JsonTTLCache


class TrendObservationTests(unittest.TestCase):
    def test_snapshot_returns_observed_daily_velocity_after_six_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = JsonTTLCache(str(Path(directory) / "cache.sqlite3"))
            video = {
                "id": "video-1",
                "research_keyword": "walking pad desk",
                "market": "en",
                "view_count": 10_000,
                "publish_time": "2026-07-01T00:00:00Z",
            }
            first = cache.record_video_observations([video], now=100_000)
            self.assertEqual(first, {})

            video["view_count"] = 10_700
            second = cache.record_video_observations(
                [video], min_velocity_hours=6, now=100_000 + 7 * 3_600
            )
            self.assertAlmostEqual(second["video-1"], 2_400)


if __name__ == "__main__":
    unittest.main()
