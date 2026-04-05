# TODOS

## Push Notifications for Settlement Reminders
**What:** Add push notification support to remind users about unsettled debts ("You still owe Alice $40").
**Why:** The current growth loop (share link → install → use) handles acquisition but has no retention mechanism. Push reminders bring users back to the app.
**Pros:** Higher retention, closes the loop on settlements, competitive parity with Splitwise.
**Cons:** Requires `expo-notifications` setup, backend push logic (Lambda → APNs/FCM), notification permission UX.
**Context:** The native app growth loop is: Alice shares → Bob installs → Bob uses next time. But without reminders, Bob forgets to pay Alice. Push notifications solve this. Needs: expo-notifications, backend endpoint to register device tokens, scheduled Lambda to check unsettled debts and push.
**Depends on:** Native app must be live on App Store first. Needs user identity (at minimum, device token registration per share session).

## Participant Color Unification
**What:** Each participant gets one fixed color everywhere (chip, avatar border, expense card, settlement).
**Why:** Currently colors are assigned independently in different views. User "Alice" might be blue in chips but green in settlement.
**Context:** Tracked since initial design sessions. Affects both web and native app.
**Depends on:** Nothing. Can be done independently.

## Split Slider (Custom Proportions)
**What:** Allow custom proportion splitting, not just equal splits.
**Why:** Real-world dining often involves unequal splits (kids eat less, birthday person doesn't pay).
**Context:** Tracked since initial design. Most competitors support this.
**Depends on:** Nothing. Affects both web and native app calculation logic.

## Custom Domain
**What:** Register splitsenpai.com (or similar) and point to API Gateway.
**Why:** REQUIRED for Universal Links / App Links to work. Also better branding than the API Gateway URL.
**Context:** Identified as blocking dependency for the native app growth loop. Must be done before app store submission.
**Depends on:** Domain registration + DNS setup. Then add AASA + assetlinks.json routes to Lambda.
