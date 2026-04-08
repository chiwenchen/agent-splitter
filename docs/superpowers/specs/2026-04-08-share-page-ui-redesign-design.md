# Share Page UI Redesign

**Date:** 2026-04-08
**Status:** Draft

## Goal

Clean up the share page (`/s/{id}`) so that users land on a focused, one-glance view of what they personally need to do, and collapse the bank-account sharing flow into a non-intrusive affordance.

## Problems with current UI

1. Too many stacked sections (title → "I am..." → me-picker → my-account panel → settlements) → user must scroll and mentally parse layers.
2. me-picker filter buttons and "my account" textarea sit next to each other with unclear separation — users can't tell which is "who am I" vs "fill my account".
3. My-account panel is always visible and takes vertical space even after the user has already filled it in.
4. Payee bank-account info inlined into each settlement row makes rows uneven and visually noisy.
5. No filter applied by default — user sees every transfer and has to tap themselves.
6. My-account panel is offered even when no one owes the user money (pointless — nobody will read that account).

## Non-Goals

- No change to backend API contract (`GET/PUT/DELETE /v1/share/{id}/accounts[/{participant}]` stay identical).
- No change to identity-claim modal behavior (first visit still prompts).
- No change to the create / home page.
- No new settings, no user accounts, no encryption.

## Design: single-card header + filtered list

### Layout (top → bottom)

```
┌─ phone card ────────────────────────────┐
│ NT 4,500 split                          │
│ 2026-04-08                              │
│ Alice, Bob, Charlie · Total: NT 4,500   │
│                                         │
│ ┌─ Identity Card ─────────────────────┐ │
│ │ 嗨，Alice        [分享轉帳帳號 ✏️] │ │
│ │ 別人欠你 NT 1,800                   │ │  ← green
│ │ 你要付 NT 300                       │ │  ← red/orange
│ │ ┌─ (expanded on click) ───────────┐ │ │
│ │ │ [textarea: account text]        │ │ │
│ │ │ [儲存] [status]                 │ │ │
│ │ └─────────────────────────────────┘ │ │
│ └─────────────────────────────────────┘ │
│                              顯示全部 ▼ │
│                                         │
│ ── divider ──                           │
│                                         │
│ Bob     → Alice      NT 1,200           │
│   [copy] 國泰 700-12345678              │
│                                         │
│ Charlie → Alice      NT 600             │
│   Alice 還沒提供帳號                    │
│                                         │
│ 2 transfers to settle ✓                 │
│ [CTA] Need to split a bill?             │
└─────────────────────────────────────────┘
```

### Identity Card

A single rounded-inset card replaces the current "I am..." label + me-picker + my-account-panel triad.

**Content:**
- **Greeting line:** `嗨，{identity}` (left) + optional `[分享轉帳帳號 ✏️]` button (right)
  - Name overflows with ellipsis on narrow screens; button never shrinks; row wraps if truly needed (`flex-wrap:wrap`)
  - Button is **only shown when `owed_to_me > 0`** (see rule below)
- **Summary lines** (each line only rendered if non-zero):
  - `別人欠你 NT {owed}` — green, color `#7fc69a`
  - `你要付 NT {owes}` — red/orange, color `#d96848`
  - Both numbers are derived client-side from `settlements` bootstrap data, not from server.
- **Expandable account editor** (hidden by default):
  - Clicking `分享轉帳帳號` toggles a panel inside the card: textarea (maxlength 500) + `儲存` button + inline status text
  - Pre-filled with `accounts[identity]` if present
  - `儲存` issues `PUT /v1/share/{id}/accounts/{identity}` with `x-device-id` header, shows `儲存中…` → `已儲存 ✓` or `儲存失敗`
  - After successful save, updates local `accounts` and re-renders settlement payee rows

**Rule: when to show `分享轉帳帳號` button:**
- Show iff `settlements.some(s => s.to === identity && s.amount > 0)` — i.e., at least one person owes the current user money. If nobody owes you anything, the button (and the whole panel) is suppressed — no point asking for your account.

### Default filter = claimed identity

- On first load after identity is known (either freshly claimed or persisted from localStorage), the settlement list is filtered to show only rows where `s.from === identity || s.to === identity`.
- Guests (`__guest__`) see all settlements by default.
- A small `顯示全部 ▼` / `只看自己 ▲` toggle link sits above the divider (no heavy buttons).
- `me-picker` row of buttons is **removed**. The only "viewing-as" control is the toggle link.

### Settlement rows

- Visual remains the same gold gradient row with `from → to amount`.
- **Payee account is still inlined below the main row** (same as now), but only rendered when the user's identity matches `s.from` (i.e., you are the payer). Dashed top border separates account block from main row.
- If payee has not shared their account → italic muted text `{payee} 還沒提供帳號`.
- If identity is unknown (guest) → no account block rendered at all.

### Identity modal (unchanged)

- First visit still shows the modal listing participants + `我只是路人`.
- After claiming, identity is stored in `localStorage` under `split_identity:{share_id}`.
- No UI to switch identity — clear `localStorage` to reset (documented in prior spec, unchanged).

## Color code

Match the existing settlement row color semantics:
- `from` (outgoing money) / `你要付` → red-ish `#d96848` (or `#5a2020` on gold bg)
- `to` (incoming money) / `別人欠你` → green-ish `#7fc69a` (or `#1a4a3a` on gold bg)
- Identity card itself keeps the inset-dark background already used in the phone skin; accent `#e8a84c` for title and button outline.

## What gets deleted

- `.me-picker` row of buttons and its click handler (`filterMe` function).
- `.my-account-panel` as a separate div outside the identity card (its content moves inside the card as the expandable editor).
- The old `me-picker` event listener and its delegation logic.
- The `{{iam}}` / `{{all_label}}` / `{{me_buttons}}` template variables (no longer needed).

## What gets added

- `.identity-card` block with nested `.id-row`, `.id-summary`, `.acct-editor`.
- `.view-toggle` link (顯示全部 / 只看自己).
- Client-side total computation: `owed = sum(s.amount where s.to===identity)`; `owes = sum(s.amount where s.from===identity)`.
- Default filter application on load based on identity.

## Data flow

No backend change. Existing endpoints:
- `GET /v1/share/{id}/accounts` — fetched once on load
- `PUT /v1/share/{id}/accounts/{participant}` — on save
- `DELETE /v1/share/{id}/accounts/{participant}` — still supported but not wired into UI (consistent with current state)

Client state (unchanged keys):
- `localStorage['split_device_id']` — UUID
- `localStorage['split_identity:'+share_id]` — claimed name or `__guest__`

## Tests

Existing tests in `tests/test_share_accounts.py` and `tests/test_handler.py` stay green — no API changes. New tests cover rendering only:

1. **`test_share_page_has_identity_card`** — rendered HTML contains `identity-card` class and no `me-picker` class.
2. **`test_share_page_no_me_picker_buttons`** — `{{me_buttons}}` template var is gone; HTML has no `me-btn` elements.
3. **`test_share_page_bootstrap_has_settlements`** — `window.__SHARE` bootstrap still contains `participants` and `settlements` arrays (used by new total-computation JS).
4. **XSS regression** (`test_render_share_page_no_xss_via_participant_name` already exists) — re-run to confirm new template still escapes properly.

No new pytest file needed; extend `tests/test_handler.py`.

## Out of scope / follow-ups

- Real-time sync of accounts across devices (current behavior: refresh to see updates).
- Identity switching UI.
- Rate-limiting `PUT` (same as existing spec — still open).
- Dark/light theme toggle.
