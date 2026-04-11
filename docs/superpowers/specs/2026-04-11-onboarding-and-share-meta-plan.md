# Implementation Plan — Onboarding Modal + ta-aware Share Meta

> Spec：[2026-04-11-onboarding-and-share-meta-design.md](./2026-04-11-onboarding-and-share-meta-design.md)
> Branch：`feat/onboarding-and-share-meta`
> 預計 commit 數：6 個

## 執行順序

依序執行下面 6 個 step。每個 step 是一個獨立的 logical commit。Step 1-5 全部完成後 push + 開 PR。

### Step 1：Housekeeping (`.gitignore`)

把 brainstorm 工作目錄 + 平行 worktree 加進 ignore（這次 session 會產生 `.superpowers/` 這類 untracked 檔案）。

**檔案**：
- `.gitignore` — append `.superpowers/`、`.claude/worktrees/`

**Commit**：`chore: gitignore .superpowers and .claude/worktrees`

---

### Step 2：Spec + Plan docs

把這次的設計檔 commit 進 git。

**檔案**：
- `docs/superpowers/specs/2026-04-11-onboarding-and-share-meta-design.md`（已寫好）
- `docs/superpowers/specs/2026-04-11-onboarding-and-share-meta-plan.md`（這份）

**Commit**：`docs: spec and plan for onboarding modal and ta-aware share meta`

---

### Step 3：`ta_mapping` 模組 + tests

純 Python module，無 side effect。先寫 test、再寫 code（TDD）。

**檔案**：
- `src/split_settle/ta_mapping.py` — TA_KEYS、TA_SUBTITLES、normalize_ta、resolve_subtitle
- `tests/test_ta_mapping.py` — pure unit tests
- `tests/test_ta_mapping_js_parity.py` — 跨語言 drift test（先 stub，因為 app.js 還沒改；等 Step 6 改完 app.js 後 test 才會 meaningful）

**Test cases**：
1. `normalize_ta(None) == "default"`
2. `normalize_ta("") == "default"`
3. `normalize_ta("camping") == "camping"`
4. `normalize_ta("Camping") == "camping"`（大小寫）
5. `normalize_ta("  camping  ") == "camping"`（trim）
6. `normalize_ta("foo") == "default"`（未知）
7. `resolve_subtitle("camping", "zh-TW") == "享受露營，輕鬆分帳"`
8. `resolve_subtitle("camping", "en") == "Enjoy the campsite, split with ease"`
9. `resolve_subtitle("camping", "ja") == "キャンプを楽しんで、かんたん割り勘"`
10. `resolve_subtitle(None, "zh-TW") == "與朋友同樂，輕鬆分帳"`
11. `resolve_subtitle("foo", "zh-TW") == "與朋友同樂，輕鬆分帳"`（unknown ta）
12. `resolve_subtitle("camping", "fr") == "享受露營，輕鬆分帳"`（unknown lang fallback to zh-TW）
13. 三語的 keys 集合等於 `set(TA_KEYS)`
14. 沒有空字串文案

執行：`python3 -m pytest tests/test_ta_mapping.py -v` → all green。

**Commit**：`feat(ta-mapping): add target audience subtitle resolver module`

---

### Step 4：`handler.py` 修改 + tests

改造 `_APP_HTML_TEMPLATE` + 加 `_render_app_html` + 加 `/og-image.png` route。

**檔案**：
- `src/split_settle/handler.py`：
  - import `from .ta_mapping import normalize_ta, resolve_subtitle`（注意 handler.py 不是 package style，看現有 import 風格決定是 absolute 還是 relative）
  - `_APP_HTML_TEMPLATE` 把 `<title>...</title>` 改為 `{{page_title}}`，把 `<meta name="description" ...>` 改為 `{{page_desc}}`，並補上完整 og:* / twitter:* meta
  - 新增 `_render_app_html(event)` 函式
  - 新增 `_OG_IMAGE_PATH` / `_OG_IMAGE_BYTES` / `_OG_IMAGE_B64` module-level（**Step 5 之前先用空 b''，等 Step 5 commit PNG 後就會自動生效**）
  - `lambda_handler` `/` 分支改為 `_html_response(200, _render_app_html(event))`
  - `lambda_handler` 加 `/og-image.png` 分支
  - `_ROUTE_METHODS` 加 `"/og-image.png": "GET"`
- `tests/test_handler_root.py`（新）— 整合測試：
  1. `GET /` 無 query → response body 含「與朋友同樂，輕鬆分帳」+ `og:title="分帳仙貝 - 與朋友同樂，輕鬆分帳"`
  2. `GET /?ta=camping` → 含「享受露營，輕鬆分帳」
  3. `GET /?ta=Camping` → 同上
  4. `GET /?ta=foo` → fallback default 不報錯
  5. `GET /?ta=<script>alert(1)</script>` → escaped (`&lt;script&gt;`)，不存在 raw `<script>`
  6. `og:image` URL 用 `x-forwarded-host` 構造
- `tests/test_handler_og_image.py`（新）— 整合測試：
  1. 沒有 PNG asset → 404
  2. 有 PNG asset (mock `_OG_IMAGE_B64 = "fake"` 或實際讀檔) → 200 + correct content-type + `isBase64Encoded:True`

執行：`python3 -m pytest tests/test_handler_root.py tests/test_handler_og_image.py -v` → all green。

**注意**：Step 4 commit 時，因為 PNG 還沒生（Step 5 才生），所以 `/og-image.png` route 在 production 會回 404。這是預期的中間狀態，Step 5 commit 後就會解決。**整個 PR 一起 review、一起 deploy**，使用者不會看到中間態。

**Commit**：`feat(handler): inject ta-aware OG meta on root + add og-image route`

---

### Step 5：OG image asset + Makefile + template.yaml

**檔案**：
- `scripts/generate_og_image.py`（新）— Pillow 腳本
- `assets/og-image.png`（新）— 跑 `python3 scripts/generate_og_image.py` 生成的 binary
- `Makefile`（新）— SAM custom build hook
- `template.yaml` — 在 `SplitSettleFunction.Properties` 同層加 `Metadata: BuildMethod: makefile`，並加 `OgImage` event
- `tests/test_assets.py`（新）— sanity test：檔案存在 + IHDR 解碼 1200×630（zero-dep，純 struct）

執行：
1. `python3 -m pip install --user Pillow`（如果還沒裝）
2. `python3 scripts/generate_og_image.py` → 應該輸出 `wrote .../assets/og-image.png (XXkb, 1200x630)`
3. `python3 -m pytest tests/test_assets.py -v` → green
4. `PATH="/opt/homebrew/bin:$PATH" sam build` → 應該成功
5. `ls .aws-sam/build/SplitSettleFunction/assets/og-image.png` → 應該存在

**Commit**：`feat(og-image): generate and serve 1200x630 share card`

---

### Step 6：前端 `app.js` 修改

改 `src/split_settle/app.js`：

**修改**：
1. 在 `i18n` 物件**外部**（同檔案 top）加 `TA_KEYS` + `taSubtitles`
2. 每個語言的 `i18n` table 加 `intro*` 7 條文案
3. 加 `getTa()` 函式
4. 加 `INTRO_SEEN_KEY` 常數
5. 加 `IntroModal` component
6. `App()` 組件加 `useEffect` 檢查 localStorage、`useState` for showIntro、`dismissIntro` 函式
7. `App()` 內把 `${t.subtitle}` 換成 `${subtitle}`（local var by lang+ta）
8. Render 中插入 `${showIntro ? html\`<${IntroModal} ...\`/> : ''}`
9. 在 `_APP_HTML_TEMPLATE` 的 `<style>` 區塊加 `.intro-overlay` `.intro-card` `.intro-step` 等 CSS

**完成 Step 6 後**，回到 `tests/test_ta_mapping_js_parity.py`，把 stub 改成 real test：用 regex 從 `app.js` 抓 `taSubtitles` 三語的 key 集合，斷言等於 `set(ta_mapping.TA_KEYS)`。

執行：`python3 -m pytest tests/ -v` → 全綠（包含舊 tests 沒 break）

**Commit**：`feat(app): onboarding modal + ta-aware in-app subtitle`

---

### Step 7：Push + PR + CI

```bash
git push -u origin feat/onboarding-and-share-meta
gh pr create \
  --title "feat: onboarding modal + ta-aware share meta" \
  --body "$(cat <<'EOF'
## Summary
- Onboarding modal (3 steps) on first visit, gated by localStorage
- ta-aware OG meta + in-app subtitle (?ta=camping → '享受露營，輕鬆分帳')
- New /og-image.png route serving 1200x630 brand card
- Full i18n: 7 ta keys × 3 langs (zh-TW/en/ja)

## Test plan
- [x] pytest passes (ta_mapping unit, handler integration, asset sanity, JS parity)
- [ ] sam build succeeds with Makefile build hook
- [ ] sam deploy succeeds
- [ ] Playwright E2E: onboarding modal shown on first visit, dismissed on click, not re-shown after reload
- [ ] Playwright E2E: ?ta=camping shows '享受露營，輕鬆分帳' in subtitle
- [ ] curl /og-image.png returns 200 PNG
- [ ] view-source contains correct og:title / og:description / og:image
EOF
)"

# Monitor CI
gh run list --branch feat/onboarding-and-share-meta --limit 5
# Wait for checks; fix any failure
```

如有 CI failure：`gh run view <id> --log-failed` → 修 → commit → push → 重 monitor，直到全綠。

---

### Step 8：Deploy

```bash
PATH="/opt/homebrew/bin:$PATH" sam build
PATH="/opt/homebrew/bin:$PATH" sam deploy
```

驗證：
```bash
curl -sI https://split.redarch.dev/og-image.png | head -5
curl -sL https://split.redarch.dev/?ta=camping | grep -E '(og:|<title>|description)'
```

---

### Step 9：Playwright E2E

對 production 跑：
1. 清 localStorage → navigate `https://split.redarch.dev`
2. 斷言 `.intro-card` 存在 + 文字「加入朋友」
3. 點 `.intro-cta` (`button:has-text("開始分帳")`)
4. 斷言 `.intro-card` 消失
5. Reload → 斷言 `.intro-card` 不存在
6. 清 localStorage → navigate `https://split.redarch.dev/?ta=camping`
7. 斷言 `.subtitle` 文字 == "享受露營，輕鬆分帳"
8. 截圖存證，丟進 PR comment

---

### Step 10：最終回報

按 CLAUDE.md `Development Status Report` 格式：
- PR Number + URL
- PR Status
- Test Report (pytest pass count, coverage)
- E2E Report (Playwright pass count)

---

## 風險 / 應對

| 風險 | 應對 |
|---|---|
| `sam build` 不認 Makefile build hook | 用 `sam build --debug` 看 log；fallback 改用 `CodeUri: .` + Makefile 也可 |
| Makefile 在 macOS 上 tab 縮排錯誤 | 用 hard tab，verify with `cat -A Makefile` |
| Pillow 找不到 PingFang.ttc | fallback 字型 chain 涵蓋 STHeiti + Noto CJK，最差會 raise，不會 silent 出爛圖 |
| Lambda cold start 變慢 | OG image base64 ~50KB，module init 多讀檔 + base64 一次，預估 +20ms cold start，可接受 |
| `_render_app_html` 改造可能碰到 `_esc()` 對 URL 過度 escape | URL 裡 `?ta=camping` 的 `?` 跟 `=` 會被轉成 `&quot;`-style 嗎？不會（`_esc` 只轉 `&<>"'`），但要 verify test 涵蓋 |
| Playwright 抓 SPA 的 subtitle 時 race condition | 用 `await page.waitForSelector('.subtitle')` + 取 textContent，不要立刻讀 |
| Production 上 `?ta=camping` 被 ALLOWED_HOSTS 阻擋 | 不會，host gate 跟 query string 無關 |
| `sam deploy` 失敗 | 看 CloudFormation event log；最差 rollback |
