# Security Policy

Vitals is a self-hosted app: you run it on your own infrastructure, with your
own OAuth credentials, and your own health data. There is no shared multi-tenant
backend where a vulnerability could affect anyone but you — but that also means
you are your own security team. This document covers both how to report a
vulnerability in the project's code, and the operational practices that keep
*your* instance safe.

## Reporting a vulnerability

If you find a security issue in this codebase (auth bypass, path traversal,
injection, secrets handling, etc.):

1. **Do not open a public GitHub issue for it.**
2. Open a [GitHub Security Advisory](../../security/advisories/new) on this
   repository (private by default), or contact the maintainer directly if you
   don't have access to that flow.
3. Include: affected file(s)/endpoint(s), a reproduction, and the impact you
   believe it has (e.g. "unauthenticated attacker on the same tailnet can
   read/write X").

We'll acknowledge reports as quickly as we can and credit you in the fix commit
unless you'd prefer otherwise. There is no bug bounty — this is a personal/
community project — but real reports are taken seriously and fixed promptly.

## Threat model — what Vitals does and doesn't protect against

Vitals assumes it runs on a **private network** (localhost, a home LAN, or a
Tailscale/WireGuard tailnet) reachable only by devices you trust. It is **not**
designed to be exposed directly to the public internet without a reverse proxy
that adds its own authentication layer.

What Vitals *does* enforce on its own:

- `INGEST_TOKEN` (mandatory as of Fase 8C): every `/api/ingest` and `/api/ecg`
  push from the iOS companion app must present this exact shared secret as the
  `X-Vitals-Token` header, or it's rejected with 401. It's auto-generated and
  persisted if you don't set one, so there is no unauthenticated push path.
- Path-traversal hardening on household multi-profile user IDs
  (`app/userctx.py::_sanitize_uid`) — a crafted user id can't escape
  `data/users/<uid>/` to read or delete arbitrary files. This was a real bug
  caught during Fase 8D validation and fixed before release; see `CHANGELOG.md`.
- OAuth `state` parameter validated on every `/auth/callback` (CSRF protection
  for the OAuth flow).
- Demo mode (`VITALS_DEMO=1`) is hermetic by construction: it never reads or
  writes `data/`, never requires real credentials, and blocks every
  sync/auth/ingest endpoint from having any real-world effect — safe to run
  publicly as a live demo.

What Vitals does **not** provide, by design (self-hosted, single/household
user, not a SaaS):

- No rate limiting on the API.
- No encryption at rest for `data/*.json` — if your disk is encrypted (FileVault,
  BitLocker, LUKS), your data is; if not, it isn't.

### DASHBOARD_TOKEN (recommended)

By default there is **no login/password screen** in front of the dashboard —
anyone who can reach the port can view your data. Set `DASHBOARD_TOKEN` in
`.env` to require authentication for the web dashboard (every route except
`/login`, static PWA assets, `/api/ingest` + `/api/ecg` POST, and `/api/v1/*`,
which all keep their own independent auth):

- `GET /login` shows a minimal password form; `POST /login` with the correct
  token sets an HttpOnly, `SameSite=Lax` cookie (`vitals_dash`) valid for a
  year, then redirects to `/`.
- Alternatively, send `Authorization: Bearer <DASHBOARD_TOKEN>` on any
  request — useful for `curl`, Grafana, or any client that can't hold cookies.
- Comparison is always done in bytes via `secrets.compare_digest` (never a
  plain `==` on `str`), so it's timing-safe and never throws on non-ASCII
  input.
- Bonus: because the cookie is `SameSite=Lax`, enabling `DASHBOARD_TOKEN` also
  gives the dashboard's own POST endpoints free CSRF protection — cross-site
  POSTs simply don't carry the cookie.
- Leaving it empty (the default) keeps today's behavior byte-for-byte — this
  is fully opt-in, nothing changes until you set it.
- We still recommend putting the app behind Tailscale, a VPN, or a reverse
  proxy with its own auth if you expose it outside a trusted network —
  `DASHBOARD_TOKEN` is a second layer, not a replacement for network isolation.

### `/api/ingest-token` exposure

`GET /api/ingest-token` returns the current `INGEST_TOKEN` in plain JSON so the
"More" tab (and its pairing QR code) can show it to you. **Without
`DASHBOARD_TOKEN` set, this endpoint is unauthenticated** — anyone on your
network who can reach the app can read your ingest token this way. Setting
`DASHBOARD_TOKEN` closes this: `/api/ingest-token` requires the same
cookie/Bearer auth as the rest of the dashboard.

### QR pairing code and access logs

The pairing QR code (`GET /api/qr?data=...`) embeds the ingest token directly
in the URL you scan. If that URL ever passes through a reverse proxy, load
balancer, or any HTTP access log, the token will appear there in plain text —
treat proxy/access logs for this app as sensitive, and rotate `INGEST_TOKEN` if
you suspect one leaked.

## Secrets hygiene (before you publish a fork)

- `.env` and `data/` are gitignored — never commit them. Run
  `bash scripts/preflight_publish.sh` before pushing to a public remote; it
  fails the build if either is staged, or if it finds a real-looking
  `CLIENT_SECRET` in a tracked file.
- If a real OAuth `CLIENT_SECRET` or `INGEST_TOKEN` ever left your `.env` (e.g.
  pasted into a chat, a screenshot, or a public gist), treat it as burned —
  rotate it in the Google Cloud Console / Oura / WHOOP developer portal.
- See `CONTRIBUTING.md#before-you-publish` for the full pre-publish checklist.

## Supported versions

This is a single-branch personal/community project — only `main` is supported.
There are no LTS branches or backported security fixes; always run the latest
commit.
