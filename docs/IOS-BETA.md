# Vitals iOS — Build your own beta (TestFlight)

The iOS app is a **thin native shell** (Capacitor + WKWebView) that loads **your own
self-hosted Vitals instance**. No binaries are distributed: you build your own app with
your own Apple account. First-time setup takes ~30 min.

## Requirements
- A Mac with **Xcode** (including the iOS platform components — Xcode > Settings > Components).
- **Node.js** and this repo cloned.
- A **paid Apple Developer account** ($99/yr) to upload to TestFlight.
  *(With a free Apple ID you can run it on your own iPhone with 7-day signing, but NOT TestFlight.)*
- Your Vitals instance running and reachable over HTTPS (e.g. via Tailscale).

## 1. Prepare the project
```bash
npm install
npx cap sync ios
```

## 2. Open in Xcode
```bash
open ios/App/App.xcodeproj
```

## 3. Signing (Signing & Capabilities)
1. Select the **App** target → **Signing & Capabilities** tab.
2. **Team**: pick your Apple Developer team.
3. **Bundle Identifier**: change it to your own unique one (e.g. `com.yourname.vitals`).
   The default may already be taken — use yours.

## 4. (Optional) Test on your iPhone or the simulator
- Connect your iPhone (or pick a simulator) and hit **Run** (▶).
- On first launch you'll see the **"Connect your Vitals"** screen: paste your
  instance URL (your Tailscale HTTPS, for example) and tap **Connect**. The app remembers it.
  *(QR scanning is planned for a future version — see "Pending".)*

## 5. Upload to TestFlight
1. In Xcode: select the **Any iOS Device (arm64)** destination.
2. **Product > Archive**. Wait for it to finish.
3. In the Organizer: **Distribute App > App Store Connect > Upload**.
4. In [App Store Connect](https://appstoreconnect.apple.com) → your app → **TestFlight**:
   - Internal testers (you plus up to 100 team members): available almost instantly.
   - External testers (public link, up to 10k): requires a light **Beta App Review**.
5. Install **TestFlight** from the App Store on your iPhone, accept the invite, install Vitals.

> ⚠️ TestFlight builds **expire after 90 days** — upload a new one each quarter.

## 6. Connect the app to your instance
On first launch, paste your HTTPS URL and tap **Connect**.
The app remembers it and loads your Vitals as if it were fully native.

## Pending (future improvements)
- **QR scanning**: the camera plugin we tried (`@capacitor-mlkit/barcode-scanning`) is not
  compatible with Capacitor 7's SPM mode. It will return once we migrate to CocoaPods or an
  SPM-compatible scanner. The shell already has the handler in place (it reappears once a
  scanner exists) and the web app generates the QR under **More → Connect mobile app**.
- **Persistent "switch instance" button**. For now, to point at a different instance:
  delete and reinstall the app.

## Troubleshooting
- **"iOS XX.X is not installed" / outdated simulator**: Xcode > Settings > Components →
  download the iOS platform and simulator. Restart the Mac if it persists.
- **Signing fails**: confirm the Team plus a unique Bundle ID of your own (not the default).
- **Camera doesn't open**: check you granted the permission; scanning uses
  `NSCameraUsageDescription` (already included). Otherwise use manual URL entry.
- **Icon**: the repo ships a placeholder. Replace `assets/icon.png` (1024×1024, no
  transparency) with your own and run `npx @capacitor/assets generate --ios`.
