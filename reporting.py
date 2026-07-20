"""結構化 Gemini schema、prompt 與安全的報告渲染。"""

from __future__ import annotations

import json
from typing import Any

from radar_core import CONFIDENCE_LABEL, MARKET_LABEL, brief_to_text, fmt_num


RESEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "zh_seeds": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "en_seeds": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "negative_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "intent_buckets": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "zh_query": {"type": "string"},
                    "en_query": {"type": "string"},
                },
                "required": ["intent", "zh_query", "en_query"],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["zh_seeds", "en_seeds", "negative_keywords", "intent_buckets", "assumptions"],
}


KEYWORD_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "zh": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kw": {"type": "string"},
                    "reason": {"type": "string"},
                    "intent": {"type": "string"},
                },
                "required": ["kw", "reason", "intent"],
            },
        },
        "en": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kw": {"type": "string"},
                    "reason": {"type": "string"},
                    "intent": {"type": "string"},
                },
                "required": ["kw", "reason", "intent"],
            },
        },
    },
    "required": ["zh", "en"],
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


MENU_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunity_summary": {"type": "string"},
        "rising_topics": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "signal_key": {"type": "string"},
                    "topic": {"type": "string"},
                    "why_rising": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "suggested_move": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                },
                "required": [
                    "signal_key",
                    "topic",
                    "why_rising",
                    "confidence",
                    "suggested_move",
                    "source_ids",
                ],
            },
        },
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "plan_name": {"type": "string"},
                    "what_to_shoot": {"type": "string"},
                    "format": {"type": "string"},
                    "opening_line": {"type": "string"},
                    "why_attractive": {"type": "string"},
                    "evidence_video_id": {"type": "string"},
                    "evidence_reason": {"type": "string"},
                    "strategy_type": {
                        "type": "string",
                        "enum": [
                            "foreign_adaptation",
                            "comment_gap",
                            "trend_extension",
                            "hybrid",
                        ],
                    },
                    "foreign_adaptation": {"type": "string"},
                    "comment_gap": {"type": "string"},
                    "trend_extension": {"type": "string"},
                    "zh_market_status": {
                        "type": "string",
                        "enum": ["green", "yellow", "red", "unknown"],
                    },
                    "zh_market_reason": {"type": "string"},
                    "recommendation_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "evidence_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "production_difficulty": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "creator_fit": {"type": "string"},
                },
                "required": [
                    "plan_name",
                    "what_to_shoot",
                    "format",
                    "opening_line",
                    "why_attractive",
                    "evidence_video_id",
                    "evidence_reason",
                    "strategy_type",
                    "foreign_adaptation",
                    "comment_gap",
                    "trend_extension",
                    "zh_market_status",
                    "zh_market_reason",
                    "recommendation_score",
                    "evidence_confidence",
                    "production_difficulty",
                    "creator_fit",
                ],
            },
        },
        "recommended_card": {"type": "integer", "minimum": 1},
        "recommendation_reason": {"type": "string"},
        "watchlist_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "input_suggestions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string"},
                    "hint": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["direction", "hint", "reason"],
            },
        },
    },
    "required": [
        "opportunity_summary",
        "rising_topics",
        "cards",
        "recommended_card",
        "recommendation_reason",
        "watchlist_ids",
        "input_suggestions",
    ],
}


def research_plan_prompt(brief: dict[str, str]) -> str:
    return f"""
你是 YouTube 研究規劃助手。依照以下已由使用者確認的需求，產生精準、可搜尋的中英文查詢起點。

{brief_to_text(brief)}

要求：
- 中文種子 3-4 個，使用台灣常見說法。
- 英文種子 4-5 個，使用國外創作者真正常用的題材語言，禁止直譯。
- 種子需涵蓋問題、比較、挑戰/實測、情境等不同意圖，而非同義詞堆疊。
- negative_keywords 只列使用者明確排除或容易造成同字異義污染的字，不能自行排除重要觀點。
- assumptions 只寫仍無法從 Brief 確定、分析時應保守處理的假設。
""".strip()


def keyword_selection_prompt(
    brief: dict[str, str],
    zh_pool: list[tuple[str, int, int]],
    en_pool: list[tuple[str, int, int]],
    n_zh: int,
    n_en: int,
) -> str:
    def block(pool: list[tuple[str, int, int]]) -> str:
        return "\n".join(f"- {term}｜排序提示 {score}" for term, score, _round in pool)

    return f"""
依照使用者確認的內容需求，從候選查詢詞選出能找到高價值參考影片的組合。

{brief_to_text(brief)}

中文候選：
{block(zh_pool) or '（空）'}

英文候選：
{block(en_pool) or '（空）'}

選中文 {n_zh} 個、英文 {n_en} 個。排序提示只是 autocomplete 的相對排序訊號，不得稱為搜尋量。
必須覆蓋不同搜尋意圖並避免同質詞；每個詞給一個簡短 intent 與 reason。
""".strip()


def relevance_prompt(brief: dict[str, str], videos: list[dict[str, Any]]) -> str:
    rows = []
    for video in videos:
        description = " ".join((video.get("description") or "").split())[:140]
        rows.append(
            f"{video['id']}｜{video.get('source_keyword', '')}｜{video.get('title', '')}｜"
            f"{video.get('channel', '')}｜{description}"
        )
    return f"""
判斷下列 YouTube 搜尋結果是否符合使用者已確認的需求。

{brief_to_text(brief)}

資料格式：id｜查詢詞｜標題｜頻道｜簡介
{chr(10).join(rows)}

只列出「明顯同字異義或完全不同題材」的 irrelevant_ids。模糊地帶保留，不得因觀點不同而刪除。
""".strip()


def breakdown_batch_prompt(packets: list[dict[str, Any]]) -> str:
    compact = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
    return f"""
你是影音內容證據分析員。請客觀拆解每支影片的「可驗證內容證據」，供後續企劃使用。

影片證據包：
{compact}

規則：
- 只能根據證據包內容，不得補寫沒出現的畫面、台詞或市場數據。
- hook 必須優先引用 00:00-00:45 的時間化字幕；沒有就明講資訊不足。
- hook_timestamp 必須填證據包內存在的 MM:SS，找不到填空字串。
- breakout_reasons 要區分「數據顯示跑贏」與「內容機制推測」。
- comment_gaps 只能來自留言；沒有明確留言證據就回空陣列。
- evidence_notes 寫支撐判斷的時間碼或留言訊號，保持精簡。
- confidence 依字幕、留言及基準樣本完整度判斷。
""".strip()


def menu_prompt(
    brief: dict[str, str],
    selected_keywords: dict[str, list[dict[str, Any]]],
    breakdowns: list[dict[str, Any]],
    pool_videos: list[dict[str, Any]],
    coverage: dict[str, Any],
    n_topics: int,
    adjust_note: str = "",
    rising_signals: list[dict[str, Any]] | None = None,
) -> str:
    # 大而固定的素材放前面，換一批時較容易命中 Gemini 隱式快取。
    keyword_summary = {
        market: [
            {"kw": item.get("kw", ""), "intent": item.get("intent", "")}
            for item in items
        ]
        for market, items in selected_keywords.items()
    }
    pool = []
    for video in sorted(
        pool_videos,
        key=lambda item: (item.get("evidence_score", 0), item.get("views_per_day", 0)),
        reverse=True,
    )[:24]:
        pool.append(
            {
                "id": video.get("id"),
                "market": video.get("market"),
                "origin": video.get("origin"),
                "title": video.get("title"),
                "views": video.get("view_count"),
                "views_per_day": video.get("views_per_day"),
                "outlier": video.get("outlier_ratio"),
                "baseline_n": video.get("baseline_sample_size"),
                "confidence": video.get("outlier_confidence"),
                "evidence_score": video.get("evidence_score"),
            }
        )
    stable_context = json.dumps(
        {
            "brief": brief,
            "keywords": keyword_summary,
            "breakdowns": breakdowns,
            "pool": pool,
            "market_coverage": coverage,
            "rising_signals": rising_signals or [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    adjustment = adjust_note.strip() or "（第一次生成，無調整要求）"
    return f"""
你是內容企劃顧問。以下是固定研究素材：
{stable_context}

請產出 {n_topics} 張可直接拍攝的企劃卡，並依推薦順序排列。

品質規則：
- 每張 evidence_video_id 必須是素材中真實存在的影片 id；沒有合適證據時填空字串並將信心設 low。
- opening_line 要能直接照唸，format 要說清楚拍攝流程，而非抽象行銷詞。
- 這個產品的三個主要策略是：
  1. foreign_adaptation：把國外已驗證的題材、Hook 或形式轉成台灣語境與案例，不得逐字照抄。
  2. comment_gap：直接補上留言中觀眾追問、爭論或原片沒回答的內容。
  3. trend_extension：用回應、續集、反方、在地版或同關鍵字，承接高動能影片的既有注意力。
- {n_topics} 張卡在證據允許時應涵蓋三種策略，避免全部同一類；可以用 hybrid，但必須說清楚三個欄位各自怎麼做。
- foreign_adaptation 只能引用英文市場影片；comment_gap 只能引用 breakdowns 內真實存在的 comment_gaps；
  trend_extension 必須綁定素材中高 views_per_day 或高 evidence_score 的來源。證據不足就留空，不能硬湊。
- 中文市場只能以 market_coverage 與本次樣本描述，禁止宣稱全市場沒人做。
- rising_topics 只能從 rising_signals 產生，signal_key 必須原樣複製；沒有訊號就回空陣列。
- 「正在竄起」不等於「已經熱門」：why_rising 只描述素材裡的跨頻道、近期速度、相對基準或歷史快照訊號；
  confidence 必須沿用對應 signal，不得自行升級。suggested_move 要說明現在如何搶先切入，而不是叫使用者照抄來源。
- recommendation_score 綜合證據、使用者契合與可執行性；production_difficulty 以一般創作者實際製作成本判斷。
- input_suggestions 同時給 3 個下一次可更精準搜尋的輸入，不要另外寫長篇說明。
- 全部使用繁體中文、短句、講人話；每個字串欄位最多 2 個短句，優先確保完整 JSON。

使用者對這一批的調整要求：{adjustment}
""".strip()


def _validated_video(video_id: str, videos: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return videos.get(video_id) if video_id else None


def _rising_reason(signal: dict[str, Any]) -> str:
    parts = [
        f"近 60 天有 {signal.get('recent_video_count', 0)} 支相關影片，"
        f"來自 {signal.get('recent_channel_count', 0)} 個不同頻道",
        f"本次樣本的日均觀看中位數約 {fmt_num(signal.get('median_daily_velocity', 0))}",
    ]
    acceleration = signal.get("acceleration_vs_older")
    if acceleration:
        parts.append(f"較早期同題樣本約 {acceleration} 倍")
    if int(signal.get("historical_velocity_samples", 0) or 0) >= 2:
        parts.append("並已有跨時間觀看快照支持")
    else:
        parts.append("目前仍屬單次取樣的早期訊號")
    return "；".join(parts) + "。"


def validate_menu_evidence(
    menu: dict[str, Any],
    pool_videos: list[dict[str, Any]],
    breakdowns: list[dict[str, Any]],
    rising_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """用程式守住來源 ID 與三種核心策略的最低證據條件。"""
    videos = {str(video.get("id", "")): video for video in pool_videos}
    breakdown_map = {
        str(breakdown.get("video_id", "")): breakdown for breakdown in breakdowns
    }
    for card in menu.get("cards", []):
        video_id = str(card.get("evidence_video_id", ""))
        video = videos.get(video_id)
        breakdown = breakdown_map.get(video_id, {})
        if not video:
            card["evidence_video_id"] = ""
            card["evidence_confidence"] = "low"
            card["foreign_adaptation"] = ""
            card["comment_gap"] = ""
            card["trend_extension"] = ""
            card["strategy_type"] = "hybrid"
            continue
        if card.get("foreign_adaptation") and video.get("market") != "en":
            card["foreign_adaptation"] = ""
            if card.get("strategy_type") == "foreign_adaptation":
                card["strategy_type"] = "hybrid"
                card["evidence_confidence"] = "low"
        if card.get("comment_gap") and not breakdown.get("comment_gaps"):
            card["comment_gap"] = ""
            if card.get("strategy_type") == "comment_gap":
                card["strategy_type"] = "hybrid"
                card["evidence_confidence"] = "low"
        if card.get("strategy_type") == "trend_extension" and not card.get("trend_extension"):
            card["strategy_type"] = "hybrid"
            card["evidence_confidence"] = "low"
    menu["watchlist_ids"] = [
        str(video_id)
        for video_id in menu.get("watchlist_ids", [])
        if str(video_id) in videos
    ][:3]
    signal_map = {
        str(signal.get("signal_key", "")): signal for signal in (rising_signals or [])
    }
    validated_topics = []
    for topic in menu.get("rising_topics", []):
        signal = signal_map.get(str(topic.get("signal_key", "")))
        if not signal:
            continue
        topic["confidence"] = signal.get("confidence", "low")
        topic["why_rising"] = _rising_reason(signal)
        topic["source_ids"] = [
            str(video_id)
            for video_id in topic.get("source_ids", [])
            if str(video_id) in videos and str(video_id) in signal.get("source_ids", [])
        ][:3]
        if not topic["source_ids"]:
            topic["source_ids"] = [
                str(video_id)
                for video_id in signal.get("source_ids", [])
                if str(video_id) in videos
            ][:3]
        validated_topics.append(topic)
    menu["rising_topics"] = validated_topics[:5]
    card_count = len(menu.get("cards", []))
    recommended = int(menu.get("recommended_card", 1) or 1)
    menu["recommended_card"] = min(max(recommended, 1), max(card_count, 1))
    return menu


def render_menu(menu: dict[str, Any], direction: str, pool_videos: list[dict[str, Any]]) -> str:
    videos = {video.get("id", ""): video for video in pool_videos}
    status_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}
    difficulty = {"low": "低", "medium": "中", "high": "高"}
    strategy_label = {
        "foreign_adaptation": "🌍 國外移植",
        "comment_gap": "💬 留言補缺",
        "trend_extension": "🏄 熱門延伸",
        "hybrid": "🧩 混合策略",
    }
    lines = [f"# 🍽 「{direction}」切角菜單", "", menu.get("opportunity_summary", "")]
    rising_topics = menu.get("rising_topics", [])
    if rising_topics:
        lines.extend(["", "## 📈 最近值得搶先卡位", ""])
        for topic in rising_topics:
            sources = []
            for video_id in topic.get("source_ids", []):
                video = _validated_video(str(video_id), videos)
                if video:
                    sources.append(f"[{video.get('title', '來源')}]({video.get('url', '')})")
            source_text = "、".join(sources) or "本次樣本訊號"
            lines.extend(
                [
                    f"### {topic.get('topic', '未命名話題')}",
                    f"- **竄升理由**：{topic.get('why_rising', '')}",
                    f"- **可信度**：{CONFIDENCE_LABEL.get(topic.get('confidence'), '低')}",
                    f"- **現在怎麼切入**：{topic.get('suggested_move', '')}",
                    f"- **訊號來源**：{source_text}",
                    "",
                ]
            )
    lines.extend(["", "---"])
    cards = menu.get("cards", [])
    for index, card in enumerate(cards, start=1):
        evidence = _validated_video(str(card.get("evidence_video_id", "")), videos)
        if evidence:
            if int(evidence.get("baseline_sample_size", 0) or 0) >= 3:
                baseline_text = f"近期基準 {evidence.get('outlier_ratio', 0)} 倍"
            else:
                baseline_text = "同頻道近期基準樣本不足"
            evidence_line = (
                f"[{evidence.get('title', '參考影片')}]({evidence.get('url', '')})｜"
                f"觀看 {fmt_num(evidence.get('view_count', 0))}｜"
                f"{baseline_text}｜{card.get('evidence_reason', '')}"
            )
        else:
            evidence_line = "⚠️ 本次樣本沒有足夠直接證據，請先小規模測試。"
        lines.extend(
            [
                "",
                f"### {index}️⃣ {card.get('plan_name', '未命名企劃')}",
                f"- 🎬 **拍什麼**：{card.get('what_to_shoot', '')}",
                f"- 🎭 **怎麼呈現**：{card.get('format', '')}",
                f"- 🗣️ **開場第一句**：「{card.get('opening_line', '')}」",
                f"- 🧲 **為什麼會吸引人**：{card.get('why_attractive', '')}",
                f"- 📎 **參考證據**：{evidence_line}",
                f"- 🏷️ **主要策略**：{strategy_label.get(card.get('strategy_type'), '🧩 混合策略')}",
                f"- 🌍 **國外怎麼轉成在地版**：{card.get('foreign_adaptation', '') or '這張不以國外移植為主'}",
                f"- 💬 **要補哪個留言缺口**：{card.get('comment_gap', '') or '本次沒有足夠留言證據'}",
                f"- 🏄 **怎麼承接熱門話題**：{card.get('trend_extension', '') or '這張不以熱門延伸為主'}",
                f"- 🇹🇼 **中文樣本**：{status_icon.get(card.get('zh_market_status'), '⚪')} "
                f"{card.get('zh_market_reason', '')}",
                f"- 🧭 **推薦度**：{card.get('recommendation_score', 0)}/100｜"
                f"證據信心 {CONFIDENCE_LABEL.get(card.get('evidence_confidence'), '低')}｜"
                f"製作難度 {difficulty.get(card.get('production_difficulty'), '中')}",
                f"- 🙋 **為什麼適合你**：{card.get('creator_fit', '')}",
                "",
                "---",
            ]
        )

    recommended = int(menu.get("recommended_card", 1) or 1)
    lines.extend(
        [
            "",
            "## 👑 優先建議",
            f"- **先拍第 {recommended} 張**：{menu.get('recommendation_reason', '')}",
            "- **拍前必看**：",
        ]
    )
    valid_watchlist = []
    for video_id in menu.get("watchlist_ids", [])[:3]:
        video = _validated_video(str(video_id), videos)
        if video and video not in valid_watchlist:
            valid_watchlist.append(video)
    if not valid_watchlist:
        valid_watchlist = sorted(
            pool_videos,
            key=lambda video: video.get("evidence_score", 0),
            reverse=True,
        )[:3]
    for index, video in enumerate(valid_watchlist, start=1):
        lines.append(
            f"  {index}. [{video.get('title', '')}]({video.get('url', '')})｜"
            f"{video.get('duration_min', 0)} 分鐘"
        )
    return "\n".join(lines)


def render_breakdown(breakdown: dict[str, Any]) -> str:
    timestamp = (
        f"（{breakdown.get('hook_timestamp')}）" if breakdown.get("hook_timestamp") else ""
    )
    lines = [
        f"**一句話主題**：{breakdown.get('topic', '')}",
        f"**開場 Hook**：{breakdown.get('hook', '')} {timestamp}",
        "**內容結構**：" + " → ".join(breakdown.get("structure", [])),
        "**可能跑出的原因**：" + "；".join(breakdown.get("breakout_reasons", [])),
        "**可借走的角度**：" + "；".join(breakdown.get("reusable_angles", [])),
        "**留言缺口**：" + ("；".join(breakdown.get("comment_gaps", [])) or "沒有足夠留言證據"),
        f"**證據信心**：{CONFIDENCE_LABEL.get(breakdown.get('confidence'), '低')}",
    ]
    return "\n\n".join(lines)


def generic_ai_comparison_prompt(brief: dict[str, str], n_topics: int = 6) -> str:
    """公開給使用者比較的通用 prompt，不包含 Angle Radar 研究方法。"""
    return f"""
你是一位 YouTube 內容企劃顧問。請根據以下需求，提出 {n_topics} 個值得拍的影片企劃：

{brief_to_text(brief, include_market=False)}

每個企劃請包含：
1. 企劃名稱
2. 這支影片要拍什麼
3. 建議呈現形式與可直接照唸的開場第一句
4. 為什麼觀眾可能想看
5. 如何做出差異化
6. 製作難度與優先順序

請使用繁體中文、具體短句，不要使用空泛行銷術語。不要虛構數據、案例或來源；不確定的內容直接標示為假設。
""".strip()


def build_public_export(
    rendered_report: str,
    menu: dict[str, Any],
    pool_videos: list[dict[str, Any]],
    created_at: str,
) -> str:
    """一般使用者匯出只保留成果與引用，不洩露研究流程和中間資料。"""
    videos = {video.get("id", ""): video for video in pool_videos}
    cited_ids = {
        str(card.get("evidence_video_id", "")) for card in menu.get("cards", [])
    } | {str(video_id) for video_id in menu.get("watchlist_ids", [])} | {
        str(video_id)
        for topic in menu.get("rising_topics", [])
        for video_id in topic.get("source_ids", [])
    }
    cited = [videos[video_id] for video_id in cited_ids if video_id in videos]
    lines = [rendered_report, "", "---", "", "## 本次引用來源", ""]
    for video in sorted(cited, key=lambda item: item.get("evidence_score", 0), reverse=True):
        lines.append(
            f"- [{video.get('title', '')}]({video.get('url', '')})｜{video.get('channel', '')}｜"
            f"觀看 {fmt_num(video.get('view_count', 0))}"
        )
    lines.extend(["", f"_生成時間：{created_at}_"])
    return "\n".join(lines)
