"""Angle Radar — 從公開內容證據中找出值得深化的影片切角。"""

from __future__ import annotations

import concurrent.futures
import logging
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
    build_reflow_candidates,
    build_search_terms,
    build_transcript_evidence,
    compress_comments,
    derive_rising_signals,
    market_coverage,
    merge_search_results,
    parse_youtube_video_ids,
    pick_evidence_ready_videos,
    pick_videos_diverse,
    prefilter_keyword_pool,
    stable_hash,
    validate_reflow_selection,
)
from reporting import (
    ANGLE_REPORT_SCHEMA,
    BREAKDOWN_BATCH_SCHEMA,
    KEYWORD_REFLOW_SCHEMA,
    KEYWORD_PLAN_SCHEMA,
    RELEVANCE_SCHEMA,
    angle_development_prompt,
    angle_report_prompt,
    breakdown_batch_prompt,
    build_public_export,
    keyword_reflow_prompt,
    keyword_plan_prompt,
    relevance_prompt,
    render_angle_report,
    render_action_card,
    render_action_evidence,
    render_breakdown,
    research_synthesis_prompt,
    validate_angle_evidence,
    validate_research_synthesis,
)
from youtube_data import YouTubeData


st.set_page_config(page_title="切角雷達", layout="wide")

DEFAULT_PIPELINE_MODEL = "gemini-3.1-flash-lite"
DEFAULT_REPORT_MODEL = "gemini-3.5-flash"
BREAKDOWN_PROMPT_VERSION = "angle-evidence-v5"
BREAKDOWN_BATCH_SIZE = 4
MAX_REFLOW_TERMS = 4
MAX_DISCOVERY_VIDEOS = 10
MAX_EVIDENCE_PROBES = 12
MAX_RELEVANCE_VIDEOS = 180
COMMENTS_PER_ORDER = 40
LOGGER = logging.getLogger(__name__)


def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, "") or ""
    except Exception:
        return ""


def _wl_enabled() -> bool:
    return bool(_secret("WHITELIST_API_URL"))


def _is_admin() -> bool:
    admin_code = _secret("ADMIN_CODE")
    signed_in_admin = (
        bool(admin_code) and st.session_state.get("_wl_code", "") == admin_code
    )
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
            response = requests.get(
                _secret("WHITELIST_API_URL"), params=payload, timeout=20
            )
        else:
            response = requests.post(
                _secret("WHITELIST_API_URL"), data=payload, timeout=20
            )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {"ok": False, "error": "api_error"}


_WL_ERR = {
    "not_found": "通行碼不在名單中，請跟站主索取試用碼",
    "depleted": "你的剩餘次數是 0，請跟站主加值",
    "unauthorized": "白名單設定有誤，請聯絡站主",
    "bad_headers": "白名單設定有誤，請聯絡站主",
    "busy": "目前使用人數較多，請幾秒後再試",
    "api_error": "服務連線失敗，請稍後再試",
}


if _wl_enabled() and not st.session_state.get("_wl_ok"):
    st.subheader("測試通行")
    st.caption("這是邀請制內測，請輸入站主提供的試用碼")
    access_code = st.text_input(
        "試用碼", label_visibility="collapsed", placeholder="輸入試用碼"
    )
    if st.button("進入", type="primary"):
        info = _wl_api("check", access_code)
        if info.get("ok") or info.get("found"):
            st.session_state.update(
                {
                    "_wl_ok": True,
                    "_wl_code": access_code.strip(),
                    "_wl_name": info.get("name", ""),
                    "_wl_remaining": int(info.get("remaining", 0) or 0),
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
        self.box = st.status("正在理解主題…", expanded=admin)

    def update(self, public_label: str, detail: str = "") -> None:
        self.box.update(label=public_label, state="running", expanded=self.admin)
        if self.admin and detail:
            self.box.write(detail)

    def complete(self) -> None:
        self.box.update(label="切角整理完成", state="complete", expanded=False)

    def fail(self) -> None:
        self.box.update(label="分析沒有完成", state="error", expanded=True)


def _apply_negatives(query: str, negatives: list[str]) -> str:
    clean = []
    for term in negatives[:5]:
        value = str(term).strip().replace('"', "")
        if value and value.lower() not in query.lower():
            clean.append(f'-"{value}"' if " " in value else f"-{value}")
    return " ".join([query, *clean]).strip()


def _video_packet(
    video: dict[str, Any],
    transcript: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = build_transcript_evidence(transcript, max_chars=3_000)
    compact_comments = compress_comments(comments, limit=12, max_chars_each=180)
    for index, comment in enumerate(compact_comments, start=1):
        if not comment.get("ref"):
            comment["ref"] = f"{video['id']}:sample-{index}"
    return {
        "video_id": video["id"],
        "title": video.get("title", ""),
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
        "source_quality": "timed_transcript"
        if evidence
        else "metadata_and_comments_only",
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

    for start in range(0, len(misses), BREAKDOWN_BATCH_SIZE):
        batch = misses[start : start + BREAKDOWN_BATCH_SIZE]
        response = gemini.generate_json(
            stage="影片證據分析",
            model=model,
            prompt=breakdown_batch_prompt(batch),
            schema=BREAKDOWN_BATCH_SCHEMA,
            max_output_tokens=min(3_800, 800 * len(batch) + 500),
            thinking_level=None,
        )
        valid_ids = {packet["video_id"] for packet in batch}
        for breakdown in response.get("videos", []):
            video_id = str(breakdown.get("video_id", ""))
            if video_id in valid_ids:
                output[video_id] = breakdown
                cache.set(key_by_id[video_id], breakdown, 604_800)

    return [
        output.get(packet["video_id"], _fallback_breakdown(packet))
        for packet in packets
    ]


def _safe_future_result(future: concurrent.futures.Future[Any]) -> list[dict[str, Any]]:
    try:
        return future.result()
    except Exception:
        return []


def _collect_comments(
    youtube: YouTubeData,
    videos: list[dict[str, Any]],
    existing: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    output = dict(existing or {})
    targets = [video for video in videos if video.get("id") not in output]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                youtube.sample_comments,
                video["id"],
                COMMENTS_PER_ORDER,
            ): video["id"]
            for video in targets
        }
        for future in concurrent.futures.as_completed(futures):
            output[futures[future]] = _safe_future_result(future)
    return output


def _collect_transcripts(
    youtube: YouTubeData,
    videos: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                youtube.transcript_segments,
                video["id"],
                video.get("market", "zh"),
            ): video["id"]
            for video in videos
        }
        for future in concurrent.futures.as_completed(futures):
            output[futures[future]] = _safe_future_result(future)
    return output


def run_pipeline(
    cfg: dict[str, Any], topic: str, exclusions: str = "", references: str = ""
) -> dict[str, Any]:
    admin = _is_admin()
    progress = ProgressView(admin)
    ledger = UsageLedger()
    cache = JsonTTLCache()
    gemini = GeminiClient(cfg["gemini_key"], ledger)
    youtube = YouTubeData(cfg["yt_key"], cache)
    result: dict[str, Any] = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "topic": topic,
        "exclusions": exclusions,
        "references": references,
        "cfg": {
            key: value
            for key, value in cfg.items()
            if key not in {"gemini_key", "yt_key"}
        },
        "collection_stats": {},
    }
    try:
        plan = gemini.generate_json(
            stage="關鍵字起點生成",
            model=cfg["pipeline_model"],
            prompt=keyword_plan_prompt(topic, exclusions, references),
            schema=KEYWORD_PLAN_SCHEMA,
            max_output_tokens=1_400,
            thinking_level="low",
        )
        if not plan.get("core_terms"):
            plan["core_terms"] = [topic]
        result["keyword_plan"] = plan
        progress.update(
            "正在整理公開資料…",
            "已完成第一輪搜尋起點；第二輪只允許從實際資料候選中選取。",
        )

        zh_seeds = []
        for field, limit in (
            ("core_terms", 4),
            ("question_terms", 4),
            ("problem_terms", 3),
            ("adjacent_terms", 3),
        ):
            zh_seeds.extend(plan.get(field, [])[:limit])
        en_seeds = plan.get("en_terms", [])[:7]
        zh_pool, zh_log = youtube.expand_keywords(zh_seeds, "zh", cfg["mine_rounds"])
        en_pool, en_log = youtube.expand_keywords(en_seeds, "en", cfg["mine_rounds"])
        zh_shortlist = prefilter_keyword_pool(zh_pool, 30)
        en_shortlist = prefilter_keyword_pool(en_pool, 30)
        selected = build_search_terms(
            plan,
            zh_shortlist,
            en_shortlist,
            zh_limit=cfg["n_zh"],
            en_limit=cfg["n_en"],
        )
        if not selected["zh"]:
            selected["zh"] = [
                {"kw": topic, "intent": "核心詞", "reason": "使用者輸入的主題"}
            ]
        result["selected_kws"] = selected
        result["mining_log"] = {"zh": zh_log, "en": en_log}

        pool_by_id: dict[str, dict[str, Any]] = {}
        search_jobs = []
        orders = ["relevance", "viewCount", "date"]
        for language in ("en", "zh"):
            for index, item in enumerate(selected.get(language, [])):
                research_keyword = item.get("kw", "")
                query = _apply_negatives(
                    research_keyword, plan.get("negative_keywords", [])
                )
                search_jobs.append(
                    (language, research_keyword, query, orders[index % len(orders)])
                )
        for language, research_keyword, query, order in search_jobs:
            found = youtube.search_videos(
                query,
                language,
                cfg["window_days"],
                cfg["per_kw"],
                order,
            )
            merge_search_results(
                pool_by_id,
                found,
                research_keyword=research_keyword,
                market=language,
                order=order,
            )

        reference_ids = parse_youtube_video_ids(references)
        if reference_ids:
            reference_videos = youtube.videos_by_ids(reference_ids, "zh")
            for video in reference_videos:
                merge_search_results(
                    pool_by_id,
                    [video],
                    research_keyword="使用者參考",
                    market=video.get("market", "zh"),
                    order="reference",
                )
                if video.get("id") in pool_by_id:
                    pool_by_id[video["id"]]["is_reference"] = True

        all_videos = list(pool_by_id.values())
        result["collection_stats"].update(
            {
                "first_round_search_terms": len(search_jobs),
                "first_round_unique_videos": len(all_videos),
                "reference_video_ids_parsed": len(reference_ids),
            }
        )
        if not all_videos:
            raise RuntimeError(
                "沒有取得可分析的公開影片，請調整主題或檢查 YouTube API Key。"
            )

        progress.update(
            "正在收斂值得看的線索…",
            f"去重後取得 {len(all_videos)} 支候選影片。",
        )
        try:
            relevance = gemini.generate_json(
                stage="候選相關性檢查",
                model=cfg["pipeline_model"],
                prompt=relevance_prompt(
                    topic,
                    sorted(
                        all_videos,
                        key=lambda video: (
                            bool(video.get("is_reference")),
                            int(video.get("view_count", 0) or 0),
                        ),
                        reverse=True,
                    )[:MAX_RELEVANCE_VIDEOS],
                    exclusions,
                    references,
                ),
                schema=RELEVANCE_SCHEMA,
                max_output_tokens=500,
                thinking_level="low",
            )
            irrelevant = set(relevance.get("irrelevant_ids", []))
            filtered = [
                video
                for video in all_videos
                if video["id"] not in irrelevant or video.get("is_reference")
            ]
            if filtered:
                all_videos = filtered
                pool_by_id = {video["id"]: video for video in all_videos}
        except Exception:
            pass
        result["collection_stats"]["after_relevance_videos"] = len(all_videos)

        # 第一輪只做便宜的本地指標，先讀少量留言，再由真實資料修正一次搜尋方向。
        attach_outlier_metrics_v2(all_videos, {}, {})
        discovery_candidates = pick_videos_diverse(
            all_videos,
            k=min(MAX_DISCOVERY_VIDEOS, len(all_videos)),
            min_views=cfg["min_views"],
            en_share=0.5,
            video_type=cfg["video_type"],
        )
        if not discovery_candidates:
            discovery_candidates = sorted(
                all_videos,
                key=lambda video: video.get("evidence_score", 0),
                reverse=True,
            )[:MAX_DISCOVERY_VIDEOS]
        references_in_pool = [
            video for video in all_videos if video.get("is_reference")
        ]
        discovery_candidates = list(
            {
                video["id"]: video
                for video in [*references_in_pool, *discovery_candidates]
            }.values()
        )[:MAX_DISCOVERY_VIDEOS]
        comments_map = _collect_comments(youtube, discovery_candidates)

        existing_terms = [
            item.get("kw", "")
            for market in ("zh", "en")
            for item in selected.get(market, [])
        ]
        reflow_candidates = build_reflow_candidates(
            discovery_candidates,
            comments_map,
            existing_terms,
            limit=48,
        )
        reflow_terms: list[dict[str, Any]] = []
        if reflow_candidates:
            try:
                reflow_response = gemini.generate_json(
                    stage="資料回流關鍵字",
                    model=cfg["pipeline_model"],
                    prompt=keyword_reflow_prompt(
                        topic, reflow_candidates, existing_terms
                    ),
                    schema=KEYWORD_REFLOW_SCHEMA,
                    max_output_tokens=800,
                    thinking_level="low",
                )
                reflow_terms = validate_reflow_selection(
                    reflow_response,
                    reflow_candidates,
                    existing_terms,
                    MAX_REFLOW_TERMS,
                )
            except Exception:
                reflow_terms = []

        for item in reflow_terms:
            market = item.get("market", "zh")
            selected.setdefault(market, []).append(item)
            query = _apply_negatives(item["kw"], plan.get("negative_keywords", []))
            found = youtube.search_videos(
                query,
                market,
                cfg["window_days"],
                cfg["per_kw"],
                "relevance",
            )
            merge_search_results(
                pool_by_id,
                found,
                research_keyword=item["kw"],
                market=market,
                order="relevance",
            )
        all_videos = list(pool_by_id.values())
        result["collection_stats"].update(
            {
                "reflow_terms": len(reflow_terms),
                "final_unique_videos": len(all_videos),
            }
        )
        result["selected_kws"] = selected
        result["reflow_candidates"] = reflow_candidates
        result["reflow_terms"] = reflow_terms
        progress.update(
            "正在比較不同來源…",
            f"第一輪後從真實資料追加 {len(reflow_terms)} 個搜尋方向；"
            f"目前共有 {len(all_videos)} 支去重候選。",
        )

        # 所有搜尋完成後才做一次完整頻道基準計算，避免第二輪重複花配額。
        profiles = youtube.channel_profiles(
            [video["channel_id"] for video in all_videos]
        )
        attach_outlier_metrics_v2(all_videos, profiles, {})
        preliminary = sorted(
            all_videos,
            key=lambda video: (
                video.get("evidence_score", 0),
                video.get("views_per_day", 0),
            ),
            reverse=True,
        )
        baseline_channels = list(
            dict.fromkeys(
                video.get("channel_id", "")
                for video in preliminary
                if video.get("channel_id")
            )
        )[:20]
        recent = youtube.recent_channel_videos(
            profiles, baseline_channels, max_results=15
        )
        attach_outlier_metrics_v2(all_videos, profiles, recent)
        try:
            observed_velocities = cache.record_video_observations(all_videos)
            rising_signals = derive_rising_signals(all_videos, observed_velocities)
        except Exception:
            rising_signals = derive_rising_signals(all_videos)
        coverage = market_coverage(all_videos)
        evidence_candidates = pick_videos_diverse(
            all_videos,
            k=min(MAX_EVIDENCE_PROBES, max(cfg["analyze_n"] + 4, 10)),
            min_views=cfg["min_views"],
            en_share=0.6,
            video_type=cfg["video_type"],
        )
        if not evidence_candidates:
            evidence_candidates = sorted(
                all_videos,
                key=lambda video: video.get("evidence_score", 0),
                reverse=True,
            )[:MAX_EVIDENCE_PROBES]
        evidence_candidates = list(
            {
                video["id"]: video
                for video in [*references_in_pool, *evidence_candidates]
            }.values()
        )[:MAX_EVIDENCE_PROBES]

        comments_map = _collect_comments(youtube, evidence_candidates, comments_map)
        transcript_map = _collect_transcripts(youtube, evidence_candidates)
        selected_videos = pick_evidence_ready_videos(
            evidence_candidates,
            transcript_map,
            comments_map,
            cfg["analyze_n"],
        )
        result["pool"] = all_videos
        result["market_coverage"] = coverage
        result["rising_signals"] = rising_signals
        result["analyzed_ids"] = [video["id"] for video in selected_videos]

        packets = [
            _video_packet(
                video,
                transcript_map.get(video["id"], []),
                comments_map.get(video["id"], []),
            )
            for video in selected_videos
        ]
        result["evidence_stats"] = [
            {
                "video_id": video["id"],
                "transcript_segments": len(transcript_map.get(video["id"], [])),
                "comments_collected": len(comments_map.get(video["id"], [])),
                "comments_sent": len(packet.get("comments", [])),
                "source_quality": packet.get("source_quality", ""),
            }
            for video, packet in zip(selected_videos, packets)
        ]
        result["collection_stats"].update(
            {
                "evidence_candidates_checked": len(evidence_candidates),
                "evidence_videos_analyzed": len(selected_videos),
                "transcript_segments": sum(
                    len(transcript_map.get(video["id"], []))
                    for video in selected_videos
                ),
                "comments_collected": sum(
                    len(comments_map.get(video["id"], [])) for video in selected_videos
                ),
                "comments_sent_to_model": sum(
                    len(packet.get("comments", [])) for packet in packets
                ),
            }
        )
        breakdowns = _analyze_packets(
            gemini,
            ledger,
            cache,
            cfg["pipeline_model"],
            packets,
        )
        result["breakdowns"] = breakdowns

        progress.update(
            "正在整理切角…",
            f"已從 {len(evidence_candidates)} 支候選中補位選出 "
            f"{len(selected_videos)} 支可用證據影片。",
        )
        compact_comments = {
            packet["video_id"]: packet.get("comments", []) for packet in packets
        }
        synthesis = gemini.generate_json(
            stage="跨來源比較歸納",
            model=cfg["pipeline_model"],
            prompt=research_synthesis_prompt(
                topic,
                selected,
                reflow_terms,
                breakdowns,
                all_videos,
                compact_comments,
                rising_signals,
            ),
            # 這一階段的內容已由本地驗證器鎖定來源；不送
            # response_schema，避免 Gemini 對複合歸納請求回報通用 400。
            schema=None,
            max_output_tokens=3_600,
            thinking_level=None,
            temperature=0.2,
        )
        synthesis = validate_research_synthesis(
            synthesis,
            all_videos,
            compact_comments,
        )
        if not synthesis.get("cross_layer_insights"):
            raise RuntimeError("跨來源資料不足，無法形成可追查的內容建議。")
        result["research_synthesis"] = synthesis

        angle_report = gemini.generate_json(
            stage="切角雷達生成",
            model=cfg["report_model"],
            prompt=angle_report_prompt(
                topic,
                synthesis,
                all_videos,
                cfg["n_angles"],
                exclusions,
                references,
            ),
            schema=ANGLE_REPORT_SCHEMA,
            max_output_tokens=5_200,
            thinking_level="medium",
            temperature=0.3,
        )
        angle_report["angles"] = angle_report.get("angles", [])[
            : min(cfg["n_angles"], 6)
        ]
        if not angle_report["angles"]:
            raise RuntimeError("模型沒有產出可用切角。")
        angle_report = validate_angle_evidence(
            angle_report, all_videos, synthesis, rising_signals
        )
        result["angle_report"] = angle_report
        result["report"] = render_angle_report(angle_report, topic, all_videos)
        result["usage"] = ledger.summary()
        result["youtube_usage"] = youtube.usage()
        result["youtube_errors"] = youtube.errors
        progress.complete()
        return result
    except Exception:
        LOGGER.exception("Angle Radar pipeline failed")
        progress.fail()
        raise


st.title("切角雷達")
st.caption("輸入一個主題，直接拿到可以拍什麼、怎麼拍，以及怎麼和現有內容拉開差異。")

has_secret_keys = bool(_secret("GEMINI_API_KEY") and _secret("YOUTUBE_API_KEY"))
with st.sidebar:
    if st.session_state.get("_wl_ok"):
        name = st.session_state.get("_wl_name") or "測試用戶"
        st.success(f"歡迎，{name}")
        remaining = st.session_state.get("_wl_remaining", 0)
        st.metric("剩餘次數", remaining)
        if remaining <= 0:
            st.caption("次數用完了，請找站主加值")

    if has_secret_keys:
        GEMINI_API_KEY = _secret("GEMINI_API_KEY")
        YOUTUBE_API_KEY = _secret("YOUTUBE_API_KEY")
    else:
        st.header("API 金鑰")
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")
        YOUTUBE_API_KEY = st.text_input("YouTube Data API Key", type="password")

    if _is_admin():
        st.markdown("---")
        st.markdown("**管理者設定**")
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
            N_ANGLES = st.slider("切角上限", 4, 6, 6)
            N_EN = st.slider("英文查詢數", 2, 8, 4)
            N_ZH = st.slider("中文查詢數", 3, 10, 6)
            MINE_ROUNDS = st.slider("探索廣度", 1, 3, 2)
            PER_KW = st.slider("每組候選數", 15, 40, 25)
            ANALYZE_N = st.slider("證據分析影片數", 5, 10, 8)
            WINDOW_DAYS = st.select_slider(
                "內容時間範圍", options=[30, 90, 180, 365], value=180
            )
            MIN_VIEWS = st.select_slider(
                "最低觀看", options=[1_000, 3_000, 5_000, 10_000, 30_000], value=3_000
            )
            VIDEO_TYPE = st.radio(
                "影片類型", ["全部", "僅長片", "僅 Shorts"], horizontal=True
            )
    else:
        REPORT_MODEL = DEFAULT_REPORT_MODEL
        PIPELINE_MODEL = DEFAULT_PIPELINE_MODEL
        N_ANGLES, N_EN, N_ZH, MINE_ROUNDS = 6, 4, 6, 2
        PER_KW, ANALYZE_N, WINDOW_DAYS, MIN_VIEWS = 25, 8, 180, 3_000
        VIDEO_TYPE = "全部"


topic = st.text_area(
    "你想拍什麼？",
    placeholder="例：想成為命理師，或把命理發展成工作",
    height=96,
    key="topic_input",
)
with st.expander("選填：縮小範圍"):
    exclusions = st.text_input(
        "不想看到什麼",
        placeholder="例：不要靈異故事、不要只談占卜準不準",
        key="exclusions_input",
    )
    references = st.text_input(
        "已有的參考內容",
        placeholder="可以貼一支或多支 YouTube 影片網址；沒有就留空",
        key="references_input",
    )

run_clicked = st.button(
    "開始找切角",
    type="primary",
    disabled=not topic.strip(),
    width="stretch",
)

if run_clicked:
    st.session_state.pop("radar_result", None)
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
                st.session_state["_wl_remaining"] = int(
                    response.get("remaining", 0) or 0
                )
            else:
                st.error(_WL_ERR.get(response.get("error", ""), "額度不足或服務異常"))
        if may_run:
            config = {
                "gemini_key": GEMINI_API_KEY,
                "yt_key": YOUTUBE_API_KEY,
                "report_model": REPORT_MODEL,
                "pipeline_model": PIPELINE_MODEL,
                "n_angles": N_ANGLES,
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
                pipeline_result = run_pipeline(
                    config, topic.strip(), exclusions.strip(), references.strip()
                )
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


if "radar_result" in st.session_state:
    result = st.session_state["radar_result"]
    st.markdown("---")
    st.markdown(f"# 「{result['topic']}」可以怎麼拍")
    st.markdown(result["angle_report"].get("radar_summary", ""))
    for index, angle in enumerate(result["angle_report"].get("angles", []), start=1):
        with st.container(border=True):
            st.markdown(render_action_card(angle, index))
            with st.expander("查看來源與限制"):
                st.markdown(render_action_evidence(angle, result["pool"]))

    st.markdown("---")
    st.markdown("# 把切角交給你的 AI")
    st.caption("展開想做的切角，複製 Prompt，再補上你的專業、案例與拍攝限制。")
    for index, angle in enumerate(result["angle_report"].get("angles", []), start=1):
        name = angle.get("angle_name", "未命名切角")
        with st.expander(f"{index}. {name}"):
            st.code(
                angle_development_prompt(result["topic"], angle, result["pool"]),
                language="text",
            )

    export = build_public_export(
        result["report"],
        result["angle_report"],
        result["pool"],
        result["created_at"],
        result["topic"],
    )
    st.download_button(
        "下載切角、Prompt 與引用來源",
        export,
        file_name=f"切角雷達_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
    )

    with st.container(border=True):
        st.markdown("#### 這批切角對你有幫助嗎？")
        verdict = st.radio(
            "最接近你的感受",
            ["有至少一個想深入", "有一些新線索", "大多原本就想得到", "沒有可用切角"],
            horizontal=True,
            key="angle_verdict",
        )
        feedback_note = st.text_input(
            "想補充什麼？（選填）",
            placeholder="例：來源有用，但切角仍太接近；或第 3 個是我沒想到的",
            key="angle_feedback_note",
        )
        if st.button("送出回饋", key="submit_angle_feedback"):
            if st.session_state.get("feedback_submitted_for") == result.get(
                "created_at"
            ):
                st.info("這份報告已經回覆過了，謝謝你。")
            else:
                saved = True
                if _wl_enabled():
                    feedback_response = _wl_api(
                        "feedback",
                        st.session_state.get("_wl_code", ""),
                        {
                            "direction": result.get("topic", "")[:120],
                            "verdict": verdict[:80],
                            "note": feedback_note[:500],
                        },
                    )
                    saved = bool(feedback_response.get("ok"))
                if saved:
                    st.session_state["feedback_submitted_for"] = result.get(
                        "created_at"
                    )
                    st.success("收到，謝謝你的回饋。")
                else:
                    st.warning("目前無法儲存，請稍後再試。")

    if _is_admin():
        st.markdown("---")
        st.markdown("## 管理者診斷")
        usage = result.get("usage", {})
        metric_cols = st.columns(4)
        metric_cols[0].metric("Input tokens", f"{usage.get('input_tokens', 0):,}")
        metric_cols[1].metric(
            "Output＋thinking",
            f"{usage.get('output_tokens', 0) + usage.get('thinking_tokens', 0):,}",
        )
        metric_cols[2].metric("本機快取命中", usage.get("local_cache_hits", 0))
        metric_cols[3].metric(
            "推估 Gemini 成本", f"NT${usage.get('estimated_twd', 0):.2f}"
        )
        st.caption(f"價格基準日 {usage.get('pricing_date', '—')}；估算不等同實際帳單。")

        with st.expander("模型使用明細"):
            usage_rows = usage.get("records", [])
            if usage_rows:
                st.dataframe(pd.DataFrame(usage_rows), width="stretch", hide_index=True)

        with st.expander("關鍵字與查詢診斷"):
            st.json({"資料漏斗": result.get("collection_stats", {})})
            st.json(result.get("keyword_plan", {}))
            st.json(result.get("selected_kws", {}))
            st.json({"資料回流詞": result.get("reflow_terms", [])})
            st.json(result.get("mining_log", {}))
            st.caption(f"YouTube 呼叫：{result.get('youtube_usage', {})}")
            if result.get("youtube_errors"):
                st.json(result["youtube_errors"])

        with st.expander("三層比較與跨層歸納"):
            st.json(result.get("research_synthesis", {}))

        with st.expander("樣本與影片評分"):
            st.json(result.get("market_coverage", {}))
            st.json({"rising_signals": result.get("rising_signals", [])})
            evidence_stats = {
                item.get("video_id", ""): item
                for item in result.get("evidence_stats", [])
            }
            rows = [
                {
                    "樣本": MARKET_LABEL.get(video.get("market"), video.get("market")),
                    "來源": video.get("origin"),
                    "標題": video.get("title"),
                    "觀看": video.get("view_count"),
                    "日均": video.get("views_per_day"),
                    "近期基準倍數": video.get("outlier_ratio"),
                    "基準樣本": video.get("baseline_sample_size"),
                    "證據分": video.get("evidence_score"),
                    "命中搜尋詞數": len(video.get("keyword_hits", [])),
                    "已分析": video.get("id") in result.get("analyzed_ids", []),
                    "字幕段數": evidence_stats.get(video.get("id", ""), {}).get(
                        "transcript_segments", 0
                    ),
                    "留言抓取": evidence_stats.get(video.get("id", ""), {}).get(
                        "comments_collected", 0
                    ),
                    "留言送模型": evidence_stats.get(video.get("id", ""), {}).get(
                        "comments_sent", 0
                    ),
                    "連結": video.get("url"),
                }
                for video in sorted(
                    result.get("pool", []),
                    key=lambda item: item.get("evidence_score", 0),
                    reverse=True,
                )
            ]
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        with st.expander("影片證據拆解"):
            video_map = {video["id"]: video for video in result.get("pool", [])}
            for breakdown in result.get("breakdowns", []):
                video = video_map.get(breakdown.get("video_id"), {})
                st.markdown(
                    f"#### [{video.get('title', breakdown.get('video_id', ''))}]"
                    f"({video.get('url', '')})"
                )
                st.markdown(render_breakdown(breakdown))
