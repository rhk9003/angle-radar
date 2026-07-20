"""Angle Radar 的純資料處理核心。

這個模組不碰 Streamlit、Gemini 或網路，讓評分與證據選取可以獨立測試。
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable


MARKET_LABEL = {"zh": "中文樣本", "en": "英文樣本"}
CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}


def parse_iso_duration(duration_str: str) -> float:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0.0
    hours, minutes, seconds = (int(value) if value else 0 for value in match.groups())
    return round(hours * 60 + minutes + seconds / 60, 1)


def video_age_days(publish_time: str, now: datetime | None = None) -> int:
    try:
        published = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max((current - published).days, 1)
    except (TypeError, ValueError):
        return 1


def fmt_num(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}億"
    if number >= 10_000:
        return f"{number / 10_000:.1f}萬"
    return f"{number:,}"


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_brief(
    direction: str,
    audience: str,
    goal: str,
    who: str = "",
    form_pref: str = "不限",
    market_focus: str = "台灣主場＋英文市場找參考",
    duration_pref: str = "不限",
    strengths: str = "",
    exclusions: str = "",
    references: str = "",
    extra: str = "",
) -> dict[str, str]:
    return {
        "direction": direction.strip(),
        "audience": audience.strip(),
        "goal": goal.strip(),
        "creator": who.strip(),
        "format": form_pref.strip(),
        "market_focus": market_focus.strip(),
        "duration": duration_pref.strip(),
        "strengths": strengths.strip(),
        "exclusions": exclusions.strip(),
        "references": references.strip(),
        "extra": extra.strip(),
    }


def brief_quality(brief: dict[str, str]) -> tuple[int, list[str]]:
    weighted = {
        "direction": 25,
        "audience": 20,
        "goal": 15,
        "creator": 10,
        "format": 10,
        "market_focus": 5,
        "duration": 5,
        "strengths": 5,
        "exclusions": 3,
        "references": 2,
    }
    score = 0
    missing: list[str] = []
    labels = {
        "direction": "想拍的主題",
        "audience": "目標觀眾",
        "goal": "拍片目的",
        "creator": "創作者／頻道定位",
        "format": "呈現形式",
        "strengths": "可運用的優勢或素材",
    }
    empty_values = {"", "不限", "請選擇"}
    for field, weight in weighted.items():
        if brief.get(field, "").strip() not in empty_values:
            score += weight
        elif field in labels:
            missing.append(labels[field])
    return min(score, 100), missing


def brief_to_text(brief: dict[str, str], *, include_market: bool = True) -> str:
    rows = [
        ("主題", brief.get("direction")),
        ("目標觀眾", brief.get("audience")),
        ("拍片目的", brief.get("goal")),
        ("創作者定位", brief.get("creator")),
        ("呈現形式", brief.get("format")),
        ("片長", brief.get("duration")),
        ("可運用優勢", brief.get("strengths")),
        ("排除內容", brief.get("exclusions")),
        ("參考對象", brief.get("references")),
        ("其他補充", brief.get("extra")),
    ]
    if include_market:
        rows.insert(5, ("市場", brief.get("market_focus")))
    return "\n".join(f"- {label}：{value}" for label, value in rows if value and value != "不限")


def _normalized_ngrams(text: str, size: int = 2) -> set[str]:
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", (text or "").lower())
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def text_similarity(left: str, right: str) -> float:
    left_set = _normalized_ngrams(left)
    right_set = _normalized_ngrams(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def select_diverse_terms(terms: Iterable[tuple[str, int]], limit: int) -> list[str]:
    selected: list[str] = []
    for term, _score in sorted(terms, key=lambda item: (-item[1], len(item[0]))):
        clean = term.strip()
        if not clean or len(clean) > 90:
            continue
        if any(text_similarity(clean, existing) >= 0.72 for existing in selected):
            continue
        selected.append(clean)
        if len(selected) >= limit:
            break
    return selected


def prefilter_keyword_pool(
    pool: list[tuple[str, int, int]], limit: int = 30
) -> list[tuple[str, int, int]]:
    """先用相關性分數與字面多樣性縮池，避免把 100+ 個字送進模型。"""
    selected: list[tuple[str, int, int]] = []
    for item in sorted(pool, key=lambda value: (-value[1], value[2], len(value[0]))):
        term = item[0].strip()
        if not term or any(text_similarity(term, existing[0]) >= 0.82 for existing in selected):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def build_search_terms(
    plan: dict[str, list[str]],
    zh_pool: list[tuple[str, int, int]],
    en_pool: list[tuple[str, int, int]],
    zh_limit: int = 8,
    en_limit: int = 6,
) -> dict[str, list[dict[str, str]]]:
    """保留 AI 產生的不同意圖，再用 autocomplete 補足字面變化。

    這一步完全是確定性的，避免為了從候選詞再挑一次而多呼叫模型。
    """

    def add(
        selected: list[dict[str, str]], term: Any, intent: str, reason: str, limit: int
    ) -> None:
        clean = str(term or "").strip()
        if not clean or len(selected) >= limit or len(clean) > 90:
            return
        if any(text_similarity(clean, item["kw"]) >= 0.82 for item in selected):
            return
        selected.append({"kw": clean, "intent": intent, "reason": reason})

    zh: list[dict[str, str]] = []
    zh_seed_target = max(1, zh_limit - 2)
    categories = [
        ("core_terms", "核心詞"),
        ("question_terms", "問句"),
        ("problem_terms", "問題"),
        ("adjacent_terms", "相鄰題材"),
    ]
    # 先讓每一類至少有機會出現，再補第二個核心／問句，避免整批都是長問句。
    for offset in range(2):
        for key, label in categories:
            values = plan.get(key, [])
            if offset < len(values):
                add(zh, values[offset], label, "AI 產生的搜尋起點", zh_seed_target)

    for term, _score, _round in zh_pool:
        add(zh, term, "延伸詞", "公開搜尋建議的字面延伸", zh_limit)
    for key, label in categories:
        for term in plan.get(key, [])[2:]:
            add(zh, term, label, "AI 產生的搜尋起點", zh_limit)
    for term, _score, _round in zh_pool:
        add(zh, term, "延伸詞", "公開搜尋建議的字面延伸", zh_limit)

    en: list[dict[str, str]] = []
    en_seed_target = max(1, en_limit - 1)
    for term in plan.get("en_terms", []):
        add(en, term, "英文搜尋詞", "AI 產生的自然英文說法", en_seed_target)
    for term, _score, _round in en_pool:
        add(en, term, "英文延伸詞", "公開搜尋建議的字面延伸", en_limit)
    for term in plan.get("en_terms", []):
        add(en, term, "英文搜尋詞", "AI 產生的自然英文說法", en_limit)

    return {"zh": zh[:zh_limit], "en": en[:en_limit]}


def infer_origin(video: dict[str, Any], channel_country: str = "") -> str:
    country = (channel_country or "").upper()
    if country == "TW":
        return "tw"
    if country == "HK":
        return "hk"
    if country in {"CN", "SG", "MY"}:
        return "zh_other"
    if video.get("market") != "zh":
        return "international"
    text = f"{video.get('title', '')} {video.get('description', '')}"
    traditional_markers = "這個為什麼開箱實測推薦裡麼讓與會後來還沒臺灣台灣"
    simplified_markers = "这个为什么开箱实测推荐里么让与会后来还没"
    traditional = sum(text.count(char) for char in traditional_markers)
    simplified = sum(text.count(char) for char in simplified_markers)
    if traditional > simplified and traditional >= 2:
        return "zh_hant_unknown"
    return "zh_unknown"


def _is_short(video: dict[str, Any]) -> bool:
    duration = float(video.get("duration_min", 0) or 0)
    return 0 < duration <= 1.05


def _percentile(value: float, sorted_values: list[float]) -> float:
    if not sorted_values:
        return 0.0
    return bisect.bisect_right(sorted_values, value) / len(sorted_values)


def attach_outlier_metrics_v2(
    videos: list[dict[str, Any]],
    channel_profiles: dict[str, dict[str, Any]],
    recent_by_channel: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """以近期同格式影片的日均觀看中位數作基準，並保留樣本信心。"""
    recent_by_channel = recent_by_channel or {}
    for video in videos:
        profile = channel_profiles.get(video.get("channel_id", ""), {})
        subscribers = int(profile.get("subs", 0) or 0)
        views = int(video.get("view_count", 0) or 0)
        likes = int(video.get("like_count", 0) or 0)
        comments = int(video.get("comment_count", 0) or 0)
        age = video_age_days(video.get("publish_time", ""))
        views_per_day = views / max(age, 7)
        video["subs"] = subscribers
        video["channel_country"] = profile.get("country", "")
        video["origin"] = infer_origin(video, profile.get("country", ""))
        video["views_per_day"] = int(views_per_day)
        video["vs_subs"] = round(views / subscribers, 2) if subscribers else 0.0
        video["engagement_rate"] = round((likes + comments * 2) / views, 4) if views else 0.0

        references: list[float] = []
        for reference in recent_by_channel.get(video.get("channel_id", ""), []):
            if reference.get("id") == video.get("id") or _is_short(reference) != _is_short(video):
                continue
            reference_age = video_age_days(reference.get("publish_time", ""))
            if reference_age > 540:
                continue
            reference_views = int(reference.get("view_count", 0) or 0)
            if reference_views > 0:
                references.append(reference_views / max(reference_age, 7))

        sample_size = len(references)
        if sample_size >= 3:
            baseline = statistics.median(references)
            ratio = views_per_day / baseline if baseline else 0.0
            confidence = "high" if sample_size >= 8 else "medium" if sample_size >= 5 else "low"
        else:
            baseline = 0.0
            ratio = 0.0
            confidence = "low"
        video["outlier_ratio"] = round(ratio, 1)
        video["baseline_views_per_day"] = int(baseline)
        video["baseline_sample_size"] = sample_size
        video["outlier_confidence"] = confidence
    return assign_evidence_scores(videos)


def assign_evidence_scores(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outliers = sorted(float(video.get("outlier_ratio", 0) or 0) for video in videos)
    velocities = sorted(math.log1p(float(video.get("views_per_day", 0) or 0)) for video in videos)
    subscriber_ratios = sorted(math.log1p(float(video.get("vs_subs", 0) or 0)) for video in videos)
    engagements = sorted(float(video.get("engagement_rate", 0) or 0) for video in videos)
    for video in videos:
        outlier_weight = 0.4 if int(video.get("baseline_sample_size", 0) or 0) >= 3 else 0.15
        velocity_weight = 0.3 + (0.4 - outlier_weight)
        score = (
            outlier_weight * _percentile(float(video.get("outlier_ratio", 0) or 0), outliers)
            + velocity_weight
            * _percentile(math.log1p(float(video.get("views_per_day", 0) or 0)), velocities)
            + 0.15
            * _percentile(math.log1p(float(video.get("vs_subs", 0) or 0)), subscriber_ratios)
            + 0.15 * _percentile(float(video.get("engagement_rate", 0) or 0), engagements)
        )
        video["evidence_score"] = round(score * 100)
    return videos


def pick_videos_diverse(
    videos: list[dict[str, Any]],
    k: int = 6,
    min_views: int = 3_000,
    en_share: float = 0.6,
    video_type: str = "全部",
) -> list[dict[str, Any]]:
    def type_ok(video: dict[str, Any]) -> bool:
        if video_type == "僅長片":
            return not _is_short(video)
        if video_type == "僅 Shorts":
            return _is_short(video)
        return True

    eligible = [
        video
        for video in videos
        if int(video.get("view_count", 0) or 0) >= min_views and type_ok(video)
    ]
    eligible.sort(
        key=lambda video: (
            float(video.get("evidence_score", 0) or 0),
            float(video.get("outlier_ratio", 0) or 0),
            float(video.get("views_per_day", 0) or 0),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    channels: set[str] = set()
    keyword_count: dict[str, int] = {}

    def take(pool: list[dict[str, Any]], amount: int, relaxed: bool = False) -> None:
        for video in pool:
            if len(selected) >= amount:
                return
            channel_id = video.get("channel_id", "")
            keyword = video.get("research_keyword") or video.get("source_keyword", "")
            if video in selected or channel_id in channels or keyword_count.get(keyword, 0) >= 2:
                continue
            if not relaxed and any(
                text_similarity(video.get("title", ""), existing.get("title", "")) >= 0.66
                for existing in selected
            ):
                continue
            selected.append(video)
            channels.add(channel_id)
            keyword_count[keyword] = keyword_count.get(keyword, 0) + 1

    desired_en = min(round(k * en_share), len([v for v in eligible if v.get("market") == "en"]))
    take([video for video in eligible if video.get("market") == "en"], desired_en)
    take(eligible, k)
    if len(selected) < k:
        take(eligible, k, relaxed=True)
    return selected[:k]


def format_timestamp(seconds: float) -> str:
    total = max(int(seconds or 0), 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_transcript_evidence(
    segments: list[dict[str, Any]], max_chars: int = 3_200
) -> str:
    """保留 Hook、等距中段與結尾，輸出帶時間碼的精簡證據。"""
    clean_segments = [
        {
            "start": float(segment.get("start", 0) or 0),
            "text": re.sub(r"\s+", " ", str(segment.get("text", ""))).strip(),
        }
        for segment in segments
        if str(segment.get("text", "")).strip()
    ]
    if not clean_segments:
        return ""

    all_lines = [f"[{format_timestamp(item['start'])}] {item['text']}" for item in clean_segments]
    if len("\n".join(all_lines)) <= max_chars:
        return "\n".join(all_lines)

    last_start = clean_segments[-1]["start"]
    selected_indices = {
        index for index, item in enumerate(clean_segments) if item["start"] <= min(45, last_start)
    }
    selected_indices.update(
        index for index, item in enumerate(clean_segments) if item["start"] >= max(last_start - 60, 0)
    )
    for fraction in (0.2, 0.4, 0.6, 0.8):
        target = last_start * fraction
        closest = min(range(len(clean_segments)), key=lambda idx: abs(clean_segments[idx]["start"] - target))
        selected_indices.update(range(max(0, closest - 1), min(len(clean_segments), closest + 2)))

    prioritized = sorted(
        selected_indices,
        key=lambda index: (
            0 if clean_segments[index]["start"] <= 45 else 1 if clean_segments[index]["start"] >= last_start - 60 else 2,
            clean_segments[index]["start"],
        ),
    )
    kept: list[int] = []
    used = 0
    for index in prioritized:
        line = f"[{format_timestamp(clean_segments[index]['start'])}] {clean_segments[index]['text']}"
        if used + len(line) + 1 > max_chars:
            continue
        kept.append(index)
        used += len(line) + 1
    return "\n".join(
        f"[{format_timestamp(clean_segments[index]['start'])}] {clean_segments[index]['text']}"
        for index in sorted(set(kept))
    )


def compress_comments(
    comments: list[dict[str, Any]], limit: int = 8, max_chars_each: int = 180
) -> list[dict[str, Any]]:
    ranked = sorted(
        comments,
        key=lambda comment: (
            int(comment.get("likes", 0) or 0) + int(comment.get("replies", 0) or 0) * 3
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for comment in ranked:
        text = re.sub(r"\s+", " ", str(comment.get("text", ""))).strip()[:max_chars_each]
        if not text or any(text_similarity(text, item["text"]) >= 0.8 for item in selected):
            continue
        selected.append(
            {
                "text": text,
                "likes": int(comment.get("likes", 0) or 0),
                "replies": int(comment.get("replies", 0) or 0),
                "reply_samples": [
                    re.sub(r"\s+", " ", str(reply)).strip()[:140]
                    for reply in comment.get("reply_samples", [])[:2]
                    if str(reply).strip()
                ],
            }
        )
        if len(selected) >= limit:
            break
    return selected


def market_coverage(videos: list[dict[str, Any]]) -> dict[str, Any]:
    zh = [video for video in videos if video.get("market") == "zh"]
    origin_counts: dict[str, int] = {}
    for video in zh:
        origin = video.get("origin", "unknown")
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    tw_count = origin_counts.get("tw", 0)
    sample_size = len(zh)
    confidence = "high" if tw_count >= 8 else "medium" if tw_count >= 4 else "low"
    return {
        "zh_sample_size": sample_size,
        "tw_confirmed_channels": tw_count,
        "origin_counts": origin_counts,
        "confidence": confidence,
        "warning": (
            "台灣來源樣本不足，只能解讀為繁中搜尋樣本。"
            if confidence == "low"
            else "台灣來源已有一定樣本，仍不代表完整市場。"
        ),
    }


def derive_rising_signals(
    videos: list[dict[str, Any]],
    observed_velocity_by_id: dict[str, float] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """以跨頻道近期密度、速度與相對基準產生「竄升訊號」，不把單支熱門誤稱趨勢。"""
    observed_velocity_by_id = observed_velocity_by_id or {}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for video in videos:
        keyword = str(
            video.get("research_keyword") or video.get("source_keyword") or ""
        ).strip()
        if keyword:
            groups.setdefault((video.get("market", ""), keyword), []).append(video)

    signals: list[dict[str, Any]] = []
    for (market, keyword), group in groups.items():
        recent = [video for video in group if video_age_days(video.get("publish_time", "")) <= 60]
        previous = [
            video
            for video in group
            if 60 < video_age_days(video.get("publish_time", "")) <= 240
        ]
        channels = {video.get("channel_id", "") for video in recent if video.get("channel_id")}
        if len(recent) < 2 or len(channels) < 2:
            continue

        recent_velocity = [
            float(observed_velocity_by_id.get(video.get("id", ""), video.get("views_per_day", 0)) or 0)
            for video in recent
        ]
        previous_velocity = [float(video.get("views_per_day", 0) or 0) for video in previous]
        median_velocity = statistics.median(recent_velocity) if recent_velocity else 0.0
        previous_median = statistics.median(previous_velocity) if len(previous_velocity) >= 2 else 0.0
        acceleration = median_velocity / previous_median if previous_median else None
        outliers = [
            float(video.get("outlier_ratio", 0) or 0)
            for video in recent
            if float(video.get("outlier_ratio", 0) or 0) > 0
        ]
        median_outlier = statistics.median(outliers) if outliers else 0.0
        historical_count = sum(video.get("id") in observed_velocity_by_id for video in recent)
        score = (
            min(math.log10(median_velocity + 1) / 4, 1) * 35
            + min(len(channels) / 4, 1) * 25
            + min(median_outlier / 5, 1) * 20
            + (min(math.log2(acceleration + 1) / 2, 1) * 20 if acceleration else 5)
        )
        if historical_count >= 2 and len(channels) >= 3:
            confidence = "high"
        elif len(channels) >= 3 or (acceleration is not None and len(previous) >= 2):
            confidence = "medium"
        else:
            confidence = "low"
        sources = sorted(
            recent,
            key=lambda video: (
                observed_velocity_by_id.get(video.get("id", ""), video.get("views_per_day", 0)),
                video.get("evidence_score", 0),
            ),
            reverse=True,
        )[:3]
        signals.append(
            {
                "signal_key": f"{market}:{keyword}",
                "keyword": keyword,
                "market": market,
                "recent_video_count": len(recent),
                "recent_channel_count": len(channels),
                "median_daily_velocity": int(median_velocity),
                "acceleration_vs_older": round(acceleration, 2) if acceleration else None,
                "median_outlier": round(median_outlier, 1),
                "historical_velocity_samples": historical_count,
                "confidence": confidence,
                "signal_score": round(score),
                "source_ids": [video.get("id", "") for video in sources],
            }
        )
    return sorted(signals, key=lambda signal: signal["signal_score"], reverse=True)[:limit]
