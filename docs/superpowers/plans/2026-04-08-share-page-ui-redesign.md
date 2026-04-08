# Share Page UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the share page (`/s/{id}`) layout with a single Identity Card + filtered settlement list, collapsing bank-account editing into an expandable affordance.

**Architecture:** All changes are inside `src/split_settle/handler.py` — specifically `SHARE_PAGE_TEMPLATE` (HTML + inline CSS + inline JS) and `_render_share_page`. The template variables `{{iam}}`, `{{all_label}}`, `{{me_buttons}}` are removed and replaced by `{{owed_label}}`, `{{owes_label}}`, `{{edit_btn_label}}`, `{{view_all_label}}`, `{{view_mine_label}}`. Client-side JS computes per-user `owed`/`owes` totals from `settlements`, applies default filter to claimed identity, and hosts the expandable account editor inside the identity card.

**Tech Stack:** Python 3.13, Lambda, server-rendered HTML + inline vanilla JS, pytest.

**Reference:** Preview mockup at `/tmp/share-preview.html` (approved design — 3 variants: Alice with account, Bob owes-only, Charlie with unfilled payee).

**Spec:** `docs/superpowers/specs/2026-04-08-share-page-ui-redesign-design.md`

---

## File Inventory

- Modify: `src/split_settle/handler.py` — `SHARE_PAGE_TEMPLATE` constant (~lines 1167-1441), `_render_share_page` (~lines 1449-1522), `si` default strings
- Modify: `tests/test_handler.py` — add rendering tests for new structure

No new files. No template/tooling changes.

---

## Task 1: Rendering test — identity card present, me-picker removed

**Files:**
- Modify: `tests/test_handler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_handler.py`:

```python
def test_share_page_has_identity_card():
    """New layout uses identity-card; me-picker is removed."""
    import handler
    result = {
        "currency": "NT",
        "total_expenses": 4500,
        "settlements": [
            {"from": "Bob", "to": "Alice", "amount": 1200},
            {"from": "Charlie", "to": "Alice", "amount": 600},
        ],
        "summary": [
            {"participant": "Alice"},
            {"participant": "Bob"},
            {"participant": "Charlie"},
        ],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="abc12345")
    assert "identity-card" in html
    assert "me-picker" not in html
    assert "me-btn" not in html
    # No leftover template variables
    assert "{{iam}}" not in html
    assert "{{me_buttons}}" not in html
    assert "{{all_label}}" not in html


def test_share_page_bootstrap_includes_settlements():
    """Client JS needs settlements + participants to compute owed/owes."""
    import handler
    import json
    result = {
        "currency": "NT",
        "total_expenses": 1000,
        "settlements": [{"from": "Bob", "to": "Alice", "amount": 1000}],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="xyz99999")
    # Extract the bootstrap JSON between `window.__SHARE = ` and `;</script>`
    marker = "window.__SHARE = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    payload = html[start:end]
    # Un-escape defensive \u003c etc. for JSON parsing
    payload_clean = (payload.replace("\\u003c", "<")
                            .replace("\\u0026", "&")
                            .replace("\\u0027", "'"))
    data = json.loads(payload_clean)
    assert data["share_id"] == "xyz99999"
    assert data["participants"] == ["Alice", "Bob"]
    assert len(data["settlements"]) == 1
    assert data["settlements"][0]["from"] == "Bob"
```

- [ ] **Step 2: Run tests — expect failures**

```bash
python3 -m pytest tests/test_handler.py::test_share_page_has_identity_card tests/test_handler.py::test_share_page_bootstrap_includes_settlements -v
```

Expected: FAIL — `me-picker` still present in current template.

---

## Task 2: Rewrite SHARE_PAGE_TEMPLATE — CSS block

**Files:**
- Modify: `src/split_settle/handler.py` — CSS section of `SHARE_PAGE_TEMPLATE` (roughly lines 1177-1252)

- [ ] **Step 1: Replace the CSS block**

Locate the `<style>` block inside `SHARE_PAGE_TEMPLATE`. Replace the section from `* { margin:0;` through `</style>` with:

```css
    * { margin:0;padding:0;box-sizing:border-box; }
    body { font-family:'Inter',-apple-system,system-ui,sans-serif; background:#d5d0c8;
           min-height:100vh; display:flex; justify-content:center; padding:16px; }
    .phone { width:100%;max-width:420px;background:#2d4a4a;border-radius:28px;padding:24px;
             color:#e0d5c4;box-shadow:12px 12px 12px rgba(30,50,50,0.4);margin:0 auto;height:fit-content; }
    @media(max-width:460px){body{padding:0}.phone{border-radius:0;min-height:100vh}}
    h1 { font-size:22px;font-weight:800;color:#e8a84c;margin-bottom:2px; }
    .date { font-size:11px;color:#5a7a70;margin-bottom:14px; }
    .total-line { font-size:12px;color:#8aaa9e;margin-bottom:14px; }
    .divider { border:none;height:2px;margin:16px 0;
               background:linear-gradient(90deg,transparent,#e8a84c,#8aaa9e,#e8a84c,transparent); }

    /* Identity card */
    .identity-card { background:linear-gradient(135deg,#1e3636,#234040);border-radius:14px;padding:14px 16px;
                     margin-bottom:10px;
                     box-shadow:inset -2px 2px 5px rgba(10,30,30,0.5),inset 2px -2px 5px rgba(60,100,100,0.15); }
    .id-row { display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap; }
    .id-name { font-size:16px;font-weight:700;color:#e8a84c;min-width:0;flex:1;
               overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
    .id-edit { background:transparent;border:1px solid #e8a84c;color:#e8a84c;
               padding:4px 10px;border-radius:8px;font-size:11px;cursor:pointer;font-weight:600;
               flex-shrink:0;white-space:nowrap;font-family:inherit; }
    .id-summary { font-size:12px;line-height:1.6; }
    .id-summary .owed { color:#7fc69a;font-weight:700; }
    .id-summary .owes { color:#d96848;font-weight:700; }
    .id-summary > div { display:block; }

    /* Account editor (expandable inside identity card) */
    .acct-editor { margin-top:10px;padding-top:10px;border-top:1px solid rgba(90,122,112,0.3);display:none;
                   flex-direction:column;gap:6px; }
    .acct-editor.open { display:flex; }
    .acct-editor textarea { width:100%;padding:8px;border-radius:8px;border:none;
                            background:#2d4a4a;color:#e0d5c4;font-family:inherit;font-size:13px;
                            resize:vertical;box-sizing:border-box;min-height:60px; }
    .acct-editor button { padding:6px 14px;border:none;border-radius:8px;
                          background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
                          font-weight:700;font-size:12px;cursor:pointer;align-self:flex-start;
                          font-family:inherit; }
    .acct-editor .status { font-size:11px;color:#5a7a70; }

    /* View toggle */
    .view-toggle { display:flex;justify-content:flex-end;margin:4px 0 8px; }
    .view-toggle button { background:transparent;border:none;color:#8aaa9e;
                          font-size:11px;cursor:pointer;text-decoration:underline;font-family:inherit; }

    /* Settlement rows */
    @keyframes slideIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
    .settlement { background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
                  border-radius:12px;padding:10px 14px;margin-bottom:8px;
                  box-shadow:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1);
                  animation:slideIn 0.3s ease-out both;animation-delay:calc(var(--i,0)*0.1s);
                  transition:all 0.35s cubic-bezier(0.4,0,0.2,1);
                  max-height:200px;overflow:hidden;opacity:1;transform:translateX(0); }
    .settlement.hidden { max-height:0;opacity:0;transform:translateX(-40px);margin-bottom:0;
                         padding-top:0;padding-bottom:0; }
    .sett-main { display:flex;justify-content:space-between;align-items:center;font-size:14px; }
    .from { font-weight:700;color:#5a2020; }
    .to { font-weight:700;color:#1a4a3a; }
    .amount { font-weight:800;font-size:15px; }
    .payee-account { margin-top:6px;padding-top:6px;border-top:1px dashed rgba(30,54,54,0.3);
                     font-size:11px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:#1e3636; }
    .payee-account code { background:rgba(30,54,54,0.15);padding:2px 6px;border-radius:5px;
                          font-family:'Menlo',monospace;color:#1e3636;word-break:break-all; }
    .payee-account .copy-btn { padding:2px 8px;font-size:10px;border:1px solid #1e3636;
                               background:transparent;border-radius:5px;cursor:pointer;color:#1e3636;font-weight:600; }
    .payee-account .muted { color:rgba(30,54,54,0.55);font-style:italic; }

    .summary { text-align:center;background:#1e3636;padding:8px 14px;border-radius:10px;
               font-size:11px;color:#8aaa9e;margin-top:10px;
               box-shadow:inset -3px 3px 6px rgba(10,30,30,0.5),inset 3px -3px 6px rgba(60,100,100,0.15); }
    .check { color:#e8a84c; }
    .cta { text-align:center;margin-top:24px;padding-top:16px;
           border-top:2px solid transparent;
           background-image:linear-gradient(#2d4a4a,#2d4a4a),linear-gradient(90deg,transparent,#e8a84c,#8aaa9e,#e8a84c,transparent);
           background-origin:padding-box,border-box;background-clip:padding-box,border-box; }
    .cta p { color:#5a7a70;font-size:12px;margin-bottom:10px; }
    .cta a { display:inline-block;background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
             text-decoration:none;padding:10px 20px;border-radius:10px;font-weight:700;font-size:13px;
             box-shadow:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1); }
    .footer { text-align:center;margin-top:16px;font-size:10px;color:#5a7a70; }
    .footer a { color:#8aaa9e; }

    /* Identity modal — unchanged */
    .modal-backdrop { position:fixed;inset:0;background:rgba(0,0,0,0.6);
      display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px; }
    .modal { background:#2d4a4a;border:2px solid #e8a84c;border-radius:16px;padding:24px;
      max-width:340px;width:100%;display:flex;flex-direction:column;gap:10px;color:#e0d5c4;
      box-shadow:8px 8px 16px rgba(10,30,30,0.6); }
    .modal h3 { color:#e8a84c;margin:0 0 4px;font-size:18px; }
    .modal p { color:#8aaa9e;font-size:13px;margin:0 0 8px; }
    .modal button { padding:10px 14px;border:none;border-radius:10px;font-size:14px;
      font-weight:600;cursor:pointer;background:#1e3636;color:#e0d5c4;
      box-shadow:3px 3px 6px rgba(10,30,30,0.4);font-family:inherit; }
    .modal button:hover { background:#e8a84c;color:#1e3636; }
    .modal button.guest { background:transparent;border:1px solid #5a7a70;color:#8aaa9e; }
```

Keep the existing `<style>` and `</style>` tags.

- [ ] **Step 2: Commit (partial, template body comes next)**

Skip — CSS and body must land together to avoid broken intermediate state. Proceed to Task 3.

---

## Task 3: Rewrite SHARE_PAGE_TEMPLATE — HTML body + JS

**Files:**
- Modify: `src/split_settle/handler.py` — body + script sections of `SHARE_PAGE_TEMPLATE` (lines ~1253-1441)

- [ ] **Step 1: Replace the body + scripts**

Replace the section from `<body>` through `</html>"""` (end of `SHARE_PAGE_TEMPLATE`) with:

```html
<body>
  <div id="identity-modal" hidden></div>
  <div class="phone">
    <h1>{{share_title}}</h1>
    <div class="date">{{date}}</div>
    <div class="total-line">{{participants}} · Total: {{currency}} {{total}}</div>

    <div class="identity-card" id="identity-card" hidden>
      <div class="id-row">
        <span class="id-name" id="id-name"></span>
        <button class="id-edit" id="id-edit" hidden>{{edit_btn_label}}</button>
      </div>
      <div class="id-summary" id="id-summary"></div>
      <div class="acct-editor" id="acct-editor">
        <textarea id="acct-text" maxlength="500" rows="3"></textarea>
        <button id="acct-save">{{save_label}}</button>
        <span class="status" id="acct-status"></span>
      </div>
    </div>

    <div class="view-toggle" id="view-toggle" hidden>
      <button id="view-toggle-btn">{{view_all_label}}</button>
    </div>

    <hr class="divider">
    {{settlements_html}}
    <div class="summary">{{num_settlements}} transfer{{s_plural}} to settle <span class="check">✓</span></div>
    <div class="cta">
      <p>{{cta_q}}</p>
      <a href="/">{{cta_btn}}</a>
    </div>
    <div class="footer"><a href="/docs">API Docs</a> · Powered by x402</div>
  </div>
  <script>window.__SHARE = {{bootstrap_json}};</script>
  <script>
  (function() {
    var SHARE = window.__SHARE || {};
    var shareId = SHARE.share_id;
    var participants = SHARE.participants || [];
    var settlements = SHARE.settlements || [];
    var LBL = SHARE.labels || {};
    if (!shareId) return;

    var deviceId = localStorage.getItem('split_device_id');
    if (!deviceId) {
      deviceId = (crypto.randomUUID && crypto.randomUUID()) ||
                 (Date.now().toString(36) + Math.random().toString(36).slice(2));
      localStorage.setItem('split_device_id', deviceId);
    }

    var IDENTITY_KEY = 'split_identity:' + shareId;
    var identity = localStorage.getItem(IDENTITY_KEY);
    var accounts = {};
    var showAll = false;

    function esc(s) {
      return String(s).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[c];
      });
    }

    function fmt(n) { return Number(n).toLocaleString('en-US', {minimumFractionDigits:0, maximumFractionDigits:2}); }

    function totalsFor(name) {
      var owed = 0, owes = 0;
      settlements.forEach(function(s) {
        if (s.to === name) owed += Number(s.amount) || 0;
        if (s.from === name) owes += Number(s.amount) || 0;
      });
      return {owed: owed, owes: owes};
    }

    function isRealIdentity() {
      return identity && identity !== '__guest__';
    }

    function fetchAccounts() {
      return fetch('/v1/share/' + encodeURIComponent(shareId) + '/accounts')
        .then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(d) { accounts = d || {}; })
        .catch(function() { accounts = {}; });
    }

    function showIdentityModal() {
      var modal = document.getElementById('identity-modal');
      if (!modal) return;
      var html = '<div class="modal-backdrop"><div class="modal">' +
                 '<h3>' + esc(LBL.modal_title || '') + '</h3>' +
                 '<p>' + esc(LBL.modal_body || '') + '</p>';
      participants.forEach(function(p) {
        html += '<button data-name="' + esc(p) + '">' + esc(p) + '</button>';
      });
      html += '<button class="guest" data-name="__guest__">' + esc(LBL.guest_label || '') + '</button>';
      html += '</div></div>';
      modal.innerHTML = html;
      modal.hidden = false;
      modal.querySelectorAll('button[data-name]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          identity = btn.getAttribute('data-name');
          localStorage.setItem(IDENTITY_KEY, identity);
          modal.hidden = true;
          modal.innerHTML = '';
          renderAll();
        });
      });
    }

    function renderIdentityCard() {
      var card = document.getElementById('identity-card');
      var nameEl = document.getElementById('id-name');
      var editBtn = document.getElementById('id-edit');
      var summary = document.getElementById('id-summary');
      var editor = document.getElementById('acct-editor');
      if (!card) return;

      if (!isRealIdentity()) {
        card.hidden = true;
        return;
      }
      card.hidden = false;
      nameEl.textContent = (LBL.greeting || '') + identity;

      var t = totalsFor(identity);
      var lines = '';
      if (t.owed > 0) {
        lines += '<div><span class="owed">' + esc(LBL.owed_label || '') + ' ' +
                 esc(SHARE.currency || '') + ' ' + esc(fmt(t.owed)) + '</span></div>';
      }
      if (t.owes > 0) {
        lines += '<div><span class="owes">' + esc(LBL.owes_label || '') + ' ' +
                 esc(SHARE.currency || '') + ' ' + esc(fmt(t.owes)) + '</span></div>';
      }
      summary.innerHTML = lines;

      // Edit-account button only when someone owes the user money.
      editBtn.hidden = !(t.owed > 0);
      if (editBtn.hidden) {
        editor.classList.remove('open');
      } else {
        // Populate textarea with current saved account (if any).
        var ta = document.getElementById('acct-text');
        ta.value = accounts[identity] || '';
      }
    }

    function renderSettlements() {
      var rows = document.querySelectorAll('.settlement');
      rows.forEach(function(row, i) {
        var s = settlements[i];
        // Filter visibility
        var visible;
        if (!isRealIdentity() || showAll) {
          visible = true;
        } else {
          visible = (s && (s.from === identity || s.to === identity));
        }
        row.classList.toggle('hidden', !visible);

        // Payee account block: only when user is the payer
        var prior = row.querySelector('.payee-account');
        if (prior) prior.remove();
        if (!isRealIdentity() || !s || s.from !== identity) return;

        var acct = accounts[s.to];
        var div = document.createElement('div');
        div.className = 'payee-account';
        if (acct) {
          var code = document.createElement('code');
          code.textContent = acct;
          var btn = document.createElement('button');
          btn.className = 'copy-btn';
          btn.textContent = LBL.copy_label || 'Copy';
          btn.addEventListener('click', function() {
            if (navigator.clipboard) navigator.clipboard.writeText(acct);
            btn.textContent = LBL.copied_label || 'Copied';
            setTimeout(function() { btn.textContent = LBL.copy_label || 'Copy'; }, 1500);
          });
          div.appendChild(code);
          div.appendChild(btn);
        } else {
          var span = document.createElement('span');
          span.className = 'muted';
          span.textContent = s.to + ' ' + (LBL.no_account_suffix || '');
          div.appendChild(span);
        }
        row.appendChild(div);
      });

      // View toggle visibility + label
      var toggleWrap = document.getElementById('view-toggle');
      var toggleBtn = document.getElementById('view-toggle-btn');
      if (isRealIdentity()) {
        toggleWrap.hidden = false;
        toggleBtn.textContent = showAll ? (LBL.view_mine_label || '') : (LBL.view_all_label || '');
      } else {
        toggleWrap.hidden = true;
      }
    }

    function renderAll() {
      renderIdentityCard();
      renderSettlements();
    }

    // Event wiring (static elements only — safe to bind once)
    document.getElementById('id-edit').addEventListener('click', function() {
      document.getElementById('acct-editor').classList.toggle('open');
    });
    document.getElementById('acct-save').addEventListener('click', function() {
      if (!isRealIdentity()) return;
      var ta = document.getElementById('acct-text');
      var status = document.getElementById('acct-status');
      var text = ta.value;
      status.textContent = LBL.saving_label || '';
      fetch('/v1/share/' + encodeURIComponent(shareId) + '/accounts/' +
            encodeURIComponent(identity), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', 'x-device-id': deviceId},
        body: JSON.stringify({account_text: text}),
      }).then(function(r) {
        if (r.ok) {
          accounts[identity] = text;
          status.textContent = LBL.saved_label || '';
          renderSettlements();
        } else {
          status.textContent = LBL.save_failed_label || '';
        }
      }).catch(function() { status.textContent = LBL.save_failed_label || ''; });
    });
    document.getElementById('view-toggle-btn').addEventListener('click', function() {
      showAll = !showAll;
      renderSettlements();
    });

    fetchAccounts().then(function() {
      if (!identity) {
        showIdentityModal();
      } else {
        renderAll();
      }
    });
  })();
  </script>
</body>
</html>"""
```

- [ ] **Step 2: Run rendering tests from Task 1**

```bash
python3 -m pytest tests/test_handler.py::test_share_page_has_identity_card tests/test_handler.py::test_share_page_bootstrap_includes_settlements -v
```

Expected: still FAIL — `_render_share_page` doesn't supply the new labels yet.

---

## Task 4: Update `_render_share_page` to supply new labels + bootstrap

**Files:**
- Modify: `src/split_settle/handler.py` — `_render_share_page` function

- [ ] **Step 1: Replace `_render_share_page`**

Replace the current `_render_share_page` function body with:

```python
def _render_share_page(result: dict, created_at: str = "", si: dict = None,
                       share_id: str = "") -> str:
    """Render the share page HTML from a split result."""
    currency = _esc(result.get("currency", ""))
    total = result.get("total_expenses", 0)
    settlements = result.get("settlements", [])
    summary = result.get("summary", [])
    names = [_esc(s["participant"]) for s in summary]
    n_sett = len(settlements)

    si = si or {}
    labels = {
        "greeting": si.get("greeting", "嗨，"),
        "owed_label": si.get("owed", "別人欠你"),
        "owes_label": si.get("owes", "你要付"),
        "view_all_label": si.get("view_all", "顯示全部 ▼"),
        "view_mine_label": si.get("view_mine", "只看自己 ▲"),
        "copy_label": si.get("copy", "複製"),
        "copied_label": si.get("copied", "已複製"),
        "no_account_suffix": si.get("no_account", "還沒提供帳號"),
        "saving_label": si.get("saving", "儲存中…"),
        "saved_label": si.get("saved", "已儲存 ✓"),
        "save_failed_label": si.get("save_failed", "儲存失敗"),
        "modal_title": si.get("modal_title", "你是哪一位？"),
        "modal_body": si.get("modal_body", "選擇身分後，需要付錢給你的人才會看到你的帳號。"),
        "guest_label": si.get("guest", "我只是路人"),
    }

    bootstrap = {
        "share_id": share_id,
        "currency": result.get("currency", ""),
        "participants": [s["participant"] for s in summary],
        "settlements": [
            {"from": s["from"], "to": s["to"], "amount": s["amount"]}
            for s in settlements
        ],
        "labels": labels,
    }
    bootstrap_json = (json.dumps(bootstrap)
                      .replace("<", "\\u003c")
                      .replace("&", "\\u0026")
                      .replace("'", "\\u0027")
                      .replace("\u2028", "\\u2028")
                      .replace("\u2029", "\\u2029"))

    settlements_html = ""
    for i, s in enumerate(settlements):
        settlements_html += (
            f'<div class="settlement" style="--i:{i}">'
            f'<div class="sett-main">'
            f'<span><span class="from">{_esc(s["from"])}</span> → '
            f'<span class="to">{_esc(s["to"])}</span></span>'
            f'<span class="amount">{currency} {s["amount"]:,.2f}</span>'
            f'</div>'
            f'</div>'
        )

    s_plural = "s" if n_sett != 1 else ""
    replacements = {
        "{{title}}": f"{currency} {total:,.0f} split",
        "{{og_title}}": f"Split: {currency} {total:,.0f} between {len(names)} people",
        "{{og_desc}}": f"{n_sett} transfer{s_plural} needed to settle",
        "{{date}}": _esc(created_at[:10]) if created_at else "",
        "{{participants}}": ", ".join(names),
        "{{currency}}": currency,
        "{{total}}": f"{total:,.2f}",
        "{{settlements_html}}": settlements_html,
        "{{num_settlements}}": str(n_sett),
        "{{s_plural}}": s_plural,
        "{{share_title}}": _esc(si.get("title", "Split Senpai")) if si else "Split Senpai",
        "{{cta_q}}": _esc(si.get("cta_q", "Need to split a bill?")) if si else "Need to split a bill?",
        "{{cta_btn}}": _esc(si.get("cta", "Start splitting →")) if si else "Start splitting →",
        "{{edit_btn_label}}": _esc(labels["owed_label"] and "分享轉帳帳號 ✏️"),
        "{{save_label}}": _esc("儲存"),
        "{{view_all_label}}": _esc(labels["view_all_label"]),
        "{{bootstrap_json}}": bootstrap_json,
    }
    html = SHARE_PAGE_TEMPLATE
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html
```

- [ ] **Step 2: Run rendering tests**

```bash
python3 -m pytest tests/test_handler.py::test_share_page_has_identity_card tests/test_handler.py::test_share_page_bootstrap_includes_settlements -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass, including `test_render_share_page_no_xss_via_participant_name` (XSS regression).

- [ ] **Step 4: Commit**

```bash
git add src/split_settle/handler.py tests/test_handler.py
git commit -m "feat(share): redesign share page with identity card layout"
```

---

## Task 5: Manual browser verification

**Files:** none (deploy + smoke test)

- [ ] **Step 1: Deploy**

```bash
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy
```

Expected: stack updates successfully.

- [ ] **Step 2: Smoke test as creator (Alice)**

1. Open https://split.redarch.dev/ in a fresh incognito window.
2. Create a split with Alice (paid 3000), Bob, Charlie.
3. Click Share → copy link → open in a second incognito window.
4. In the share page, claim "Alice" in the modal.
5. Verify: Identity Card shows `嗨，Alice` + `分享轉帳帳號` button + `別人欠你 NT 2,000` (green).
6. Click `分享轉帳帳號` → textarea expands → type `國泰 700-12345678` → click `儲存` → see `已儲存 ✓`.
7. Only settlements involving Alice are visible; click `顯示全部 ▼` → all rows show; click again (`只看自己 ▲`) → filter back.

- [ ] **Step 3: Smoke test as payer (Bob)**

1. In a third incognito window, open the same share link.
2. Claim "Bob".
3. Verify: Identity Card shows `嗨，Bob` + **no** `分享轉帳帳號` button + `你要付 NT 1,000` (red/orange).
4. Settlement row `Bob → Alice NT 1,000` shows Alice's account inline below with a `複製` button.

- [ ] **Step 4: Smoke test guest**

1. Fourth incognito window, same link, click `我只是路人`.
2. Verify: no identity card, no view-toggle, all settlements visible, no payee account blocks.

- [ ] **Step 5: Commit deploy marker (if any changes made during smoke test)**

No code changes expected. If smoke test surfaces bugs, fix + re-run tests + re-deploy.

---

## Task 6: Open PR

**Files:** none

- [ ] **Step 1: Push branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(share): redesign share page with identity card layout" --body "$(cat <<'EOF'
## Summary
- Replaces me-picker + separate my-account panel with a single Identity Card
- Default filter = claimed identity (guests see all)
- Collapses account editor into an expandable affordance inside the card
- Suppresses editor button when no one owes the user money
- Hides zero-value owed/owes summary lines
- Payee account block stays inline in settlement rows, only when user is the payer

## Test plan
- [ ] `python3 -m pytest tests/ -v` green
- [ ] Smoke test as Alice (has incoming) — see identity card + edit button + save account
- [ ] Smoke test as Bob (outgoing only) — no edit button, sees payee account inline
- [ ] Smoke test as guest — no card, sees all rows
- [ ] Long-name overflow check (participant name 20+ chars)

Spec: `docs/superpowers/specs/2026-04-08-share-page-ui-redesign-design.md`
EOF
)"
```

---

## Notes / Gotchas

- The CSS/HTML/JS all live inside one Python triple-quoted string (`SHARE_PAGE_TEMPLATE`) — double-braces `{{…}}` are template vars that Python `.replace()` substitutes. Curly braces inside JS object literals (`{from: ..., to: ...}`) are NOT template vars because they're single-brace — safe.
- `_esc()` HTML-escapes strings; use it on every user-controlled value before putting it in HTML.
- `bootstrap_json` embeds user strings via `json.dumps` + defensive `\u003c` / `\u0026` / `\u0027` escapes — do not remove those escapes (existing XSS regression test pins them).
- CSP already allows `'unsafe-inline'` for scripts (required because the bootstrap script is inlined). No CSP change needed.
- The identity modal is still initially-hidden `#identity-modal` div, populated by JS on first visit. Do not pre-populate server-side.
