# Vitals iOS — Native HealthKit

Manual guide to enable Apple Watch / HealthKit sync in the native app. The code
is already written (the `VitalsHealth` plugin + `HealthSyncManager`); these are
the steps that can only be done in Xcode, by you.

## 0. What this does

The native app (Capacitor, thin shell) gains its own Swift plugin that:
- Reads ~13 HealthKit types (HRV, resting HR, respiratory rate, SpO₂, wrist
  temperature, steps, VO₂max, distance, energy, sleep, workouts) from the last
  45 days.
- Aggregates by the iPhone's **local day** (not UTC).
- Builds the same payload the backend already consumes
  (`app/sources/healthkit.py`).
- Does `POST {your-instance}/api/ingest` with the `X-Vitals-Token` header.
- Fires automatically (auto-sync) every time the app returns to the foreground —
  **only if you configured a token**. Without a token, zero calls (users who
  don't use HealthKit/Apple Watch never notice this exists).

## 1. Enable the HealthKit capability in Xcode

1. Open the project: `open ios/App/App.xcodeproj` (or via `npx cap open ios`).
2. Select the **App** target → **Signing & Capabilities** tab.
3. Click **+ Capability** → search **HealthKit** → add it.
   - This generates (or updates) `ios/App/App/App.entitlements` automatically.
   - **Important**: the HealthKit entitlement usually requires a **paid Apple
     Developer Program** account ($99/yr). With a free **Personal Team**, Xcode
     may reject the build or strip the capability at signing time. If that
     happens, note it down — it's not a code bug, it's an account limitation.
     The code stays ready for when you enroll in the paid program.
4. Inside the HealthKit capability there's no need to check "Clinical Health
   Records" — only read access to the standard types the code already requests.

## 2. Confirm the permission text (already included)

`ios/App/App/Info.plist` already ships:
```xml
<key>NSHealthShareUsageDescription</key>
<string>Vitals reads your health metrics (heart rate, HRV, sleep, oxygen,
temperature, steps and workouts) to compute your recovery, sleep and body age.
Your data is only sent to your own server.</string>
```
You don't need to touch it. Read-only → no `NSHealthUpdateUsageDescription` needed.

## 3. Build & Run on your iPhone

1. Connect your iPhone and select it as the destination in Xcode.
2. If your stable Xcode doesn't support your iOS version, use **Xcode-beta**
   (point `DEVELOPER_DIR` at the beta if you invoke it from the CLI).
3. **Product → Run** (▶). Wait for `** BUILD SUCCEEDED **`.
   - If the build fails on the entitlement (see step 1), confirm Team +
     capability before continuing.

## 4. Grant the HealthKit permission

1. The first time the app tries to sync (see step 5), iOS shows the standard
   HealthKit sheet with the list of types. Enable them all (or just the ones
   you want to share — types you don't enable simply send no data for that
   metric; the backend tolerates missing ones).
2. If you deny it by accident: **Settings → Privacy & Security → Health →
   Vitals** on the iPhone, and enable them there.

## 5. Test the full flow

1. **The token is NOT optional and you don't need to invent one by hand.** If
   `INGEST_TOKEN` isn't in your server's `.env`, the backend auto-generates one
   at startup and persists it to `data/ingest_token.json` — copy the active
   value from the **More → Connect mobile app → HealthKit/ECG token** tab
   ("Copy" button), or set your own instead:
   ```
   INGEST_TOKEN=pick-a-long-random-token
   ```
   in the `.env` and restart the service (e.g. `Restart-Service Vitals` on
   Windows, or your platform's equivalent) — this takes priority over the
   auto-generated one.
   - `/api/ingest` and `/api/ecg` respond **401 ALWAYS** when the
     `X-Vitals-Token` header is missing or doesn't match — there is no
     "no auth" mode.
2. In the web app (**More** tab), switch your profile's source (`source`) to
   `healthkit` — that way the backend knows your data comes from the native
   push and doesn't try to pull from Google/Oura/WHOOP.
3. In the native app (**"Connect your Vitals"** screen): paste your instance
   URL and, in the **"Sync token (HealthKit)"** field (NOT optional — without
   it every push gets a 401), paste the token you copied in step 1. Tap
   **Connect**.
   - This stores the token in native `UserDefaults` (via `VitalsHealth.setConfig`)
     and in the shell's `localStorage`.
   - The QR under "More → Connect mobile app" already embeds the token as a
     query param (`?ingest_token=...`) in the encoded URL — today the native
     screen still asks you to paste it manually (the app has no QR reader yet),
     but it's ready for a future QR auto-config flow.
4. Leave the app to the Home screen and reopen it (foreground) — that triggers
   `SceneDelegate.sceneDidBecomeActive` → `HealthSyncManager.shared.autoSyncIfConfigured()`.
   - The first time, it will ask for the HealthKit permission (step 4).
5. Verify:
   - On the server: check `data/healthkit_ingest.json` — it should contain the
     freshly received raw payload (with an updated `_ingested_at`).
   - In the web app: `GET /api/data` (or the **Today/Trends** tabs) should show
     recent days read from the Apple Watch.

## 6. Troubleshooting

- **`window.Capacitor.Plugins.VitalsHealth` is `undefined`**: the plugin didn't
  load. Confirm that `ios/App/App/capacitor.config.json` (the copy inside the
  target, NOT just the repo-root one) has `"packageClassList": ["VitalsHealth"]`,
  and that you ran `npx cap sync ios` after any change to the root
  `capacitor.config.json` (sync copies/regenerates the target's copy).
- **Nothing reaches the backend / `n_days: 0`**: check that you granted the
  HealthKit permission (step 4) and that your Apple Watch actually has data in
  the last 45 days for those types (some, like `vo2Max` or
  `appleSleepingWristTemperature`, require a compatible Apple Watch and some
  usage — they're optional and are skipped when there's no data).
- **401 on the POST**: the app's token doesn't match the server's active
  `INGEST_TOKEN` (from `.env` or the auto-generated one in
  `data/ingest_token.json`). Re-connect with the correct token — copy it again
  from **More → Connect mobile app**. This responds 401 ALWAYS without a valid
  token; there is no "no auth" mode.
- **Build fails on entitlement/signing**: see the note in step 1 — a free
  Personal Team account may not support HealthKit. Read Xcode's exact message;
  if it's about provisioning, that's the account limit (not the code).

## Notes

- Auto-sync is **foreground only** (v1). There's no background `HKObserverQuery`
  yet — to refresh, just reopen the app.
- A "Sync HealthKit" button inside the remote dashboard
  (`templates/vitals_ios.html`) is **deferred** to a follow-up — for now,
  relaunching the native app = syncing.
- Rollback: if something breaks, remove `"VitalsHealth"` from both
  `capacitor.config.json` files (repo root and `ios/App/App/`) and run
  `npx cap sync ios` again — the rest of the app keeps working; the token field
  in `www` stays inert with an empty value.
