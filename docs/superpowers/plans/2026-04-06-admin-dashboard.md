# Admin Dashboard + Cloudflare Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build private admin dashboard at `split-admin.redarch.dev` with stats/share management/CF analytics, plus harden public `split.redarch.dev` against abuse.

**Architecture:** Phase 1 = Cloudflare rules (no backend). Phase 2 = `/admin/*` routes in existing Lambda with JWT verification. Phase 3 = Cloudflare Worker proxy + Access policy. Phase 4 = inline preact SPA. Same Lambda, same DynamoDB, same patterns.

**Tech Stack:** AWS Lambda (Python 3.13), DynamoDB, AWS SAM, Cloudflare API/Workers/Access, pycryptodome (already in deps), inline preact + htm + SVG.

**Working dir:** `/Users/chiwenchen/Documents/repos/agent-splitter` for backend tasks; `~/Documents/repos/split-admin-proxy` for Worker.

---

## Prerequisites

Before starting, the user must create a Cloudflare API Token:

1. https://dash.cloudflare.com/profile/api-tokens → Create Token → Custom token
2. Permissions:
   - Zone → Zone WAF → Edit
   - Zone → Zone Settings → Edit
   - Zone → Zone → Read
   - Account → Cloudflare Access → Edit
   - Zone → Analytics → Read
3. Zone Resources: Include → Specific zone → redarch.dev
4. Save token, then: `export CF_API_TOKEN=<token>`

Also get the zone ID once at the start:

```bash
ZONE_ID=$(curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=redarch.dev" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['id'])")
echo "ZONE_ID=$ZONE_ID"  # save this — used in many tasks
```

---

# Phase 1: Cloudflare Hardening

### Task 1: Create Rate Limiting rules

**Files:** None (Cloudflare API only)

- [ ] **Step 1: Create the rate limit ruleset**

```bash
curl -s -X PUT -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/rulesets/phases/http_ratelimit/entrypoint" \
  -d '{
    "rules": [
      {
        "description": "Throttle /v1/share POST to 5/min/IP",
        "expression": "(http.request.method eq \"POST\" and http.request.uri.path eq \"/v1/share\" and http.host eq \"split.redarch.dev\")",
        "action": "block",
        "ratelimit": {
          "characteristics": ["ip.src", "cf.colo.id"],
          "period": 60,
          "requests_per_period": 5,
          "mitigation_timeout": 600
        }
      },
      {
        "description": "Throttle /s/* GET to 60/min/IP",
        "expression": "(http.request.method eq \"GET\" and starts_with(http.request.uri.path, \"/s/\") and http.host eq \"split.redarch.dev\")",
        "action": "block",
        "ratelimit": {
          "characteristics": ["ip.src", "cf.colo.id"],
          "period": 60,
          "requests_per_period": 60,
          "mitigation_timeout": 600
        }
      }
    ]
  }' | python3 -m json.tool | tail -20
```

Expected: `"success": true`. If 403 → API token missing WAF permission.

- [ ] **Step 2: Verify by hitting endpoint 7 times**

```bash
for i in 1 2 3 4 5 6 7; do
  curl -s -o /dev/null -w "Attempt $i: %{http_code}\n" -X POST \
    https://split.redarch.dev/v1/share \
    -H "Content-Type: application/json" \
    -d '{"currency":"TWD","participants":["A","B"],"expenses":[{"paid_by":"A","amount":1,"split_among":["A","B"]}]}'
done
```

Expected: First 5 = 200, attempts 6+ = 429. Wait 60s between test runs to avoid persistent ban.

### Task 2: Enable WAF Managed Rules

- [ ] **Step 1: Deploy Cloudflare Managed Ruleset**

```bash
curl -s -X PUT -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/rulesets/phases/http_request_firewall_managed/entrypoint" \
  -d '{
    "rules": [
      {
        "action": "execute",
        "action_parameters": { "id": "efb7b8c949ac4650a09736fc376e9aee" },
        "expression": "true",
        "description": "Cloudflare Managed Ruleset",
        "enabled": true
      }
    ]
  }' | python3 -m json.tool | tail -10
```

`efb7b8c949ac4650a09736fc376e9aee` is the public ID of Cloudflare Managed Ruleset (constant). Expected: `"success": true`.

### Task 3: Enable Always Use HTTPS

- [ ] **Step 1: Set Always Use HTTPS**

```bash
curl -s -X PATCH -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/always_use_https" \
  -d '{"value": "on"}'
```

- [ ] **Step 2: Verify HTTP redirects to HTTPS**

```bash
curl -sI http://split.redarch.dev/health | head -3
```

Expected: `301 Moved Permanently` with `Location: https://split.redarch.dev/health`.

---

# Phase 2: Backend Admin Endpoints

### Task 4: Update template.yaml for admin

**Files:**
- Modify: `template.yaml`

- [ ] **Step 1: Add CF env vars to Environment.Variables**

In `template.yaml`, the `Environment.Variables` block under `SplitSettleFunction.Properties` becomes:

```yaml
      Environment:
        Variables:
          SECRET_ARN: !Ref ApiKeySecret
          ALCHEMY_SECRET_ARN: !Ref AlchemySecret
          PAYMENTS_TABLE: !Ref UsedPaymentsTable
          GROUPS_TABLE: !Ref GroupsTable
          CF_ACCESS_TEAM_DOMAIN: ""
          CF_ACCESS_AUD: ""
          CF_ALLOWED_EMAIL: "cwchen2000@gmail.com"
          CF_API_TOKEN_ARN: ""
          CF_ZONE_ID: ""
```

Empty values are placeholders — set via AWS console after Phase 3 creates the Access app.

- [ ] **Step 2: Add IAM permission for future Cloudflare API token secret**

In the `Policies` block, add this Statement after the AlchemySecret block:

```yaml
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Sub "arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:split-settle/cloudflare-api-token-*"
```

- [ ] **Step 3: Add admin events**

In the `Events` block, after `SharePage`:

```yaml
        AdminRoot:
          Type: HttpApi
          Properties:
            Path: /admin
            Method: get
            ApiId: !Ref SplitSettleApi
        AdminApi:
          Type: HttpApi
          Properties:
            Path: /admin/{proxy+}
            Method: any
            ApiId: !Ref SplitSettleApi
```

- [ ] **Step 4: Validate and commit**

```bash
PATH="/opt/homebrew/bin:$PATH" sam validate
```

Expected: `template.yaml is a valid SAM Template`

```bash
git add template.yaml
git commit -m "feat(admin): add admin routes and CF Access env vars to template"
```

### Task 5: Add JWT verification helper (TDD)

**Files:**
- Modify: `src/split_settle/handler.py`
- Create: `tests/test_admin.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_admin.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler


def test_verify_jwt_returns_none_when_team_domain_unset(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("a.b.c") is None


def test_verify_jwt_returns_none_when_aud_unset(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "")
    assert handler._verify_access_jwt("a.b.c") is None


def test_verify_jwt_returns_none_for_malformed_token(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("not-a-jwt") is None


def test_verify_jwt_returns_none_for_two_part_token(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("only.two") is None
```

- [ ] **Step 2: Run tests, expect failure**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: `AttributeError: module 'handler' has no attribute '_verify_access_jwt'`

- [ ] **Step 3: Add `import base64` to handler.py top imports (after `import urllib.request`)**

Edit lines 1-7 of `handler.py` to add base64:

```python
import base64
import json
import logging
import os
import re
import secrets
import time
import urllib.request
```

- [ ] **Step 4: Add JWT helper functions before `def _esc(s: str)`**

Find the line `def _esc(s: str) -> str:` in handler.py (search for it). Insert ABOVE it:

```python
# ---------- Cloudflare Access JWT verification ----------

_jwks_cache: dict = {}

def _b64url_decode(data: str) -> bytes:
    """Base64-url decode with padding."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def _fetch_jwks(team_domain: str) -> dict:
    """Fetch and cache JWKS from Cloudflare Access. 1h cache."""
    global _jwks_cache
    now = time.time()
    if team_domain in _jwks_cache and _jwks_cache[team_domain]["expires"] > now:
        return _jwks_cache[team_domain]["jwks"]
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            jwks = json.loads(resp.read())
        _jwks_cache[team_domain] = {"jwks": jwks, "expires": now + 3600}
        return jwks
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        return {"keys": []}


def _verify_access_jwt(token: str):
    """Verify CF Access JWT (RS256). Returns claims dict or None."""
    team_domain = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
    expected_aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    if not team_domain or not expected_aud:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except Exception:
        return None
    kid = header.get("kid")
    if not kid:
        return None
    jwks = _fetch_jwks(team_domain)
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key_data:
        return None
    try:
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15
        from Crypto.Hash import SHA256
        n = int.from_bytes(_b64url_decode(key_data["n"]), "big")
        e = int.from_bytes(_b64url_decode(key_data["e"]), "big")
        rsa_key = RSA.construct((n, e))
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        h = SHA256.new(signing_input)
        pkcs1_15.new(rsa_key).verify(h, signature)
    except Exception as e:
        logger.error(f"JWT verify failed: {e}")
        return None
    expected_iss = f"https://{team_domain}"
    if payload.get("iss") != expected_iss:
        return None
    aud = payload.get("aud")
    if isinstance(aud, list):
        if expected_aud not in aud:
            return None
    elif aud != expected_aud:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


```

- [ ] **Step 5: Run tests, expect pass**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Run all existing tests to verify no regression**

```bash
python3 -m pytest tests/ -v
```

Expected: all pass (existing tests from test_handler.py + new test_admin.py).

- [ ] **Step 7: Commit**

```bash
git add src/split_settle/handler.py tests/test_admin.py
git commit -m "feat(admin): add Cloudflare Access JWT verification helper"
```

### Task 6: Add admin auth wrapper + 401/503 logic (TDD)

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Add tests for the auth wrapper**

Append to `tests/test_admin.py`:

```python
def test_admin_auth_returns_503_when_unconfigured(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    event = {"rawPath": "/admin", "headers": {}, "requestContext": {"http": {"method": "GET", "path": "/admin"}}}
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 503


def test_admin_auth_returns_401_without_jwt_header(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    event = {"rawPath": "/admin", "headers": {}, "requestContext": {"http": {"method": "GET", "path": "/admin"}}}
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 401


def test_admin_auth_returns_401_with_invalid_jwt(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    event = {
        "rawPath": "/admin",
        "headers": {"cf-access-jwt-assertion": "invalid.jwt.token"},
        "requestContext": {"http": {"method": "GET", "path": "/admin"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 401


def test_admin_auth_returns_403_when_wrong_email(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    monkeypatch.setenv("CF_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setattr(handler, "_verify_access_jwt", lambda t: {"email": "stranger@example.com"})
    event = {
        "rawPath": "/admin",
        "headers": {"cf-access-jwt-assertion": "valid.jwt"},
        "requestContext": {"http": {"method": "GET", "path": "/admin"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 403
```

- [ ] **Step 2: Run tests, expect failure**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 4 new tests fail (lambda_handler doesn't know about /admin).

- [ ] **Step 3: Add admin auth wrapper helper**

In `handler.py`, after the JWT helper functions added in Task 5, add:

```python
def _admin_unauthorized(status: int, reason: str):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": reason}),
    }


def _check_admin_auth(event: dict):
    """
    Verify admin authentication. Returns (claims_dict, None) on success
    or (None, error_response) on failure.
    """
    if not os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip():
        return None, _admin_unauthorized(503, "admin not configured")
    headers = event.get("headers") or {}
    # Headers may be lowercase or camelcase depending on API Gateway
    jwt = (
        headers.get("cf-access-jwt-assertion")
        or headers.get("Cf-Access-Jwt-Assertion")
        or headers.get("CF-Access-Jwt-Assertion")
    )
    if not jwt:
        return None, _admin_unauthorized(401, "missing access token")
    claims = _verify_access_jwt(jwt)
    if not claims:
        return None, _admin_unauthorized(401, "invalid access token")
    allowed_email = os.environ.get("CF_ALLOWED_EMAIL", "").strip().lower()
    user_email = (claims.get("email") or "").strip().lower()
    if allowed_email and user_email != allowed_email:
        logger.warning(f"Admin access denied for email: {user_email}")
        return None, _admin_unauthorized(403, "forbidden")
    return claims, None
```

- [ ] **Step 4: Add /admin route to lambda_handler**

Find `def lambda_handler(event, context):` (around line 1189) and find the routing block. After the existing route handlers (after `if path == "/v1/groups":`), add:

```python
    if path == "/admin" or path.startswith("/admin/"):
        claims, err = _check_admin_auth(event)
        if err:
            return err
        return _handle_admin(event, claims)
```

Then add a stub `_handle_admin` function after `_handle_share_page`:

```python
def _handle_admin(event: dict, claims: dict) -> dict:
    """Route /admin/* requests."""
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if path == "/admin" or path == "/admin/":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": "<html><body>Admin (placeholder — Phase 4 will replace this)</body></html>",
        }

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 8 passed (4 from Task 5 + 4 from this task).

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/split_settle/handler.py tests/test_admin.py
git commit -m "feat(admin): /admin auth wrapper + placeholder route"
```

### Task 7: Add /admin/api/stats endpoint (TDD)

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Add test**

Append to `tests/test_admin.py`:

```python
def test_admin_stats_aggregates_shares(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    monkeypatch.setenv("CF_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setattr(handler, "_verify_access_jwt", lambda t: {"email": "owner@example.com"})

    fake_items = [
        {
            "PK": {"S": "SHARE#1"},
            "request_body": {"S": '{"currency":"TWD"}'},
            "result": {"S": '{"currency":"TWD","total_expenses":1500}'},
            "created_at": {"S": "2026-04-06T10:00:00Z"},
        },
        {
            "PK": {"S": "SHARE#2"},
            "request_body": {"S": '{"currency":"TWD"}'},
            "result": {"S": '{"currency":"TWD","total_expenses":500}'},
            "created_at": {"S": "2026-04-06T11:00:00Z"},
        },
        {
            "PK": {"S": "SHARE#3"},
            "request_body": {"S": '{"currency":"USD"}'},
            "result": {"S": '{"currency":"USD","total_expenses":50}'},
            "created_at": {"S": "2026-04-05T10:00:00Z"},
        },
    ]

    def fake_scan_shares():
        return fake_items

    monkeypatch.setattr(handler, "_scan_all_shares", fake_scan_shares)

    event = {
        "rawPath": "/admin/api/stats",
        "headers": {"cf-access-jwt-assertion": "valid"},
        "requestContext": {"http": {"method": "GET", "path": "/admin/api/stats"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["total_shares"] == 3
    assert body["currency_breakdown"]["TWD"] == 2
    assert body["currency_breakdown"]["USD"] == 1
    assert body["avg_amount_by_currency"]["TWD"] == 1000.0
    assert body["avg_amount_by_currency"]["USD"] == 50.0
    # shares_by_day should have 2 entries
    days = {d["date"]: d["count"] for d in body["shares_by_day"]}
    assert days["2026-04-06"] == 2
    assert days["2026-04-05"] == 1
```

Add `import json` at the top of the test file if not already there.

- [ ] **Step 2: Run test, expect failure**

```bash
python3 -m pytest tests/test_admin.py::test_admin_stats_aggregates_shares -v
```

Expected: 404 response (route doesn't exist yet).

- [ ] **Step 3: Add `_scan_all_shares` and stats handler**

In `handler.py`, after the existing `_get_share` function (around line 245), add:

```python
def _scan_all_shares() -> list:
    """Scan GroupsTable for all SHARE# items. Returns list of raw DynamoDB items."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return []
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    items = []
    last_key = None
    while True:
        kwargs = {
            "TableName": table,
            "FilterExpression": "begins_with(PK, :prefix)",
            "ExpressionAttributeValues": {":prefix": {"S": "SHARE#"}},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return items
```

In `_handle_admin`, add the stats route:

```python
def _handle_admin(event: dict, claims: dict) -> dict:
    """Route /admin/* requests."""
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if path == "/admin" or path == "/admin/":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": "<html><body>Admin (placeholder — Phase 4 will replace this)</body></html>",
        }

    if path == "/admin/api/stats" and method == "GET":
        return _admin_stats()

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }


def _admin_stats() -> dict:
    """Aggregate share stats from DynamoDB."""
    items = _scan_all_shares()
    total = len(items)
    currency_count = {}
    currency_total = {}
    day_count = {}
    for item in items:
        try:
            result = json.loads(item.get("result", {}).get("S", "{}"))
            currency = result.get("currency", "?")
            amount = float(result.get("total_expenses", 0))
            currency_count[currency] = currency_count.get(currency, 0) + 1
            currency_total[currency] = currency_total.get(currency, 0) + amount
            created = item.get("created_at", {}).get("S", "")
            day = created[:10] if created else "?"
            day_count[day] = day_count.get(day, 0) + 1
        except Exception:
            continue
    avg_by_currency = {
        c: round(currency_total[c] / currency_count[c], 2)
        for c in currency_count
    }
    shares_by_day = sorted(
        [{"date": d, "count": c} for d, c in day_count.items()],
        key=lambda x: x["date"],
    )
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "total_shares": total,
            "currency_breakdown": currency_count,
            "avg_amount_by_currency": avg_by_currency,
            "shares_by_day": shares_by_day,
        }),
    }
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/split_settle/handler.py tests/test_admin.py
git commit -m "feat(admin): GET /admin/api/stats with currency/day aggregation"
```

### Task 8: Add /admin/api/shares CRUD endpoints (TDD)

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Add tests for list, get, delete**

Append to `tests/test_admin.py`:

```python
def _admin_event(path, method="GET"):
    return {
        "rawPath": path,
        "headers": {"cf-access-jwt-assertion": "valid"},
        "requestContext": {"http": {"method": method, "path": path}},
    }


def _setup_admin_auth(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    monkeypatch.setenv("CF_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setattr(handler, "_verify_access_jwt", lambda t: {"email": "owner@example.com"})


def test_admin_list_shares_returns_items(monkeypatch):
    _setup_admin_auth(monkeypatch)
    fake_items = [
        {
            "PK": {"S": "SHARE#abc"},
            "request_body": {"S": '{"currency":"TWD","participants":["Alice","Bob","Carol"]}'},
            "result": {"S": '{"currency":"TWD","total_expenses":1500}'},
            "created_at": {"S": "2026-04-06T10:00:00Z"},
        },
    ]
    monkeypatch.setattr(handler, "_scan_all_shares", lambda: fake_items)

    response = handler.lambda_handler(_admin_event("/admin/api/shares"), {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["items"]) == 1
    assert body["items"][0]["share_id"] == "abc"
    assert body["items"][0]["currency"] == "TWD"
    assert body["items"][0]["total"] == 1500
    assert body["items"][0]["participants_count"] == 3
    assert "Alice" in body["items"][0]["participants_preview"]


def test_admin_get_share_returns_full_data(monkeypatch):
    _setup_admin_auth(monkeypatch)
    fake_data = {"request_body": {"currency": "TWD"}, "result": {"total_expenses": 100}, "created_at": "2026-04-06T10:00:00Z"}
    monkeypatch.setattr(handler, "_get_share", lambda sid: fake_data if sid == "abc" else None)

    response = handler.lambda_handler(_admin_event("/admin/api/shares/abc"), {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["request_body"]["currency"] == "TWD"


def test_admin_get_share_returns_404_for_missing(monkeypatch):
    _setup_admin_auth(monkeypatch)
    monkeypatch.setattr(handler, "_get_share", lambda sid: None)
    response = handler.lambda_handler(_admin_event("/admin/api/shares/missing"), {})
    assert response["statusCode"] == 404


def test_admin_delete_share_calls_dynamodb(monkeypatch):
    _setup_admin_auth(monkeypatch)
    deleted = []
    monkeypatch.setattr(handler, "_delete_share", lambda sid: deleted.append(sid))
    response = handler.lambda_handler(_admin_event("/admin/api/shares/abc", "DELETE"), {})
    assert response["statusCode"] == 200
    assert deleted == ["abc"]
```

- [ ] **Step 2: Run tests, expect failures**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: new tests fail.

- [ ] **Step 3: Add `_delete_share` helper after `_get_share` in handler.py**

```python
def _delete_share(share_id: str) -> None:
    """Delete a share from DynamoDB by id."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.delete_item(
        TableName=table,
        Key={"PK": {"S": f"SHARE#{share_id}"}, "SK": {"S": "RESULT"}},
    )
    logger.info(f"Admin deleted share: {share_id}")
```

- [ ] **Step 4: Add list/get/delete routes to `_handle_admin`**

Replace the `_handle_admin` body with:

```python
def _handle_admin(event: dict, claims: dict) -> dict:
    """Route /admin/* requests."""
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if path == "/admin" or path == "/admin/":
        return _admin_render_dashboard()

    if path == "/admin/api/stats" and method == "GET":
        return _admin_stats()

    if path == "/admin/api/shares" and method == "GET":
        return _admin_list_shares()

    if path.startswith("/admin/api/shares/"):
        share_id = path.split("/admin/api/shares/", 1)[1]
        if not share_id or "/" in share_id:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "invalid share id"}),
            }
        if method == "GET":
            return _admin_get_share(share_id)
        if method == "DELETE":
            return _admin_delete_share(share_id)

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }


def _admin_render_dashboard() -> dict:
    """Stub — Phase 4 replaces with the SPA HTML."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": "<html><body>Admin dashboard placeholder</body></html>",
    }


def _admin_list_shares() -> dict:
    items = _scan_all_shares()
    out = []
    for item in items:
        try:
            pk = item.get("PK", {}).get("S", "")
            share_id = pk.replace("SHARE#", "")
            request_body = json.loads(item.get("request_body", {}).get("S", "{}"))
            result = json.loads(item.get("result", {}).get("S", "{}"))
            participants = request_body.get("participants", [])
            preview = ", ".join(participants[:3])
            if len(participants) > 3:
                preview += f" +{len(participants) - 3}"
            out.append({
                "share_id": share_id,
                "created_at": item.get("created_at", {}).get("S", ""),
                "currency": result.get("currency", "?"),
                "total": result.get("total_expenses", 0),
                "participants_count": len(participants),
                "participants_preview": preview,
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"items": out}),
    }


def _admin_get_share(share_id: str) -> dict:
    data = _get_share(share_id)
    if not data:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "not found"}),
        }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(data),
    }


def _admin_delete_share(share_id: str) -> dict:
    try:
        _delete_share(share_id)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"deleted": True}),
        }
    except Exception as e:
        logger.error(f"Failed to delete share {share_id}: {e}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "delete failed"}),
        }
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
git add src/split_settle/handler.py tests/test_admin.py
git commit -m "feat(admin): list/get/delete share endpoints"
```

### Task 9: Add /admin/api/cloudflare/analytics endpoint

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Add test**

Append to `tests/test_admin.py`:

```python
def test_admin_cf_analytics_returns_503_when_unconfigured(monkeypatch):
    _setup_admin_auth(monkeypatch)
    monkeypatch.setenv("CF_API_TOKEN_ARN", "")
    response = handler.lambda_handler(_admin_event("/admin/api/cloudflare/analytics"), {})
    assert response["statusCode"] == 503


def test_admin_cf_analytics_calls_graphql(monkeypatch):
    _setup_admin_auth(monkeypatch)
    monkeypatch.setenv("CF_API_TOKEN_ARN", "arn:fake")
    monkeypatch.setenv("CF_ZONE_ID", "fake-zone")
    monkeypatch.setattr(handler, "_get_cf_api_token", lambda: "fake-token")

    fake_response = {
        "data": {
            "viewer": {
                "zones": [{
                    "httpRequests1dGroups": [{
                        "sum": {"requests": 1234, "threats": 56},
                        "dimensions": {"date": "2026-04-06"},
                    }],
                }]
            }
        }
    }
    captured = {}
    def fake_post(url, token, query):
        captured["url"] = url
        captured["query"] = query
        return fake_response
    monkeypatch.setattr(handler, "_cf_graphql_query", fake_post)

    response = handler.lambda_handler(_admin_event("/admin/api/cloudflare/analytics"), {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["requests_24h"] == 1234
    assert body["blocked_24h"] == 56
```

- [ ] **Step 2: Run tests, expect failure**

- [ ] **Step 3: Add Cloudflare analytics helpers and route**

In `handler.py`, after `_admin_delete_share`, add:

```python
def _get_cf_api_token() -> str:
    """Read Cloudflare API token from Secrets Manager."""
    arn = os.environ.get("CF_API_TOKEN_ARN", "").strip()
    if not arn:
        return ""
    if arn in _secret_cache:
        return _secret_cache[arn]
    import boto3
    client = boto3.client("secretsmanager", region_name="ap-northeast-1")
    try:
        resp = client.get_secret_value(SecretId=arn)
        token = resp["SecretString"].strip()
        _secret_cache[arn] = token
        return token
    except Exception as e:
        logger.error(f"Failed to fetch CF token: {e}")
        return ""


def _cf_graphql_query(url: str, token: str, query: str) -> dict:
    """Execute a GraphQL query against the Cloudflare Analytics API."""
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _admin_cf_analytics() -> dict:
    arn = os.environ.get("CF_API_TOKEN_ARN", "").strip()
    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    if not arn or not zone_id:
        return {
            "statusCode": 503,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "cloudflare analytics not configured"}),
        }
    token = _get_cf_api_token()
    if not token:
        return {
            "statusCode": 503,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "cloudflare token unavailable"}),
        }
    # Use httpRequests1dGroups for past 24h aggregate
    today = time.strftime("%Y-%m-%d", time.gmtime())
    query = """
    query {
      viewer {
        zones(filter: { zoneTag: "%s" }) {
          httpRequests1dGroups(
            limit: 1,
            filter: { date: "%s" },
            orderBy: [date_DESC]
          ) {
            sum { requests threats }
            dimensions { date }
          }
        }
      }
    }
    """ % (zone_id, today)
    try:
        resp = _cf_graphql_query("https://api.cloudflare.com/client/v4/graphql", token, query)
        zones = resp.get("data", {}).get("viewer", {}).get("zones", [])
        if not zones or not zones[0].get("httpRequests1dGroups"):
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"requests_24h": 0, "blocked_24h": 0}),
            }
        group = zones[0]["httpRequests1dGroups"][0]
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "requests_24h": group["sum"]["requests"],
                "blocked_24h": group["sum"]["threats"],
            }),
        }
    except Exception as e:
        logger.error(f"CF analytics query failed: {e}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "analytics fetch failed"}),
        }
```

In `_handle_admin`, add the route after the stats handler:

```python
    if path == "/admin/api/cloudflare/analytics" and method == "GET":
        return _admin_cf_analytics()
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_admin.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/split_settle/handler.py tests/test_admin.py
git commit -m "feat(admin): GET /admin/api/cloudflare/analytics"
```

### Task 10: Deploy backend admin endpoints

**Files:** None (deployment)

- [ ] **Step 1: SAM build and deploy**

```bash
cd /Users/chiwenchen/Documents/repos/agent-splitter
PATH="/opt/homebrew/bin:$PATH" sam build && PATH="/opt/homebrew/bin:$PATH" sam deploy --no-confirm-changeset 2>&1 | tail -10
```

Expected: `Successfully created/updated stack`.

- [ ] **Step 2: Verify /admin returns 503 (not configured yet)**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://split.redarch.dev/admin
```

Expected: `503` (because CF_ACCESS_TEAM_DOMAIN is empty).

- [ ] **Step 3: Verify other endpoints still work**

```bash
curl -s https://split.redarch.dev/health
```

Expected: `{"status": "ok"}`.

---

# Phase 3: Cloudflare Worker Proxy + Access

### Task 11: Create split-admin-proxy Worker

**Files:**
- Create: `~/Documents/repos/split-admin-proxy/wrangler.toml`
- Create: `~/Documents/repos/split-admin-proxy/src/index.js`

- [ ] **Step 1: Create the proxy directory**

```bash
mkdir -p ~/Documents/repos/split-admin-proxy/src
```

- [ ] **Step 2: Create wrangler.toml**

```toml
name = "split-admin-proxy"
main = "src/index.js"
compatibility_date = "2026-04-01"
account_id = "ef603862133476dbd88473e0be7ccb5c"

routes = [
  { pattern = "split-admin.redarch.dev", custom_domain = true }
]
```

- [ ] **Step 3: Create the Worker source**

```javascript
/**
 * Split Senpai Admin Proxy
 *
 * Proxies split-admin.redarch.dev/* → split.redarch.dev/admin/*
 * Cloudflare Access protects this hostname.
 */

const ORIGIN = 'https://split.redarch.dev';

export default {
  async fetch(request) {
    const url = new URL(request.url);
    // Strip trailing slash from path for /admin
    let pathname = url.pathname;
    if (pathname === '/' || pathname === '') {
      pathname = '';
    }
    const targetPath = '/admin' + pathname;
    const target = new URL(targetPath + url.search, ORIGIN);

    const headers = new Headers(request.headers);
    headers.delete('host');

    const proxyRequest = new Request(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
      redirect: 'follow',
    });

    const response = await fetch(proxyRequest);
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  },
};
```

- [ ] **Step 4: Deploy the Worker**

```bash
cd ~/Documents/repos/split-admin-proxy
wrangler deploy
```

Expected: `Deployed split-admin-proxy triggers` with `split-admin.redarch.dev (custom domain)`.

- [ ] **Step 5: Verify proxy works (should return 503 because admin not yet configured)**

```bash
sleep 30  # wait for SSL cert
curl -s -o /dev/null -w "%{http_code}\n" https://split-admin.redarch.dev/
```

Expected: `503` (Lambda returns 503 because CF_ACCESS_TEAM_DOMAIN is empty).

### Task 12: Create Cloudflare Access Application

**Files:** None (Cloudflare API)

- [ ] **Step 1: Get the account team name**

```bash
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/ef603862133476dbd88473e0be7ccb5c/access/organizations" \
  | python3 -m json.tool | grep -E "auth_domain|name"
```

Expected: returns an `auth_domain` like `cwchen2000.cloudflareaccess.com`. If no organization exists, ask the user to:
1. Go to https://one.dash.cloudflare.com/
2. Set up Zero Trust (free, requires team name)
3. Choose a team name (becomes `<name>.cloudflareaccess.com`)
4. Re-run this step

Save the team domain (e.g. `cwchen2000.cloudflareaccess.com`).

- [ ] **Step 2: Create the Access Application**

```bash
TEAM_DOMAIN="cwchen2000.cloudflareaccess.com"  # replace if different

curl -s -X POST -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/accounts/ef603862133476dbd88473e0be7ccb5c/access/apps" \
  -d '{
    "name": "split-senpai-admin",
    "domain": "split-admin.redarch.dev",
    "type": "self_hosted",
    "session_duration": "24h",
    "allowed_idps": [],
    "auto_redirect_to_identity": false
  }' | python3 -m json.tool | tail -20
```

Expected: response includes `"id"` and `"aud"`. **Save both** — the `aud` is the Application Audience tag we need.

```bash
APP_ID="<id from response>"
APP_AUD="<aud from response>"
```

- [ ] **Step 3: Add a policy that allows only cwchen2000@gmail.com**

```bash
curl -s -X POST -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/accounts/ef603862133476dbd88473e0be7ccb5c/access/apps/$APP_ID/policies" \
  -d '{
    "name": "Allow owner",
    "decision": "allow",
    "include": [
      { "email": { "email": "cwchen2000@gmail.com" } }
    ]
  }' | python3 -m json.tool | tail -10
```

Expected: `"success": true`.

- [ ] **Step 4: Verify One-time PIN is enabled as identity provider**

By default, Zero Trust enables One-time PIN. If not, ask the user to enable it at:
https://one.dash.cloudflare.com/ → Settings → Authentication → Login methods → One-time PIN

### Task 13: Update Lambda env vars with CF Access details

**Files:** None (AWS console / CLI)

- [ ] **Step 1: Update Lambda environment variables**

```bash
TEAM_DOMAIN="cwchen2000.cloudflareaccess.com"  # from Task 12
APP_AUD="<from Task 12>"
ZONE_ID="$ZONE_ID"  # from prerequisites

aws lambda update-function-configuration \
  --function-name agent-splitter-SplitSettleFunction-c7OAq7UMUV56 \
  --region ap-northeast-1 \
  --environment "Variables={SECRET_ARN=arn:aws:secretsmanager:ap-northeast-1:274571492950:secret:split-settle/api-key-eYEczV,ALCHEMY_SECRET_ARN=arn:aws:secretsmanager:ap-northeast-1:274571492950:secret:split-settle/alchemy-api-key-pCSAJC,PAYMENTS_TABLE=agent-splitter-used-payments,GROUPS_TABLE=agent-splitter-groups,CF_ACCESS_TEAM_DOMAIN=$TEAM_DOMAIN,CF_ACCESS_AUD=$APP_AUD,CF_ALLOWED_EMAIL=cwchen2000@gmail.com,CF_ZONE_ID=$ZONE_ID}" \
  2>&1 | tail -10
```

⚠️ This must include ALL existing env vars or they get cleared. The above includes all vars from Task 4 except `CF_API_TOKEN_ARN` (set in Task 14).

Expected: returns updated config.

- [ ] **Step 2: Verify the env vars**

```bash
aws lambda get-function-configuration \
  --function-name agent-splitter-SplitSettleFunction-c7OAq7UMUV56 \
  --region ap-northeast-1 \
  --query 'Environment.Variables'
```

Expected: shows all 8 env vars with correct values.

- [ ] **Step 3: Test admin endpoint via browser**

Ask the user to open https://split-admin.redarch.dev/ in a browser. Expected:
1. Cloudflare Access page appears
2. Enter `cwchen2000@gmail.com`
3. Click "Send me a code"
4. Check email for code
5. Enter code
6. Redirected to admin placeholder page

If 401/403: check Lambda env vars match Access app.

### Task 14: Set up Cloudflare API token secret for analytics

**Files:** None (AWS Secrets Manager)

- [ ] **Step 1: Store the Cloudflare API token in Secrets Manager**

```bash
aws secretsmanager create-secret \
  --name split-settle/cloudflare-api-token \
  --description "Cloudflare API token for analytics queries" \
  --secret-string "$CF_API_TOKEN" \
  --region ap-northeast-1
```

Expected: returns ARN.

- [ ] **Step 2: Update Lambda to point at it**

Get the ARN:
```bash
SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id split-settle/cloudflare-api-token \
  --region ap-northeast-1 \
  --query 'ARN' --output text)
echo $SECRET_ARN
```

Add `CF_API_TOKEN_ARN=$SECRET_ARN` to the Lambda env vars (re-run Task 13 step 1 with `CF_API_TOKEN_ARN=$SECRET_ARN` appended).

- [ ] **Step 3: Verify analytics endpoint**

After Phase 3 is complete, test from a logged-in browser session:
```
https://split-admin.redarch.dev/api/cloudflare/analytics
```

Expected: JSON with `requests_24h` and `blocked_24h`.

---

# Phase 4: Admin SPA

### Task 15: Replace placeholder with full SPA

**Files:**
- Modify: `src/split_settle/handler.py` (the `_admin_render_dashboard` function)

- [ ] **Step 1: Replace `_admin_render_dashboard` with the full SPA**

In `handler.py`, find `def _admin_render_dashboard()` and replace it with:

```python
def _admin_render_dashboard() -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": _ADMIN_SPA_HTML,
    }
```

Then add the SPA HTML constant before this function (or near the existing `SHARE_PAGE_TEMPLATE`):

```python
_ADMIN_SPA_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>Split Senpai Admin</title>
<style>
  :root {
    --bg: #2d4a4a;
    --layer1: #1e3636;
    --layer2: #162a2a;
    --accent: #e8a84c;
    --text: #e0d5c4;
    --muted: #a0c4b8;
    --border: #3a5e5e;
    --error: #e06050;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
  h1 { color: var(--accent); font-size: 24px; margin: 0 0 24px; }
  h2 { color: var(--accent); font-size: 16px; margin: 24px 0 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .card {
    background: var(--layer1);
    border-radius: 12px;
    padding: 16px;
    border: 1px solid var(--border);
  }
  .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { color: var(--accent); font-size: 24px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  table { width: 100%; border-collapse: collapse; background: var(--layer1); border-radius: 12px; overflow: hidden; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }
  th { background: var(--layer2); color: var(--muted); font-size: 11px; text-transform: uppercase; }
  td { font-size: 13px; }
  td.amount { color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
  button { background: var(--accent); color: var(--layer1); border: none; padding: 6px 12px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 12px; }
  button:hover { opacity: 0.85; }
  button.danger { background: var(--error); color: white; }
  button.outline { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
  .chart-container { background: var(--layer1); border-radius: 12px; padding: 16px; }
  .empty { color: var(--muted); text-align: center; padding: 32px; font-style: italic; }
  .loading { color: var(--muted); padding: 16px; text-align: center; }
  .error { color: var(--error); padding: 16px; }
</style>
</head>
<body>
<div id="app" class="container">
  <h1>分帳仙貝 Admin</h1>
  <div id="content" class="loading">Loading...</div>
</div>
<script type="module">
import { h, render } from 'https://esm.sh/preact@10.19.0';
import { useState, useEffect } from 'https://esm.sh/preact@10.19.0/hooks';
import htm from 'https://esm.sh/htm@3.1.1';
const html = htm.bind(h);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

function StatCard({ label, value }) {
  return html`<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

function LineChart({ data }) {
  if (!data || data.length === 0) return html`<div class="empty">No data</div>`;
  const w = 600, hgt = 180, pad = 30;
  const max = Math.max(...data.map(d => d.count), 1);
  const points = data.map((d, i) => {
    const x = pad + (i / Math.max(data.length - 1, 1)) * (w - 2 * pad);
    const y = hgt - pad - (d.count / max) * (hgt - 2 * pad);
    return `${x},${y}`;
  }).join(' ');
  return html`
    <svg viewBox="0 0 ${w} ${hgt}" style="width:100%;height:auto">
      <polyline fill="none" stroke="#e8a84c" stroke-width="2" points=${points} />
      ${data.map((d, i) => {
        const x = pad + (i / Math.max(data.length - 1, 1)) * (w - 2 * pad);
        const y = hgt - pad - (d.count / max) * (hgt - 2 * pad);
        return html`<circle cx=${x} cy=${y} r="3" fill="#e8a84c" />`;
      })}
      <text x=${pad} y=${hgt - 8} fill="#a0c4b8" font-size="10">${data[0].date}</text>
      <text x=${w - pad} y=${hgt - 8} fill="#a0c4b8" font-size="10" text-anchor="end">${data[data.length - 1].date}</text>
      <text x="8" y="20" fill="#a0c4b8" font-size="10">${max}</text>
    </svg>
  `;
}

function PieChart({ data }) {
  const entries = Object.entries(data || {});
  if (entries.length === 0) return html`<div class="empty">No data</div>`;
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const colors = ['#e8a84c', '#7aa0d0', '#3a5a9a', '#b0c8e8', '#e06050'];
  let angle = -Math.PI / 2;
  const cx = 100, cy = 100, r = 80;
  const slices = entries.map(([key, val], i) => {
    const sliceAngle = (val / total) * 2 * Math.PI;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    angle += sliceAngle;
    const x2 = cx + r * Math.cos(angle);
    const y2 = cy + r * Math.sin(angle);
    const large = sliceAngle > Math.PI ? 1 : 0;
    const path = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`;
    return { path, color: colors[i % colors.length], key, val };
  });
  return html`
    <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
      <svg viewBox="0 0 200 200" style="width:200px;height:200px">
        ${slices.map(s => html`<path d=${s.path} fill=${s.color} />`)}
      </svg>
      <div>
        ${slices.map(s => html`
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <div style="width:12px;height:12px;background:${s.color};border-radius:2px"></div>
            <span style="color:#e0d5c4">${s.key}: ${s.val}</span>
          </div>
        `)}
      </div>
    </div>
  `;
}

function ShareList({ items, onDelete }) {
  if (!items || items.length === 0) return html`<div class="empty">No shares</div>`;
  return html`
    <table>
      <thead>
        <tr><th>ID</th><th>Date</th><th>Currency</th><th>Total</th><th>People</th><th></th></tr>
      </thead>
      <tbody>
        ${items.map(item => html`
          <tr>
            <td><code>${item.share_id}</code></td>
            <td>${(item.created_at || '').slice(0, 16).replace('T', ' ')}</td>
            <td>${item.currency}</td>
            <td class="amount">${item.total.toLocaleString()}</td>
            <td>${item.participants_preview}</td>
            <td>
              <button class="outline" onClick=${() => window.open('/s/' + item.share_id, '_blank')}>View</button>
              ${' '}
              <button class="danger" onClick=${() => {
                if (confirm('Delete share ' + item.share_id + '?')) onDelete(item.share_id);
              }}>Delete</button>
            </td>
          </tr>
        `)}
      </tbody>
    </table>
  `;
}

function App() {
  const [stats, setStats] = useState(null);
  const [shares, setShares] = useState(null);
  const [cf, setCf] = useState(null);
  const [error, setError] = useState(null);

  async function loadAll() {
    try {
      const [s, sh, c] = await Promise.all([
        api('/api/stats'),
        api('/api/shares'),
        api('/api/cloudflare/analytics').catch(() => ({ requests_24h: 0, blocked_24h: 0 })),
      ]);
      setStats(s);
      setShares(sh.items);
      setCf(c);
    } catch (e) {
      setError(e.message);
    }
  }

  async function deleteShare(id) {
    try {
      await fetch('/api/shares/' + id, { method: 'DELETE' });
      loadAll();
    } catch (e) {
      alert('Delete failed: ' + e.message);
    }
  }

  useEffect(() => { loadAll(); }, []);

  if (error) return html`<div class="error">Error: ${error}</div>`;
  if (!stats || !shares) return html`<div class="loading">Loading...</div>`;

  return html`
    <div>
      <h2>📊 Stats</h2>
      <div class="grid">
        <${StatCard} label="Total Shares" value=${stats.total_shares} />
        <${StatCard} label="CF Requests 24h" value=${cf?.requests_24h ?? '-'} />
        <${StatCard} label="CF Blocked 24h" value=${cf?.blocked_24h ?? '-'} />
        <${StatCard} label="Currencies" value=${Object.keys(stats.currency_breakdown).length} />
      </div>

      <h2>📈 Shares per Day</h2>
      <div class="chart-container">
        <${LineChart} data=${stats.shares_by_day} />
      </div>

      <h2>🥧 Currency Breakdown</h2>
      <div class="chart-container">
        <${PieChart} data=${stats.currency_breakdown} />
      </div>

      <h2>📋 Shares</h2>
      <${ShareList} items=${shares} onDelete=${deleteShare} />
    </div>
  `;
}

render(h(App), document.getElementById('content'));
</script>
</body>
</html>
"""
```

- [ ] **Step 2: Run all tests to confirm no regression**

```bash
python3 -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 3: Deploy**

```bash
PATH="/opt/homebrew/bin:$PATH" sam build && PATH="/opt/homebrew/bin:$PATH" sam deploy --no-confirm-changeset 2>&1 | tail -5
```

Expected: `Successfully created/updated stack`.

- [ ] **Step 4: Test in browser**

Open https://split-admin.redarch.dev/ — should show the dashboard with stats, charts, and shares list. The CF Access PIN flow runs first time.

- [ ] **Step 5: Test delete from the dashboard**

Click delete on a test share, confirm it disappears from the list and is gone from DynamoDB:

```bash
aws dynamodb get-item \
  --table-name agent-splitter-groups \
  --key '{"PK":{"S":"SHARE#<test_id>"},"SK":{"S":"RESULT"}}' \
  --region ap-northeast-1
```

Expected: empty response (item deleted).

- [ ] **Step 6: Commit**

```bash
git add src/split_settle/handler.py
git commit -m "feat(admin): inline preact SPA dashboard with stats and CRUD"
```

---

## Phase Summary

| Phase | Tasks | What it delivers |
|-------|-------|------------------|
| 1 | 1-3 | Cloudflare rate limiting + WAF + HTTPS forced |
| 2 | 4-10 | `/admin/*` Lambda routes with JWT auth, stats/shares/analytics APIs, all unit tested |
| 3 | 11-14 | `split-admin.redarch.dev` Worker proxy + CF Access app + secrets configured |
| 4 | 15 | Full preact SPA dashboard live |

After all phases: visit https://split-admin.redarch.dev/, enter email PIN, see stats and manage shares.
