/**
 * 切角雷達 — 白名單「剩餘次數」API（綁在白名單 Google Sheet 上）
 *
 * 白名單分頁（第一列表頭，順序不拘）：code | name | remaining | deep_mode
 *   remaining：剩餘可用次數，consume 一次扣 1，扣到 0 就不能用；加值＝把數字改大
 *   deep_mode：FALSE 關閉管理者的額外畫面檢查（空白＝允許）
 *
 * 端點（GET 或 POST，帶 ?key=API_KEY）：
 *   action=check   ：查詢，不扣次數（登入＋顯示剩餘用）
 *   action=consume ：以 request_id 冪等地原子扣 1
 *   action=refund  ：同 request_id 最多退還一次，且必須已有 consume
 *   action=feedback：記錄切角新穎度與可用性回饋
 *   action=log_usage：記錄每次分析的輸入、輸出、狀態與時間
 *   action=track_event：記錄收藏、複製 Prompt 與下載等最終行為
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
  const requestId = String(p.request_id || '').trim();
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
        if (action === 'log_usage') {
          appendUsageLog_(code, name, p);
          return json_({ ok: true });
        }
        if (action === 'track_event') {
          appendUsageEvent_(code, name, p);
          return json_({ ok: true });
        }
        if (action === 'consume') {
          if (!requestId) return json_({ ok: false, error: 'no_request_id' });
          if (quotaTransactionExists_(code, requestId, 'consume')) {
            return json_({ ok: true, name: name, remaining: remaining, deep: deep, replay: true });
          }
          if (remaining <= 0) return json_({ ok: false, error: 'depleted', name: name, remaining: 0, deep: deep });
          remaining -= 1;
          sh.getRange(i + 1, ri + 1).setValue(remaining);
          appendQuotaTransaction_(code, requestId, 'consume', -1, remaining);
          return json_({ ok: true, name: name, remaining: remaining, deep: deep });
        }
        if (action === 'refund') {
          if (!requestId) return json_({ ok: false, error: 'no_request_id' });
          if (quotaTransactionExists_(code, requestId, 'refund')) {
            return json_({ ok: true, name: name, remaining: remaining, deep: deep, replay: true });
          }
          if (!quotaTransactionExists_(code, requestId, 'consume')) {
            return json_({ ok: false, error: 'not_consumed' });
          }
          remaining += 1;
          sh.getRange(i + 1, ri + 1).setValue(remaining);
          appendQuotaTransaction_(code, requestId, 'refund', 1, remaining);
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

function quotaTransactionsSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('quota_transactions');
  if (!sh) {
    sh = ss.insertSheet('quota_transactions');
    sh.appendRow(['timestamp', 'request_id', 'code', 'action', 'delta', 'remaining']);
    sh.setFrozenRows(1);
  }
  return sh;
}

function quotaTransactionExists_(code, requestId, action) {
  const sh = quotaTransactionsSheet_();
  const data = sh.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (
      String(data[i][1]) === requestId &&
      String(data[i][2]) === code &&
      String(data[i][3]) === action
    ) return true;
  }
  return false;
}

function appendQuotaTransaction_(code, requestId, action, delta, remaining) {
  quotaTransactionsSheet_().appendRow([
    new Date(), safeCell_(requestId), safeCell_(code), safeCell_(action),
    Number(delta), Number(remaining)
  ]);
}

function safeCell_(value, maxLength) {
  const text = String(value || '').slice(0, maxLength || 1000);
  return /^[=+\-@]/.test(text) ? "'" + text : text;
}

function appendUsageLog_(code, name, p) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('usage_logs');
  if (!sh) {
    sh = ss.insertSheet('usage_logs');
    sh.appendRow([
      'timestamp', 'request_id', 'started_at', 'completed_at', 'duration_seconds',
      'code', 'name', 'status', 'quota_consumed', 'quota_refunded', 'input_mode',
      'input', 'exclusions', 'output', 'error'
    ]);
    sh.setFrozenRows(1);
  }
  sh.appendRow([
    new Date(), safeCell_(p.request_id), safeCell_(p.started_at),
    safeCell_(p.completed_at), safeCell_(p.duration_seconds), safeCell_(code),
    safeCell_(name), safeCell_(p.status), safeCell_(p.quota_consumed),
    safeCell_(p.quota_refunded), safeCell_(p.input_mode), safeCell_(p.input, 10000),
    safeCell_(p.exclusions, 10000), safeCell_(p.output, 49000),
    safeCell_(p.error, 5000)
  ]);
}

function appendUsageEvent_(code, name, p) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('usage_events');
  if (!sh) {
    sh = ss.insertSheet('usage_events');
    sh.appendRow([
      'timestamp', 'request_id', 'code', 'name', 'event_type', 'angle_key',
      'angle_index', 'angle_name', 'topic', 'details'
    ]);
    sh.setFrozenRows(1);
  }
  sh.appendRow([
    new Date(), safeCell_(p.request_id), safeCell_(code), safeCell_(name),
    safeCell_(p.event_type), safeCell_(p.angle_key), safeCell_(p.angle_index),
    safeCell_(p.angle_name, 5000), safeCell_(p.topic, 10000),
    safeCell_(p.details, 10000)
  ]);
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
