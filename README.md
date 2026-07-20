# 🍽 切角點單機（Angle Radar）

給影音創作者的「切角發想」工具。輸入一個想拍的方向（例：口紅、增肌飲食），
自動掃描國內外 YouTube 爆款、拆解參考影片，直接給你**可拍的切角**：拍什麼主題、
用什麼呈現方式、為什麼這樣會吸引人，每張卡片附國外爆款證據與中文市場紅綠燈。

## 管線（全自動，約 2–5 分鐘）

1. AI 解析方向 → 生成中英種子字（英文為國外創作者圈道地用語）
2. 迭代探勘關鍵字：autocomplete 擴充 → AI 挑方向續挖（含爬取上限）
3. AI 自動選字（國外為找 reference 主戰場）
4. 雙市場搜尋（美國＋台灣）
5. 爆款偵測：觀看數 ÷ 頻道平均 = 爆款倍率
6. 影片拆解：真字幕＋留言；深度模式改用 Gemini 原生影片理解
7. 出菜單：卡片式切角（拍什麼／怎麼呈現／為什麼吸引人＋證據／蹭它／超越它／中文市場紅綠燈）

## 本機執行

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 填入你的 key
streamlit run app.py
```

需要兩把金鑰：
- **Gemini API Key** — https://aistudio.google.com
- **YouTube Data API Key** — Google Cloud Console 啟用 YouTube Data API v3

沒設 secrets 時，也可在側邊欄手動填 key。

## 部署給朋友測試（Streamlit Community Cloud，免費）

1. 這個 repo 推到 GitHub（public 即可）
2. 到 https://share.streamlit.io → New app → 選這個 repo、`app.py`
3. 在 **App settings → Secrets** 貼上 `secrets.toml.example` 的內容並填實際值
   （金鑰只存在 Streamlit Cloud 的 Secrets，**不會**進 repo）
4. 部署後把網址＋試用碼發給朋友

> ⚠️ 部署後是**你的金鑰在付費**，任何拿到網址的人都能燒你的額度——所以務必啟用下方白名單。

## 白名單（用 Google Sheet 控管：誰能用｜每人幾次｜哪些功能）

Sheet 第一列表頭放這五欄，一人一列：

| 欄 | 意義 |
|---|---|
| `code` | 發給朋友的試用碼（每人一組，登入用） |
| `name` | 暱稱（登入後打招呼、對帳用） |
| `active` | `FALSE` 停用；空白＝啟用 |
| `daily_limit` | 每日點單次數上限；空白或 `0`＝不限 |
| `deep_mode` | `FALSE` 關閉這人的「深度拆解」（貴功能）；空白＝允許 |

**改 Sheet 即時生效**（快取 2 分鐘）：要新增／停用試用者、調次數、開關功能，直接編輯 Sheet，不用動程式或重部署。

### LITE 模式（唯讀，最快上手）

只設 `WHITELIST_SHEET_ID`：

1. Sheet → 共用 → 一般存取權改「**知道連結的人皆可檢視**」
2. 網址取出 Sheet ID 填進 secrets 的 `WHITELIST_SHEET_ID`

功能開關（active／deep_mode）完全可用；**但「每日次數」無法跨 session 強制**——因為唯讀 Sheet 沒辦法記錄用量，限次只在單一瀏覽器 session 內軟性計數（重整就歸零）。想真正限次請用 FULL 模式。

### FULL 模式（可寫，限次真正生效）

讓 app 用 Google **服務帳號**存取 Sheet，就能把每次點單寫進一個 `usage` 分頁、據以強制限次：

1. [Google Cloud Console](https://console.cloud.google.com)（用你開 YouTube key 的同專案即可）→ 啟用 **Google Sheets API**
2. IAM → 服務帳號 → 建立 → 建立 JSON 金鑰下載
3. 把白名單 Sheet **共用給那個服務帳號的 email**（`xxx@xxx.iam.gserviceaccount.com`），權限給「**編輯者**」
4. 把 JSON 內容依 `secrets.toml.example` 的 `[gcp_service_account]` 格式貼進 secrets（同時保留 `WHITELIST_SHEET_ID`）
5. app 會自動在 Sheet 建一個 `usage` 分頁記錄用量；白名單放**第一個分頁**

> 沒設 `WHITELIST_SHEET_ID` 時閘門完全不啟用（本機自用直接進）。

## 成本與配額（參考）

- 單次點單：Gemini 約 NT$1–3（深度模式看片較高）；YouTube 配額約 820 units（免費額度 10,000/天 ≈ 12 次全站共用）
- 多人測試易撞 YouTube 每日配額——可向 Google 申請提額，並用白名單控管人數

## 隱私與安全

- `secrets.toml` 已被 `.gitignore` 排除，金鑰不會進這個 public repo
- 白名單 Sheet 只放試用碼與暱稱，別放個資
