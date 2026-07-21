# 白名單「剩餘次數」API — 安裝步驟

讓 app 用 Apps Script 對白名單 Sheet 倒扣剩餘次數。Sheet 保持私有、免服務帳號金鑰。

## 1. 白名單 Sheet 改成這個欄位

打開白名單 Sheet，第一列表頭改成（順序不拘）：

| code | name | remaining |
|---|---|---|
| mei001 | 小美 | 20 |
| jim002 | 阿吉 | 10 |

- `code`：發給朋友的試用碼（**每人不同**）
- `name`：暱稱
- `remaining`：剩餘次數（用完＝0 就不能用；加值就把數字改大）
- 舊表的 `deep_mode` 欄位可以保留，新版一般模式不會使用它

> 這張表就是你[原本建好的白名單](https://docs.google.com/spreadsheets/d/1x5Z4C58mDJ8YZObi3HoTyybF0vRea4DUiMl9ubATB2Y/edit)。把 `active/daily_limit/total_limit` 那幾欄換成一欄 `remaining` 即可。用這個 GAS 版後，Sheet 的「知道連結的人可檢視」共用可以收回（改回私有更安全，因為 GAS 用你自己的權限讀寫）。

## 2. 貼 GAS 程式

Sheet 上方 **擴充功能 → Apps Script** → 把 `Code.gs` 全部貼進去。
把第一行 `const API_KEY = '換成你自己編的密鑰';` 改成你自己的一串亂數（等下 Streamlit 那邊要填一樣的）。

## 3. 部署 Web App

右上 **部署 → 新增部署作業 → 類型「網頁應用程式」**：
- 執行身分：**我**
- 誰可以存取：**任何人**

複製部署網址（`https://script.google.com/macros/s/……/exec`）。

## 4. 填進 Streamlit secrets

本機 `.streamlit/secrets.toml`（與部署時 Streamlit Cloud 的 Secrets）加：

```toml
WHITELIST_API_URL = "上一步複製的 /exec 網址"
WHITELIST_API_KEY = "與 Code.gs 內 API_KEY 一模一樣的密鑰"
```

## 5. 測試

瀏覽器直接開（把 URL/KEY/CODE 換掉）驗證：
```
<你的exec網址>?key=<你的密鑰>&action=check&code=mei001
```
應回 `{"ok":true,"found":true,"name":"小美","remaining":20,"deep":true}`。

之後在 app 裡用該碼登入 → 側邊欄顯示「剩餘次數 20」→ 每出一份切角報告扣 1；跑失敗會自動退還。

使用者送出切角新穎度與可用性回饋後，程式會自動建立 `feedback` 分頁，欄位為：

| timestamp | code | name | direction | verdict | note |
|---|---|---|---|---|---|

此分頁可用來統計有多少報告至少提供一個想深入的切角，不會影響剩餘次數。

使用者每次開始分析後，程式也會自動建立 `usage_logs` 分頁。成功與失敗都會留下一筆，欄位為：

| 欄位 | 內容 |
|---|---|
| `timestamp` | Apps Script 寫入紀錄的時間 |
| `request_id` | 單次分析的唯一識別碼 |
| `started_at` / `completed_at` / `duration_seconds` | 開始、完成時間與耗時 |
| `code` / `name` | 白名單使用者 |
| `status` | `success` 或 `failed` |
| `quota_consumed` / `quota_refunded` | 是否扣除、退回試用次數 |
| `input_mode` / `input` / `exclusions` | 使用者的輸入模式、原始輸入與排除條件 |
| `output` | 成功時實際顯示的切角報告 |
| `error` | 失敗時的錯誤內容 |

Google Sheets 單一儲存格最多約 5 萬字元，因此 `output` 最多保留前 49,000 字元。一般報告不會碰到此上限。

## 運作邏輯

- 登入：`check`（不扣）→ 顯示剩餘
- 出報告：`consume`（原子扣 1；remaining>0 才扣得動，用 LockService 防並發重扣）
- 跑失敗：`refund`（加 1 退還）
- 切角回饋：`feedback`（寫入 `feedback` 分頁，不扣次數）
- 使用紀錄：`log_usage`（寫入 `usage_logs` 分頁，不扣次數；管理者分析也會記錄）
- 加值：你直接在 Sheet 把 `remaining` 數字改大即可，即時生效
