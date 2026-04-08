# Share Bank Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-participant free-text "how to pay me" accounts to existing `/s/{id}` share links, with per-device identity selection so payers see only the accounts of people they owe money to.

**Architecture:** Three new public REST endpoints (`GET/PUT/DELETE /v1/share/{id}/accounts[/{participant}]`) backed by new `ACCOUNT#` rows on the existing `agent-splitter` DynamoDB table (same PK as the parent `SHARE#` row, shared TTL). Frontend changes are inline edits to the Preact SPA rendered by `_render_share_page` in `src/split_settle/handler.py`: bootstrap a `split_device_id` UUID in localStorage, prompt for identity on first visit per share, fetch accounts, render copy-buttons under each transfer the current user owes.

**Tech Stack:** Python 3, AWS Lambda, DynamoDB (boto3 low-level client), Preact via inline `<script type="module">`, pytest with `moto` mocking (already used by repo).

---

## Spec

See `docs/superpowers/specs/2026-04-08-share-bank-accounts-design.md`.

## File Structure

- **Modify** `src/split_settle/handler.py`
  - Add helpers near `_save_share` / `_get_share` (around line 215):
    `_save_account`, `_get_accounts`, `_delete_account`, `_handle_share_accounts`.
  - Add a new router branch in the dispatch block (around line 1337) — must
    come **before** the existing `if path.startswith("/v1/share/"):` line so
    the accounts paths win the prefix match.
  - Extend the inline JS inside `_render_share_page` (currently around lines
    700–1100) with: device_id bootstrap, identity modal, accounts fetch, save
    handler, per-transfer render hook.
- **Create** `tests/test_share_accounts.py` — pytest tests for the three new
  endpoints, mirroring the style of existing share tests.

No new Python modules, no new files outside the test file. The handler is
already a single-file Lambda by convention; keep it that way.

---

## Task 1: Test scaffolding for accounts API

**Files:**
- Create: `tests/test_share_accounts.py`

- [ ] **Step 1: Look at existing share test for fixtures and style**

Run: `grep -n "def test_\|fixture\|moto\|GROUPS_TABLE" tests/test_*.py | head -40`
Goal: identify the moto fixture name and the helper that seeds a SHARE row.
You will reuse it. If no helper exists, the next step creates one inline.

- [ ] **Step 2: Write the first failing test (GET empty)**

```python
# tests/test_share_accounts.py
import json
import os
import time
import pytest
import boto3
from moto import mock_aws

from src.split_settle import handler

TABLE = "agent-splitter-test"


@pytest.fixture
def ddb():
    with mock_aws():
        os.environ["GROUPS_TABLE"] = TABLE
        os.environ["API_KEY"] = "test-key"
        client = boto3.client("dynamodb", region_name="ap-northeast-1")
        client.create_table(
            TableName=TABLE,
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def _seed_share(share_id="abc12345", participants=("Alice", "Bob")):
    handler._save_share(
        share_id,
        {"participants": list(participants), "expenses": []},
        {"transfers": []},
    )


def _invoke(method, path, body=None, headers=None):
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers or {},
        "body": json.dumps(body) if body is not None else None,
    }
    return handler.lambda_handler(event, None)


def test_get_accounts_empty(ddb):
    _seed_share()
    resp = _invoke("GET", "/v1/share/abc12345/accounts",
                   headers={"host": "split.redarch.dev"})
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {}
```

- [ ] **Step 3: Run the test, expect failure**

Run: `python3 -m pytest tests/test_share_accounts.py::test_get_accounts_empty -v`
Expected: FAIL — either 404 (path falls through to `_handle_share_json`) or
KeyError. Either way, the new route does not exist yet.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/test_share_accounts.py
git commit -m "test: failing test for GET share accounts"
```

---

## Task 2: `_get_accounts` helper + GET endpoint

**Files:**
- Modify: `src/split_settle/handler.py`

- [ ] **Step 1: Add `_get_accounts` helper**

Insert after `_get_share` (after line ~255). Uses a `Query` on the share PK
filtering by SK prefix `ACCOUNT#`.

```python
def _get_accounts(share_id: str) -> dict:
    """Return {participant_name: account_text} for a share. Empty dict if none."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return {}
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    response = client.query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"SHARE#{share_id}"},
            ":sk": {"S": "ACCOUNT#"},
        },
    )
    out = {}
    for item in response.get("Items", []):
        name = item["SK"]["S"].replace("ACCOUNT#", "", 1)
        out[name] = item.get("account_text", {}).get("S", "")
    return out
```

- [ ] **Step 2: Add `_handle_share_accounts` dispatcher (GET only for now)**

Insert near the other `_handle_share_*` functions (around line 1396). It
needs to parse `/v1/share/{id}/accounts` and `/v1/share/{id}/accounts/{name}`.

```python
def _handle_share_accounts(event):
    """Handle /v1/share/{id}/accounts[/{participant}] — public, no API key."""
    path = event.get("rawPath", "")
    method = (event.get("requestContext", {})
              .get("http", {}).get("method", "GET")).upper()

    # Parse: /v1/share/{id}/accounts or /v1/share/{id}/accounts/{participant}
    rest = path.split("/v1/share/", 1)[-1]  # "{id}/accounts[/{p}]"
    parts = rest.split("/")
    if len(parts) < 2 or parts[1] != "accounts":
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "not found"}),
        }
    share_id = parts[0]
    participant = parts[2] if len(parts) >= 3 and parts[2] else None

    share = _get_share(share_id)
    if not share or share["ttl_expiry"] < time.time():
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "share not found"}),
        }

    if method == "GET" and participant is None:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(_get_accounts(share_id)),
        }

    return {
        "statusCode": 405,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "method not allowed"}),
    }
```

- [ ] **Step 3: Wire into the router**

In the dispatch block around line 1337, add a branch **before** the existing
`if path.startswith("/v1/share/"):` line:

```python
    if path.startswith("/v1/share/") and "/accounts" in path:
        return _handle_share_accounts(event)

    if path.startswith("/v1/share/"):
        return _handle_share_json(event)
```

- [ ] **Step 4: Run the test, expect pass**

Run: `python3 -m pytest tests/test_share_accounts.py::test_get_accounts_empty -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/split_settle/handler.py tests/test_share_accounts.py
git commit -m "feat: GET /v1/share/{id}/accounts returns empty dict"
```

---

## Task 3: `_save_account` helper + PUT endpoint (happy path)

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_share_accounts.py`

- [ ] **Step 1: Write failing PUT-then-GET test**

Append to `tests/test_share_accounts.py`:

```python
def test_put_then_get_account(ddb):
    _seed_share()
    put = _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
                  body={"account_text": "國泰 700-12345678"},
                  headers={"x-device-id": "dev-1"})
    assert put["statusCode"] == 200
    assert json.loads(put["body"]) == {"ok": True}

    get = _invoke("GET", "/v1/share/abc12345/accounts")
    assert get["statusCode"] == 200
    assert json.loads(get["body"]) == {"Alice": "國泰 700-12345678"}
```

- [ ] **Step 2: Run, expect failure (405)**

Run: `python3 -m pytest tests/test_share_accounts.py::test_put_then_get_account -v`
Expected: FAIL with status 405.

- [ ] **Step 3: Add `_save_account` helper**

Insert after `_get_accounts`:

```python
ACCOUNT_TEXT_MAX = 500


def _save_account(share_id: str, participant: str, account_text: str,
                  device_id: str, ttl_expiry: int) -> None:
    """Upsert an ACCOUNT# row. Caller must have validated inputs."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.put_item(
        TableName=table,
        Item={
            "PK": {"S": f"SHARE#{share_id}"},
            "SK": {"S": f"ACCOUNT#{participant}"},
            "account_text": {"S": account_text},
            "updated_at": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            "updated_by": {"S": device_id or ""},
            "ttl_expiry": {"N": str(ttl_expiry)},
        },
    )
```

- [ ] **Step 4: Extend `_handle_share_accounts` with PUT branch**

Replace the trailing `405` block with:

```python
    if method == "PUT" and participant is not None:
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _bad_request("invalid json")
        account_text = body.get("account_text", "")
        if not isinstance(account_text, str):
            return _bad_request("account_text must be a string")
        if len(account_text) > ACCOUNT_TEXT_MAX:
            return _bad_request(f"account_text exceeds {ACCOUNT_TEXT_MAX} chars")
        participants = share["request_body"].get("participants", [])
        if participant not in participants:
            return _bad_request("participant not in this share")
        device_id = (event.get("headers") or {}).get("x-device-id", "")
        _save_account(share_id, participant, account_text, device_id,
                      share["ttl_expiry"])
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }

    return {
        "statusCode": 405,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "method not allowed"}),
    }
```

And add a small helper near the top of the file (next to other response
helpers, or just above `_handle_share_accounts`):

```python
def _bad_request(msg: str) -> dict:
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }
```

- [ ] **Step 5: Run, expect pass**

Run: `python3 -m pytest tests/test_share_accounts.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/split_settle/handler.py tests/test_share_accounts.py
git commit -m "feat: PUT /v1/share/{id}/accounts/{participant}"
```

---

## Task 4: PUT validation tests

**Files:**
- Modify: `tests/test_share_accounts.py`

- [ ] **Step 1: Write all four validation tests**

Append:

```python
def test_put_unknown_participant(ddb):
    _seed_share()
    resp = _invoke("PUT", "/v1/share/abc12345/accounts/Charlie",
                   body={"account_text": "x"},
                   headers={"x-device-id": "d"})
    assert resp["statusCode"] == 400


def test_put_too_long(ddb):
    _seed_share()
    resp = _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
                   body={"account_text": "x" * 501},
                   headers={"x-device-id": "d"})
    assert resp["statusCode"] == 400


def test_put_missing_share(ddb):
    resp = _invoke("PUT", "/v1/share/nope0000/accounts/Alice",
                   body={"account_text": "x"},
                   headers={"x-device-id": "d"})
    assert resp["statusCode"] == 404


def test_put_expired_share(ddb, monkeypatch):
    _seed_share()
    # Force TTL into the past by patching time.time used inside handler
    real_time = time.time
    monkeypatch.setattr(handler.time, "time",
                        lambda: real_time() + 86400 * 31)
    resp = _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
                   body={"account_text": "x"},
                   headers={"x-device-id": "d"})
    assert resp["statusCode"] == 404
```

- [ ] **Step 2: Run all account tests**

Run: `python3 -m pytest tests/test_share_accounts.py -v`
Expected: all PASS (validation paths are already implemented in Task 3).

- [ ] **Step 3: Commit**

```bash
git add tests/test_share_accounts.py
git commit -m "test: validation cases for PUT share accounts"
```

---

## Task 5: DELETE endpoint

**Files:**
- Modify: `src/split_settle/handler.py`
- Modify: `tests/test_share_accounts.py`

- [ ] **Step 1: Write failing DELETE test**

Append:

```python
def test_delete_account(ddb):
    _seed_share()
    _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
            body={"account_text": "x"}, headers={"x-device-id": "d"})
    resp = _invoke("DELETE", "/v1/share/abc12345/accounts/Alice",
                   headers={"x-device-id": "d"})
    assert resp["statusCode"] == 200
    get = _invoke("GET", "/v1/share/abc12345/accounts")
    assert json.loads(get["body"]) == {}
```

- [ ] **Step 2: Run, expect failure (405)**

Run: `python3 -m pytest tests/test_share_accounts.py::test_delete_account -v`

- [ ] **Step 3: Add `_delete_account` helper**

Insert after `_save_account`:

```python
def _delete_account(share_id: str, participant: str) -> None:
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.delete_item(
        TableName=table,
        Key={
            "PK": {"S": f"SHARE#{share_id}"},
            "SK": {"S": f"ACCOUNT#{participant}"},
        },
    )
```

- [ ] **Step 4: Add DELETE branch in `_handle_share_accounts`**

Insert just before the trailing 405 return:

```python
    if method == "DELETE" and participant is not None:
        participants = share["request_body"].get("participants", [])
        if participant not in participants:
            return _bad_request("participant not in this share")
        _delete_account(share_id, participant)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }
```

- [ ] **Step 5: Run, expect pass**

Run: `python3 -m pytest tests/test_share_accounts.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/split_settle/handler.py tests/test_share_accounts.py
git commit -m "feat: DELETE /v1/share/{id}/accounts/{participant}"
```

---

## Task 6: Last-write-wins audit test

**Files:**
- Modify: `tests/test_share_accounts.py`

- [ ] **Step 1: Write the test**

```python
def test_last_write_wins(ddb):
    _seed_share()
    _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
            body={"account_text": "first"}, headers={"x-device-id": "dev-1"})
    _invoke("PUT", "/v1/share/abc12345/accounts/Alice",
            body={"account_text": "second"}, headers={"x-device-id": "dev-2"})

    # Confirm via GET
    get = _invoke("GET", "/v1/share/abc12345/accounts")
    assert json.loads(get["body"]) == {"Alice": "second"}

    # Confirm updated_by audit field
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    item = client.get_item(
        TableName=TABLE,
        Key={"PK": {"S": "SHARE#abc12345"},
             "SK": {"S": "ACCOUNT#Alice"}},
    )["Item"]
    assert item["updated_by"]["S"] == "dev-2"
```

- [ ] **Step 2: Run, expect pass**

Run: `python3 -m pytest tests/test_share_accounts.py::test_last_write_wins -v`
Expected: PASS (Task 3 already implemented this; this test just locks the
contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_share_accounts.py
git commit -m "test: last-write-wins for shared account edits"
```

---

## Task 7: Frontend — fetch and expose accounts data

**Files:**
- Modify: `src/split_settle/handler.py` (the inline JS inside `_render_share_page`)

The share page is a single inline Preact app. This task only wires the data
flow; UI rendering comes in Task 8.

- [ ] **Step 1: Read the existing share-page component to find the right hook**

Run: `grep -n "useState\|useEffect\|fetch.*share\|participants" src/split_settle/handler.py | head -30`
Identify the existing component that calls `GET /v1/share/{id}` and stores
the response in state. You will mirror that pattern.

- [ ] **Step 2: Add device_id bootstrap**

Inside the existing share-page `<script type="module">` block, near the top
of the component (before any other state), add:

```javascript
function getDeviceId() {
  let id = localStorage.getItem('split_device_id');
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID()) ||
         (Date.now().toString(36) + Math.random().toString(36).slice(2));
    localStorage.setItem('split_device_id', id);
  }
  return id;
}
const DEVICE_ID = getDeviceId();
```

- [ ] **Step 3: Add accounts state + fetch**

Inside the same component that already loads `/v1/share/{id}`, add:

```javascript
const [accounts, setAccounts] = useState({});

async function loadAccounts() {
  try {
    const res = await fetch(`/v1/share/${shareId}/accounts`);
    if (res.ok) setAccounts(await res.json());
  } catch (e) { /* non-fatal */ }
}

useEffect(() => { loadAccounts(); }, [shareId]);
```

(Use whatever variable name the existing code uses for the share id — grep
first.)

- [ ] **Step 4: Smoke-render the data so we can verify by hand**

Temporarily add `<pre>{JSON.stringify(accounts)}</pre>` somewhere visible
inside the component's return.

- [ ] **Step 5: Build & run tests to make sure handler still imports**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/split_settle/handler.py
git commit -m "feat(ui): fetch accounts and bootstrap device_id on share page"
```

---

## Task 8: Frontend — identity modal

**Files:**
- Modify: `src/split_settle/handler.py` (inline JS)

- [ ] **Step 1: Add identity state hooked to localStorage**

Inside the same component, near the other state hooks:

```javascript
const IDENTITY_KEY = `split_identity:${shareId}`;
const [identity, setIdentity] = useState(() => localStorage.getItem(IDENTITY_KEY));
const [showIdentityModal, setShowIdentityModal] = useState(false);

useEffect(() => {
  if (!identity && participants && participants.length) {
    setShowIdentityModal(true);
  }
}, [identity, participants]);

function chooseIdentity(name) {
  localStorage.setItem(IDENTITY_KEY, name);
  setIdentity(name);
  setShowIdentityModal(false);
}
```

(Replace `participants` with the actual variable name used by the existing
component for the participants list.)

- [ ] **Step 2: Render the modal**

Add to the component's return, conditional on `showIdentityModal`:

```javascript
${showIdentityModal ? html`
  <div class="modal-backdrop">
    <div class="modal">
      <h3>你是哪一位？</h3>
      <p>選擇你的身分後，需要付錢給你的人才會看到你的帳號。</p>
      ${participants.map(p => html`
        <button class="btn btn-outline" onClick=${() => chooseIdentity(p)}>${p}</button>
      `)}
      <button class="btn btn-link" onClick=${() => chooseIdentity('__guest__')}>我只是路人</button>
    </div>
  </div>
` : ''}
```

Add minimal CSS in the existing `<style>` block:

```css
.modal-backdrop { position:fixed;inset:0;background:rgba(0,0,0,.5);
  display:flex;align-items:center;justify-content:center;z-index:1000; }
.modal { background:var(--layer-1);padding:24px;border-radius:12px;
  max-width:320px;display:flex;flex-direction:column;gap:8px; }
```

- [ ] **Step 3: Manual verify (optional)**

Run: `sam build` then `sam local start-api` and load `/s/<existing-share>` —
or just visually inspect the inlined HTML by deploying. Skip if lazy; the
real check comes when Task 9 reads the identity.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`

- [ ] **Step 5: Commit**

```bash
git add src/split_settle/handler.py
git commit -m "feat(ui): identity modal on first share visit"
```

---

## Task 9: Frontend — "my account" panel

**Files:**
- Modify: `src/split_settle/handler.py` (inline JS)

- [ ] **Step 1: Add panel state and save handler**

```javascript
const [myAccountDraft, setMyAccountDraft] = useState('');
const [savingAccount, setSavingAccount] = useState(false);

useEffect(() => {
  if (identity && identity !== '__guest__') {
    setMyAccountDraft(accounts[identity] || '');
  }
}, [identity, accounts]);

async function saveMyAccount() {
  if (!identity || identity === '__guest__') return;
  setSavingAccount(true);
  try {
    const res = await fetch(`/v1/share/${shareId}/accounts/${encodeURIComponent(identity)}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json', 'x-device-id': DEVICE_ID},
      body: JSON.stringify({account_text: myAccountDraft}),
    });
    if (res.ok) {
      setAccounts({...accounts, [identity]: myAccountDraft});
    }
  } finally {
    setSavingAccount(false);
  }
}
```

- [ ] **Step 2: Render the panel**

Place above the existing transfers list, conditional on
`identity && identity !== '__guest__'`:

```javascript
${identity && identity !== '__guest__' ? html`
  <div class="my-account-panel">
    <h3>我的收款帳號（${identity}）</h3>
    <p class="hint">貼上你的銀行帳號 / Line Pay / 任何收款方式，需要付錢給你的人會看到。</p>
    <textarea
      maxlength="500"
      rows="3"
      value=${myAccountDraft}
      onInput=${e => setMyAccountDraft(e.target.value)}
    ></textarea>
    <button class="btn btn-primary" onClick=${saveMyAccount} disabled=${savingAccount}>
      ${savingAccount ? '儲存中…' : '儲存'}
    </button>
  </div>
` : ''}
```

Add CSS:

```css
.my-account-panel { background:var(--layer-2);padding:16px;border-radius:8px;
  margin-bottom:16px;display:flex;flex-direction:column;gap:8px; }
.my-account-panel textarea { width:100%;padding:8px;border-radius:6px;
  border:1px solid var(--border);font-family:inherit; }
.my-account-panel .hint { font-size:.85em;color:var(--muted);margin:0; }
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/ -v`

- [ ] **Step 4: Commit**

```bash
git add src/split_settle/handler.py
git commit -m "feat(ui): my-account edit panel on share page"
```

---

## Task 10: Frontend — show payee account under each owed transfer

**Files:**
- Modify: `src/split_settle/handler.py` (inline JS)

- [ ] **Step 1: Find the transfers render block**

Run: `grep -n "transfers\.\|→\|transfer" src/split_settle/handler.py | head -20`
Locate the `.map(...)` over transfers in the share-page component. Each
entry has `from`, `to`, `amount` (or whatever the existing field names are —
confirm by reading 10 lines around the match).

- [ ] **Step 2: Inject account display**

Replace the existing transfer row template with one that, when
`identity === t.from` (i.e. I am the payer), appends the payee's account:

```javascript
${transfers.map(t => html`
  <div class="transfer-row">
    <div>${t.from} → ${t.to} <strong>${formatAmount(t.amount)}</strong></div>
    ${identity === t.from ? html`
      <div class="payee-account">
        ${accounts[t.to] ? html`
          <code>${accounts[t.to]}</code>
          <button class="btn btn-xs" onClick=${() => navigator.clipboard.writeText(accounts[t.to])}>複製</button>
        ` : html`<span class="muted">${t.to} 還沒提供帳號</span>`}
      </div>
    ` : ''}
  </div>
`)}
```

(Field names — `t.from`, `t.to`, `t.amount`, `formatAmount` — must match the
existing code. Use whatever the current map already references.)

Add CSS:

```css
.payee-account { margin-top:4px;font-size:.9em;display:flex;gap:8px;align-items:center; }
.payee-account code { background:var(--layer-1);padding:2px 6px;border-radius:4px;
  word-break:break-all; }
.btn-xs { padding:2px 8px;font-size:.8em; }
.muted { color:var(--muted); }
```

- [ ] **Step 3: Remove the temporary `<pre>{JSON.stringify(accounts)}</pre>` from Task 7**

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`

- [ ] **Step 5: Commit**

```bash
git add src/split_settle/handler.py
git commit -m "feat(ui): show payee account under owed transfers"
```

---

## Task 11: Deploy & smoke test

**Files:** none (deploy + manual verification)

- [ ] **Step 1: SAM build & deploy**

Run: `PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy`
Expected: stack updates cleanly. If `UPDATE_ROLLBACK_FAILED` on
`GroupsTable`, follow the recovery procedure in CLAUDE.md.

- [ ] **Step 2: Manual smoke test**

1. POST a new split via the existing UI on `https://split.redarch.dev`
2. Open the resulting share link in an incognito window → identity modal
3. Pick a participant who owes money → fill in account → save
4. Open the same link in another incognito window → pick the payer →
   verify the payee's account appears with copy button
5. Open in a third window → pick "我只是路人" → verify no account fields

- [ ] **Step 3: Commit nothing — record results**

If anything fails, file a follow-up task. Otherwise, push and open a PR per
the repo's standard `Development Workflow` section in CLAUDE.md.

```bash
git push -u origin HEAD
gh pr create --title "feat: shared bank accounts in share links" \
  --body "Implements docs/superpowers/specs/2026-04-08-share-bank-accounts-design.md"
```

- [ ] **Step 4: Monitor CI**

Run: `gh run list --branch $(git branch --show-current)`
Wait for green; fix any failures per the workflow in CLAUDE.md.

---

## Self-Review Notes

- **Spec coverage:** data model (Task 2/3/5), GET (T2), PUT + validation
  (T3/T4), DELETE (T5), last-write-wins audit (T6), device_id bootstrap
  (T7), identity modal (T8), my-account panel (T9), per-transfer reveal
  (T10), guest mode (handled by `identity === '__guest__'` checks in
  T8/T9/T10), deploy & smoke (T11). All eight test cases listed in the
  spec are present in T2/T3/T4/T5/T6.
- **Field names in JS tasks (T7–T10) are placeholders** — the engineer
  must grep the existing inline component to confirm `shareId`,
  `participants`, `transfers`, `t.from`, `t.to`, `t.amount`,
  `formatAmount` actually match. T7 Step 1 and T10 Step 1 explicitly
  call this out.
- **No new files outside `tests/test_share_accounts.py`** — keeps the
  Lambda single-file convention intact.
- **No `iam/claudecli-policy.json` change needed** — DynamoDB
  PutItem/GetItem/Query/DeleteItem on the existing `agent-splitter-*`
  table is already covered.
