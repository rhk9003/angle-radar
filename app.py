# ==========================================================
# 🍽 切角點單機（網紅版）— 獨立於 app.py 的單檔工具
#
# 定位：創作者有想切的方向時來「點單」，直接拿到可拍的切角：
#   拍什麼主題、用什麼呈現方式、為什麼這樣會吸引人。
# 輸入一個詞（口紅／面膜／增肌飲食）即可，管線全自動：
#   AI 生成中英種子字 → 迭代探勘關鍵字（含爬取上限）→ AI 自動選字
#   → 雙市場搜尋（以國外為主）→ 爆款偵測（outlier）
#   → 真字幕/Gemini 影片理解拆解 → 點單式切角菜單＋拍前必看片單
#
# 執行：streamlit run app.py
# 部署與白名單設定見 README.md
# ==========================================================

import streamlit as st
import requests
import json
import re
import google.generativeai as genai
from googleapiclient.discovery import build
import concurrent.futures
import pandas as pd
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 系統配置
# ==========================================

st.set_page_config(
    page_title="切角點單機",
    page_icon="🍽",
    layout="wide"
)

# 管線工作（選字、過濾、拆解）用低成本模型；菜單模型與管線模型皆可在側邊欄切換
# （3.1-flash-lite：輸入 $0.25/M、輸出 $1.50/M，比 2.5-flash 便宜且新一代；
#   深度模式看影片也用它最划算——影片輸入同樣 $0.25/M）
PIPELINE_MODEL = "gemini-3.1-flash-lite"

# 關鍵字探勘的爬取上限（保護機制，避免循環失控＋autocomplete 打太兇被 Google 暫時封 IP）
MAX_MINE_QUERIES = 60   # 每個市場每次分析最多打幾次 autocomplete
MAX_POOL_SIZE = 150     # 關鍵字池滿了就停，挖再多也用不到

# 修飾詞探針：逼出 autocomplete 平常不會主動給的長尾詞（沿用 app.py 驗證過的設計）
PROBE_WORDS = {
    "zh": {
        "suffix": ["教學", "推薦", "開箱", "實測", "新手", "vlog", "挑戰"],
        "prefix": ["怎麼"],
    },
    "en": {
        "suffix": ["tutorial", "challenge", "vlog", "for beginners", "tips", "i tried"],
        "prefix": ["how to", "best"],
    },
}

MARKET_LABEL = {"zh": "🇹🇼 中文", "en": "🌍 國外"}


# ==========================================
# 2. 小工具
# ==========================================

def parse_iso_duration(duration_str):
    """將 ISO 8601 時長 (PT12M34S) 轉為分鐘數"""
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str or '')
    if not m:
        return 0
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return round(h * 60 + mi + s / 60, 1)


def video_age_days(publish_time):
    """影片上架至今的天數（最小 1）"""
    try:
        dt = datetime.strptime(publish_time[:10], '%Y-%m-%d')
        return max((datetime.now() - dt).days, 1)
    except Exception:
        return 1


def fmt_num(n):
    """觀看數格式化：12.3萬 / 4,560"""
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 10000:
        return f"{n / 10000:.1f}萬"
    return f"{n:,}"


def gemini_json(api_key, prompt, model_version=None):
    """要求 Gemini 回傳 JSON 並穩健解析；失敗回傳 None。
    model_version 不指定時，於呼叫當下讀取全域 PIPELINE_MODEL（側邊欄可切換）"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_version or PIPELINE_MODEL)
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        text = response.text.strip()
    except Exception:
        return None
    # 剝除可能的 code fence 再解析
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


def gemini_text(api_key, prompt_parts, model_version=None):
    """一般文字生成；prompt_parts 可為字串或 parts list（含 file_data）。
    model_version 不指定時，於呼叫當下讀取全域 PIPELINE_MODEL（側邊欄可切換）"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_version or PIPELINE_MODEL)
    response = model.generate_content(prompt_parts)
    return response.text


# ==========================================
# 3. 資料層（YouTube 公開資料）
# ==========================================

def get_suggestions_with_scores(keyword, lang="zh-TW"):
    """YouTube autocomplete（免 API 配額），回傳 [(term, score), ...]"""
    try:
        url = "http://suggestqueries.google.com/complete/search"
        params = {"client": "chrome", "ds": "yt", "q": keyword, "hl": lang}
        response = requests.get(url, params=params, timeout=3)
        response.encoding = "utf-8"
        data = response.json()
        terms = data[1] if len(data) > 1 else []
        meta = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
        scores = meta.get("google:suggestrelevance", [])
        return [(t, scores[i] if i < len(scores) else 0) for i, t in enumerate(terms)]
    except Exception:
        return []


def _autocomplete_batch(queries, lang):
    """並行打一批 autocomplete，回傳 {term: score}（同字取最高分）"""
    found = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_suggestions_with_scores, q, lang): q for q in queries}
        for future in concurrent.futures.as_completed(futures):
            try:
                for term, score in future.result():
                    term = term.strip()
                    if term:
                        found[term] = max(found.get(term, 0), score)
            except Exception:
                pass
    return found


def ai_pick_expansion_directions(gemini_key, direction, fresh_terms, market, n=7):
    """模擬人工探勘循環裡「看到延伸字 → 挑有潛力的再往下挖」的那個決策"""
    label = "中文" if market == "zh" else "英文"
    terms_block = "\n".join(f"- {t}（熱度 {s}）" for t, s in fresh_terms[:60])
    prompt = f"""
使用者是影音創作者，方向：{direction}

以下是這一輪 YouTube autocomplete 新挖出的{label}關鍵字：
{terms_block}

請挑出最多 {n} 個「值得再丟回 YouTube autocomplete 往下挖」的字。
挑選邏輯（模擬人工探勘）：
- 挑「能開出新題材空間」的字：帶出新場景、新受眾、新形式的字
- 不挑同義重複的字，不挑已經太長太窄、再也挖不出東西的字
- 跟使用者方向無關的字直接跳過（防止越挖越偏題）
- 若沒有值得續挖的字，回傳空陣列

回傳 JSON：{{"expand": ["...", "..."]}}
"""
    data = gemini_json(gemini_key, prompt)
    if not data:
        # AI 失敗就挑熱度最高的續挖
        return [t for t, _ in fresh_terms[:n]]
    return [t for t in data.get("expand", []) if t][:n]


def expand_keywords_iterative(gemini_key, direction, seeds, market="zh", max_rounds=2, log=None,
                              max_queries=MAX_MINE_QUERIES, max_pool=MAX_POOL_SIZE):
    """迭代探勘：擴充 → AI 挑方向 → 再擴充 → …
    模擬人工使用時「初始輸入只是起點，價值來自循環往下挖」的行為。

    四道煞車，任一觸發就停：
      1. 輪數上限 max_rounds
      2. 查詢預算 max_queries（autocomplete 總次數）
      3. 字池上限 max_pool
      4. 乾涸偵測（這輪新字 < 5 個）

    回傳 [(term, score, round)]；log（若傳入 list）記錄每輪探勘過程。
    """
    lang = "zh-TW" if market == "zh" else "en"
    probes = PROBE_WORDS[market]
    pool = {}   # term -> (score, round)
    frontier = list(seeds)
    queries_used = 0

    for round_n in range(1, max_rounds + 1):
        queries = list(frontier)
        if round_n == 1:
            # 修飾詞探針只打在種子字上；後續輪次的長尾字再加探針只會挖出雜訊
            for s in frontier:
                queries += [f"{s} {p}" for p in probes["suffix"]]
                queries += [f"{p} {s}" for p in probes["prefix"]]
        # 裁到剩餘預算
        queries = queries[:max(max_queries - queries_used, 0)]
        if not queries:
            break
        queries_used += len(queries)

        found = _autocomplete_batch(queries, lang)
        fresh = {t: s for t, s in found.items() if t not in pool}
        for t, s in fresh.items():
            pool[t] = (s, round_n)
        if log is not None:
            log.append({"round": round_n, "frontier": list(frontier), "queries": len(queries),
                        "fresh": len(fresh), "total": len(pool)})
        if (round_n >= max_rounds or len(fresh) < 5
                or len(pool) >= max_pool or queries_used >= max_queries):
            break
        ranked_fresh = sorted(fresh.items(), key=lambda x: -x[1])
        frontier = ai_pick_expansion_directions(gemini_key, direction, ranked_fresh, market)
        if not frontier:
            break

    # 種子字本身也保留（沒有 autocomplete 分數就給 0）
    for s in seeds:
        pool.setdefault(s.strip(), (0, 1))
    ranked = sorted(((t, sc, rd) for t, (sc, rd) in pool.items()), key=lambda x: -x[1])
    return ranked[:max_pool]


def search_videos(api_key, query, market="en", published_within_days=180, max_results=15):
    """搜尋單一關鍵字（search.list = 100 units），並帶回影片詳細數據"""
    region = "TW" if market == "zh" else "US"
    rel_lang = "zh-Hant" if market == "zh" else "en"
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=published_within_days)
    ).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        youtube = build('youtube', 'v3', developerKey=api_key)
        search_response = youtube.search().list(
            q=query, part='id', maxResults=max_results, type='video',
            order='relevance', regionCode=region,
            relevanceLanguage=rel_lang, publishedAfter=published_after
        ).execute()
        video_ids = [it['id']['videoId'] for it in search_response.get('items', [])]
        if not video_ids:
            return []
        stats_response = youtube.videos().list(
            part='snippet,statistics,contentDetails', id=','.join(video_ids)
        ).execute()
        results = []
        for item in stats_response.get('items', []):
            sn = item['snippet']
            stt = item.get('statistics', {})
            results.append({
                'id': item['id'],
                'title': sn['title'],
                'description': (sn.get('description') or '')[:500],
                'channel': sn['channelTitle'],
                'channel_id': sn.get('channelId', ''),
                'publish_time': sn['publishedAt'],
                'view_count': int(stt.get('viewCount', 0)),
                'like_count': int(stt.get('likeCount', 0)),
                'comment_count': int(stt.get('commentCount', 0)),
                'duration_min': parse_iso_duration(item.get('contentDetails', {}).get('duration', '')),
                'url': f"https://www.youtube.com/watch?v={item['id']}",
                'source_keyword': query,
                'market': market,
            })
        return results
    except Exception as e:
        st.warning(f"YouTube 搜尋失敗（{query}）：{e}")
        return []


def fetch_channel_stats_full(api_key, channel_ids):
    """批次抓頻道統計（channels.list = 1 unit/50 個），爆款偵測的分母"""
    stats = {}
    ids = [c for c in set(channel_ids) if c]
    if not ids:
        return stats
    try:
        youtube = build('youtube', 'v3', developerKey=api_key)
        for i in range(0, len(ids), 50):
            resp = youtube.channels().list(
                part='statistics', id=','.join(ids[i:i + 50])
            ).execute()
            for item in resp.get('items', []):
                s = item.get('statistics', {})
                stats[item['id']] = {
                    'subs': int(s.get('subscriberCount', 0)),
                    'total_views': int(s.get('viewCount', 0)),
                    'video_count': int(s.get('videoCount', 0)),
                }
    except Exception:
        pass
    return stats


def attach_outlier_metrics(videos, ch_stats):
    """計算爆款指標：
    - outlier_ratio：觀看數 ÷ 頻道平均觀看（>3 即顯著跑贏該頻道日常水準）
    - vs_subs：觀看數 ÷ 訂閱數（>1 代表破圈觸及非粉絲）
    - views_per_day：近期動能
    """
    for v in videos:
        ch = ch_stats.get(v['channel_id'], {})
        subs = ch.get('subs', 0)
        avg = (ch['total_views'] / ch['video_count']) if ch.get('video_count') else 0
        v['subs'] = subs
        v['outlier_ratio'] = round(v['view_count'] / avg, 1) if avg > 0 else 0.0
        v['vs_subs'] = round(v['view_count'] / subs, 2) if subs > 0 else 0.0
        v['views_per_day'] = int(v['view_count'] / video_age_days(v['publish_time']))
    return videos


def pick_videos_to_analyze(videos, k=6, min_views=3000, en_share=0.6, video_type="全部"):
    """從爆款池挑出要拆解的影片：outlier 排序、單頻道最多 2 支、國外優先佔比"""
    def type_ok(v):
        if video_type == "僅長片":
            return v['duration_min'] > 1.05
        if video_type == "僅 Shorts":
            return 0 < v['duration_min'] <= 1.05
        return True

    eligible = [v for v in videos if v['view_count'] >= min_views and type_ok(v)]
    # outlier_ratio 為主、vs_subs 為輔（頻道統計缺漏時仍有排序依據）
    eligible.sort(key=lambda v: (v['outlier_ratio'], v['vs_subs'], v['view_count']), reverse=True)

    def take(pool, n, picked, per_channel):
        out = []
        for v in pool:
            if len(out) >= n:
                break
            if v['id'] in picked or per_channel.get(v['channel_id'], 0) >= 2:
                continue
            picked.add(v['id'])
            per_channel[v['channel_id']] = per_channel.get(v['channel_id'], 0) + 1
            out.append(v)
        return out

    picked_ids, per_channel = set(), {}
    en_pool = [v for v in eligible if v['market'] == 'en']
    zh_pool = [v for v in eligible if v['market'] == 'zh']
    n_en = min(round(k * en_share), len(en_pool)) if en_pool else 0
    selected = take(en_pool, n_en, picked_ids, per_channel)
    selected += take(zh_pool + en_pool, k - len(selected), picked_ids, per_channel)
    return selected


def fetch_top_comments(youtube_api_key, video_id, max_results=15):
    """熱門留言（commentThreads.list = 1 unit）——觀眾需求與爭論的訊號源"""
    try:
        youtube = build('youtube', 'v3', developerKey=youtube_api_key)
        response = youtube.commentThreads().list(
            part='snippet', videoId=video_id, order='relevance',
            maxResults=max_results, textFormat='plainText'
        ).execute()
        comments = []
        for item in response.get('items', []):
            sn = item['snippet']['topLevelComment']['snippet']
            comments.append({'text': sn['textDisplay'][:300], 'likes': sn.get('likeCount', 0)})
        return comments
    except Exception:
        return []


def fetch_transcript_text(video_id, market="en", max_chars=9000):
    """抓真字幕（youtube-transcript-api），相容新舊版 API；失敗回傳空字串"""
    langs = ['zh-TW', 'zh-Hant', 'zh', 'zh-CN', 'en'] if market == "zh" else ['en', 'en-US', 'en-GB', 'zh-TW', 'zh']
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            # 1.x 介面
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=langs)
            snippets = getattr(fetched, 'snippets', fetched)
            text = ' '.join(s.text if hasattr(s, 'text') else s['text'] for s in snippets)
        except AttributeError:
            # 0.x 介面
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            text = ' '.join(d['text'] for d in data)
        return re.sub(r'\s+', ' ', text).strip()[:max_chars]
    except Exception:
        return ''


# ==========================================
# 4. AI 層
# ==========================================

def ai_generate_seeds(gemini_key, direction, extra):
    """從使用者方向產出中英兩組種子搜尋詞（英文須是國外創作者圈道地用語，非直翻）"""
    prompt = f"""
你是 YouTube 內容策略專家。使用者是影音創作者，想切入的方向如下：

方向：{direction}
補充：{extra or "（無）"}

請產出兩組「拿去 YouTube 搜尋參考影片」用的種子關鍵字：
1. zh_seeds：3-4 個繁體中文搜尋詞（台灣觀眾實際會搜的字）
2. en_seeds：4-5 個英文搜尋詞（**必須是國外創作者圈道地的講法**，
   例如題材類型慣用語 "what I eat in a day"、"no bs guide"、"I tried X for 30 days" 這類真實存在的搜尋模式，
   嚴禁中文直翻）

種子字要能撈到「同類型創作者的影片」，偏內容題材，不要太廣泛的大字。

回傳 JSON：{{"zh_seeds": ["..."], "en_seeds": ["..."]}}
"""
    data = gemini_json(gemini_key, prompt)
    if not data:
        return {"zh_seeds": [direction[:20]], "en_seeds": []}
    return {
        "zh_seeds": [s for s in data.get("zh_seeds", []) if s][:4],
        "en_seeds": [s for s in data.get("en_seeds", []) if s][:5],
    }


def ai_select_keywords(gemini_key, direction, zh_pool, en_pool, n_zh, n_en):
    """AI 從擴充池自動選字（取代人工勾選），附選字理由"""
    def fmt_pool(pool):
        return "\n".join(f"- {t}（熱度 {s}｜第 {r} 輪挖出）" for t, s, r in pool[:100]) or "（清單為空）"

    prompt = f"""
你是 YouTube 內容策略專家。使用者方向：{direction}

以下是 YouTube autocomplete 擴充出的候選關鍵字（附 Google 相關性熱度分數，分數高代表搜尋需求強）：

【中文候選】
{fmt_pool(zh_pool)}

【英文候選】
{fmt_pool(en_pool)}

請選出最值得拿去搜尋參考影片的關鍵字：
- 中文選 {n_zh} 個、英文選 {n_en} 個
- 選字邏輯：優先選「能撈到爆款參考影片」的字——具體題材、有企劃感（挑戰、實測、vlog、教學⋯），
  兼顧不同意圖類型，避免同質字重複
- 英文選字特別重要：國外是找 reference 的主戰場，選國外創作者真的在用的題材字
- 若某語言候選清單為空，請依方向自行給出該語言道地的搜尋詞
- 每個字附一句選字理由（為什麼這個字撈得到好參考）

回傳 JSON：
{{"zh": [{{"kw": "...", "reason": "..."}}], "en": [{{"kw": "...", "reason": "..."}}]}}
"""
    data = gemini_json(gemini_key, prompt)
    if not data:
        # AI 失敗時退回：直接用熱度最高的候選字
        return {
            "zh": [{"kw": t, "reason": "autocomplete 熱度最高（AI 選字失敗的備援）"} for t, *_ in zh_pool[:n_zh]],
            "en": [{"kw": t, "reason": "autocomplete 熱度最高（AI 選字失敗的備援）"} for t, *_ in en_pool[:n_en]],
        }
    return {
        "zh": data.get("zh", [])[:n_zh],
        "en": data.get("en", [])[:n_en],
    }


def ai_filter_relevant(gemini_key, direction, videos):
    """踢掉跟方向明顯無關的搜尋結果（同字不同義的關鍵字污染，
    例如搜 agency 撈到招募代理）。回傳 (保留清單, 踢掉數)；AI 失敗時不過濾。"""
    if not videos:
        return videos, 0
    lines = "\n".join(f"- {v['id']}｜{v['title']}｜{v['channel']}" for v in videos)
    prompt = f"""
使用者是影音創作者，要找的參考方向：{direction}

以下是搜尋撈回的影片（id｜標題｜頻道）：
{lines}

請找出「跟方向明顯無關」的影片 id——典型情況是同字不同義（例如方向是廣告代操，
卻撈到招募代理、保險代理的影片）或完全不同產業的內容。
判斷從寬：只踢明顯無關的，模糊地帶一律保留。

回傳 JSON：{{"irrelevant_ids": ["...", "..."]}}
"""
    data = gemini_json(gemini_key, prompt)
    if not data or not isinstance(data.get("irrelevant_ids"), list):
        return videos, 0
    bad = set(data["irrelevant_ids"])
    kept = [v for v in videos if v['id'] not in bad]
    if not kept:
        # 全部被踢代表判斷失準，寧可不過濾
        return videos, 0
    return kept, len(videos) - len(kept)


def ai_breakdown_video(gemini_key, video, transcript, comments, deep_mode=False):
    """拆解單支影片：hook / 結構 / 爆款原因 / 可複製切角 / 留言洞察。
    deep_mode=True 時把 YouTube URL 以 file_data 餵給 Gemini 原生影片理解（真的看畫面）"""
    comment_lines = "\n".join(
        f"- ({c['likes']} 讚) {c['text']}" for c in comments[:15]
    ) or "（無留言資料）"

    stats_line = (
        f"觀看 {fmt_num(video['view_count'])}｜頻道訂閱 {fmt_num(video['subs'])}｜"
        f"爆款倍率 {video['outlier_ratio']} 倍（vs 頻道平均）｜日均觀看 {fmt_num(video['views_per_day'])}｜"
        f"片長 {video['duration_min']} 分鐘｜市場 {MARKET_LABEL[video['market']]}"
    )

    base_prompt = f"""
你是影音內容拆解專家。請拆解這支 YouTube 影片，供另一位創作者規劃自己的企劃時參考。

影片標題：{video['title']}
頻道：{video['channel']}
數據：{stats_line}

熱門留言（觀眾真實反應）：
{comment_lines}

請用繁體中文輸出（精簡、每點 1-3 句）：
1. **一句話主題**：這支影片在做什麼
2. **開場 Hook**：前 30 秒用了什麼手法抓人（具體描述句子/畫面/懸念設計）
3. **內容結構**：段落節奏怎麼鋪（開頭→中段→結尾）
4. **爆款原因推測**：結合上面的數據（特別是爆款倍率），推測它為什麼跑贏
5. **可複製的切角**：另一位創作者可以借走的企劃點（2-3 個）
6. **留言洞察**：觀眾在留言裡表達的需求、痛點或爭論（沒有留言就寫「無」）
"""

    try:
        if deep_mode:
            # Gemini 原生影片理解：真的看畫面與節奏（較慢、token 較多）
            parts = [
                {"file_data": {"file_uri": video['url']}},
                base_prompt + "\n（你可以直接觀看這支影片，請以實際畫面與口播內容為準）",
            ]
            return gemini_text(gemini_key, parts), "deep"
        if transcript:
            return gemini_text(
                gemini_key,
                base_prompt + f"\n影片字幕逐字稿（節錄）：\n{transcript}\n（請以逐字稿為準，不要腦補）"
            ), "transcript"
        # 沒字幕、沒開深度模式：僅靠標題＋數據＋留言，明確標注依據有限
        return gemini_text(
            gemini_key,
            base_prompt + "\n（注意：此影片無字幕可用，僅能依標題、數據與留言推測，"
                          "請在輸出開頭標注「⚠️ 無字幕，以下為有限資訊推測」）"
        ), "meta_only"
    except Exception as e:
        # 深度模式失敗時退回字幕/metadata
        if deep_mode:
            return ai_breakdown_video(gemini_key, video, transcript, comments, deep_mode=False)
        return f"拆解失敗：{e}", "failed"


def ai_generate_menu(gemini_key, model_version, direction, extra, adjust_note,
                     selected_kws, breakdowns, pool_videos, n_topics):
    """整合所有素材，產出「點單式」切角菜單：拍什麼、怎麼呈現、為什麼會吸引人"""
    kw_lines = "\n".join(
        f"- [{MARKET_LABEL[m]}] {item['kw']}：{item.get('reason', '')}"
        for m in ("en", "zh") for item in selected_kws.get(m, [])
    )

    bd_blocks = []
    for b in breakdowns:
        v = b['video']
        bd_blocks.append(
            f"### {MARKET_LABEL[v['market']]}｜{v['title']}\n"
            f"頻道：{v['channel']}｜觀看 {fmt_num(v['view_count'])}｜訂閱 {fmt_num(v['subs'])}｜"
            f"爆款倍率 {v['outlier_ratio']} 倍｜日均 {fmt_num(v['views_per_day'])}｜"
            f"片長 {v['duration_min']} 分｜{v['url']}\n"
            f"{b['analysis'][:2200]}"
        )

    # 爆款池摘要（沒拆解的也給 AI 看標題與數據，用來判斷市場全貌與中文市場缺口）
    pool_lines = []
    for v in sorted(pool_videos, key=lambda x: -x['outlier_ratio'])[:40]:
        pool_lines.append(
            f"- [{MARKET_LABEL[v['market']]}] {v['title']}｜{v['channel']}｜"
            f"觀看 {fmt_num(v['view_count'])}｜爆款倍率 {v['outlier_ratio']}｜{v['url']}"
        )

    adjust_block = f"\n客人對上一份菜單的調整要求（必須遵守）：{adjust_note}\n" if adjust_note else ""

    prompt = f"""
你是內容切角顧問。一位影音創作者來「點單」，點的方向是：「{direction}」
補充：{extra or "（無）"}
{adjust_block}
他沒時間也不想看數據報告。你要像餐廳老闆遞菜單一樣，直接告訴他：
**可以拍什麼主題、用什麼呈現方式、為什麼這樣會吸引人。**

以下是自動蒐集的市場素材（你的判斷依據，不要原樣輸出給他）：

## AI 選定的搜尋關鍵字（附理由）
{kw_lines}

## 深度拆解的參考影片（{len(bd_blocks)} 支）
{chr(10).join(bd_blocks)}

## 爆款池總表（含未拆解影片，供判斷市場全貌與中文市場現況）
{chr(10).join(pool_lines)}

---

請用繁體中文輸出「切角菜單」，格式如下（Markdown）：

# 🍽 「{direction}」切角菜單

（開場一句話：這個題材現在的機會在哪，40 字內，講人話）

---

### 1️⃣ 企劃名（10 字內、有記憶點）
- 🎬 **拍什麼**：一句話說完這支片的內容
- 🎭 **怎麼呈現**：形式（盲測／一週實測／對比實驗／挑戰／vlog⋯）＋開場第一句（寫到可以照唸）
- 🧲 **為什麼會吸引人**：用人話講機制（反差／懸念／幫觀眾省錢／資訊差／共鳴⋯），接一行證據：國外同款《標題》(連結) 做到頻道平均 N 倍觀看
- 🏄 **想蹭它**：怎麼借參考影片的現成流量——回應影片／補充觀點／標題融合它的關鍵字／它留言區在吵什麼就拍什麼（挑最適合這張卡的一招，講具體）
- 🥊 **想超越它**：參考影片沒講清楚或做得弱的地方，你怎麼一支打過它——補實作步驟／更在地案例／視覺升級／更新資訊（點出具體的那個弱點）
- 🇹🇼 **中文市場**：🟢 樣本中沒看到有人做／🟡 有人做但角度舊／🔴 很多人做（附一句判斷依據）

（每張卡片就這 6 個欄位、每欄一行，禁止展開長篇。共 {n_topics} 張，依推薦順序排列，卡片之間用 --- 分隔）

---

## 👑 老闆推薦
- **先拍這張**：第 N 張，一句話講為什麼
- **拍前必看**（親自看過再開拍——hook 的節奏感要自己看才能內化）：
  1. 《標題》(連結)｜N 分鐘｜**看什麼**：一句話點出要注意的地方
  2. ⋯（共 3 支，優先挑國外爆款）

規則：
- 直接從「# 🍽」標題開始輸出，前面不准有任何開場白或聊天句
- 講人話，禁止行銷黑話（「賦能」「痛點」「聲量」這類字全部不准出現）
- 證據紀律：挑證據優先選「倍率高且觀看數有量」的影片；小頻道小樣本要標注（小樣本）；
  完全沒有影片證據、純從搜尋需求推導的卡最多 1 張，且必須標注「⚠️ 純需求推導，無爆款驗證」
- 「中文市場」紅綠燈只能以本次爆款池的中文樣本為據，措辭是「樣本中沒看到」而非全市場斷言；
  若中文樣本多為簡體／中國市場內容，要點出「台灣本土內容更少見」這個機會訊號
- 「怎麼呈現」要具體到看完就知道怎麼開拍
- 若補充裡有拍片目的，切角要對齊：接案信任→破解迷思／幫觀眾避坑／專業展示；
  導購賣課→實測／比較／省錢；漲粉曝光→挑戰／反差／共鳴
- 「想蹭它」的招式庫：回應影片、補充觀點、關聯標題融合對方關鍵字、24-48 小時時效跟進、
  對方留言區的爭論與未解問題就是現成題目
- 「想超越它」的招式庫：深挖對方沒講清楚的、只講概念你補實作步驟、換更在地的案例、
  視覺化升級（動畫／圖表／實拍）、展示對方沒有的權威性或第一手數據
"""
    return gemini_text(gemini_key, prompt, model_version=model_version)


# ==========================================
# 5. 管線協調器
# ==========================================

def run_pipeline(cfg):
    """全自動管線；回傳結果 dict 存入 session_state"""
    result = {"created_at": datetime.now().strftime('%Y-%m-%d %H:%M')}

    # --- Stage 1：AI 生成種子字 ---
    with st.status("🧠 STEP 1｜AI 解析方向、生成中英種子字…", expanded=True) as status:
        seeds = ai_generate_seeds(cfg['gemini_key'], cfg['direction'], cfg['extra'])
        st.write(f"中文種子：{'、'.join(seeds['zh_seeds']) or '（無）'}")
        st.write(f"英文種子：{'、'.join(seeds['en_seeds']) or '（無）'}")
        result['seeds'] = seeds
        status.update(label="✅ STEP 1｜種子字完成", state="complete", expanded=False)

    # --- Stage 2：迭代探勘關鍵字（擴充 → AI 挑方向續挖 → 再擴充；autocomplete 免配額）---
    with st.status("⛏ STEP 2｜迭代探勘關鍵字（模擬人工循環往下挖）…", expanded=True) as status:
        mining_log = {"zh": [], "en": []}
        zh_pool, en_pool = [], []
        for market, pool_seeds in (("en", seeds['en_seeds']), ("zh", seeds['zh_seeds'])):
            if not pool_seeds:
                continue
            ranked = expand_keywords_iterative(
                cfg['gemini_key'], cfg['direction'], pool_seeds,
                market, cfg['mine_rounds'], mining_log[market]
            )
            if market == "zh":
                zh_pool = ranked
            else:
                en_pool = ranked
            for entry in mining_log[market]:
                st.write(
                    f"{MARKET_LABEL[market]} 第 {entry['round']} 輪：從 {len(entry['frontier'])} 個方向"
                    f"挖出 {entry['fresh']} 個新字（累計 {entry['total']}｜查詢 {entry['queries']} 次）"
                )
        result['mining_log'] = mining_log
        status.update(label=f"✅ STEP 2｜探勘完成（中 {len(zh_pool)}／英 {len(en_pool)} 字）",
                      state="complete", expanded=False)

    # --- Stage 3：AI 自動選字 ---
    with st.status("🎯 STEP 3｜AI 自動選字（取代人工勾選）…", expanded=True) as status:
        selected = ai_select_keywords(
            cfg['gemini_key'], cfg['direction'], zh_pool, en_pool,
            cfg['n_zh'], cfg['n_en']
        )
        result['selected_kws'] = selected
        for m in ("en", "zh"):
            for item in selected.get(m, []):
                st.write(f"[{MARKET_LABEL[m]}] **{item['kw']}** — {item.get('reason', '')}")
        status.update(label="✅ STEP 3｜選字完成", state="complete", expanded=False)

    # --- Stage 4：雙市場搜尋 ---
    all_videos, seen = [], set()
    n_search = len(selected.get('zh', [])) + len(selected.get('en', []))
    with st.status(f"📡 STEP 4｜搜尋雙市場影片（{n_search} 個關鍵字）…", expanded=True) as status:
        prog = st.progress(0.0)
        done = 0
        for market in ("en", "zh"):
            for item in selected.get(market, []):
                vids = search_videos(
                    cfg['yt_key'], item['kw'], market,
                    cfg['window_days'], cfg['per_kw']
                )
                for v in vids:
                    if v['id'] not in seen:
                        seen.add(v['id'])
                        all_videos.append(v)
                done += 1
                prog.progress(done / max(n_search, 1))
        st.write(f"共撈到 {len(all_videos)} 支不重複影片")
        status.update(label=f"✅ STEP 4｜搜尋完成（{len(all_videos)} 支）", state="complete", expanded=False)

    if not all_videos:
        st.error("搜尋不到任何影片——請檢查 YouTube API Key 或把方向寫得更具體一點。")
        return None

    # --- Stage 5：相關性過濾＋爆款偵測 ---
    with st.status("🔥 STEP 5｜相關性過濾＋爆款偵測…", expanded=True) as status:
        all_videos, dropped = ai_filter_relevant(cfg['gemini_key'], cfg['direction'], all_videos)
        if dropped:
            st.write(f"🧹 踢掉 {dropped} 支跟方向無關的影片（同字不同義的搜尋污染）")
        ch_stats = fetch_channel_stats_full(cfg['yt_key'], [v['channel_id'] for v in all_videos])
        all_videos = attach_outlier_metrics(all_videos, ch_stats)
        to_analyze = pick_videos_to_analyze(
            all_videos, cfg['analyze_n'], cfg['min_views'],
            en_share=0.6, video_type=cfg['video_type']
        )
        result['pool'] = all_videos
        result['analyzed_ids'] = [v['id'] for v in to_analyze]
        top_line = "、".join(f"{v['title'][:24]}…（{v['outlier_ratio']}x）" for v in to_analyze[:3])
        st.write(f"選出 {len(to_analyze)} 支拆解（國外優先）：{top_line}")
        status.update(label=f"✅ STEP 5｜爆款偵測完成（拆解 {len(to_analyze)} 支）",
                      state="complete", expanded=False)

    # --- Stage 6：抓字幕與留言（並行）---
    with st.status("💬 STEP 6｜抓取真字幕與熱門留言…", expanded=True) as status:
        transcripts, comments_map = {}, {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            t_futures = {ex.submit(fetch_transcript_text, v['id'], v['market']): v['id'] for v in to_analyze}
            c_futures = {ex.submit(fetch_top_comments, cfg['yt_key'], v['id']): v['id'] for v in to_analyze}
            for f in concurrent.futures.as_completed(list(t_futures) + list(c_futures)):
                vid = t_futures.get(f) or c_futures.get(f)
                try:
                    r = f.result()
                except Exception:
                    r = '' if f in t_futures else []
                if f in t_futures:
                    transcripts[vid] = r
                else:
                    comments_map[vid] = r
        got_t = sum(1 for t in transcripts.values() if t)
        st.write(f"字幕成功 {got_t}/{len(to_analyze)} 支｜留言成功 "
                 f"{sum(1 for c in comments_map.values() if c)}/{len(to_analyze)} 支")
        status.update(label=f"✅ STEP 6｜字幕 {got_t} 支、留言完成", state="complete", expanded=False)

    # --- Stage 7：AI 拆解影片（並行）---
    mode_label = "Gemini 直接看影片" if cfg['deep_mode'] else "字幕＋留言"
    with st.status(f"🧩 STEP 7｜AI 拆解 {len(to_analyze)} 支影片（{mode_label}）…", expanded=True) as status:
        prog = st.progress(0.0)
        breakdowns = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(
                    ai_breakdown_video, cfg['gemini_key'], v,
                    transcripts.get(v['id'], ''), comments_map.get(v['id'], []),
                    cfg['deep_mode']
                ): v for v in to_analyze
            }
            done = 0
            for f in concurrent.futures.as_completed(futures):
                v = futures[f]
                try:
                    analysis, source = f.result()
                except Exception as e:
                    analysis, source = f"拆解失敗：{e}", "failed"
                breakdowns.append({'video': v, 'analysis': analysis, 'source': source})
                done += 1
                prog.progress(done / len(futures))
                st.write(f"✓ {v['title'][:40]}（依據：{source}）")
        # 依爆款倍率排序，讓報告先看到最強的
        breakdowns.sort(key=lambda b: -b['video']['outlier_ratio'])
        result['breakdowns'] = breakdowns
        status.update(label=f"✅ STEP 7｜拆解完成（{len(breakdowns)} 支）", state="complete", expanded=False)

    # --- Stage 8：產出切角菜單 ---
    with st.status(f"🍽 STEP 8｜產出切角菜單（{cfg['report_model']}）…", expanded=True) as status:
        report = ai_generate_menu(
            cfg['gemini_key'], cfg['report_model'], cfg['direction'], cfg['extra'],
            None, result['selected_kws'], breakdowns, all_videos, cfg['n_topics']
        )
        result['report'] = report
        status.update(label="✅ STEP 8｜菜單完成", state="complete", expanded=False)

    quota = n_search * 100 + (len(all_videos) // 50 + 1) * 2 + len(to_analyze)
    result['quota_estimate'] = quota
    return result


# ==========================================
# 6. 報告匯出
# ==========================================

def build_export_md(result, cfg):
    """報告＋附錄（選字表、爆款池）打包成一份 Markdown"""
    lines = [result['report'], "\n\n---\n\n# 附錄", f"\n_生成時間：{result['created_at']}_\n"]
    lines.append("\n## 附錄 A｜AI 選字結果\n\n| 市場 | 關鍵字 | 選字理由 |\n|---|---|---|")
    for m in ("en", "zh"):
        for item in result['selected_kws'].get(m, []):
            lines.append(f"| {MARKET_LABEL[m]} | {item['kw']} | {item.get('reason', '')} |")
    lines.append("\n## 附錄 B｜爆款影片池（前 30，依爆款倍率）\n\n"
                 "| 市場 | 標題 | 頻道 | 觀看 | 訂閱 | 爆款倍率 | 日均觀看 | 連結 |\n"
                 "|---|---|---|---|---|---|---|---|")
    for v in sorted(result['pool'], key=lambda x: -x['outlier_ratio'])[:30]:
        lines.append(
            f"| {MARKET_LABEL[v['market']]} | {v['title'][:60]} | {v['channel'][:20]} | "
            f"{fmt_num(v['view_count'])} | {fmt_num(v['subs'])} | {v['outlier_ratio']} | "
            f"{fmt_num(v['views_per_day'])} | {v['url']} |"
        )
    return "\n".join(lines)


# ==========================================
# 7. UI
# ==========================================

st.title("🍽 切角點單機")
st.caption("像點單一樣：輸入你想切的方向 → 直接拿到可拍的切角——拍什麼、怎麼呈現、為什麼會吸引人")

def _secret(name):
    """從 .streamlit/secrets.toml 讀 key（檔案不存在或沒設就回空字串）"""
    try:
        return st.secrets.get(name, "") or ""
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════
# 白名單（剩餘次數倒扣模型）
#
# 由一個綁在白名單 Google Sheet 上的 Apps Script Web App 負責讀寫，
# app 只呼叫它的 HTTP 端點；Sheet 完全私有、免服務帳號金鑰。
# Sheet 欄位（第一列表頭）：code | name | remaining | deep_mode
#   remaining：剩餘可用次數，每成功點單一次由 GAS 扣 1，扣到 0 就不能用；加值＝把數字改大
#   deep_mode：FALSE 關閉該用戶深度拆解（空白＝允許）
#
# secrets 需要：
#   WHITELIST_API_URL = GAS Web App 的 /exec 網址
#   WHITELIST_API_KEY = 與 GAS 內 API_KEY 相同的密鑰
# 兩者都沒設 → 白名單關閉（本機自用直接進）。GAS 程式與設定見 whitelist_gas/。
# ══════════════════════════════════════════════════════════
def _wl_enabled():
    return bool(_secret("WHITELIST_API_URL"))

def _is_admin():
    """管理者才看得到模型設定與進階設定。本機無白名單時視為管理者；
    有白名單時，登入碼需等於 secrets 的 ADMIN_CODE"""
    if not _wl_enabled():
        return True
    ac = _secret("ADMIN_CODE")
    return bool(ac) and st.session_state.get("_wl_code", "") == ac

def _wl_api(action, code):
    """呼叫 GAS 白名單 API。回 dict；失敗回 {'ok': False, 'error': ...}"""
    try:
        resp = requests.get(_secret("WHITELIST_API_URL"), params={
            "key": _secret("WHITELIST_API_KEY"), "action": action, "code": code.strip(),
        }, timeout=20)
        return resp.json()
    except Exception:
        return {"ok": False, "error": "api_error"}

_WL_ERR = {
    "not_found": "通行碼不在名單中，跟站主索取試用碼 🙏",
    "depleted": "你的剩餘次數是 0 了，跟站主加值吧 🙏",
    "unauthorized": "白名單設定有誤（密鑰不符），請聯絡站主",
    "bad_headers": "白名單表頭需含 code 與 remaining 欄，請聯絡站主",
    "api_error": "白名單服務連線失敗，請稍後再試",
}

if _wl_enabled() and not st.session_state.get("_wl_ok"):
    st.subheader("🔑 測試通行")
    st.caption("這是邀請制的內測，請輸入站主給你的試用碼")
    _code = st.text_input("試用碼", label_visibility="collapsed", placeholder="輸入試用碼")
    if st.button("進入", type="primary"):
        info = _wl_api("check", _code)
        if info.get("ok") or info.get("found"):
            st.session_state.update({
                "_wl_ok": True, "_wl_code": _code.strip(),
                "_wl_name": info.get("name", ""),
                "_wl_remaining": int(info.get("remaining", 0) or 0),
                "_wl_deep": bool(info.get("deep", True)),
            })
            st.rerun()
        else:
            st.error(_WL_ERR.get(info.get("error", ""), "通行碼無效"))
    st.stop()

# 金鑰：站主已在 secrets 設定就直接用（部署給朋友時，朋友看不到也不用填）；
# 否則（本機自用）在側邊欄手動填。
_has_secret_keys = bool(_secret("GEMINI_API_KEY") and _secret("YOUTUBE_API_KEY"))
with st.sidebar:
    if st.session_state.get("_wl_ok"):
        _nm = st.session_state.get("_wl_name") or "測試用戶"
        st.success(f"歡迎，{_nm} 👋")
        _rem = st.session_state.get("_wl_remaining", 0)
        st.metric("剩餘次數", _rem, help="每成功出一份菜單扣 1 次；用完請找站主加值")
        if _rem <= 0:
            st.caption("⚠️ 次數用完了，找站主加值")
        if not st.session_state.get("_wl_deep", True):
            st.caption("🎥 深度拆解未對你開放")
    st.header("🔑 API 金鑰")
    if _has_secret_keys:
        GEMINI_API_KEY = _secret("GEMINI_API_KEY")
        YOUTUBE_API_KEY = _secret("YOUTUBE_API_KEY")
        st.caption("✅ 金鑰已由站主設定")
    else:
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password",
                                       value=_secret("GEMINI_API_KEY"))
        YOUTUBE_API_KEY = st.text_input("YouTube Data API Key", type="password",
                                        value=_secret("YOUTUBE_API_KEY"),
                                        help="需至 Google Cloud Console 啟用 YouTube Data API v3")
        st.caption("💡 把 key 存進 `.streamlit/secrets.toml` 就不用每次填")

    # 深度拆解：依 deep_mode 權限，有權限者（含一般試用者）都能自己開關
    _deep_allowed = st.session_state.get("_wl_deep", True)
    DEEP_MODE = st.toggle(
        "🎥 深度拆解（Gemini 直接看影片）",
        value=False,
        disabled=not _deep_allowed,
        help=("開啟後 Gemini 會實際觀看影片畫面來分析 hook 與節奏，較慢、token 消耗較多；"
              "關閉時使用真字幕＋留言分析（快速）") if _deep_allowed
             else "此功能未對你的帳號開放，請洽站主"
    )
    if not _deep_allowed:
        DEEP_MODE = False

    if _is_admin():
        # 管理者：可調模型與進階參數
        st.markdown("---")
        st.markdown("**模型設定** 🔧")
        REPORT_MODEL = st.selectbox(
            "菜單生成模型",
            ["gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3-flash-preview",
             "gemini-2.5-pro", "gemini-2.5-flash"],
            help="菜單品質的核心。3.5-flash 是新一代中階旗艦（~NT$2/輪）；"
                 "3.1-pro 最強但稍貴；預算極省選 3-flash-preview"
        )
        PIPELINE_MODEL = st.selectbox(
            "管線模型（選字／過濾／拆解）",
            ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-2.5-flash"],
            help="機械工作用便宜模型即可。3.1-flash-lite 最省（含深度模式看影片也最划算）；"
                 "若你的 key 打不到新模型，退回 2.5-flash"
        )
        with st.expander("⚙️ 進階設定 🔧"):
            N_TOPICS = st.slider("菜單切角數", 4, 10, 6)
            N_EN = st.slider("英文（國外）關鍵字數", 2, 8, 5,
                             help="國外是找 reference 的主戰場，建議多於中文")
            N_ZH = st.slider("中文關鍵字數", 1, 6, 3,
                             help="中文搜尋主要用來驗證『這題在中文市場有沒有人做』")
            MINE_ROUNDS = st.slider("關鍵字探勘輪數", 1, 3, 2,
                                    help="模擬人工「看到延伸字 → 挑有潛力的 → 再往下挖」的循環，"
                                         "每輪由 AI 決定哪些字值得續挖。autocomplete 免配額，多挖不花錢")
            PER_KW = st.slider("每個關鍵字抓片數", 8, 25, 15)
            ANALYZE_N = st.slider("深度拆解影片數", 3, 10, 6)
            WINDOW_DAYS = st.select_slider("只看最近", options=[30, 90, 180, 365], value=180,
                                           help="找 reference 要看近期爆款，太舊的參考價值低")
            MIN_VIEWS = st.select_slider("最低觀看數門檻", options=[1000, 3000, 5000, 10000, 30000], value=3000)
            VIDEO_TYPE = st.radio("影片類型", ["全部", "僅長片", "僅 Shorts"], horizontal=True)
        quota_est = (N_EN + N_ZH) * 100 + 20
        st.caption(f"📊 單次分析約消耗 YouTube API 配額 ~{quota_est} units（每日免費 10,000）")
    else:
        # 一般試用者：固定預設值，不顯示可調 UI（控成本、介面乾淨）
        REPORT_MODEL = "gemini-3.5-flash"
        PIPELINE_MODEL = "gemini-3.1-flash-lite"
        N_TOPICS, N_EN, N_ZH, MINE_ROUNDS = 6, 5, 3, 2
        PER_KW, ANALYZE_N, WINDOW_DAYS, MIN_VIEWS = 15, 6, 180, 3000
        VIDEO_TYPE = "全部"

direction = st.text_area(
    "🧭 你想切的方向（一個詞也行）",
    placeholder="例：口紅／面膜／增肌飲食／新手理財⋯⋯想拍什麼就點什麼",
    height=80,
)

with st.expander("🎯 讓切角更貼你（全部選填，切角會更準）"):
    col_a, col_b = st.columns(2)
    with col_a:
        who = st.text_input("我是誰／頻道在做什麼",
                            placeholder="例：接案廣告投手，頻道剛起步")
        goal = st.selectbox("拍片目的", [
            "不限", "漲粉曝光", "建立專業信任（接案／接業配）",
            "導購／賣課", "經營個人品牌",
        ])
    with col_b:
        audience = st.text_input("想吸引誰",
                                 placeholder="例：30-45 歲的中小企業老闆")
        form_pref = st.selectbox("呈現偏好", [
            "不限", "教學解說", "實測／挑戰", "觀點評論", "vlog／情境短劇",
        ])
    free_note = st.text_input("其他補充",
                              placeholder="不想做的題材、想模仿的頻道、片長偏好…")

_extra_parts = []
if who.strip():
    _extra_parts.append(f"我是誰：{who.strip()}")
if audience.strip():
    _extra_parts.append(f"目標觀眾：{audience.strip()}")
if goal != "不限":
    _extra_parts.append(f"拍片目的：{goal}")
if form_pref != "不限":
    _extra_parts.append(f"呈現偏好：{form_pref}")
if free_note.strip():
    _extra_parts.append(f"補充：{free_note.strip()}")
extra = "；".join(_extra_parts)

col_run, col_hint = st.columns([1, 3])
with col_run:
    run_clicked = st.button("🍽 幫我出菜單", type="primary", use_container_width=True)
with col_hint:
    st.caption("約 2-5 分鐘：AI 選字 → 掃描國內外爆款 → 拆解參考影片 → 出切角菜單")

if run_clicked:
    _code = st.session_state.get("_wl_code", "")
    if not GEMINI_API_KEY or not YOUTUBE_API_KEY:
        st.error("請先在左側填入 Gemini 與 YouTube API Key")
    elif not direction.strip():
        st.error("請先輸入你想切入的方向")
    else:
        # 先向 GAS 扣一次（原子操作：remaining>0 才扣得動）；扣不動代表額度用完
        _consumed = False
        if _wl_enabled():
            _r = _wl_api("consume", _code)
            if not _r.get("ok"):
                st.error(_WL_ERR.get(_r.get("error", ""), "額度不足或服務異常"))
            else:
                _consumed = True
                st.session_state["_wl_remaining"] = int(_r.get("remaining", 0) or 0)
        _may_run = _consumed or not _wl_enabled()
        if _may_run:
            cfg = {
                'gemini_key': GEMINI_API_KEY, 'yt_key': YOUTUBE_API_KEY,
                'report_model': REPORT_MODEL, 'deep_mode': DEEP_MODE,
                'direction': direction.strip(), 'extra': extra.strip(),
                'n_topics': N_TOPICS, 'n_zh': N_ZH, 'n_en': N_EN,
                'mine_rounds': MINE_ROUNDS,
                'per_kw': PER_KW, 'analyze_n': ANALYZE_N,
                'window_days': WINDOW_DAYS, 'min_views': MIN_VIEWS,
                'video_type': VIDEO_TYPE,
            }
            result = run_pipeline(cfg)
            if result:
                result['cfg'] = cfg
                st.session_state['radar_result'] = result
            elif _consumed:
                # 跑失敗 → 把剛扣的次數退還，不讓使用者白白損失
                _rf = _wl_api("refund", _code)
                if _rf.get("ok"):
                    st.session_state["_wl_remaining"] = int(_rf.get("remaining", 0) or 0)
                st.info("這次沒跑成功，已把扣掉的次數退還給你。")

# ---- 結果呈現（從 session_state 讀，rerun 不消失）----
if 'radar_result' in st.session_state:
    result = st.session_state['radar_result']
    st.markdown("---")

    st.markdown(result['report'])

    st.download_button(
        "📥 下載菜單（含資料附錄）",
        build_export_md(result, result.get('cfg', {})),
        file_name=f"切角菜單_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
    )
    st.caption(f"本次消耗 YouTube API 配額約 {result.get('quota_estimate', '?')} units")

    with st.expander("🔍 AI 選字結果與理由"):
        rows = [
            {"市場": MARKET_LABEL[m], "關鍵字": item['kw'], "選字理由": item.get('reason', '')}
            for m in ("en", "zh") for item in result['selected_kws'].get(m, [])
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if result.get('mining_log'):
        with st.expander("⛏ 關鍵字探勘過程（迭代循環）"):
            for m in ("en", "zh"):
                for entry in result['mining_log'].get(m, []):
                    st.markdown(
                        f"**{MARKET_LABEL[m]}｜第 {entry['round']} 輪**　"
                        f"續挖方向：`{'、'.join(entry['frontier'][:8])}`　→　"
                        f"新增 {entry['fresh']} 字（累計 {entry['total']}｜查詢 {entry['queries']} 次）"
                    )

    with st.expander("🔥 爆款影片池（依爆款倍率排序）"):
        pool_df = pd.DataFrame([
            {
                "市場": MARKET_LABEL[v['market']],
                "標題": v['title'],
                "頻道": v['channel'],
                "觀看": v['view_count'],
                "訂閱": v['subs'],
                "爆款倍率": v['outlier_ratio'],
                "日均觀看": v['views_per_day'],
                "片長(分)": v['duration_min'],
                "已拆解": "✓" if v['id'] in result.get('analyzed_ids', []) else "",
                "連結": v['url'],
            }
            for v in sorted(result['pool'], key=lambda x: -x['outlier_ratio'])
        ])
        try:
            st.dataframe(
                pool_df, use_container_width=True, hide_index=True,
                column_config={"連結": st.column_config.LinkColumn("連結", display_text="▶ 開啟")},
            )
        except Exception:
            st.dataframe(pool_df, use_container_width=True, hide_index=True)

    with st.expander("🧩 各影片拆解全文"):
        for b in result.get('breakdowns', []):
            v = b['video']
            st.markdown(
                f"#### {MARKET_LABEL[v['market']]}｜[{v['title']}]({v['url']})\n"
                f"`{v['channel']}`｜觀看 {fmt_num(v['view_count'])}｜爆款倍率 {v['outlier_ratio']} 倍"
                f"｜依據：{b['source']}"
            )
            st.markdown(b['analysis'])
            st.markdown("---")

    # ---- 換一批切角（只重出菜單，不重新搜尋，省配額）----
    st.markdown("### 🔁 這批不合口味？")
    adjust = st.text_input(
        "跟老闆說怎麼調（只重出菜單，不重新搜尋）",
        placeholder="例：開箱類太多了，我想要挑戰型的／全部改成 Shorts／再狠一點的反差",
    )
    if st.button("🍽 換一批切角") and adjust.strip():
        cfg = result.get('cfg', {})
        with st.spinner("依你的口味重出菜單中…"):
            new_report = ai_generate_menu(
                cfg.get('gemini_key', GEMINI_API_KEY), cfg.get('report_model', REPORT_MODEL),
                cfg.get('direction', direction), cfg.get('extra', extra), adjust.strip(),
                result['selected_kws'], result['breakdowns'], result['pool'],
                cfg.get('n_topics', 6),
            )
        result['report'] = new_report
        st.session_state['radar_result'] = result
        st.rerun()
