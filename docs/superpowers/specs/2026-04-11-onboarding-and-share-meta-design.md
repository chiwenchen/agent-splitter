# Onboarding Modal + ta-aware Share Meta 設計規格

> 日期：2026-04-11
> 狀態：待實作
> Branch：`feat/onboarding-and-share-meta`

## 目標

在 `split.redarch.dev`（agent-splitter 的 Preact SPA 落地頁）加兩個新功能：

1. **首次進入的 onboarding modal** — 用 3 個步驟卡片解釋產品流程，看過後用 `localStorage` 記錄、不再顯示。
2. **可分享的 OG 預覽** — 加上 1200×630 縮圖、`<title>` / `<meta>` / Open Graph / Twitter card meta tag，讓 LINE / iMessage / Facebook 預覽顯示品牌「分帳仙貝」+ 副標題。副標題可以透過 `?ta=<key>` query param 客製化（例如 `?ta=camping` 顯示「享受露營，輕鬆分帳」），方便針對不同 target audience 分享同一個產品。

兩件事一起做、放在同一個 PR。

---

## 範圍

- ✅ 修改 `agent-splitter` 這個 repo 的 Lambda + 內嵌的 Preact SPA
- ✅ 新增 1200×630 的靜態 PNG（一張共用，不做動態合成）
- ✅ 新增 `?ta=<key>` query param 解析 + 副標題動態切換（OG meta + app 內部 subtitle 同時生效）
- ❌ **不**動 `split-senpai-proxy` (Cloudflare Worker) — Worker 維持純 proxy
- ❌ **不**做 onboarding 的 swipe carousel / 多頁版本 — 只做單卡片 overlay（最簡）
- ❌ **不**碰 React Native 的 `split-senpai-app`（不同 repo、跟這次無關）
- ❌ **不**為 SPA 引入 jest/vitest 等 JS test framework（基礎設施成本 > 收益）

---

## 架構

```
┌──────────────────────────────────────────────────────────────┐
│  ta_mapping module (純資料 + 純函式)                          │
│    src/split_settle/ta_mapping.py                            │
│    - TA_KEYS                                                 │
│    - TA_SUBTITLES: dict[lang, dict[ta, str]]                 │
│    - normalize_ta(raw) -> str                                │
│    - resolve_subtitle(ta, lang) -> str                       │
│    ↑ 100% 純函式，pytest 直接 cover                           │
└──────────────────────────────────────────────────────────────┘
            ↑                                ↑
            │ (server-side render)           │ (client-side render)
            │                                │
┌───────────────────────┐         ┌──────────────────────────┐
│  Lambda HTML          │         │  Preact SPA (app.js)     │
│  injection (handler)  │         │                          │
│                       │         │  - getTa()               │
│  GET /                │         │  - subtitle override     │
│   ↓                   │         │  - <IntroModal />        │
│  - read ?ta query     │         │  - localStorage gate     │
│  - resolve subtitle   │         │                          │
│    (zh-TW for OG)     │         │  + i18n table (mirror    │
│  - replace tokens     │         │    of ta_mapping.py,     │
│    in APP_HTML        │         │    drift-tested in       │
│    (page_title,       │         │    pytest)               │
│     og:*, x-ta meta)  │         │                          │
│                       │         │                          │
│  GET /og-image.png    │         │                          │
│   ↓                   │         │                          │
│  - load disk asset    │         │                          │
│    once at module     │         │                          │
│    init               │         │                          │
│  - return base64      │         │                          │
└───────────────────────┘         └──────────────────────────┘
```

三個單元彼此**沒有狀態耦合**：ta_mapping 不知道誰呼叫它；handler 不知道前端 modal 的存在；前端 modal 不知道 server 注入了什麼 OG。

---

## 檔案異動清單

| 檔案 | 動作 | 規模 |
|---|---|---|
| `src/split_settle/ta_mapping.py` | **新增** | ~50 行 |
| `src/split_settle/handler.py` | 改 `APP_HTML` template + `lambda_handler` 加 og-image route + `/` route 加 query 解析 | ~100 行 diff |
| `src/split_settle/app.js` | i18n 加 ta 文案、加 `<IntroModal />`、加 `getTa()`、CSS（在 `_APP_HTML_TEMPLATE`） | ~150 行 diff |
| `assets/og-image.png` | **新增**（生成的 binary） | ~30-60 KB |
| `scripts/generate_og_image.py` | **新增** — 一次性生成 PNG 的 Pillow 腳本 | ~80 行 |
| `Makefile` | **新增** — SAM custom build hook 把 `assets/og-image.png` 複製進 build artifact | ~6 行 |
| `template.yaml` | 在 `SplitSettleFunction` 加 `Metadata.BuildMethod: makefile` + 新增 `OgImage` event | ~10 行 |
| `tests/test_ta_mapping.py` | **新增** — pure unit test | ~50 行 |
| `tests/test_handler_root.py` | **新增** — 測 `GET /?ta=camping` 的 HTML 注入 | ~80 行 |
| `tests/test_handler_og_image.py` | **新增** — 測 `GET /og-image.png` 回 PNG | ~30 行 |
| `tests/test_assets.py` | **新增** — `assets/og-image.png` 存在且尺寸 1200×630（zero-dep） | ~30 行 |
| `tests/test_ta_mapping_js_parity.py` | **新增** — Python 跟 JS 兩端 ta keys 不漂移 | ~40 行 |
| `.gitignore` | 加 `.superpowers/`、`.claude/worktrees/` | 2 行 |

---

## 詳細設計

### 1. ta mapping 文案表

7 個 ta key (default + 6 個情境) × 3 個語言 = **21 條副標題**。

| `ta=` | zh-TW | en | ja |
|---|---|---|---|
| *(無/default)* | 與朋友同樂，輕鬆分帳 | Hang out with friends, split with ease | 友だちと楽しく、かんたん割り勘 |
| `camping` | 享受露營，輕鬆分帳 | Enjoy the campsite, split with ease | キャンプを楽しんで、かんたん割り勘 |
| `travel` | 享受旅行，輕鬆分帳 | Travel together, split with ease | 旅を楽しんで、かんたん割り勘 |
| `dining` | 享受美食，輕鬆分帳 | Share the meal, split with ease | 食事を楽しんで、かんたん割り勘 |
| `roommate` | 室友同住，輕鬆分攤 | Share the place, split the rent | ルームシェアでも、かんたん割り勘 |
| `family` | 家庭時光，輕鬆分帳 | Family time, split with ease | 家族の時間、かんたん割り勘 |
| `work` | 同事聚會，輕鬆分帳 | Team gatherings, split with ease | 職場の集まり、かんたん割り勘 |

**Resolve 規則**：
- `ta=foo`（未知值）→ fallback 到 `default`，**不**回 404
- `ta=Camping`（大小寫）→ `.lower()` 正規化後命中 `camping`
- 空字串 / `None` → `default`
- 不認識的 lang → fallback 到 `zh-TW`（目標市場）

**OG meta 用哪個語言版本？** 因為 crawler 不送 Accept-Language，OG meta 一律用 **zh-TW**。
**App 內部的 subtitle**：點進來後 client-side 根據使用者語言（既有 `detectLang()`）+ ta 顯示對應那欄。

### 2. `src/split_settle/ta_mapping.py`

```python
"""TA (target audience) subtitle mapping for landing page personalization.

Reads ?ta= query param on GET / and returns a localized subtitle.
Unknown ta values fall back to `default` silently (no 404).
"""
from __future__ import annotations

# Single source of truth for valid ta keys.
# The SPA's i18n must mirror this set (drift-tested in test_ta_mapping_js_parity).
TA_KEYS: tuple[str, ...] = (
    "default", "camping", "travel", "dining", "roommate", "family", "work",
)

TA_SUBTITLES: dict[str, dict[str, str]] = {
    "zh-TW": {
        "default":  "與朋友同樂，輕鬆分帳",
        "camping":  "享受露營，輕鬆分帳",
        "travel":   "享受旅行，輕鬆分帳",
        "dining":   "享受美食，輕鬆分帳",
        "roommate": "室友同住，輕鬆分攤",
        "family":   "家庭時光，輕鬆分帳",
        "work":     "同事聚會，輕鬆分帳",
    },
    "en": {
        "default":  "Hang out with friends, split with ease",
        "camping":  "Enjoy the campsite, split with ease",
        "travel":   "Travel together, split with ease",
        "dining":   "Share the meal, split with ease",
        "roommate": "Share the place, split the rent",
        "family":   "Family time, split with ease",
        "work":     "Team gatherings, split with ease",
    },
    "ja": {
        "default":  "友だちと楽しく、かんたん割り勘",
        "camping":  "キャンプを楽しんで、かんたん割り勘",
        "travel":   "旅を楽しんで、かんたん割り勘",
        "dining":   "食事を楽しんで、かんたん割り勘",
        "roommate": "ルームシェアでも、かんたん割り勘",
        "family":   "家族の時間、かんたん割り勘",
        "work":     "職場の集まり、かんたん割り勘",
    },
}


def normalize_ta(raw: str | None) -> str:
    """Normalize incoming ta param. Unknown / empty / None → 'default'."""
    if not raw:
        return "default"
    key = raw.strip().lower()
    return key if key in TA_KEYS else "default"


def resolve_subtitle(ta: str | None, lang: str = "zh-TW") -> str:
    """Resolve subtitle text. Falls back to zh-TW default on any miss."""
    table = TA_SUBTITLES.get(lang, TA_SUBTITLES["zh-TW"])
    return table.get(normalize_ta(ta), table["default"])
```

### 3. `handler.py` 修改

**3.1 `_APP_HTML_TEMPLATE` 加入 placeholder**

把目前的 `<title>` 和 `<meta name="description">` 換成 token 形式，並補上完整 OG / Twitter card meta：

```html
<title>{{page_title}}</title>
<meta name="description" content="{{page_desc}}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="分帳仙貝">
<meta property="og:title" content="{{og_title}}">
<meta property="og:description" content="{{og_desc}}">
<meta property="og:image" content="{{og_image_url}}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="分帳仙貝 — {{og_desc}}">
<meta property="og:url" content="{{canonical_url}}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{og_title}}">
<meta name="twitter:description" content="{{og_desc}}">
<meta name="twitter:image" content="{{og_image_url}}">
```

**3.2 新增 render 函式**

```python
def _render_app_html(event: dict) -> str:
    """Inject ta-aware OG meta + canonical URL into APP_HTML."""
    qs = event.get("queryStringParameters") or {}
    ta = normalize_ta(qs.get("ta"))

    # OG always uses zh-TW (crawlers don't send Accept-Language)
    subtitle_zh = resolve_subtitle(ta, "zh-TW")
    title_full = f"分帳仙貝 - {subtitle_zh}"

    # Build canonical URL from forwarded host (set by Cloudflare worker)
    headers = event.get("headers") or {}
    host = headers.get("x-forwarded-host") or headers.get("host") or "split.redarch.dev"
    proto = headers.get("x-forwarded-proto", "https")
    qstring = f"?ta={ta}" if ta != "default" else ""
    canonical = f"{proto}://{host}/{qstring}"
    og_image = f"{proto}://{host}/og-image.png"

    replacements = {
        "{{page_title}}": title_full,
        "{{page_desc}}": subtitle_zh,
        "{{og_title}}": title_full,
        "{{og_desc}}": subtitle_zh,
        "{{og_image_url}}": og_image,
        "{{canonical_url}}": canonical,
    }
    html = APP_HTML
    for k, v in replacements.items():
        html = html.replace(k, _esc(v))
    return html
```

**3.3 `lambda_handler` 的 `/` 分支**

```python
if path == "/":
    return _html_response(200, _render_app_html(event))
```

**3.4 新 route `/og-image.png`**

模組初始化時讀檔（cold start cost 一次）：

```python
import base64
from pathlib import Path

_OG_IMAGE_PATH = Path(__file__).parent / "assets" / "og-image.png"
try:
    _OG_IMAGE_BYTES = _OG_IMAGE_PATH.read_bytes()
    _OG_IMAGE_B64 = base64.b64encode(_OG_IMAGE_BYTES).decode("ascii")
except FileNotFoundError:
    logger.warning("og-image.png not found at %s", _OG_IMAGE_PATH)
    _OG_IMAGE_B64 = ""
```

route handler:

```python
if path == "/og-image.png":
    if not _OG_IMAGE_B64:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "text/plain"},
            "body": "not found",
        }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "image/png",
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
        "body": _OG_IMAGE_B64,
        "isBase64Encoded": True,
    }
```

`_ROUTE_METHODS` 增加 `"/og-image.png": "GET"`。

### 4. `template.yaml` + `Makefile`（SAM build hook）

`template.yaml` 在 `SplitSettleFunction.Properties` 同層加：

```yaml
Metadata:
  BuildMethod: makefile
```

新增 `OgImage` event：

```yaml
OgImage:
  Type: HttpApi
  Properties:
    Path: /og-image.png
    Method: get
    ApiId: !Ref SplitSettleApi
```

新增 repo root 的 `Makefile`：

```makefile
build-SplitSettleFunction:
	cp -r src/split_settle/. $(ARTIFACTS_DIR)
	mkdir -p $(ARTIFACTS_DIR)/assets
	cp assets/og-image.png $(ARTIFACTS_DIR)/assets/og-image.png
```

`handler.py` 讀檔路徑用 `Path(__file__).parent / "assets" / "og-image.png"`，**和 build artifact 的相對位置一致**。

### 5. OG image 視覺設計（Centered Minimal）

1200 × 630 PNG，背景墨綠 `#2d4a4a`，1px 金色 inset frame `#e8a84c`：

```
┌──────────────────────────────────────┐
│  ┌────────────────────────────────┐  │
│  │                                │  │
│  │                                │  │
│  │           分帳仙貝              │  │  ← 170pt PingFang Heavy, accent gold
│  │           ━━━━                 │  │  ← 240×6px gold bar
│  │       三步驟完成分帳            │  │  ← 48pt PingFang Regular, text color
│  │                                │  │
│  │                                │  │
│  │         split.redarch.dev      │  │  ← 28pt mono-ish, muted color
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

**完全沒有 emoji**（user preference: emoji 看起來廉價）。**生成腳本見下方**。

### 6. `scripts/generate_og_image.py`

```python
#!/usr/bin/env python3
"""Generate the OG share card for 分帳仙貝 → assets/og-image.png

Run once locally:
    python3 -m pip install --user Pillow
    python3 scripts/generate_og_image.py

Outputs assets/og-image.png (1200x630). Commit the PNG to git so SAM
build doesn't need Pillow at deploy time.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Brand palette (matches handler.py CSS variables)
BG     = (45, 74, 74)      # --layer-1 #2d4a4a
ACCENT = (232, 168, 76)    # --accent  #e8a84c
TEXT   = (224, 213, 196)   # --text-on-dark #e0d5c4
MUTED  = (138, 170, 158)   # --text-muted #8aaa9e

WIDTH, HEIGHT = 1200, 630

# Font discovery (CJK-capable, fallback chain)
FONT_CANDIDATES_BOLD = [
    ("/System/Library/Fonts/PingFang.ttc", 4),                  # macOS Heavy
    ("/System/Library/Fonts/PingFang.ttc", 3),                  # macOS Semibold
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
]
FONT_CANDIDATES_REG = [
    ("/System/Library/Fonts/PingFang.ttc", 1),                  # macOS Regular
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
]


def _load(size: int, candidates: list) -> ImageFont.FreeTypeFont:
    for path, idx in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue
    raise RuntimeError("No CJK-capable font found. Install noto-cjk or run on macOS.")


def _center_text(draw, text, font, y, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) // 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def main():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    pad = 24
    draw.rectangle([pad, pad, WIDTH - pad, HEIGHT - pad], outline=ACCENT, width=2)

    title_font = _load(170, FONT_CANDIDATES_BOLD)
    title_h = _center_text(draw, "分帳仙貝", title_font, y=170, fill=ACCENT)

    bar_y = 170 + title_h + 36
    bar_w, bar_h = 240, 6
    draw.rectangle(
        [(WIDTH - bar_w) // 2, bar_y, (WIDTH + bar_w) // 2, bar_y + bar_h],
        fill=ACCENT,
    )

    tag_font = _load(48, FONT_CANDIDATES_REG)
    _center_text(draw, "三步驟完成分帳", tag_font, y=bar_y + bar_h + 36, fill=TEXT)

    url_font = _load(28, FONT_CANDIDATES_REG)
    _center_text(draw, "split.redarch.dev", url_font, y=HEIGHT - 80, fill=MUTED)

    out = Path(__file__).resolve().parent.parent / "assets" / "og-image.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
```

**Pillow 是 dev-time only**：腳本本地跑一次 → commit PNG → Lambda runtime 完全不需要 Pillow。

### 7. 前端 `app.js` 修改

**7.1 i18n 加 ta 副標 + onboarding 文案**

```javascript
const TA_KEYS = ['default','camping','travel','dining','roommate','family','work'];

const taSubtitles = {
  en: {
    default:  'Hang out with friends, split with ease',
    camping:  'Enjoy the campsite, split with ease',
    travel:   'Travel together, split with ease',
    dining:   'Share the meal, split with ease',
    roommate: 'Share the place, split the rent',
    family:   'Family time, split with ease',
    work:     'Team gatherings, split with ease',
  },
  'zh-TW': {
    default:  '與朋友同樂，輕鬆分帳',
    camping:  '享受露營，輕鬆分帳',
    travel:   '享受旅行，輕鬆分帳',
    dining:   '享受美食，輕鬆分帳',
    roommate: '室友同住，輕鬆分攤',
    family:   '家庭時光，輕鬆分帳',
    work:     '同事聚會，輕鬆分帳',
  },
  ja: {
    default:  '友だちと楽しく、かんたん割り勘',
    camping:  'キャンプを楽しんで、かんたん割り勘',
    travel:   '旅を楽しんで、かんたん割り勘',
    dining:   '食事を楽しんで、かんたん割り勘',
    roommate: 'ルームシェアでも、かんたん割り勘',
    family:   '家族の時間、かんたん割り勘',
    work:     '職場の集まり、かんたん割り勘',
  },
};
```

每個語言的 i18n 表加入：

```javascript
introWelcomeEyebrow: 'WELCOME',
introTitle:          '歡迎使用 分帳仙貝',  // zh-TW
introTagline:        '三個步驟，輕鬆完成分帳',
introStep1:          '加入朋友',
introStep2:          '添加帳單',
introStep3:          '分享分帳連結',
introCta:            '開始分帳',
```

對應 en：
```
introWelcomeEyebrow: 'WELCOME'
introTitle:          'Welcome to 分帳仙貝'
introTagline:        'Three quick steps to settle up'
introStep1:          'Add your friends'
introStep2:          'Add the expenses'
introStep3:          'Share the link'
introCta:            'Get started'
```

對應 ja：
```
introWelcomeEyebrow: 'ようこそ'
introTitle:          '分帳仙貝へようこそ'
introTagline:        '3ステップでかんたん割り勘'
introStep1:          '友だちを追加'
introStep2:          '支出を追加'
introStep3:          'リンクを共有'
introCta:            'はじめる'
```

**7.2 URL param 讀取**

```javascript
function getTa() {
  try {
    const raw = new URLSearchParams(window.location.search).get('ta') || '';
    const key = raw.trim().toLowerCase();
    return TA_KEYS.includes(key) ? key : 'default';
  } catch { return 'default'; }
}
```

**7.3 Subtitle override**

```javascript
const ta = getTa();   // top of App()
const subtitle = taSubtitles[lang]?.[ta] || taSubtitles[lang]?.default || t.subtitle;
// ...
<div class="subtitle">${subtitle}</div>
```

**7.4 IntroModal component**

```javascript
const INTRO_SEEN_KEY = 'ss_seen_intro';

function IntroModal({ t, onDismiss }) {
  return html`
    <div class="intro-overlay" onClick=${onDismiss}>
      <div class="intro-card" onClick=${e => e.stopPropagation()}>
        <div class="intro-eyebrow">${t.introWelcomeEyebrow}</div>
        <h2 class="intro-title">${t.introTitle}</h2>
        <div class="intro-tagline">${t.introTagline}</div>
        <div class="intro-step">
          <span class="intro-num">1</span>
          <span class="intro-text">${t.introStep1}</span>
        </div>
        <div class="intro-step">
          <span class="intro-num">2</span>
          <span class="intro-text">${t.introStep2}</span>
        </div>
        <div class="intro-step">
          <span class="intro-num">3</span>
          <span class="intro-text">${t.introStep3}</span>
        </div>
        <button class="intro-cta" onClick=${onDismiss}>${t.introCta}</button>
      </div>
    </div>
  `;
}
```

**7.5 觸發邏輯**

```javascript
const [showIntro, setShowIntro] = useState(false);

useEffect(() => {
  try {
    if (!localStorage.getItem(INTRO_SEEN_KEY)) {
      setShowIntro(true);
    }
  } catch {}
}, []);

function dismissIntro() {
  setShowIntro(false);
  try { localStorage.setItem(INTRO_SEEN_KEY, '1'); } catch {}
}
```

Render:

```javascript
return html`
  ${showIntro ? html`<${IntroModal} t=${t} onDismiss=${dismissIntro} />` : ''}
  <div class="header-row">
    ...
  </div>
`;
```

**7.6 CSS（加進 `_APP_HTML_TEMPLATE` 的 `<style>`）**

```css
.intro-overlay {
  position:fixed; inset:0;
  background:rgba(15,28,28,0.78);
  backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
  display:flex; align-items:center; justify-content:center;
  padding:18px; z-index:1000;
  animation:introFadeIn 0.25s ease-out;
}
@keyframes introFadeIn { from {opacity:0} to {opacity:1} }

.intro-card {
  width:100%; max-width:380px;
  background:var(--layer-1);
  border-radius:20px;
  padding:24px 22px 22px;
  color:var(--text-on-dark);
  box-shadow: 6px 6px 14px rgba(10,30,30,0.5),
              -2px -2px 8px rgba(60,100,100,0.15),
              0 0 0 1px rgba(232,168,76,0.18);
  animation:introPopIn 0.3s cubic-bezier(0.2,0.9,0.3,1.2);
}
@keyframes introPopIn {
  from { opacity:0; transform:scale(0.92) translateY(8px); }
  to   { opacity:1; transform:scale(1) translateY(0); }
}

.intro-eyebrow { font-size:10px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:1.5px; text-align:center; margin-bottom:6px; }
.intro-title { font-size:21px; font-weight:800; color:var(--accent); text-align:center; margin:0 0 6px; }
.intro-tagline { font-size:12px; color:var(--text-muted); text-align:center; margin-bottom:18px; }

.intro-step {
  display:flex; align-items:center; gap:12px;
  padding:11px 12px;
  background:var(--layer-2);
  border-radius:12px;
  margin-bottom:8px;
  box-shadow:var(--neu-in);
}
.intro-num {
  flex-shrink:0;
  width:26px; height:26px; border-radius:50%;
  background:linear-gradient(135deg,var(--accent),var(--accent-dark));
  color:var(--layer-2);
  font-weight:800; font-size:13px;
  display:flex; align-items:center; justify-content:center;
  box-shadow:2px 2px 4px rgba(10,30,30,0.4);
}
.intro-text { font-size:13px; font-weight:600; color:var(--text-on-dark); }

.intro-cta {
  width:100%; margin-top:14px;
  background:linear-gradient(135deg,var(--accent),var(--accent-dark));
  color:var(--layer-2); border:none; border-radius:12px;
  padding:13px; font-size:13px; font-weight:800;
  box-shadow:var(--neu-out);
  cursor:pointer;
}
.intro-cta:active { box-shadow:var(--neu-in); }
```

---

## 安全性

| 議題 | 處理 |
|---|---|
| `ta` 是 user-controlled query param | `normalize_ta` 白名單檢查 + `_esc()` 二次防禦，不可能 XSS |
| host header 偽造 | canonical URL 用 `x-forwarded-host`（Worker 已 set）+ fallback 到 `host` + 最終 fallback 到固定值 `split.redarch.dev` |
| 大量 `ta=foo` 噪音流量 | normalize 後一律打到 default，無 DB 寫入、無外部呼叫，純記憶體 lookup |
| Cold start | OG image 讀檔在 module init 一次，後續 request 都從常駐記憶體吐 base64 |
| Lambda response size | 1200×630 PNG 約 30-60KB → API Gateway 限制 6MB，遠在範圍內 |
| `ALLOWED_HOSTS` host gate | 在 routing 之前；新 route `/og-image.png` 自動繼承這個保護 |

---

## 測試策略

| 層級 | 檔案 | 測什麼 |
|---|---|---|
| Pure unit | `tests/test_ta_mapping.py` | `normalize_ta()` 各種輸入；`resolve_subtitle()` 三語 × 7 keys；未知 ta 落到 default；大小寫不敏感；空字串/None |
| i18n drift | `tests/test_ta_mapping.py` | `set(zh-TW.keys()) == set(en.keys()) == set(ja.keys()) == set(TA_KEYS)` |
| Cross-language drift | `tests/test_ta_mapping_js_parity.py` | regex 從 `app.js` 抓 JS 端 `taSubtitles` keys，對比 `ta_mapping.py` 的 `TA_KEYS` |
| Lambda integration | `tests/test_handler_root.py` | `GET /` 無 query → default；`GET /?ta=camping` → 露營副標出現在 OG meta；`?ta=Camping` → 一樣命中；`?ta=foo` → fallback default 不報錯；`?ta=<script>` → escaped |
| Lambda integration | `tests/test_handler_og_image.py` | `GET /og-image.png` 回 200 + `Content-Type: image/png` + `isBase64Encoded:True` + body 解 base64 後前 8 byte 是 PNG signature |
| Asset existence | `tests/test_assets.py` | `assets/og-image.png` 存在；用 `struct` 從 IHDR chunk 解 width/height 斷言 1200×630（**zero-dep，不 import Pillow**） |
| Regression | 既有 `tests/test_handler.py` etc. | 其他既有 routes 全部不能 break |

**JS 端不引入 jest/vitest**。drift test 用 Python 跨語言驗證。

### Playwright E2E（生產環境驗證）

部署完後跑：

1. Navigate to `https://split.redarch.dev` (clean state, no localStorage)
2. 斷言 onboarding modal 出現（`.intro-card` element 存在）
3. 斷言三步驟文字「加入朋友 / 添加帳單 / 分享分帳連結」存在
4. 點 CTA dismiss
5. Reload 頁面，斷言 modal **不**再出現
6. 清空 localStorage，navigate to `?ta=camping`
7. 斷言 subtitle DOM 顯示「享受露營，輕鬆分帳」
8. `curl -I https://split.redarch.dev/og-image.png` 回 200 + `Content-Type: image/png`
9. View page source 含 `<meta property="og:title" content="分帳仙貝 - 享受露營，輕鬆分帳">`
10. 抓 https://www.opengraph.xyz/url/... 或本地 head request 驗證

---

## 部署

| 步驟 | Command |
|---|---|
| Local PNG generation | `python3 scripts/generate_og_image.py` |
| Local SAM build | `PATH="/opt/homebrew/bin:$PATH" sam build` |
| 驗證 PNG packaged | `ls .aws-sam/build/SplitSettleFunction/assets/og-image.png` |
| Deploy | `PATH="/opt/homebrew/bin:$PATH" sam deploy` |
| Smoke test | `curl -I https://split.redarch.dev/og-image.png` |
| Playwright E2E | 見上 |

---

## 不做的事 / Out of scope

- ❌ Pillow 進 Lambda runtime
- ❌ 動態合成 OG image（per-ta 不同圖）
- ❌ Cloudflare Worker 端的 HTMLRewriter（保持 Worker 純 proxy）
- ❌ ESC 鍵 dismiss onboarding（複雜度不值）
- ❌ Onboarding swipe 多頁版本（單卡片更乾淨）
- ❌ 為前端 SPA 引入 jest/vitest
- ❌ React Native `split-senpai-app` 同步改動
