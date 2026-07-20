# 🍽 切角點單機（Angle Radar）

給影音創作者的內容企劃工具。使用者先確認受眾、目的、形式與限制，再取得一份可以直接開拍的切角菜單。每張企劃卡包含開場、呈現方式、參考來源、差異化做法與優先順序。

一般使用者只會看到需求確認、簡化進度與最終成果；研究診斷、模型用量與中間資料只對管理者顯示。

## 主要功能

- 結構化需求摘要，避免只靠一個模糊關鍵字開始分析
- 一般模式使用字幕、留言與公開資料，產出帶來源的企劃卡
- 分開標示「已熱門」與「正在竄起」，並對早期訊號標示可信度
- 需求旁提供一份可複製的通用 AI Prompt，方便使用者自行比較
- 報告後可回覆 Angle Radar 與一般 AI 哪份較有用
- 一般下載檔只包含企劃成果與引用來源
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

API 金鑰只應放在 Streamlit Secrets，不能提交進 GitHub。
公開部署請勿開啟 `SHOW_ADMIN_DIAGNOSTICS`；本機需要診斷時才設為 `true`。

## 白名單與試用次數

白名單由私有 Google Sheet 與 Apps Script 管理。Sheet 欄位：

| 欄位 | 用途 |
|---|---|
| `code` | 每位測試者不同的試用碼 |
| `name` | 顯示名稱 |
| `remaining` | 剩餘產出次數 |
| `deep_mode` | `FALSE` 時不開放管理者的額外畫面檢查 |

設定方式見 [whitelist_gas/SETUP.md](whitelist_gas/SETUP.md)。比較回饋會寫入同一份試算表的 `feedback` 分頁。

## 成本

一般模式的實際價格會依字幕長度與模型 thinking tokens 浮動。管理者完成每次分析後，可以在診斷區看到該次實際 token 與依目前價格表推估的成本；該數字不會顯示給一般測試者。

## 隱私與公開原始碼提醒

- `.streamlit/secrets.toml`、`.env` 與 key 檔已被 `.gitignore` 排除。
- 白名單請勿放入不必要的個資。
- 隱藏 app 畫面中的流程不等於保護原始碼；只要 repo 是 public，程式實作仍可被閱讀。若未來要保護核心方法，應改用 private repo 或將核心分析移至不公開的後端服務。
