"""YouTube 搜尋、頻道基準、字幕與留言資料層。"""

from __future__ import annotations

import concurrent.futures
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from googleapiclient.discovery import build

from cache_store import JsonTTLCache
from radar_core import parse_iso_duration, select_diverse_terms, stable_hash


PROBE_WORDS = {
    "zh": {
        "suffix": ["教學", "推薦", "開箱", "實測", "新手", "挑戰"],
        "prefix": ["怎麼"],
    },
    "en": {
        "suffix": ["tutorial", "challenge", "vlog", "for beginners", "tips", "i tried"],
        "prefix": ["how to", "best"],
    },
}


class YouTubeData:
    def __init__(self, api_key: str, cache: JsonTTLCache | None = None) -> None:
        self.api_key = api_key
        self.cache = cache or JsonTTLCache()
        self._usage = {"search_calls": 0, "data_calls": 0, "cache_hits": 0}
        self._lock = threading.Lock()
        self.errors: list[str] = []

    def _hit(self) -> None:
        with self._lock:
            self._usage["cache_hits"] += 1

    def _used(self, kind: str) -> None:
        with self._lock:
            self._usage[kind] += 1

    def usage(self) -> dict[str, int]:
        with self._lock:
            return dict(self._usage)

    def suggestions(self, keyword: str, lang: str) -> list[tuple[str, int]]:
        key = f"autocomplete:{stable_hash([keyword, lang])}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return [(str(item[0]), int(item[1])) for item in cached]
        try:
            response = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "chrome", "ds": "yt", "q": keyword, "hl": lang},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            terms = data[1] if len(data) > 1 else []
            meta = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
            scores = meta.get("google:suggestrelevance", [])
            result = [
                (str(term).strip(), int(scores[index]) if index < len(scores) else 0)
                for index, term in enumerate(terms)
                if str(term).strip()
            ]
            self.cache.set(key, result, 86_400)
            return result
        except Exception as exc:
            self.errors.append(f"autocomplete:{keyword}:{exc}")
            return []

    def autocomplete_batch(self, queries: list[str], lang: str) -> dict[str, int]:
        found: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(self.suggestions, query, lang) for query in queries]
            for future in concurrent.futures.as_completed(futures):
                try:
                    for term, score in future.result():
                        found[term] = max(found.get(term, 0), score)
                except Exception:
                    continue
        return found

    def expand_keywords(
        self,
        seeds: list[str],
        market: str,
        max_rounds: int = 2,
        max_queries: int = 60,
        max_pool: int = 150,
    ) -> tuple[list[tuple[str, int, int]], list[dict[str, Any]]]:
        lang = "zh-TW" if market == "zh" else "en"
        probes = PROBE_WORDS[market]
        pool: dict[str, tuple[int, int]] = {}
        frontier = [seed.strip() for seed in seeds if seed.strip()]
        queries_used = 0
        log: list[dict[str, Any]] = []
        for round_number in range(1, max_rounds + 1):
            queries = list(frontier)
            if round_number == 1:
                for seed in frontier:
                    queries.extend(f"{seed} {probe}" for probe in probes["suffix"])
                    queries.extend(f"{probe} {seed}" for probe in probes["prefix"])
            queries = queries[: max(max_queries - queries_used, 0)]
            if not queries:
                break
            queries_used += len(queries)
            found = self.autocomplete_batch(queries, lang)
            fresh = {term: score for term, score in found.items() if term not in pool}
            for term, score in fresh.items():
                pool[term] = (score, round_number)
            log.append(
                {
                    "round": round_number,
                    "frontier_count": len(frontier),
                    "queries": len(queries),
                    "fresh": len(fresh),
                    "total": len(pool),
                }
            )
            if (
                round_number >= max_rounds
                or len(fresh) < 5
                or len(pool) >= max_pool
                or queries_used >= max_queries
            ):
                break
            frontier = select_diverse_terms(list(fresh.items()), 7)
            if not frontier:
                break
        for seed in seeds:
            if seed.strip():
                pool.setdefault(seed.strip(), (0, 1))
        ranked = sorted(
            ((term, score, round_number) for term, (score, round_number) in pool.items()),
            key=lambda item: (-item[1], item[2], len(item[0])),
        )
        return ranked[:max_pool], log

    def search_videos(
        self,
        query: str,
        market: str,
        published_within_days: int,
        max_results: int,
        order: str = "relevance",
    ) -> list[dict[str, Any]]:
        key = f"search:{stable_hash([query, market, published_within_days, max_results, order])}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return cached
        region = "TW" if market == "zh" else "US"
        language = "zh-Hant" if market == "zh" else "en"
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=published_within_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            search_response = (
                service.search()
                .list(
                    q=query,
                    part="id",
                    maxResults=max_results,
                    type="video",
                    order=order,
                    regionCode=region,
                    relevanceLanguage=language,
                    publishedAfter=published_after,
                )
                .execute()
            )
            self._used("search_calls")
            video_ids = [
                item["id"]["videoId"] for item in search_response.get("items", [])
            ]
            if not video_ids:
                self.cache.set(key, [], 21_600)
                return []
            stats_response = (
                service.videos()
                .list(part="snippet,statistics,contentDetails", id=",".join(video_ids))
                .execute()
            )
            self._used("data_calls")
            result = []
            for item in stats_response.get("items", []):
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                result.append(
                    {
                        "id": item["id"],
                        "title": snippet.get("title", ""),
                        "description": (snippet.get("description") or "")[:500],
                        "channel": snippet.get("channelTitle", ""),
                        "channel_id": snippet.get("channelId", ""),
                        "publish_time": snippet.get("publishedAt", ""),
                        "view_count": int(statistics.get("viewCount", 0) or 0),
                        "like_count": int(statistics.get("likeCount", 0) or 0),
                        "comment_count": int(statistics.get("commentCount", 0) or 0),
                        "duration_min": parse_iso_duration(
                            item.get("contentDetails", {}).get("duration", "")
                        ),
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                        "source_keyword": query,
                        "search_order": order,
                        "market": market,
                    }
                )
            self.cache.set(key, result, 21_600)
            return result
        except Exception as exc:
            self.errors.append(f"search:{query}:{exc}")
            return []

    def channel_profiles(self, channel_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = sorted({channel_id for channel_id in channel_ids if channel_id})
        if not ids:
            return {}
        key = f"channels:{stable_hash(ids)}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return cached
        profiles: dict[str, dict[str, Any]] = {}
        try:
            service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            for index in range(0, len(ids), 50):
                response = (
                    service.channels()
                    .list(
                        part="snippet,statistics,contentDetails",
                        id=",".join(ids[index : index + 50]),
                    )
                    .execute()
                )
                self._used("data_calls")
                for item in response.get("items", []):
                    statistics = item.get("statistics", {})
                    snippet = item.get("snippet", {})
                    profiles[item["id"]] = {
                        "subs": int(statistics.get("subscriberCount", 0) or 0),
                        "video_count": int(statistics.get("videoCount", 0) or 0),
                        "country": snippet.get("country", ""),
                        "uploads_playlist": item.get("contentDetails", {})
                        .get("relatedPlaylists", {})
                        .get("uploads", ""),
                    }
            self.cache.set(key, profiles, 43_200)
            return profiles
        except Exception as exc:
            self.errors.append(f"channels:{exc}")
            return profiles

    def _recent_for_channel(
        self, channel_id: str, profile: dict[str, Any], max_results: int
    ) -> list[dict[str, Any]]:
        key = f"recent:{stable_hash([channel_id, max_results])}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return cached
        playlist_id = profile.get("uploads_playlist", "")
        if not playlist_id:
            return []
        try:
            service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            playlist_response = (
                service.playlistItems()
                .list(part="contentDetails", playlistId=playlist_id, maxResults=max_results)
                .execute()
            )
            self._used("data_calls")
            ids = [
                item.get("contentDetails", {}).get("videoId", "")
                for item in playlist_response.get("items", [])
            ]
            ids = [video_id for video_id in ids if video_id]
            if not ids:
                return []
            videos_response = (
                service.videos()
                .list(part="snippet,statistics,contentDetails", id=",".join(ids))
                .execute()
            )
            self._used("data_calls")
            videos = []
            for item in videos_response.get("items", []):
                statistics = item.get("statistics", {})
                snippet = item.get("snippet", {})
                videos.append(
                    {
                        "id": item["id"],
                        "view_count": int(statistics.get("viewCount", 0) or 0),
                        "publish_time": snippet.get("publishedAt", ""),
                        "duration_min": parse_iso_duration(
                            item.get("contentDetails", {}).get("duration", "")
                        ),
                    }
                )
            self.cache.set(key, videos, 21_600)
            return videos
        except Exception as exc:
            self.errors.append(f"recent:{channel_id}:{exc}")
            return []

    def recent_channel_videos(
        self,
        profiles: dict[str, dict[str, Any]],
        channel_ids: list[str],
        max_results: int = 15,
    ) -> dict[str, list[dict[str, Any]]]:
        targets = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id in profiles]
        output: dict[str, list[dict[str, Any]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    self._recent_for_channel, channel_id, profiles[channel_id], max_results
                ): channel_id
                for channel_id in targets
            }
            for future in concurrent.futures.as_completed(futures):
                channel_id = futures[future]
                try:
                    output[channel_id] = future.result()
                except Exception:
                    output[channel_id] = []
        return output

    def top_comments(self, video_id: str, max_results: int = 20) -> list[dict[str, Any]]:
        key = f"comments:{stable_hash([video_id, max_results])}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return cached
        try:
            service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            response = (
                service.commentThreads()
                .list(
                    part="snippet,replies",
                    videoId=video_id,
                    order="relevance",
                    maxResults=max_results,
                    textFormat="plainText",
                )
                .execute()
            )
            self._used("data_calls")
            comments = []
            for item in response.get("items", []):
                thread = item.get("snippet", {})
                snippet = thread.get("topLevelComment", {}).get("snippet", {})
                reply_samples = []
                for reply in item.get("replies", {}).get("comments", [])[:3]:
                    reply_text = re.sub(
                        r"\s+", " ", str(reply.get("snippet", {}).get("textDisplay", ""))
                    ).strip()
                    if reply_text:
                        reply_samples.append(reply_text[:220])
                comments.append(
                    {
                        "text": snippet.get("textDisplay", ""),
                        "likes": int(snippet.get("likeCount", 0) or 0),
                        "replies": int(thread.get("totalReplyCount", 0) or 0),
                        "reply_samples": reply_samples,
                    }
                )
            self.cache.set(key, comments, 43_200)
            return comments
        except Exception as exc:
            self.errors.append(f"comments:{video_id}:{exc}")
            return []

    def transcript_segments(self, video_id: str, market: str) -> list[dict[str, Any]]:
        key = f"transcript:{stable_hash([video_id, market])}"
        cached = self.cache.get(key)
        if cached is not None:
            self._hit()
            return cached
        languages = (
            ["zh-TW", "zh-Hant", "zh", "zh-CN", "en"]
            if market == "zh"
            else ["en", "en-US", "en-GB", "zh-TW", "zh"]
        )
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            try:
                fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
                snippets = getattr(fetched, "snippets", fetched)
            except AttributeError:
                snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            output = []
            for snippet in snippets:
                if hasattr(snippet, "text"):
                    text = snippet.text
                    start = getattr(snippet, "start", 0)
                    duration = getattr(snippet, "duration", 0)
                else:
                    text = snippet.get("text", "")
                    start = snippet.get("start", 0)
                    duration = snippet.get("duration", 0)
                clean = re.sub(r"\s+", " ", str(text)).strip()
                if clean:
                    output.append(
                        {"text": clean, "start": float(start or 0), "duration": float(duration or 0)}
                    )
            self.cache.set(key, output, 604_800)
            return output
        except Exception as exc:
            self.errors.append(f"transcript:{video_id}:{exc}")
            return []
