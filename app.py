"""🍽 Angle Radar — 以市場證據輔助影音創作者產生可拍切角。"""

from __future__ import annotations

import concurrent.futures
import json
from datetime import datetime
from typing import Any

import pandas as pd
import requests
import streamlit as st

from cache_store import JsonTTLCache
from llm_client import GeminiClient, UsageLedger
from radar_core import (
    MARKET_LABEL,
    attach_outlier_metrics_v2,
    brief_quality,
    brief_to_text,
    build_brief,
    build_transcript_evidence,
    compress_comments,
    derive_rising_signals,
    fmt_num,
    market_coverage,
    pick_videos_diverse,
    prefilter_keyword_pool,
    stable_hash,
)
from reporting import (
    BREAKDOWN_BATCH_SCHEMA,
    BREAKDOWN_ITEM_SCHEMA,
    KEYWORD_SELECTION_SCHEMA,
    MENU_SCHEMA,
    RELEVANCE_SCHEMA,
    RESEARCH_PLAN_SCHEMA,
    breakdown_batch_prompt,
    build_public_export,
    generic_ai_comparison_prompt,
    keyword_selection_prompt,
    menu_prompt,
    relevance_prompt,
    render_breakdown,
    render_menu,
    research_plan_prompt,
    validate_menu_evidence,
)
from youtube_data import YouTubeData


st.set_page_config(page_title="切角點單機", page_icon="🍽", layout="wide")

DEFAULT_PIPELINE_MODEL = "gemini-3.1-flash-lite"
DEFAULT_REPORT_MODEL = "gemini-3.5-flash"
BREAKDOWN_PROMPT_VERSION = "general-v2.2"


def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, "") or ""
    except Exception:
        return ""


def _wl_enabled() -> bool:
    return bool(_secret("WHITELIST_API_URL"))


def _is_admin() -> bool:
    admin_code = _secret("ADMIN_CODE")
    signed_in_admin = bool(admin_code) and st.session_state.get("_wl_code", "") == admin_code
    explicit_local_admin = str(_secret("SHOW_ADMIN_DIAGNOSTICS")).lower() in {
        "1",
        "true",
        "yes",
    }
    return signed_in_admin or explicit_local_admin


def _wl_api(
    action: str, code: str, extra_payload: dict[str, str] | None = None
) -> dict[str, Any]:
    payload = {
        "key": _secret("WHITELIST_API_KEY"),
        "action": action,
        "code": code.strip(),
    }
    if extra_payload:
        payload.update(extra_payload)
    try:
        if action == "check":
            response = requests.get(_secret("WHITELIST_API_URL"), params=payload, timeout=20)
        else:
            response = requests.post(_secret("WHITELIST_API_URL"), data=payload, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception:
        return {"ok": False, "error": "api_error"}


_WL_ERR = {
    "not_found": "通行碼不在名單中，跟站主索取試用碼 🙏",
    "depleted": "你的剩餘次數是 0 了，跟站主加值吧 🙏",
    "unauthorized": "白名單設定有誤，請聯絡站主",
    "bad_headers": "白名單設定有誤，請聯絡站主",
    "busy": "目前使用人數較多，請幾秒後再試",
    "api_error": "服務連線失敗，請稍後再試",
}


if _wl_enabled() and not st.session_state.get("_wl_ok"):
    st.subheader("🔑 測試通行")
    st.caption("這是邀請制內測，請輸入站主提供的試用碼")
    access_code = st.text_input("試用碼", label_visibility="collapsed", placeholder="輸入試用碼")
    if st.button("進入", type="primary"):
        info = _wl_api("check", access_code)
        if info.get("ok") or info.get("found"):
            st.session_state.update(
                {
                    "_wl_ok": True,
                    "_wl_code": access_code.strip(),
                    "_wl_name": info.get("name", ""),
                    "_wl_remaining": int(info.get("remaining", 0) or 0),
                    "_wl_deep": bool(info.get("deep", True)),
                }
            )
            st.rerun()
        else:
            st.error(_WL_ERR.get(info.get("error", ""), "通行碼無效"))
    st.stop()


class ProgressView:
    """一般使用者只看到模糊進度；管理者才看得到診斷細節。"""

    def __init__(self, admin: bool) -> None:
        self.admin = admin
        self.box = st.status("正在確認需求…", expanded=admin)

    def update(self, public_label: str, detail: str = "") -> None:
        self.box.update(label=public_label, state="running", expanded=self.admin)
        if self.admin and detail:
            self.box.write(detail)

    def complete(self) -> None:
        self.box.update(label="✅ 分析完成，菜單已上桌", state="complete", expanded=False)

    def fail(self) -> None:
        self.box.update(label="分析沒有完成", state="error", expanded=True)


def _apply_negatives(query: str, negatives: list[str]) -> str:
    clean = []
    for term in negatives[:5]:
        value = str(term).strip().replace('"', "")
        if value and value.lower() not in query.lower():
            clean.append(f'-"{value}"' if " " in value else f"-{value}")
    return " ".join([query, *clean]).strip()


def _fallback_keywords(
    zh_pool: list[tuple[str, int, int]],
    en_pool: list[tuple[str, int, int]],
    n_zh: int,
    n_en: int,
) -> dict[str, list[dict[str, str]]]:
    return {
        "zh": [
            {"kw": term, "reason": "候選排序較前", "intent": "reference"}
            for term, _score, _round in zh_pool[:n_zh]
        ],
        "en": [
            {"kw": term, "reason": "候選排序較前", "intent": "reference"}
            for term, _score, _round in en_pool[:n_en]
        ],
    }


def _video_packet(
    video: dict[str, Any],
    transcript: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = build_transcript_evidence(transcript, max_chars=3_200)
    compact_comments = compress_comments(comments, limit=8, max_chars_each=180)
    return {
        "video_id": video["id"],
        "title": video.get("title", ""),
        "market": video.get("market", ""),
        "duration_min": video.get("duration_min", 0),
        "metrics": {
            "views": video.get("view_count", 0),
            "views_per_day": video.get("views_per_day", 0),
            "recent_baseline_ratio": video.get("outlier_ratio", 0),
            "baseline_sample_size": video.get("baseline_sample_size", 0),
            "metric_confidence": video.get("outlier_confidence", "low"),
        },
        "timed_transcript_evidence": evidence or "（沒有可用字幕）",
        "comments": compact_comments,
        "source_quality": "timed_transcript" if evidence else "metadata_and_comments_only",
    }


def _fallback_breakdown(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_id": packet["video_id"],
        "topic": packet.get("title", ""),
        "hook": "可用證據不足，建議親自觀看開場。",
        "hook_timestamp": "",
        "structure": [],
        "breakout_reasons": ["只有影片數據，無法可靠判斷內容機制。"],
        "reusable_angles": [],
        "comment_gaps": [],
        "evidence_notes": [],
        "confidence": "low",
    }


def _analyze_packets(
    gemini: GeminiClient,
    ledger: UsageLedger,
    cache: JsonTTLCache,
    model: str,
    packets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, Any]] = []
    key_by_id: dict[str, str] = {}
    for packet in packets:
        cache_key = "breakdown:" + stable_hash(
            [BREAKDOWN_PROMPT_VERSION, model, packet]
        )
        key_by_id[packet["video_id"]] = cache_key
        cached = cache.get(cache_key)
        if cached:
            output[packet["video_id"]] = cached
            ledger.local_cache("影片證據分析", model)
        else:
            misses.append(packet)

    if misses:
        response = gemini.generate_json(
            stage="影片證據分析",
            model=model,
            prompt=breakdown_batch_prompt(misses),
            schema=BREAKDOWN_BATCH_SCHEMA,
            max_output_tokens=min(5_500, 850 * len(misses) + 600),
            thinking_level="low",
        )
        valid_ids = {packet["video_id"] for packet in misses}
        for breakdown in response.get("videos", []):
            video_id = str(breakdown.get("video_id", ""))
            if video_id in valid_ids:
                output[video_id] = breakdown
                cache.set(key_by_id[video_id], breakdown, 604_800)

    return [output.get(packet["video_id"], _fallback_breakdown(packet)) for packet in packets]


def _deep_supplement(
    gemini: GeminiClient,
    model: str,
    video: dict[str, Any],
    existing_packet: dict[str, Any],
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {"video": BREAKDOWN_ITEM_SCHEMA},
        "required": ["video"],
    }
    prompt = (
        breakdown_batch_prompt([existing_packet])
        + "\n這次可直接觀看影片。請特別補足畫面 Hook、節奏和視覺呈現，仍須提供時間碼。"
    )
    result = gemini.generate_json(
        stage="視覺補充",
        model=model,
        contents=[
            {"file_data": {"file_uri": video["url"]}},
            {"text": prompt},
        ],
        schema=schema,
        max_output_tokens=1_200,
        thinking_level="low",
        media_resolution_low=True,
    )
    breakdown = result.get("video", {})
    return breakdown if breakdown.get("video_id") == video["id"] else _fallback_breakdown(existing_packet)


def run_pipeline(cfg: dict[str, Any], brief: dict[str, str]) -> dict[str, Any]:
    admin = _is_admin()
    progress = ProgressView(admin)
    ledger = UsageLedger()
    cache = JsonTTLCache()
    gemini = GeminiClient(cfg["gemini_key"], ledger)
    youtube = YouTubeData(cfg["yt_key"], cache)
    result: dict[str, Any] = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "brief": brief,
        "cfg": {key: value for key, value in cfg.items() if key not in {"gemini_key", "yt_key"}},
    }
    try:
        progress.update("正在理解你的需求…")
        plan = gemini.generate_json(
            stage="需求研究規劃",
            model=cfg["pipeline_model"],
            prompt=research_plan_prompt(brief),
            schema=RESEARCH_PLAN_SCHEMA,
            max_output_tokens=900,
            thinking_level="low",
        )
        if not plan.get("zh_seeds"):
            plan["zh_seeds"] = [brief["direction"]]
        result["research_plan"] = plan
        progress.update(
            "正在蒐集有用的參考…",
            f"研究規劃完成：中文 {len(plan.get('zh_seeds', []))}、英文 {len(plan.get('en_seeds', []))} 個起點",
        )

        zh_pool, zh_log = youtube.expand_keywords(
            plan.get("zh_seeds", []), "zh", cfg["mine_rounds"]
        )
        en_pool, en_log = youtube.expand_keywords(
            plan.get("en_seeds", []), "en", cfg["mine_rounds"]
        )
        zh_shortlist = prefilter_keyword_pool(zh_pool, 30)
        en_shortlist = prefilter_keyword_pool(en_pool, 30)
        result["mining_log"] = {"zh": zh_log, "en": en_log}
        try:
            selected = gemini.generate_json(
                stage="參考查詢選擇",
                model=cfg["pipeline_model"],
                prompt=keyword_selection_prompt(
                    brief,
                    zh_shortlist,
                    en_shortlist,
                    cfg["n_zh"],
                    cfg["n_en"],
                ),
                schema=KEYWORD_SELECTION_SCHEMA,
                max_output_tokens=1_000,
                thinking_level="low",
            )
            selected["zh"] = selected.get("zh", [])[: cfg["n_zh"]]
            selected["en"] = selected.get("en", [])[: cfg["n_en"]]
        except Exception:
            selected = _fallback_keywords(
                zh_shortlist, en_shortlist, cfg["n_zh"], cfg["n_en"]
            )
        result["selected_kws"] = selected

        all_videos: list[dict[str, Any]] = []
        seen: set[str] = set()
        search_jobs = []
        orders = ["relevance", "viewCount", "date"]
        for market in ("en", "zh"):
            for index, item in enumerate(selected.get(market, [])):
                research_keyword = item.get("kw", "")
                query = _apply_negatives(research_keyword, plan.get("negative_keywords", []))
                search_jobs.append(
                    (market, research_keyword, query, orders[index % len(orders)])
                )
        for market, research_keyword, query, order in search_jobs:
            for video in youtube.search_videos(
                query,
                market,
                cfg["window_days"],
                cfg["per_kw"],
                order,
            ):
                video["research_keyword"] = research_keyword
                if video["id"] not in seen:
                    seen.add(video["id"])
                    all_videos.append(video)
        if not all_videos:
            raise RuntimeError("沒有取得可分析的公開影片，請調整需求或檢查 YouTube API Key。")

        progress.update(
            "正在整理參考內容…",
            f"去重後取得 {len(all_videos)} 支候選影片",
        )
        try:
            relevance = gemini.generate_json(
                stage="候選相關性檢查",
                model=cfg["pipeline_model"],
                prompt=relevance_prompt(brief, all_videos),
                schema=RELEVANCE_SCHEMA,
                max_output_tokens=500,
                thinking_level="low",
            )
            irrelevant = set(relevance.get("irrelevant_ids", []))
            filtered = [video for video in all_videos if video["id"] not in irrelevant]
            if filtered:
                all_videos = filtered
        except Exception:
            pass

        profiles = youtube.channel_profiles([video["channel_id"] for video in all_videos])
        attach_outlier_metrics_v2(all_videos, profiles, {})
        preliminary = sorted(
            all_videos,
            key=lambda video: (video.get("evidence_score", 0), video.get("views_per_day", 0)),
            reverse=True,
        )
        baseline_channels = list(
            dict.fromkeys(video.get("channel_id", "") for video in preliminary if video.get("channel_id"))
        )[:18]
        recent = youtube.recent_channel_videos(profiles, baseline_channels, max_results=15)
        attach_outlier_metrics_v2(all_videos, profiles, recent)
        try:
            observed_velocities = cache.record_video_observations(all_videos)
            rising_signals = derive_rising_signals(all_videos, observed_velocities)
        except Exception:
            rising_signals = derive_rising_signals(all_videos)
        coverage = market_coverage(all_videos)
        selected_videos = pick_videos_diverse(
            all_videos,
            k=cfg["analyze_n"],
            min_views=cfg["min_views"],
            en_share=0.6,
            video_type=cfg["video_type"],
        )
        if not selected_videos:
            selected_videos = sorted(
                all_videos, key=lambda video: video.get("evidence_score", 0), reverse=True
            )[: cfg["analyze_n"]]
        result["pool"] = all_videos
        result["market_coverage"] = coverage
        result["rising_signals"] = rising_signals
        result["analyzed_ids"] = [video["id"] for video in selected_videos]

        progress.update(
            "正在閱讀最有參考價值的內容…",
            f"以近期同格式基準與多樣性選出 {len(selected_videos)} 支",
        )
        transcript_map: dict[str, list[dict[str, Any]]] = {}
        comments_map: dict[str, list[dict[str, Any]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            transcript_futures = {
                executor.submit(youtube.transcript_segments, video["id"], video["market"]): video["id"]
                for video in selected_videos
            }
            comment_futures = {
                executor.submit(youtube.top_comments, video["id"]): video["id"]
                for video in selected_videos
            }
            for future in concurrent.futures.as_completed(
                [*transcript_futures.keys(), *comment_futures.keys()]
            ):
                if future in transcript_futures:
                    transcript_map[transcript_futures[future]] = future.result()
                else:
                    comments_map[comment_futures[future]] = future.result()

        packets = [
            _video_packet(
                video,
                transcript_map.get(video["id"], []),
                comments_map.get(video["id"], []),
            )
            for video in selected_videos
        ]
        breakdowns = _analyze_packets(
            gemini,
            ledger,
            cache,
            cfg["pipeline_model"],
            packets,
        )
        if cfg.get("deep_mode") and selected_videos:
            try:
                breakdowns[0] = _deep_supplement(
                    gemini,
                    cfg["pipeline_model"],
                    selected_videos[0],
                    packets[0],
                )
            except Exception as exc:
                if admin:
                    progress.box.write(f"視覺補充失敗，已保留字幕分析：{exc}")
        result["breakdowns"] = breakdowns

        progress.update("正在把洞察整理成可拍企劃…")
        menu = gemini.generate_json(
            stage="切角菜單生成",
            model=cfg["report_model"],
            prompt=menu_prompt(
                brief,
                selected,
                breakdowns,
                all_videos,
                coverage,
                cfg["n_topics"],
                rising_signals=rising_signals,
            ),
            schema=MENU_SCHEMA,
            max_output_tokens=4_500,
            thinking_level="medium",
            temperature=0.35,
        )
        menu["cards"] = menu.get("cards", [])[: cfg["n_topics"]]
        menu = validate_menu_evidence(menu, all_videos, breakdowns, rising_signals)
        result["menu"] = menu
        result["report"] = render_menu(menu, brief["direction"], all_videos)
        result["input_suggestions"] = menu.get("input_suggestions", [])[:3]
        result["usage"] = ledger.summary()
        result["youtube_usage"] = youtube.usage()
        result["youtube_errors"] = youtube.errors
        progress.complete()
        return result
    except Exception:
        progress.fail()
        raise


def regenerate_menu(result: dict[str, Any], adjust_note: str, api_key: str) -> dict[str, Any]:
    cfg = result["cfg"]
    ledger = UsageLedger()
    gemini = GeminiClient(api_key, ledger)
    menu = gemini.generate_json(
        stage="切角菜單調整",
        model=cfg["report_model"],
        prompt=menu_prompt(
            result["brief"],
            result["selected_kws"],
            result["breakdowns"],
            result["pool"],
            result["market_coverage"],
            cfg["n_topics"],
            adjust_note,
            rising_signals=result.get("rising_signals", []),
        ),
        schema=MENU_SCHEMA,
        max_output_tokens=4_500,
        thinking_level="medium",
        temperature=0.4,
    )
    menu["cards"] = menu.get("cards", [])[: cfg["n_topics"]]
    menu = validate_menu_evidence(
        menu,
        result["pool"],
        result["breakdowns"],
        result.get("rising_signals", []),
    )
    result["menu"] = menu
    result["report"] = render_menu(menu, result["brief"]["direction"], result["pool"])
    result["input_suggestions"] = menu.get("input_suggestions", [])[:3]
    result["last_regeneration_usage"] = ledger.summary()
    return result


st.title("🍽 切角點單機")
st.caption("把想法整理成有參考依據、可以直接開拍的內容企劃")

has_secret_keys = bool(_secret("GEMINI_API_KEY") and _secret("YOUTUBE_API_KEY"))
with st.sidebar:
    if st.session_state.get("_wl_ok"):
        name = st.session_state.get("_wl_name") or "測試用戶"
        st.success(f"歡迎，{name} 👋")
        remaining = st.session_state.get("_wl_remaining", 0)
        st.metric("剩餘次數", remaining)
        if remaining <= 0:
            st.caption("⚠️ 次數用完了，請找站主加值")

    if has_secret_keys:
        GEMINI_API_KEY = _secret("GEMINI_API_KEY")
        YOUTUBE_API_KEY = _secret("YOUTUBE_API_KEY")
    else:
        st.header("🔑 API 金鑰")
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")
        YOUTUBE_API_KEY = st.text_input("YouTube Data API Key", type="password")

    DEEP_MODE = False
    if _is_admin():
        st.markdown("---")
        st.markdown("**管理者設定** 🔧")
        deep_allowed = st.session_state.get("_wl_deep", True)
        DEEP_MODE = st.toggle(
            "額外畫面檢查",
            value=False,
            disabled=not deep_allowed,
            help="只補充第一支影片；一般模式不需要開啟。",
        )
        REPORT_MODEL = st.selectbox(
            "報告模型",
            [
                "gemini-3.5-flash",
                "gemini-3.1-pro-preview",
                "gemini-3-flash-preview",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
            ],
        )
        PIPELINE_MODEL = st.selectbox(
            "資料整理模型",
            ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-2.5-flash"],
        )
        with st.expander("進階參數"):
            N_TOPICS = st.slider("企劃數", 4, 10, 6)
            N_EN = st.slider("英文查詢數", 2, 8, 5)
            N_ZH = st.slider("中文查詢數", 1, 6, 3)
            MINE_ROUNDS = st.slider("探索廣度", 1, 3, 2)
            PER_KW = st.slider("每組候選數", 8, 25, 15)
            ANALYZE_N = st.slider("證據分析影片數", 3, 8, 6)
            WINDOW_DAYS = st.select_slider(
                "內容時間範圍", options=[30, 90, 180, 365], value=180
            )
            MIN_VIEWS = st.select_slider(
                "最低觀看", options=[1_000, 3_000, 5_000, 10_000, 30_000], value=3_000
            )
            VIDEO_TYPE = st.radio("影片類型", ["全部", "僅長片", "僅 Shorts"], horizontal=True)
    else:
        REPORT_MODEL = DEFAULT_REPORT_MODEL
        PIPELINE_MODEL = DEFAULT_PIPELINE_MODEL
        N_TOPICS, N_EN, N_ZH, MINE_ROUNDS = 6, 5, 3, 2
        PER_KW, ANALYZE_N, WINDOW_DAYS, MIN_VIEWS = 15, 6, 180, 3_000
        VIDEO_TYPE = "全部"


st.markdown("### 1. 告訴我這次想解決什麼")
direction = st.text_area(
    "想拍的主題",
    placeholder="例：給外食上班族的增肌飲食",
    height=76,
    key="direction_input",
)
col_a, col_b = st.columns(2)
with col_a:
    audience = st.text_input(
        "想吸引誰（必填）",
        placeholder="例：25–35 歲、想增肌但沒時間煮飯的上班族",
        key="audience_input",
    )
    who = st.text_input(
        "你／頻道的定位",
        placeholder="例：剛起步的健身教練頻道",
        key="creator_input",
    )
    goal = st.selectbox(
        "這支片最重要的目的（必填）",
        [
            "請選擇",
            "漲粉曝光",
            "建立專業信任（接案／接業配）",
            "導購／賣課",
            "經營個人品牌",
        ],
        key="goal_input",
    )
with col_b:
    form_pref = st.selectbox(
        "呈現偏好",
        ["不限", "教學解說", "實測／挑戰", "觀點評論", "vlog／情境短劇"],
        key="format_input",
    )
    duration_pref = st.selectbox(
        "片長偏好",
        ["不限", "60 秒內 Shorts", "3–8 分鐘", "8–15 分鐘", "15 分鐘以上"],
        key="duration_input",
    )
    market_focus = st.selectbox(
        "市場重心",
        ["台灣主場＋英文市場找參考", "台灣／繁中為主", "英文市場為主"],
        key="market_input",
    )

with st.expander("再補一些限制，結果會更貼近你"):
    strengths = st.text_input(
        "你有哪些可運用的優勢／素材",
        placeholder="例：教練證照、三位學員案例、能實拍一週",
        key="strengths_input",
    )
    exclusions = st.text_input(
        "不想碰的內容",
        placeholder="例：不談補劑、不做醫療宣稱、不拍惡搞",
        key="exclusions_input",
    )
    references = st.text_input(
        "喜歡或不喜歡的參考頻道／影片",
        placeholder="可以貼網址，並註明喜歡或不喜歡什麼",
        key="references_input",
    )
    free_note = st.text_input(
        "其他補充",
        placeholder="例：器材只有手機、希望兩天內能拍完",
        key="freenote_input",
    )

brief = build_brief(
    direction=direction,
    audience=audience,
    goal=goal,
    who=who,
    form_pref=form_pref,
    market_focus=market_focus,
    duration_pref=duration_pref,
    strengths=strengths,
    exclusions=exclusions,
    references=references,
    extra=free_note,
)
quality, missing = brief_quality(brief)

st.markdown("### 2. 確認需求，也可以先跟一般 AI 比一場")
brief_col, compare_col = st.columns([1, 1])
with brief_col:
    with st.container(border=True):
        st.markdown("#### 這次的需求摘要")
        st.markdown(brief_to_text(brief) or "請先填寫上方需求。")
        st.progress(quality / 100, text=f"需求完整度 {quality}%")
        if missing and direction:
            st.caption("再補充會更準：" + "、".join(missing[:3]))
with compare_col:
    with st.container(border=True):
        st.markdown("#### 🆚 想自己問 AI 看看？")
        st.caption("右上角可直接複製。這是一般 AI 對照組，不含 Angle Radar 的市場證據。")
        st.code(generic_ai_comparison_prompt(brief, N_TOPICS), language="text")

required_ok = bool(direction.strip() and audience.strip() and goal != "請選擇")
confirmed = st.checkbox(
    "我確認以上需求正確",
    disabled=not required_ok,
    key="brief_confirmed",
)
run_clicked = st.button(
    "🍽 幫我出菜單",
    type="primary",
    disabled=not (required_ok and confirmed),
    use_container_width=True,
)
if not required_ok:
    st.caption("填完主題、目標觀眾與拍片目的後，就可以開始。")

if run_clicked:
    if not GEMINI_API_KEY or not YOUTUBE_API_KEY:
        st.error("請先設定 Gemini 與 YouTube API Key")
    else:
        code = st.session_state.get("_wl_code", "")
        consumed = False
        may_run = not _wl_enabled()
        if _wl_enabled():
            response = _wl_api("consume", code)
            if response.get("ok"):
                consumed = True
                may_run = True
                st.session_state["_wl_remaining"] = int(response.get("remaining", 0) or 0)
            else:
                st.error(_WL_ERR.get(response.get("error", ""), "額度不足或服務異常"))
        if may_run:
            config = {
                "gemini_key": GEMINI_API_KEY,
                "yt_key": YOUTUBE_API_KEY,
                "report_model": REPORT_MODEL,
                "pipeline_model": PIPELINE_MODEL,
                "deep_mode": DEEP_MODE,
                "n_topics": N_TOPICS,
                "n_zh": N_ZH,
                "n_en": N_EN,
                "mine_rounds": MINE_ROUNDS,
                "per_kw": PER_KW,
                "analyze_n": ANALYZE_N,
                "window_days": WINDOW_DAYS,
                "min_views": MIN_VIEWS,
                "video_type": VIDEO_TYPE,
            }
            try:
                pipeline_result = run_pipeline(config, brief)
                st.session_state["radar_result"] = pipeline_result
            except Exception as exc:
                if _is_admin():
                    st.error(str(exc))
                else:
                    st.error("這次分析沒有完成，請稍後再試。")
                if consumed:
                    refund = _wl_api("refund", code)
                    if refund.get("ok"):
                        st.session_state["_wl_remaining"] = int(
                            refund.get("remaining", 0) or 0
                        )
                        st.info("這次沒有完成，已退還使用次數。")
                    else:
                        st.warning("使用次數暫時無法自動退回，請聯絡站主處理。")


def _apply_suggestion(direction_text: str, hint_text: str) -> None:
    st.session_state["direction_input"] = direction_text
    st.session_state["freenote_input"] = hint_text
    st.session_state["brief_confirmed"] = False
    st.toast("已帶入，可再修改後重新確認", icon="✍️")


if "radar_result" in st.session_state:
    result = st.session_state["radar_result"]
    st.markdown("---")
    st.markdown(result["report"])

    suggestions = result.get("input_suggestions", [])
    if suggestions:
        with st.container(border=True):
            st.markdown("#### 想換一個更聚焦的題目？")
            for index, suggestion in enumerate(suggestions):
                text_col, button_col = st.columns([5, 1])
                with text_col:
                    st.markdown(f"**{suggestion.get('direction', '')}**")
                    if suggestion.get("hint"):
                        st.caption(suggestion["hint"])
                    if suggestion.get("reason"):
                        st.caption("↳ " + suggestion["reason"])
                with button_col:
                    st.button(
                        "帶入",
                        key=f"apply_suggestion_{index}",
                        on_click=_apply_suggestion,
                        args=(suggestion.get("direction", ""), suggestion.get("hint", "")),
                        use_container_width=True,
                    )

    export = build_public_export(
        result["report"], result["menu"], result["pool"], result["created_at"]
    )
    st.download_button(
        "📥 下載菜單與引用來源",
        export,
        file_name=f"切角菜單_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
    )

    with st.container(border=True):
        st.markdown("#### 🆚 如果你也拿通用 Prompt 問了 AI")
        st.caption("請誠實選；『差不多』和『一般 AI 較好』對產品改進最有價值。")
        comparison = st.radio(
            "哪一份更有用？",
            ["Angle Radar 明顯較好", "Angle Radar 稍好", "差不多", "一般 AI 較好"],
            horizontal=True,
            key="comparison_verdict",
        )
        comparison_note = st.text_input(
            "為什麼？（選填）",
            placeholder="例：一般 AI 點子比較新，但這份比較知道怎麼拍",
            key="comparison_note",
        )
        if st.button("送出比較結果", key="submit_comparison"):
            if st.session_state.get("comparison_submitted_for") == result.get("created_at"):
                st.info("這份報告已經回覆過了，謝謝你。")
            else:
                saved = True
                if _wl_enabled():
                    feedback_response = _wl_api(
                        "feedback",
                        st.session_state.get("_wl_code", ""),
                        {
                            "direction": result.get("brief", {}).get("direction", "")[:120],
                            "verdict": comparison[:80],
                            "note": comparison_note[:500],
                        },
                    )
                    saved = bool(feedback_response.get("ok"))
                if saved:
                    st.session_state["comparison_submitted_for"] = result.get("created_at")
                    st.success("收到，這會直接用來衡量產品是不是真的有贏。")
                else:
                    st.warning("目前無法儲存，請稍後再試。")

    st.markdown("### 🔁 想換一種口味？")
    adjust = st.text_input(
        "告訴我怎麼調整",
        placeholder="例：開箱太多，改成三個能在家完成的挑戰型企劃",
        key="adjust_input",
    )
    if st.button("重新出一批", disabled=not adjust.strip()):
        with st.spinner("正在依你的回饋調整…"):
            try:
                result = regenerate_menu(result, adjust.strip(), GEMINI_API_KEY)
                st.session_state["radar_result"] = result
                st.rerun()
            except Exception as exc:
                if _is_admin():
                    st.error(f"調整失敗：{exc}")
                else:
                    st.error("這次調整沒有完成，請稍後再試。")

    if _is_admin():
        st.markdown("---")
        st.markdown("## 🔧 管理者診斷")
        usage = result.get("usage", {})
        metric_cols = st.columns(4)
        metric_cols[0].metric("Input tokens", f"{usage.get('input_tokens', 0):,}")
        metric_cols[1].metric(
            "Output＋thinking",
            f"{usage.get('output_tokens', 0) + usage.get('thinking_tokens', 0):,}",
        )
        metric_cols[2].metric("本機快取命中", usage.get("local_cache_hits", 0))
        metric_cols[3].metric("推估 Gemini 成本", f"NT${usage.get('estimated_twd', 0):.2f}")
        st.caption(
            f"價格基準日 {usage.get('pricing_date', '—')}；估算不等同實際帳單。"
        )

        with st.expander("模型使用明細"):
            usage_rows = usage.get("records", [])
            if usage_rows:
                st.dataframe(pd.DataFrame(usage_rows), use_container_width=True, hide_index=True)

        with st.expander("研究規劃與查詢診斷"):
            st.json(result.get("research_plan", {}))
            st.json(result.get("selected_kws", {}))
            st.json(result.get("mining_log", {}))
            st.caption(f"YouTube 呼叫：{result.get('youtube_usage', {})}")
            if result.get("youtube_errors"):
                st.json(result["youtube_errors"])

        with st.expander("市場樣本與影片評分"):
            st.json(result.get("market_coverage", {}))
            st.json({"rising_signals": result.get("rising_signals", [])})
            rows = [
                {
                    "市場": MARKET_LABEL.get(video.get("market"), video.get("market")),
                    "來源": video.get("origin"),
                    "標題": video.get("title"),
                    "觀看": video.get("view_count"),
                    "日均": video.get("views_per_day"),
                    "近期基準倍數": video.get("outlier_ratio"),
                    "基準樣本": video.get("baseline_sample_size"),
                    "證據分": video.get("evidence_score"),
                    "已分析": video.get("id") in result.get("analyzed_ids", []),
                    "連結": video.get("url"),
                }
                for video in sorted(
                    result.get("pool", []),
                    key=lambda item: item.get("evidence_score", 0),
                    reverse=True,
                )
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with st.expander("影片證據拆解"):
            video_map = {video["id"]: video for video in result.get("pool", [])}
            for breakdown in result.get("breakdowns", []):
                video = video_map.get(breakdown.get("video_id"), {})
                st.markdown(
                    f"#### [{video.get('title', breakdown.get('video_id', ''))}]({video.get('url', '')})"
                )
                st.markdown(render_breakdown(breakdown))
