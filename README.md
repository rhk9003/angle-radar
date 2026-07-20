# 切角雷達（Angle Radar）

給影音創作者的內容研究工具。使用者只要輸入「想拍什麼」，系統就會整理一批通常需要花時間搜尋影片、閱讀字幕與留言才會發現的內容切角。

它不替使用者完成整份拍片企劃。公開報告聚焦在切角本身、切角來源、觀眾留下的問題、參考來源與線索完整度；每個切角另附一份可複製 Prompt，讓使用者補上自己的專業、案例與限制，再交給自己的 AI 發展成拍攝建議。

一般使用者只會看到簡化輸入、模糊進度與最終成果。查詢詞、分析細節、模型用量與中間資料只對管理者顯示。

## 主要功能

- 前台只要求一個「想拍什麼」欄位，排除內容與參考網址皆為選填
- 只用一次模型呼叫產生搜尋起點，同時涵蓋短核心詞、問句、問題詞、相鄰題材與自然英文詞
- 後續候選詞採確定性選取，不再多用一次模型挑關鍵字
- 使用公開影片資料、字幕與留言，產出有來源可追查的切角
- 在證據足夠時，切角會涵蓋既有題材的新版本、留言未解問題、熱門內容延伸與正在抬頭的話題
- 每張切角呈現切角來源、來源內容結論、觀看速度與相對頻道基準，而不是只給泛化摘要
- 留言缺口必須能對回已取得的留言證據，無證據時不硬寫
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

實際價格會依找到的字幕長度、可用留言、模型輸出與 thinking tokens 浮動。新版少了一次模型關鍵字挑選，報告也不再生成完整拍片企劃，因此一般模式的輸出 token 會低於舊版。

管理者完成每次分析後，可以在診斷區看到該次實際 token 與推估成本；該數字不會顯示給一般測試者。

## 隱私與公開原始碼提醒

- `.streamlit/secrets.toml`、`.env` 與 key 檔已被 `.gitignore` 排除。
- 白名單請勿放入不必要的個資。
- 隱藏 app 畫面中的流程不等於保護原始碼；只要 repo 是 public，程式實作仍可被閱讀。若要真正保護核心方法，應改用 private repo 或將核心分析移至不公開的後端服務。
