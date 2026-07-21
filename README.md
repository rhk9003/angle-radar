# 切角雷達（Angle Radar）

給影音創作者的內容研究工具。使用者只要輸入「想拍什麼」，系統會在受控成本內搜尋、比較影片與留言，最後直接給「可以拍什麼、怎麼拍、如何和現有內容拉開差異」。

公開結果採口語化行動卡：選題、核心訊息、建議拍法、可直接照念的開場、差異化與應避免的普通版本。嚴謹的需求端、供給端、留言端比較留在後台；每張卡仍保留可展開的來源與限制。每個切角另附一份可複製 Prompt，讓使用者補上自己的專業、案例與限制後繼續發展。

一般使用者只會看到簡化輸入、模糊進度與最終成果。查詢詞、分析細節、模型用量與中間資料只對管理者顯示。

## 主要功能

- 前台只要求一個「想拍什麼」欄位，排除內容與參考網址皆為選填
- 第一輪搜尋後，從實際標題、Tags 與留言建立候選，只允許一次、最多 4 詞的資料回流搜尋
- 同一影片命中的不同關鍵字、排序與名次會保留，供需求與供給比較使用
- 每支證據候選同時抽樣高互動與近期留言，再分層保留追問、希望補拍、比較、反對、卡點與共鳴
- 先檢查 10–12 支候選的字幕與留言可用性，無資料時自動補位，再選出最多 8 支深度分析
- 需求端、供給端、留言端先分開比較，再產生至少由兩層共同支持的跨層洞察
- 跨層洞察、最終選題、影片來源與留言 ref 逐層驗證，來源不是事後附上的裝飾
- 在證據足夠時，行動卡可涵蓋差異化選題、跨情境轉譯、留言補題、熱門續題與早期話題
- 使用者貼入的 YouTube 影片網址會真正讀取影片資料、字幕與留言，而不只放進 Prompt
- 近期異動只能作為早期線索，報告不把單支熱門影片宣稱成完整趨勢
- 每個切角附一份不含內部方法與市場分組的深化 Prompt
- 公開報告不顯示搜尋流程、評分公式、策略分類或大量 emoji
- 報告後收集切角的新穎度與可用性回饋，不再做通用 AI 勝負比較
- 管理者可查看 token、推估成本、樣本品質與診斷資料
- 相同公開資料與影片分析具備快取，減少重複成本

## 本機執行

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app.py
```

需要：

- Gemini API Key：<https://aistudio.google.com>
- YouTube Data API Key：Google Cloud Console 啟用 YouTube Data API v3

## Streamlit Community Cloud 部署

1. 將 repo 連接至 Streamlit Community Cloud。
2. App entry point 選擇 `app.py`。
3. 在 App settings → Secrets 填入 `.streamlit/secrets.toml.example` 的設定。
4. 啟用白名單後，再將 app 網址和個別試用碼交給測試者。

API 金鑰只應放在 Streamlit Secrets，不能提交進 GitHub。公開部署請勿開啟 `SHOW_ADMIN_DIAGNOSTICS`；本機需要診斷時才設為 `true`。

## 白名單與試用次數

白名單由私有 Google Sheet 與 Apps Script 管理。Sheet 的必要欄位：

| 欄位 | 用途 |
|---|---|
| `code` | 每位測試者不同的試用碼 |
| `name` | 顯示名稱 |
| `remaining` | 剩餘產出次數 |

舊表中的 `deep_mode` 欄位可以保留，但新版一般模式不會使用它。

設定方式見 [whitelist_gas/SETUP.md](whitelist_gas/SETUP.md)。切角價值回饋會寫入同一份試算表的 `feedback` 分頁。

## 成本

實際價格會依找到的字幕長度、可用留言、模型輸出與 thinking tokens 浮動。MVP 以固定上限控制成本：

- 第一輪預設 10 組查詢，每組 25 支；第二輪最多再加 4 組，不做無限迴圈
- 相關性檢查最多送入 180 支候選
- 回流候選最多 48 個；證據探測最多 12 支；深度分析預設 8 支
- 每支留言只取 relevance 與 time 各一頁，每頁 40 則，壓成最多 12 則代表樣本
- 影片拆解採批次呼叫；三層歸納使用低成本模型；高成本模型只負責最後 4–6 張行動卡
- 最終卡片依證據量決定，不固定湊滿分類或數量
- 搜尋、影片資料、字幕、留言與影片拆解都有快取

管理者完成每次分析後，可以在診斷區看到實際 token、thinking tokens、推估 Gemini 成本、YouTube 呼叫數與估算配額單位；該數字不會顯示給一般測試者。

## 隱私與公開原始碼提醒

- `.streamlit/secrets.toml`、`.env` 與 key 檔已被 `.gitignore` 排除。
- 白名單請勿放入不必要的個資。
- 隱藏 app 畫面中的流程不等於保護原始碼；只要 repo 是 public，程式實作仍可被閱讀。若要真正保護核心方法，應改用 private repo 或將核心分析移至不公開的後端服務。
