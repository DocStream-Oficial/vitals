# Connecting your data sources

Vitals never uses a shared account. **You create your own private key** to your
own health data, and that key lives only in your `.env` file on your own server.
Google, Oura, and WHOOP all call this "OAuth" — it sounds technical, but it's just
three things every time:

1. Create a free developer "app" on the provider's site.
2. Tell it to trust one address: `http://localhost:8700/auth/callback`.
3. Copy the two values it gives you (a **Client ID** and a **Client Secret**) into
   your `.env`.

That's it. Below is the click-by-click for each provider. **You only need one** —
do the one for the wearable you actually use. Google is the longest; Oura and
WHOOP are shorter.

> New to `.env`? It's a plain text file in the root of the repo. Copy
> `.env.example` to `.env` and fill in the values as you go. `install.py` creates
> it for you.

---

## Google Health (Google Fit, Pixel Watch, Fitbit, Garmin via Health Connect)

Vitals reads from Google's **new Google Health API** (`health.googleapis.com`) —
**not** the old Google Fit / Fitness API, which Google stopped accepting new
sign-ups for in 2024 and is retiring end of 2026. So you're on the current,
supported path.

You'll do this once, in the [Google Cloud Console](https://console.cloud.google.com/).
A Google account is all you need; there's no cost.

### 1. Create a project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. At the top, click the project dropdown → **New Project**. Name it anything
   (e.g. `vitals`) and create it. Make sure it's selected before continuing.

### 2. Enable the Google Health API

1. Go to **APIs & Services → Library** (or search "API Library" in the top bar).
2. Search for **Google Health API** and open it.
3. Click **Enable**.

> ⚠️ Because health data is sensitive, Google may show an access-request or
> verification step here the first time. If it asks you to request access or
> submit the app for verification, follow its prompts — for personal use you can
> usually proceed with your own Google account as a test user (see step 4).

### 3. Configure the consent screen

This is the screen *you* will see when you first connect — you're setting it up
for yourself.

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose **External** user type → **Create**.
3. Fill only the required fields: app name (`Vitals`), your email as the support
   and developer contact. Skip everything optional. Save and continue.
4. On the **Scopes** step, click **Add or remove scopes** and add these three
   (paste them into the "manually add scopes" box if you don't see them listed):

   ```
   https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   https://www.googleapis.com/auth/googlehealth.sleep.readonly
   ```

   All three are **read-only** — Vitals can never write to or change your Google
   data. Save and continue.

### 4. Add yourself as a test user

1. On the **Test users** step, click **Add users** and add the Google account
   whose health data you want to read (your own).
2. Save. (While the app is in "Testing" mode this is what lets you log in.)

### 5. Create the OAuth client ID

1. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Web application**.
3. Under **Authorized redirect URIs**, click **Add URI** and paste exactly:

   ```
   http://localhost:8700/auth/callback
   ```

   - Running Vitals on a remote box you reach over Tailscale/HTTPS? Also add:
     `https://your-box.your-tailnet.ts.net/auth/callback` (your real HTTPS URL).
4. Click **Create**. Google shows you a **Client ID** and **Client Secret** —
   keep this dialog open.

### 6. Put the values in `.env`

Open your `.env` and set:

```bash
CLIENT_ID=the-client-id-google-just-showed-you.apps.googleusercontent.com
CLIENT_SECRET=the-client-secret-google-just-showed-you
REDIRECT_URI=http://localhost:8700/auth/callback
```

Restart Vitals, open the app, go to **More → connect Google Health**, and log in.
Done.

### About the 7-day "Reconnect" nag

While your OAuth app is in **Testing** status, Google expires the token every
7 days and the dashboard shows a "Reconnect" banner — one tap re-auths you.
To make it permanent: in the OAuth consent screen, click **Publish app**
(for personal use you can leave Google's optional verification unfinished). Then
set `GOOGLE_TOKEN_EXPIRY_DAYS=0` in `.env` (the default) so no false countdown
shows.

---

## Oura

1. Go to the [Oura developer portal](https://cloud.ouraring.com/oauth/applications)
   and sign in with your Oura account.
2. Click **Create New Application**.
3. Set the **Redirect URI** to exactly:

   ```
   http://localhost:8700/auth/callback
   ```

   (Same value Google uses — Vitals routes the callback to whichever source you're
   connecting.)
4. When asked for scopes, enable: **personal, daily, heartrate, workout, spo2,
   session**.
5. Copy the **Client ID** and **Client Secret** into `.env`:

   ```bash
   OURA_CLIENT_ID=your-oura-client-id
   OURA_CLIENT_SECRET=your-oura-client-secret
   ```

Restart Vitals → **More → connect Oura** → log in.

---

## WHOOP

1. Go to the [WHOOP developer portal](https://developer.whoop.com) and sign in.
2. Create a new app.
3. Set the **Redirect URI** to exactly:

   ```
   http://localhost:8700/auth/callback
   ```

4. Enable these scopes — **`offline` is mandatory**, or WHOOP won't give Vitals a
   refresh token and you'll be logged out constantly:

   ```
   read:recovery  read:sleep  read:workout  read:cycles
   read:profile   read:body_measurement   offline
   ```

5. Copy the **Client ID** and **Client Secret** into `.env`:

   ```bash
   WHOOP_CLIENT_ID=your-whoop-client-id
   WHOOP_CLIENT_SECRET=your-whoop-client-secret
   ```

Restart Vitals → **More → connect WHOOP** → log in.

---

## Apple Watch / HealthKit

Apple is different — it doesn't use OAuth. The native iOS companion app reads
HealthKit on-device and pushes to Vitals with a shared token. See
[`docs/IOS-HEALTHKIT.md`](IOS-HEALTHKIT.md).

---

## Connecting more than one person (household)

Each person in your household creates **their own** OAuth app on the provider
(their own Client ID/Secret) and connects it under their own profile in Vitals —
their data stays fully isolated. One Vitals install, one server, everyone's data
separate. See the household feature in the main [README](../README.md#features).

---

## Troubleshooting

- **"redirect_uri_mismatch"** — the redirect URI in the provider's app must match
  `REDIRECT_URI` in your `.env` character-for-character (including `http` vs
  `https` and the port). This is the #1 cause of connection errors.
- **Logged out after ~7 days (Google)** — your OAuth app is still in Testing mode.
  Publish it (see above).
- **Logged out constantly (WHOOP)** — you're missing the `offline` scope. Add it
  and reconnect.
- **Nothing syncs after connecting** — give it until the next scheduled sync
  (default 9am, `SYNC_HOUR` in `.env`) or hit **Sync now** in the app.
