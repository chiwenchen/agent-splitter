# Share Bank Accounts in Split Senpai Share Links

**Date:** 2026-04-08
**Status:** Design approved, ready for implementation plan

## Problem

After Split Senpai computes a settlement (`Bob → Alice $300`), users still
have to leave the app and use a chat tool to send each other bank account
numbers. We want the share link itself to carry each participant's preferred
collection method, so the receiver of a transfer can copy-paste the account
without any out-of-band messaging.

## Goals

- Let any participant attach a free-text "how to pay me" string to their name
  inside an existing `/s/{id}` share.
- Let participants who owe money to someone see that person's account directly
  on the share page, with a copy button.
- Remember "I am Alice" per device per share so the page personalises itself
  on subsequent visits.

## Non-Goals

- No rate limiting on edits. Anyone with the share link can edit any account.
  30-day TTL + 500-char cap are the only abuse defences. Add later if needed.
- No history / undo. Overwrites are destructive.
- No server-side identity enforcement. Identity is a client-side localStorage
  self-declaration; the backend trusts "holds share id = may edit".
- No encryption at rest. The share link is already a bearer token; encrypting
  the payload would be security theatre.
- No "guest mode" enforcement on the server. Devtools users can still call
  `/v1/share/{id}/accounts` and see everything. Acceptable: link holders are
  already inside the trust boundary.
- No owner-fills-on-behalf flow. If a participant never opens the link, payers
  just see "Alice hasn't provided an account yet".
- No identity-switch UI. Identity is set once per device per share via the
  initial modal; changing it requires clearing localStorage manually.

## Data Model

New row class on the existing `agent-splitter` DynamoDB table, sharing the
same partition key as the parent share so a single `Query` can fetch
everything:

```
PK = SHARE#<share_id>
SK = ACCOUNT#<participant_name>
attrs:
  account_text : str   # free-form, ≤ 500 chars
  updated_at   : str   # ISO-8601 UTC
  updated_by   : str   # x-device-id header value, audit only
  ttl_expiry   : int   # epoch seconds, matches parent SHARE row (30 days)
```

`device_id` is recorded for debugging only — it never participates in
authorisation.

## Identity & Device ID (client-side only)

- **`split_device_id`** (global): `crypto.randomUUID()` minted on first visit
  to any share page, stored in `localStorage`. Sent on every accounts API
  call as header `x-device-id`.
- **`split_identity:<share_id>`** (per share): the participant name the user
  selected in the identity modal, or the sentinel `__guest__`.

On first visit to `/s/{id}`:
1. `GET /v1/share/{id}` (existing) → participants + transfers
2. `GET /v1/share/{id}/accounts` (new) → `{ name: account_text }`
3. If no `split_identity:<share_id>`, show modal: radio list of
   participants + "I'm just visiting". Selection writes localStorage.
4. Render.

## API

All endpoints are public (same trust level as `/v1/share` / `/s/{id}`).
No `x-api-key` required.

### `GET /v1/share/{id}/accounts`

Returns every account row for the share. Backend does not filter — the
client masks based on its declared identity and the settlement transfers.

```json
200 { "Alice": "國泰 700-12345678", "Bob": "Line Pay 0912345678" }
404 { "error": "share not found" }   // share missing or expired
```

### `PUT /v1/share/{id}/accounts/{participant}`

Headers: `x-device-id: <uuid>`
Body: `{ "account_text": "..." }`

Validation:
- Share exists and not expired → else 404
- `participant` ∈ original `request_body.participants` → else 400
- `account_text` is a string, length ≤ 500 → else 400

Writes the `ACCOUNT#<participant>` row, sets `updated_by = x-device-id`,
`updated_at = now`, `ttl_expiry` copied from parent share.

```json
200 { "ok": true }
```

### `DELETE /v1/share/{id}/accounts/{participant}`

Headers: `x-device-id: <uuid>`

Removes the row. Same validation as PUT (share exists, participant valid).

```json
200 { "ok": true }
```

## Frontend UX (inline Preact SPA in `_render_share_page`)

**Identity modal** — first visit only, blocks the page until answered.

**"My collection account" panel** — visible only when identity ≠ guest.
Textarea pre-filled with current value, "Save" button → `PUT`. Helper text:
"貼上你的銀行帳號 / Line Pay / 任何收款方式，需要付錢給你的人會看到".

**Per-transfer account display** — under each `Bob → Alice $300` row in the
existing transfers list:
- If my identity is Bob (the payer): show Alice's `account_text` + copy
  button. If empty → grey "Alice 還沒提供帳號".
- Otherwise: render nothing for that row.

**Guest mode** — settlement numbers visible, no account fields, no "my
account" panel.

## Backend Changes (`src/split_settle/handler.py`)

- New helpers:
  - `_save_account(share_id, participant, account_text, device_id)`
  - `_get_accounts(share_id) -> dict[str, str]`
  - `_delete_account(share_id, participant)`
- New router branches in the existing dispatch (next to `/v1/share/...`):
  - `GET /v1/share/{id}/accounts`
  - `PUT /v1/share/{id}/accounts/{participant}`
  - `DELETE /v1/share/{id}/accounts/{participant}`
- Inline JS in `_render_share_page` extended with: device_id bootstrap,
  identity modal, accounts fetch, save/delete handlers, per-transfer render
  hook.

Estimated diff: handler.py +~150 lines, new test file +~120 lines.

## Tests (`tests/test_share_accounts.py`, pytest)

- `PUT` then `GET` round-trip
- `PUT` with participant not in `request_body.participants` → 400
- `PUT` with body > 500 chars → 400
- `PUT` against missing share → 404
- `PUT` against expired share → 404
- `DELETE` removes the row
- Two `PUT`s from different `x-device-id`s → last write wins, `updated_by`
  reflects the most recent caller
- `GET` against a share with no accounts → `{}`

## Open Questions

None — design fully resolved through brainstorming session 2026-04-08.
