"""小型 JSON TTL 快取，降低重複 YouTube 與 Gemini 呼叫。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any


class JsonTTLCache:
    def __init__(self, path: str | None = None) -> None:
        default_path = Path(tempfile.gettempdir()) / "angle_radar_v2_cache.sqlite3"
        self.path = str(path or os.getenv("ANGLE_RADAR_CACHE_DB", default_path))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trend_observations (
                    observed_at REAL NOT NULL,
                    video_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    market TEXT NOT NULL,
                    view_count INTEGER NOT NULL,
                    publish_time TEXT NOT NULL,
                    PRIMARY KEY (video_id, observed_at)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS trend_observations_video_time
                ON trend_observations(video_id, observed_at DESC)
                """
            )

    def get(self, key: str) -> Any | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, created_at, ttl_seconds FROM cache_entries WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            payload, created_at, ttl_seconds = row
            if time.time() - float(created_at) > int(ttl_seconds):
                connection.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                connection.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cache_entries(cache_key, payload, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at,
                    ttl_seconds = excluded.ttl_seconds
                """,
                (key, payload, time.time(), ttl_seconds),
            )

    def delete_prefix(self, prefix: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM cache_entries WHERE cache_key LIKE ?", (f"{prefix}%",))

    def record_video_observations(
        self,
        videos: list[dict[str, Any]],
        min_velocity_hours: float = 6,
        now: float | None = None,
    ) -> dict[str, float]:
        """留下觀看快照；間隔足夠時回傳實際新增觀看的日速度。"""
        observed_at = float(now if now is not None else time.time())
        velocities: dict[str, float] = {}
        with self._connect() as connection:
            for video in videos:
                video_id = str(video.get("id", "")).strip()
                if not video_id:
                    continue
                view_count = int(video.get("view_count", 0) or 0)
                latest = connection.execute(
                    """
                    SELECT observed_at, view_count
                    FROM trend_observations
                    WHERE video_id = ?
                    ORDER BY observed_at DESC
                    LIMIT 1
                    """,
                    (video_id,),
                ).fetchone()
                if latest:
                    elapsed_hours = max((observed_at - float(latest[0])) / 3_600, 0)
                    if elapsed_hours >= min_velocity_hours:
                        gained = max(view_count - int(latest[1]), 0)
                        velocities[video_id] = gained / elapsed_hours * 24

                # 一小時內重跑不新增近乎相同的快照，避免資料膨脹。
                should_insert = not latest or observed_at - float(latest[0]) >= 3_600
                if should_insert:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO trend_observations(
                            observed_at, video_id, keyword, market, view_count, publish_time
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observed_at,
                            video_id,
                            str(
                                video.get("research_keyword")
                                or video.get("source_keyword")
                                or ""
                            ),
                            str(video.get("market", "")),
                            view_count,
                            str(video.get("publish_time", "")),
                        ),
                    )
            connection.execute(
                "DELETE FROM trend_observations WHERE observed_at < ?",
                (observed_at - 90 * 86_400,),
            )
        return velocities
