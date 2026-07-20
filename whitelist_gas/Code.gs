/**
 * 切角點單機 — 白名單「剩餘次數」API（綁在白名單 Google Sheet 上）
 *
 * 白名單分頁（第一列表頭，順序不拘）：code | name | remaining | deep_mode
 *   remaining：剩餘可用次數，consume 一次扣 1，扣到 0 就不能用；加值＝把數字改大
 *   deep_mode：FALSE 關閉管理者的額外畫面檢查（空白＝允許）
 *
 * 端點（GET 或 POST，帶 ?key=API_KEY）：
 *   action=check   ：查詢，不扣次數（登入＋顯示剩餘用）
 *   action=consume ：原子扣 1（remaining>0 才扣得動；否則回 depleted）
 *   action=refund  ：加 1（點單失敗時退還）
 *   action=feedback：記錄 Angle Radar 與通用 AI 的比較結果
 * 回傳 JSON：{ok, name, remaining, deep, error?}
 *
 * 安裝見 SETUP.md
 */

const API_KEY = '換成你自己編的密鑰';   // ← 必須與 Streamlit secrets 的 WHITELIST_API_KEY 一致

function doGet(e)  { return handle_(e); }
function doPost(e) { return handle_(e); }

function handle_(e) {
  const p = (e && e.parameter) || {};
  if (p.key !== API_KEY) return json_({ ok: false, error: 'unauthorized' });

  const action = p.action || 'check';
  const code = String(p.code || '').trim();
  if (!code) return json_({ ok: false, error: 'no_code' });

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return json_({ ok: false, error: 'busy' });
  try {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    const data = sh.getDataRange().getValues();
    const head = data[0].map(function (h) { return String(h).trim().toLowerCase(); });
    const ci = head.indexOf('code'), ni = head.indexOf('name'),
          ri = head.indexOf('remaining'), di = head.indexOf('deep_mode');
    if (ci < 0 || ri < 0) return json_({ ok: false, error: 'bad_headers' });

    for (let i = 1; i < data.length; i++) {
      if (String(data[i][ci]).trim() === code) {
        let remaining = Number(data[i][ri]) || 0;
        const name = ni >= 0 ? data[i][ni] : '';
        const deep = di < 0 || String(data[i][di]).trim().toUpperCase() !== 'FALSE';

        if (action === 'feedback') {
          appendFeedback_(code, name, p.direction, p.verdict, p.note);
          return json_({ ok: true });
        }
        if (action === 'consume') {
          if (remaining <= 0) return json_({ ok: false, error: 'depleted', name: name, remaining: 0, deep: deep });
          remaining -= 1;
          sh.getRange(i + 1, ri + 1).setValue(remaining);
          return json_({ ok: true, name: name, remaining: remaining, deep: deep });
        }
        if (action === 'refund') {
          remaining += 1;
          sh.getRange(i + 1, ri + 1).setValue(remaining);
          return json_({ ok: true, name: name, remaining: remaining, deep: deep });
        }
        // check：找到就回 found:true（即使 remaining=0 也讓他登入看到 0）
        return json_({ ok: remaining > 0, found: true, name: name, remaining: remaining, deep: deep });
      }
    }
    return json_({ ok: false, error: 'not_found' });
  } finally {
    lock.releaseLock();
  }
}

function safeCell_(value) {
  const text = String(value || '').slice(0, 1000);
  return /^[=+\-@]/.test(text) ? "'" + text : text;
}

function appendFeedback_(code, name, direction, verdict, note) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('feedback');
  if (!sh) {
    sh = ss.insertSheet('feedback');
    sh.appendRow(['timestamp', 'code', 'name', 'direction', 'verdict', 'note']);
  }
  sh.appendRow([
    new Date(), safeCell_(code), safeCell_(name), safeCell_(direction),
    safeCell_(verdict), safeCell_(note)
  ]);
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}
