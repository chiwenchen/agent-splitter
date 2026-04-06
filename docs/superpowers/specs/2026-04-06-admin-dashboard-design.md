# Admin Dashboard + Cloudflare 安全強化 設計規格

> 日期：2026-04-06
> 狀態：待實作

## 目標

兩件事一起做：

1. **Cloudflare 安全強化**：立即部署，讓 `split.redarch.dev` 抗濫用
2. **Admin Dashboard**：私人後台 `split-admin.redarch.dev`，管理 share 內容、看使用統計、看 Cloudflare analytics

---

## Part 1: Cloudflare 安全設定

立即用 Cloudflare CLI/API 套用以下設定到 `redarch.dev` zone：

### 1.1 Rate Limiting Rules

| Rule | Path | Method | Threshold | Action |
|------|------|--------|-----------|--------|
| Share creation throttle | `/v1/share` | POST | 5 req/min/IP | Block 10 min |
| Share view throttle | `/s/*` | GET | 60 req/min/IP | Block 10 min |

理由：正常 app 使用者不會 1 分鐘內呼叫 `/v1/share` 超過 5 次。`/s/{id}` 60 req/min 防止有人爬所有歷史 shares。

### 1.2 WAF Managed Rules
- **ON**: Cloudflare Managed Ruleset (Free plan)
- 自動擋 SQL injection、XSS、Path Traversal 等常見攻擊

### 1.3 Always Use HTTPS
- **ON**: 強制所有 HTTP 請求 redirect 到 HTTPS

### 1.4 不開的設定（理由）
- **Bot Fight Mode**: OFF — Free plan 版本無法細調，可能誤擋 React Native app
- **Security Level**: Low — Medium/High 會用 JS Challenge，RN app 不執行 JS
- **Super Bot Fight Mode**: 需要 Pro plan ($20/月)，目前不開

### 1.5 套用範圍
僅 `split.redarch.dev`（public API + share pages）。`split-admin.redarch.dev` 由 Cloudflare Access 保護，不需要這些 rules。

---

## Part 2: Admin Dashboard

### 2.1 架構

```
                    Cloudflare Access
                    (One-time PIN, only cwchen2000@gmail.com)
                            ↓
split-admin.redarch.dev → Cloudflare Worker (proxy)
                            ↓
            POST /admin/* → AWS Lambda (existing agent-splitter)
                            ↓
                          DynamoDB GroupsTable
                          Cloudflare Analytics API (for traffic stats)
```

- **單一 Lambda**：在現有 `agent-splitter-SplitSettleFunction` 加 `/admin/*` 路由
- **單一 frontend**：inline HTML SPA in `handler.py`（同 share page 的 htm + preact 模式）
- **新 Worker**：`split-admin-proxy`，proxy `split-admin.redarch.dev` → AWS API Gateway，加上 Access JWT 驗證
- **Cloudflare Access**：保護 `split-admin.redarch.dev`，One-time PIN，allow `cwchen2000@gmail.com`

### 2.2 認證流程

1. 使用者打開 `split-admin.redarch.dev`
2. Cloudflare Access 攔截，要求 email 驗證（One-time PIN 寄到 cwchen2000@gmail.com）
3. 驗證成功後，Cloudflare 在 request header 加上 `Cf-Access-Jwt-Assertion`
4. 請求進入 Cloudflare Worker proxy，proxy 把 header 一併轉發
5. AWS Lambda 收到請求後，在 `/admin/*` 路由處理器**驗證 `Cf-Access-Jwt-Assertion` JWT**：
   - 用 Cloudflare 的公鑰驗 JWT 簽名
   - 確認 `email` claim 為 `cwchen2000@gmail.com`
   - 失敗 → 401
6. 驗證通過 → 處理 admin 邏輯

**為什麼 Lambda 也要驗 JWT？** 防止有人繞過 Cloudflare Access 直接打 AWS API Gateway。Lambda 必須當作零信任。

### 2.3 後端 API endpoints

所有 `/admin/*` 路由都需要 Cf-Access-Jwt 驗證。

#### `GET /admin` (or `/admin/`)
- 回傳 admin SPA HTML（內嵌 dashboard 的 htm + preact code）

#### `GET /admin/api/stats`
- 用途：dashboard 統計卡片資料
- 回傳：
  ```json
  {
    "total_shares": 42,
    "shares_by_day": [
      { "date": "2026-04-01", "count": 3 },
      ...
    ],
    "currency_breakdown": {
      "TWD": 25,
      "USD": 10,
      "JPY": 7
    },
    "avg_amount_by_currency": {
      "TWD": 1500.5,
      "USD": 75.3
    }
  }
  ```
- 實作：scan `GroupsTable` 找所有 `PK begins_with 'SHARE#'`，aggregate 計算

#### `GET /admin/api/shares?cursor={cursor}&limit=20`
- 用途：share 列表，cursor-based 分頁
- 回傳：
  ```json
  {
    "items": [
      {
        "share_id": "abc123",
        "created_at": "2026-04-06T...",
        "currency": "TWD",
        "total": 1500,
        "participants_count": 3,
        "participants_preview": "Alice, Bob, Carol"
      }
    ],
    "next_cursor": "..."  // null if last page
  }
  ```

#### `GET /admin/api/shares/{id}`
- 用途：查看單筆完整內容
- 回傳：完整 share data（request_body + result）

#### `DELETE /admin/api/shares/{id}`
- 用途：刪除一筆 share
- 回傳：`{ "deleted": true }`
- 在 Lambda 端也要 log 刪除動作（CloudWatch）

#### `GET /admin/api/cloudflare/analytics`
- 用途：拉 Cloudflare zone analytics
- Backend 呼叫 Cloudflare GraphQL Analytics API
- 需要 Cloudflare API Token（存在 AWS Secrets Manager）
- 回傳：
  ```json
  {
    "requests_24h": 1234,
    "blocked_24h": 56,
    "top_countries": [...],
    "top_paths": [...]
  }
  ```

### 2.4 Frontend SPA（dashboard）

inline 在 `handler.py` 的 `_render_admin_page()` function。

#### 頁面結構

```
┌────────────────────────────────────────┐
│  分帳仙貝 Admin                  [登出] │
├────────────────────────────────────────┤
│                                         │
│  📊 統計                                │
│  ┌─────────┬─────────┬─────────┐       │
│  │ 總 Share│ 24h 新增 │ 平均金額 │       │
│  │   42    │    3     │ TWD 1500│       │
│  └─────────┴─────────┴─────────┘       │
│                                         │
│  📈 過去 30 天每日新增（折線圖）         │
│  [SVG line chart]                       │
│                                         │
│  🥧 各幣別佔比（圓餅圖）                 │
│  [SVG pie chart]                        │
│                                         │
│  🛡️ Cloudflare 流量（過去 24h）          │
│  ┌─────────┬─────────┐                 │
│  │ 總請求數│ 被擋次數 │                 │
│  │  1234   │   56     │                 │
│  └─────────┴─────────┘                 │
│                                         │
│  📋 Share 列表                          │
│  ┌──────────────────────────────────┐  │
│  │ abc123 | 04/06 | TWD 1500 | 3人  │  │
│  │ [查看] [刪除]                     │  │
│  ├──────────────────────────────────┤  │
│  │ ...                               │  │
│  └──────────────────────────────────┘  │
│                                         │
│  [上一頁] [下一頁]                      │
└────────────────────────────────────────┘
```

#### 圖表
- **不引入 chart 套件**，用 inline SVG 手刻簡單的折線圖和圓餅圖
- 折線圖：30 個點 + 連線 + 軸線
- 圓餅圖：根據 currency_breakdown 的比例畫扇形

#### 配色
- 用既有的 Split Senpai theme（teal background, amber accent）
- 一致的視覺識別

### 2.5 Cloudflare Worker proxy `split-admin-proxy`

新建 Worker，部署到 `~/Documents/repos/split-admin-proxy/`：

```javascript
const ORIGIN = 'https://split.redarch.dev'; // 同個 Lambda backend，但走 admin 路徑

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // 把 split-admin.redarch.dev/X → split.redarch.dev/admin/X
    const targetPath = '/admin' + url.pathname;
    const target = new URL(targetPath + url.search, ORIGIN);

    const headers = new Headers(request.headers);
    headers.delete('host');

    const proxyRequest = new Request(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
      redirect: 'follow',
    });

    return fetch(proxyRequest);
  },
};
```

`wrangler.toml`：
```toml
name = "split-admin-proxy"
main = "src/index.js"
compatibility_date = "2026-04-01"
account_id = "ef603862133476dbd88473e0be7ccb5c"

routes = [
  { pattern = "split-admin.redarch.dev", custom_domain = true }
]
```

### 2.6 Cloudflare Access policy

用 Cloudflare API/CLI 設定：

- **Application name**: `split-senpai-admin`
- **Domain**: `split-admin.redarch.dev`
- **Session duration**: 24 hours
- **Identity providers**: One-time PIN
- **Policies**:
  - Name: `Allow owner`
  - Action: Allow
  - Include: `Email == cwchen2000@gmail.com`

### 2.7 JWT 驗證實作（Lambda 端）

在 `handler.py` 加：

```python
def _verify_access_jwt(token: str) -> dict | None:
    """Verify Cloudflare Access JWT and return claims, or None if invalid."""
    # 1. Fetch Cloudflare Access JWKS (cached)
    # 2. Verify JWT signature
    # 3. Check 'aud' (Application Audience tag from CF Access)
    # 4. Check 'iss' (https://<team>.cloudflareaccess.com)
    # 5. Check 'exp' not expired
    # 6. Return claims dict
```

需要的環境變數：
- `CF_ACCESS_TEAM_DOMAIN`: e.g. `cwchen2000.cloudflareaccess.com`
- `CF_ACCESS_AUD`: Application Audience tag (從 CF Access 拿到)
- `CF_ALLOWED_EMAIL`: `cwchen2000@gmail.com`

不引入 `pyjwt` 套件（避免 Lambda 增加 dependency），自己用 `urllib + hashlib + base64` 實作 JWT verification（RS256 簽名驗證）。

實際上 RS256 驗證需要 `cryptography` 或 `pycryptodome`，後者已經在 Lambda 裡了。可以用 pycryptodome 的 `Crypto.PublicKey.RSA` + `Crypto.Signature.pkcs1_15`。

### 2.8 Cloudflare API Token

要拉 Cloudflare Analytics 需要 API Token：

1. 在 Cloudflare dashboard 建立 API Token，權限：
   - Zone → Analytics → Read
   - Zone → Zone → Read
   - Account → Account Analytics → Read
2. 把 token 存到 AWS Secrets Manager: `split-settle/cloudflare-api-token`
3. Lambda 環境變數加 `CF_API_TOKEN_ARN`
4. Handler 用 boto3 讀取 secret，呼叫 Cloudflare GraphQL Analytics API

---

## Part 3: 部署順序

### Phase 1: Cloudflare 安全強化（立即，無 backend 改動）
1. 建立 Rate Limiting rules
2. 啟用 WAF Managed Rules
3. 確認 Always Use HTTPS

### Phase 2: 後端 admin endpoints
1. 在 `handler.py` 加 admin 路由
2. 加 JWT 驗證 helper
3. 加 stats / shares CRUD endpoints
4. 加 Cloudflare Analytics fetcher
5. SAM template 加 admin routes、env vars、secrets manager 權限
6. SAM deploy

### Phase 3: Cloudflare Access + Worker
1. 建立 Cloudflare Access application
2. 建立 split-admin-proxy Worker
3. 部署 worker 並綁定 split-admin.redarch.dev

### Phase 4: Admin SPA
1. 在 handler.py 加 inline admin HTML
2. preact 元件：StatsCard, LineChart, PieChart, ShareList, CloudflareAnalytics
3. 部署 + 端到端測試

---

## TODO（後續）

- [ ] IP-based abuse detection inside Lambda（目前依賴 Cloudflare）
- [ ] Pro plan 升級評估（Super Bot Fight Mode）
- [ ] Email alerting（使用量異常時通知）

---

## 不在範圍內

- 多使用者帳號管理（單一 admin email）
- 圖表互動性（hover、zoom）
- 實時更新（每次 reload）
- 編輯 share 內容（只支援查看 + 刪除）
