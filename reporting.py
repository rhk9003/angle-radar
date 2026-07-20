"""結構化 Gemini schema、prompt 與安全的公開報告渲染。"""

from __future__ import annotations

import json
import re
from typing import Any

from radar_core import CONFIDENCE_LABEL, fmt_num


KEYWORD_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "core_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "question_terms": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "problem_terms": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "adjacent_terms": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "en_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 7},
        "negative_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
        },
    },
    "required": [
        "core_terms",
        "question_terms",
        "problem_terms",
        "adjacent_terms",
        "en_terms",
        "negative_keywords",
    ],
}


RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "irrelevant_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["irrelevant_ids"],
}


BREAKDOWN_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "video_id": {"type": "string"},
        "topic": {"type": "string"},
        "hook": {"type": "string"},
        "hook_timestamp": {"type": "string"},
        "structure": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "breakout_reasons": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "reusable_angles": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "comment_gaps": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "evidence_notes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "video_id",
        "topic",
        "hook",
        "hook_timestamp",
        "structure",
        "breakout_reasons",
        "reusable_angles",
        "comment_gaps",
        "evidence_notes",
        "confidence",
    ],
}


BREAKDOWN_BATCH_SCHEMA = {
    "type": "object",
    "properties": {"videos": {"type": "array", "items": BREAKDOWN_ITEM_SCHEMA}},
    "required": ["videos"],
}


ANGLE_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "radar_summary": {"type": "string"},
        "angles": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "angle_name": {"type": "string"},
                    "opportunity": {"type": "string"},
                    "signal": {"type": "string"},
                    "internal_signal_type": {
                        "type": "string",
                        "enum": [
                            "cross_context_adaptation",
                            "audience_gap",
                            "momentum_extension",
                            "rising_topic",
                            "other",
                        ],
                    },
                    "route_note": {"type": "string"},
                    "evidence_video_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                    "comment_gap": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "caution": {"type": "string"},
                },
                "required": [
                    "angle_name",
                    "opportunity",
                    "signal",
                    "internal_signal_type",
                    "route_note",
                    "evidence_video_ids",
                    "comment_gap",
                    "confidence",
                    "caution",
                ],
            },
        },
    },
    "required": ["radar_summary", "angles"],
}


_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\uFE0F"
    "]+",
    flags=re.UNICODE,
)


def _plain_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", str(value or ""))).strip()


def _public_generated_text(value: Any) -> str:
    text = _plain_text(value)
    for phrase in ("國內市場", "國外市場", "中文市場", "英文市場", "台灣市場"):
        text = text.replace(phrase, "現有內容")
    return text


def _topic_context(topic: str, exclusions: str = "", references: str = "") -> str:
    rows = [f"- 想拍的主題：{topic.strip()}"]
    if exclusions.strip():
        rows.append(f"- 不想看到：{exclusions.strip()}")
    if references.strip():
        rows.append(f"- 使用者提供的參考：{references.strip()}")
    return "\n".join(rows)


def keyword_plan_prompt(topic: str, exclusions: str = "", references: str = "") -> str:
    """一次產生搜尋起點；後續不再用模型挑第二輪關鍵字。"""
    return f"""
你是 YouTube 題材研究員。請把使用者想拍的內容轉成一輪可直接搜尋的關鍵字起點。

{_topic_context(topic, exclusions, references)}

要求：
- core_terms：3–5 個短而直接的核心詞，例如「算命」「算命創業」，不能全部寫成長句。
- question_terms：4–6 個真實使用者可能搜尋的問句，混合「如何」「怎麼」「為什麼」「值不值得」「要不要」等句型，但不要機械套模板。
- problem_terms：3–5 個卡關、風險、比較、失敗或爭議相關詞。
- adjacent_terms：2–4 個與主題有明確關係、但不是同義改寫的鄰近題材。
- en_terms：4–7 個英文內容圈真正常用的搜尋說法，不可逐字直譯中文。
- negative_keywords：只放明顯同字異義，或使用者明確排除的詞；沒有就回空陣列。
- 每組內避免同義詞堆疊。關鍵字只負責探索，不要在這一步提出拍攝企劃。
""".strip()


def relevance_prompt(
    topic: str,
    videos: list[dict[str, Any]],
    exclusions: str = "",
    references: str = "",
) -> str:
    rows = []
    for video in videos:
        description = " ".join((video.get("description") or "").split())[:140]
        rows.append(
            f"{video['id']}｜{video.get('research_keyword') or video.get('source_keyword', '')}｜"
            f"{video.get('title', '')}｜"
            f"{video.get('channel', '')}｜{description}"
        )
    return f"""
判斷下列 YouTube 搜尋結果是否和使用者想拍的主題有明確關聯。

{_topic_context(topic, exclusions, references)}

資料格式：id｜查詢詞｜標題｜頻道｜簡介
{chr(10).join(rows)}

只列出明顯同字異義或完全不同題材的 irrelevant_ids。模糊地帶保留，不得因觀點不同而刪除。
""".strip()


def breakdown_batch_prompt(packets: list[dict[str, Any]]) -> str:
    compact = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
    return f"""
你是影音內容證據分析員。請客觀拆解每支影片中可驗證的內容證據，供後續找切角使用。

影片證據包：
{compact}

規則：
- 只能根據證據包內容，不得補寫沒出現的畫面、台詞或數據。
- hook 優先引用 00:00–00:45 的時間化字幕；沒有就明講資訊不足。
- hook_timestamp 必須填證據包內存在的 MM:SS，找不到填空字串。
- breakout_reasons 要區分數據表現與內容機制推測。
- comment_gaps 只能摘錄或緊密改寫留言中的追問、爭論與未解問題；沒有就回空陣列。
- reusable_angles 只寫來源內容能支持的延伸方向，不要產出完整企劃。
- evidence_notes 寫支撐判斷的時間碼或留言訊號，保持精簡。
- confidence 依字幕、留言及基準樣本完整度判斷。
""".strip()


def _signal_inventory(
    pool_videos: list[dict[str, Any]],
    breakdowns: list[dict[str, Any]],
    rising_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    breakdown_ids = {
        str(item.get("video_id", "")) for item in breakdowns if item.get("video_id")
    }
    ranked = sorted(
        pool_videos,
        key=lambda item: (item.get("evidence_score", 0), item.get("views_per_day", 0)),
        reverse=True,
    )
    adaptation_ids = [
        str(video.get("id", ""))
        for video in ranked
        if video.get("market") == "en" and str(video.get("id", "")) in breakdown_ids
    ][:8]
    gap_sources = [
        {
            "video_id": str(item.get("video_id", "")),
            "comment_gaps": item.get("comment_gaps", []),
        }
        for item in breakdowns
        if item.get("video_id") and item.get("comment_gaps")
    ]
    rising_ids = list(
        dict.fromkeys(
            str(video_id)
            for signal in rising_signals
            for video_id in signal.get("source_ids", [])
            if video_id
        )
    )
    momentum_ids = [
        str(video.get("id", ""))
        for video in ranked
        if video.get("id")
        and int(video.get("view_count", 0) or 0) >= 3_000
        and (
            int(video.get("views_per_day", 0) or 0) >= 200
            or (
                int(video.get("baseline_sample_size", 0) or 0) >= 3
                and float(video.get("outlier_ratio", 0) or 0) >= 2
            )
        )
    ][:10]
    momentum_ids = list(dict.fromkeys([*rising_ids, *momentum_ids]))[:10]
    return {
        "cross_context_adaptation_ids": adaptation_ids,
        "audience_gap_sources": gap_sources,
        "momentum_extension_ids": momentum_ids,
        "rising_topic_ids": rising_ids,
        "rising_signals": rising_signals,
    }


def angle_report_prompt(
    topic: str,
    selected_keywords: dict[str, list[dict[str, Any]]],
    breakdowns: list[dict[str, Any]],
    pool_videos: list[dict[str, Any]],
    rising_signals: list[dict[str, Any]],
    n_angles: int,
    exclusions: str = "",
    references: str = "",
) -> str:
    keyword_summary = {
        group: [
            {"kw": item.get("kw", ""), "intent": item.get("intent", "")}
            for item in items
        ]
        for group, items in selected_keywords.items()
    }
    pool = []
    for video in sorted(
        pool_videos,
        key=lambda item: (item.get("evidence_score", 0), item.get("views_per_day", 0)),
        reverse=True,
    )[:28]:
        pool.append(
            {
                "id": video.get("id"),
                "title": video.get("title"),
                "language_group": video.get("market"),
                "views": video.get("view_count"),
                "views_per_day": video.get("views_per_day"),
                "relative_baseline": video.get("outlier_ratio"),
                "baseline_n": video.get("baseline_sample_size"),
                "evidence_score": video.get("evidence_score"),
            }
        )
    stable_context = json.dumps(
        {
            "topic": topic,
            "exclusions": exclusions,
            "references": references,
            "keywords": keyword_summary,
            "video_evidence": breakdowns,
            "candidate_videos": pool,
            "signal_inventory": _signal_inventory(
                pool_videos, breakdowns, rising_signals
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
你是內容研究編輯。工具的價值是找出使用者自己要花時間搜尋、讀字幕與留言才會發現的內容切角，不是替他完成拍片企劃。

研究素材：
{stable_context}

請為「{topic}」整理 {n_angles} 個有證據可追查的切角，按值得進一步研究的程度排序。

規則：
- angle_name 是清楚、可辨識的研究切角，不是聳動標題。
- opportunity 說明這個切角真正要追問什麼，以及和泛泛談主題有何不同。
- signal 必須寫出素材支持的具體研究結論，例如相對頻道基準、近期觀看速度、跨頻道出現、字幕內容或留言追問；禁止只寫「觀眾可能有興趣」。
- internal_signal_type 是內部欄位，不會對使用者顯示。依 signal_inventory 分成：
  - cross_context_adaptation：其他內容情境已成立，可重新詮釋成使用者自己的案例；不可逐字照搬。
  - audience_gap：來源留言明確追問，但原內容尚未充分回答。
  - momentum_extension：沿著高動能內容繼續拍，找下一題、反方、更新、續集或情境版。
  - rising_topic：跨來源的早期抬頭訊號，還不等於已成熱門。
  - other：有證據但不屬於以上四類。
- route_note 說明如何把該線索轉成新的研究方向。這是公開字串，不能出現上述內部代碼、方法名稱或市場分組。
- evidence_video_ids 放 1–3 個素材中真實存在的影片 id；來源不足就誠實降低 confidence。
- comment_gap 若使用，只能逐字複製 evidence_video_ids 對應 breakdown 的 comment_gaps；沒有就填空字串。
- caution 說明證據限制或使用者深化前應自行確認的事。
- 若 signal_inventory 有足夠來源，{n_angles} 個切角至少包含：2 個 cross_context_adaptation、2 個 audience_gap、2 個 momentum_extension、1 個 rising_topic；剩餘名額再選最佳證據。某類證據不足才可少於此數，不能用 other 逃避分配。
- audience_gap 的 comment_gap 不得為空；rising_topic 必須引用 signal_inventory.rising_topic_ids；另外兩類也必須引用各自清單內的來源。
- momentum_extension 的 route_note 要直接說明「沿著哪個熱門內容，下一支可以接著談什麼」，不可只寫值得關注。
- 角度要有差異，不要只改寫同一句話。同一來源可以支撐不同方向，但 route_note 必須明確不同。
- 不要提供片名、腳本、開場、拍攝形式、發布順序、導購建議或完整拍片方案。
- 不要向使用者揭露搜尋流程、評分方式、內部策略分類或市場分組。
- 公開字串不要寫「國內市場」「國外市場」「英文市場」「中文市場」；只描述內容、觀眾問題與可追查的來源。
- 公開字串禁止使用 emoji；全部使用繁體中文、短句，每個欄位最多兩句。
""".strip()


def validate_angle_evidence(
    report: dict[str, Any],
    pool_videos: list[dict[str, Any]],
    breakdowns: list[dict[str, Any]],
    rising_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把來源 ID 與留言缺口鎖回實際取得的證據。"""
    videos = {str(video.get("id", "")): video for video in pool_videos}
    breakdown_map = {
        str(breakdown.get("video_id", "")): breakdown for breakdown in breakdowns
    }
    inventory = _signal_inventory(pool_videos, breakdowns, rising_signals or [])
    eligible = {
        "cross_context_adaptation": set(inventory["cross_context_adaptation_ids"]),
        "audience_gap": {
            item["video_id"] for item in inventory["audience_gap_sources"]
        },
        "momentum_extension": set(inventory["momentum_extension_ids"]),
        "rising_topic": set(inventory["rising_topic_ids"]),
    }
    validated_angles = []
    for angle in report.get("angles", []):
        valid_ids = []
        for video_id in angle.get("evidence_video_ids", []):
            clean_id = str(video_id)
            if clean_id in videos and clean_id not in valid_ids:
                valid_ids.append(clean_id)
        angle["evidence_video_ids"] = valid_ids[:3]
        if not valid_ids:
            angle["confidence"] = "low"
            angle["signal"] = "本次樣本沒有足夠直接來源支持這個判斷。"

        allowed_gaps = {
            _plain_text(gap)
            for video_id in valid_ids
            for gap in breakdown_map.get(video_id, {}).get("comment_gaps", [])
            if _plain_text(gap)
        }
        gap = _plain_text(angle.get("comment_gap", ""))
        angle["comment_gap"] = gap if gap in allowed_gaps else ""
        signal_type = str(angle.get("internal_signal_type", "other"))
        route_valid = signal_type == "other" or bool(
            set(valid_ids) & eligible.get(signal_type, set())
        )
        if signal_type == "audience_gap" and not angle["comment_gap"]:
            route_valid = False
        if not route_valid:
            angle["internal_signal_type"] = "other"
            angle["route_note"] = "這個方向仍可研究，但本次資料不足以支持更明確的延伸判斷。"
            angle["confidence"] = "low"
        validated_angles.append(angle)

    report["angles"] = validated_angles
    return report


def _source_line(video: dict[str, Any]) -> str:
    title = _public_generated_text(video.get("title", "參考影片"))
    details = [f"觀看 {fmt_num(video.get('view_count', 0))}"]
    if int(video.get("views_per_day", 0) or 0) > 0:
        details.append(f"日均約 {fmt_num(video.get('views_per_day', 0))}")
    if int(video.get("baseline_sample_size", 0) or 0) >= 3:
        details.append(
            f"近期同頻道基準的 {video.get('outlier_ratio', 0)} 倍"
            f"（{video.get('baseline_sample_size', 0)} 支樣本）"
        )
    return f"[{title}]({video.get('url', '')})｜{'｜'.join(details)}"


_PUBLIC_SIGNAL_LABEL = {
    "cross_context_adaptation": "已有題材的新版本",
    "audience_gap": "觀眾未解問題",
    "momentum_extension": "熱門內容的延伸",
    "rising_topic": "正在抬頭的話題",
    "other": "其他證據型切角",
}

_PUBLIC_ROUTE_LABEL = {
    "cross_context_adaptation": "可轉成你的版本",
    "audience_gap": "可以補上的回答",
    "momentum_extension": "沿著熱門話題可以接著談",
    "rising_topic": "值得提早研究",
    "other": "可深入的方向",
}


def _rising_conclusion(
    angle: dict[str, Any], rising_signals: list[dict[str, Any]]
) -> str:
    evidence_ids = set(map(str, angle.get("evidence_video_ids", [])))
    for signal in rising_signals:
        if evidence_ids & set(map(str, signal.get("source_ids", []))):
            parts = [
                f"近 60 天樣本有 {signal.get('recent_video_count', 0)} 支相關內容",
                f"來自 {signal.get('recent_channel_count', 0)} 個不同頻道",
                f"日均觀看中位數約 {fmt_num(signal.get('median_daily_velocity', 0))}",
            ]
            if signal.get("acceleration_vs_older"):
                parts.append(f"約為較早樣本的 {signal.get('acceleration_vs_older')} 倍")
            return "；".join(parts) + "。"
    return ""


def _content_conclusion(
    angle: dict[str, Any], breakdowns: list[dict[str, Any]]
) -> str:
    breakdown_map = {
        str(item.get("video_id", "")): item for item in breakdowns
    }
    for video_id in map(str, angle.get("evidence_video_ids", [])):
        breakdown = breakdown_map.get(video_id, {})
        parts = []
        breakout = breakdown.get("breakout_reasons", [])
        reusable = breakdown.get("reusable_angles", [])
        if breakout:
            parts.append(_public_generated_text(breakout[0]))
        if reusable:
            parts.append(f"可延伸為：{_public_generated_text(reusable[0])}")
        if parts:
            return "；".join(parts) + "。"
    return ""


def render_angle_report(
    report: dict[str, Any],
    topic: str,
    pool_videos: list[dict[str, Any]],
    rising_signals: list[dict[str, Any]] | None = None,
    breakdowns: list[dict[str, Any]] | None = None,
) -> str:
    videos = {str(video.get("id", "")): video for video in pool_videos}
    signal_types = list(
        dict.fromkeys(
            str(angle.get("internal_signal_type", "other"))
            for angle in report.get("angles", [])
        )
    )
    coverage = "、".join(
        _PUBLIC_SIGNAL_LABEL.get(signal_type, _PUBLIC_SIGNAL_LABEL["other"])
        for signal_type in signal_types
    )
    lines = [
        f"# 「{_plain_text(topic)}」切角雷達",
        "",
        "以下是值得探索的內容切角，不代表已證實的市場需求。",
        "",
        _public_generated_text(report.get("radar_summary", "")),
        "",
        f"**本次找到的線索**：{coverage}",
    ]
    for index, angle in enumerate(report.get("angles", []), start=1):
        lines.extend(
            [
                "",
                f"## {index}. {_public_generated_text(angle.get('angle_name', '未命名切角'))}",
                "",
                f"**這個切角**：{_public_generated_text(angle.get('opportunity', ''))}",
                "",
                f"**這個切角從哪裡挖到**：{_public_generated_text(angle.get('signal', ''))}",
                "",
                f"**{_PUBLIC_ROUTE_LABEL.get(str(angle.get('internal_signal_type', 'other')), _PUBLIC_ROUTE_LABEL['other'])}**："
                f"{_public_generated_text(angle.get('route_note', ''))}",
            ]
        )
        if angle.get("comment_gap"):
            lines.extend(
                ["", f"**觀眾留下的問題**：{_public_generated_text(angle.get('comment_gap', ''))}"]
            )
        content_text = _content_conclusion(angle, breakdowns or [])
        if content_text:
            lines.extend(["", f"**來源內容結論**：{content_text}"])
        sources = [
            videos[video_id]
            for video_id in angle.get("evidence_video_ids", [])
            if video_id in videos
        ]
        if sources:
            lines.extend(["", "**參考來源**："])
            lines.extend(f"- {_source_line(video)}" for video in sources)
        else:
            lines.extend(["", "**參考來源**：本次樣本沒有足夠直接來源。"])
        if angle.get("internal_signal_type") == "rising_topic":
            rising_text = _rising_conclusion(angle, rising_signals or [])
            if rising_text:
                lines.extend(["", f"**近期資料**：{rising_text}"])
        lines.extend(
            [
                "",
                f"**線索完整度**：{CONFIDENCE_LABEL.get(angle.get('confidence'), '低')}",
                "",
                f"**深化前先確認**：{_public_generated_text(angle.get('caution', ''))}",
            ]
        )
    return "\n".join(lines)


def angle_development_prompt(
    topic: str,
    angle: dict[str, Any],
    pool_videos: list[dict[str, Any]],
) -> str:
    """公開給使用者帶走的單一卡 Prompt，不包含內部研究方法。"""
    videos = {str(video.get("id", "")): video for video in pool_videos}
    sources = []
    for video_id in angle.get("evidence_video_ids", []):
        video = videos.get(str(video_id))
        if video:
            sources.append(
                f"- {_public_generated_text(video.get('title', ''))}：{video.get('url', '')}"
            )
    source_text = "\n".join(sources) or "- 暫無直接來源，請先把這個切角視為待驗證假設"
    comment_gap = _public_generated_text(angle.get("comment_gap", "")) or "本次沒有足夠留言證據"
    return f"""
我想把下面這個內容切角，發展成適合我的影片。請先閱讀我補充的資料，再給建議；不要自行假設我的經歷、觀眾或可用素材。

原始主題：{_plain_text(topic)}
這次要深化的切角：{_public_generated_text(angle.get('angle_name', ''))}
切角說明：{_public_generated_text(angle.get('opportunity', ''))}
這個切角從哪裡挖到：{_public_generated_text(angle.get('signal', ''))}
建議深化重點：{_public_generated_text(angle.get('route_note', ''))}
觀眾留下的問題：{comment_gap}

參考來源：
{source_text}

我的相關資料：
- 我的頻道定位：
- 我的專業、經驗或觀點：
- 我最想服務的觀眾：
- 我能提供的案例、數據或素材：
- 我希望觀眾看完採取的行動：
- 拍攝時間、形式或其他限制：

請依照我實際提供的資料完成：
1. 判斷這個切角是否適合我，指出還缺哪些關鍵資訊。
2. 提出一個只有我比較能成立的核心觀點，避免照抄參考來源。
3. 給三組片名與縮圖文字，並說明各自承諾的觀看價值。
4. 設計開場、內容段落與收尾；每一段標示需要的案例或證據。
5. 指出哪些說法容易變成空泛推測，應如何查證或改寫。
6. 收斂成一份能在我的限制內完成的拍攝大綱。

不要虛構數據、經歷、觀眾回饋或來源內容。不確定時，直接列出要我補充的問題。
""".strip()


def render_breakdown(breakdown: dict[str, Any]) -> str:
    timestamp = (
        f"（{breakdown.get('hook_timestamp')}）" if breakdown.get("hook_timestamp") else ""
    )
    lines = [
        f"**一句話主題**：{_plain_text(breakdown.get('topic', ''))}",
        f"**開場 Hook**：{_plain_text(breakdown.get('hook', ''))} {timestamp}",
        "**內容結構**：" + " → ".join(map(_plain_text, breakdown.get("structure", []))),
        "**可能跑出的原因**：" + "；".join(map(_plain_text, breakdown.get("breakout_reasons", []))),
        "**可延伸的角度**：" + "；".join(map(_plain_text, breakdown.get("reusable_angles", []))),
        "**留言缺口**："
        + ("；".join(map(_plain_text, breakdown.get("comment_gaps", []))) or "沒有足夠留言證據"),
        f"**線索完整度**：{CONFIDENCE_LABEL.get(breakdown.get('confidence'), '低')}",
    ]
    return "\n\n".join(lines)


def build_public_export(
    rendered_report: str,
    report: dict[str, Any],
    pool_videos: list[dict[str, Any]],
    created_at: str,
    topic: str,
) -> str:
    """匯出成果、單一卡 Prompt 與引用，不包含研究流程及中間資料。"""
    videos = {str(video.get("id", "")): video for video in pool_videos}
    cited_ids = {
        str(video_id)
        for angle in report.get("angles", [])
        for video_id in angle.get("evidence_video_ids", [])
    }
    cited = [videos[video_id] for video_id in cited_ids if video_id in videos]
    lines = [rendered_report, "", "---", "", "# 把切角交給你的 AI", ""]
    for index, angle in enumerate(report.get("angles", []), start=1):
        lines.extend(
            [
                f"## {index}. {_public_generated_text(angle.get('angle_name', '未命名切角'))}",
                "",
                "```text",
                angle_development_prompt(topic, angle, pool_videos),
                "```",
                "",
            ]
        )
    lines.extend(["---", "", "# 本次引用來源", ""])
    for video in sorted(cited, key=lambda item: item.get("evidence_score", 0), reverse=True):
        lines.append(
            f"- [{_public_generated_text(video.get('title', ''))}]({video.get('url', '')})｜"
            f"{_public_generated_text(video.get('channel', ''))}｜觀看 {fmt_num(video.get('view_count', 0))}"
        )
    lines.extend(["", f"_生成時間：{created_at}_"])
    return "\n".join(lines)
