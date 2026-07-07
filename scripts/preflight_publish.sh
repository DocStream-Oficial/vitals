#!/usr/bin/env bash
# preflight_publish.sh — Fase 8A (paso A4): last-line-of-defense check before
# pushing this repo to a public remote. Run it before `git push` to a public
# GitHub repo, or wire it as a pre-push hook.
#
# Fails (exit 1) if:
#   1. `.env` is staged for commit.
#   2. Anything under `data/` is staged for commit.
#   3. A real-looking secret is found in a TRACKED file (git ls-files) —
#      currently: Google OAuth client secrets (GOCSPX-...) and any
#      CLIENT_SECRET=/OURA_CLIENT_SECRET=/WHOOP_CLIENT_SECRET=/INGEST_TOKEN=
#      assignment with a non-placeholder value in a file that isn't
#      .env.example.
#
# Exits 0 (prints "OK") if none of the above triggers — safe to publish.
#
# This is a SAFETY NET, not a substitute for judgment: it can't catch a secret
# pasted into a comment, a commit message, or a file it doesn't know to look
# at. Always eyeball `git diff --staged` yourself too.

set -uo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 1

fail=0

echo "== preflight_publish.sh =="

# ── 1. .env staged? ──────────────────────────────────────────────────────────
staged_env="$(git diff --cached --name-only -- '.env' 2>/dev/null)"
if [ -n "$staged_env" ]; then
    echo "FAIL: '.env' is staged for commit. Unstage it: git restore --staged .env"
    fail=1
else
    echo "OK: .env is not staged."
fi

# ── 2. Anything under data/ (or a data backup dir) staged? ──────────────────
# Also matches sibling backup copies like data.bak-fase8d/ or data-2026.bak/,
# which hold a full copy of the user's REAL data but do NOT live under data/
# (so the plain 'data/' pathspec misses them). These are gitignored, but a
# `git add -A -f` or a stale .gitignore could still stage them.
staged_data="$(git diff --cached --name-only -- 'data/' 'data.bak*' 'data-*.bak' 2>/dev/null)"
if [ -n "$staged_data" ]; then
    echo "FAIL: file(s) under 'data/' (or a data backup dir) are staged for commit:"
    echo "$staged_data" | sed 's/^/  - /'
    echo "  Unstage them: git restore --staged <path>"
    fail=1
else
    echo "OK: nothing under data/ (or a data backup dir) is staged."
fi

# ── 3. Real-looking secrets in TRACKED files ────────────────────────────────
# Google OAuth client secrets always start with GOCSPX- — a very low false-
# positive-rate signature. Search only tracked files (git ls-files), not the
# whole working tree (which would also flag the Doc's real, gitignored .env).
#
# Excluded from THIS scan (self-detection fix, roadmap H3):
#   - this script itself (it necessarily contains the literal "GOCSPX-"
#     pattern it searches for, which made it fail on every run — a tool that
#     always cries wolf gets ignored);
#   - .env.example (placeholders/patterns shown on purpose for onboarding);
#   - docs/*.md and any other *.md (may document the pattern in prose, e.g.
#     a roadmap describing this very check).
# A real GOCSPX- secret landing in any OTHER tracked file still fails loudly.
tracked_files="$(git ls-files)"

gocspx_hits=""
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        scripts/preflight_publish.sh|.env.example|*.md|docs/*) continue ;;
    esac
    [ -f "$f" ] || continue
    if grep -l "GOCSPX-" "$f" >/dev/null 2>&1; then
        gocspx_hits="${gocspx_hits}${f}\n"
    fi
done <<< "$tracked_files"

if [ -n "$gocspx_hits" ]; then
    echo "FAIL: found a Google OAuth client secret (GOCSPX-...) in tracked file(s):"
    printf '%b' "$gocspx_hits" | sed 's/^/  - /'
    fail=1
else
    echo "OK: no GOCSPX- (Google client secret) pattern in tracked files."
fi

# Real OAuth token files (token.json and friends) are JSON blobs with both a
# refresh_token and an access_token — a shape that never appears in this repo's
# tracked .json. Scan tracked *.json only (source .py legitimately reference
# those keys as dict keys; a real token is a data file), so a leaked copy of
# the user's real token.json (under data/, a backup dir, or anywhere) can't
# slip through the GOCSPX/*_SECRET= checks above (real tokens are NOT GOCSPX-).
token_hits=""
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        *.json) ;;
        *) continue ;;
    esac
    [ -f "$f" ] || continue
    if grep -q '"refresh_token"' "$f" 2>/dev/null && grep -q '"access_token"' "$f" 2>/dev/null; then
        token_hits="${token_hits}${f}\n"
    fi
done <<< "$tracked_files"

if [ -n "$token_hits" ]; then
    echo "FAIL: found a file with a real-looking OAuth token (refresh_token + access_token) tracked:"
    printf '%b' "$token_hits" | sed 's/^/  - /'
    fail=1
else
    echo "OK: no tracked file carries a real-looking OAuth token blob."
fi

# Generic *_SECRET=/_TOKEN= assignments with a non-placeholder value, in any
# tracked file EXCEPT this script, .env.example (which is supposed to show
# the key names with placeholder values) and docs (which may show the key
# name in prose).
#
# The value must look real (12+ chars) but NOT match an obvious placeholder
# shape: values containing "YOUR_"/"your-" (the .env.example convention,
# occasionally quoted verbatim in test fixtures) or an "existing"/"dummy"/
# "example"/"placeholder"/"fixture"/"test"/"fake"/"sample" marker word, which
# only ever show up in test fixtures and docs, never in a real leaked secret
# (real secrets are opaque provider-generated strings, not self-describing
# English words). This keeps real-secret detection intact while not flagging
# tests/test_install.py's dummy `INGEST_TOKEN=existing-token-value` and
# `CLIENT_SECRET=YOUR_CLIENT_SECRET` fixtures.
suspect_pattern='(CLIENT_SECRET|INGEST_TOKEN|OURA_CLIENT_SECRET|WHOOP_CLIENT_SECRET)=[A-Za-z0-9_\-]{12,}'
placeholder_pattern='(YOUR_|your-|existing|dummy|example|placeholder|fixture|test|fake|sample)'
secret_hits=""
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        scripts/preflight_publish.sh|.env.example|*.md|docs/*|SECURITY.md|CONTRIBUTING.md) continue ;;
    esac
    [ -f "$f" ] || continue
    matches="$(grep -Eo "$suspect_pattern" "$f" 2>/dev/null)"
    [ -z "$matches" ] && continue
    real_hit="$(printf '%s\n' "$matches" | grep -Eiv "$placeholder_pattern")"
    if [ -n "$real_hit" ]; then
        secret_hits="${secret_hits}${f}\n"
    fi
done <<< "$tracked_files"

if [ -n "$secret_hits" ]; then
    echo "FAIL: found what looks like a real secret assignment in tracked file(s):"
    printf '%b' "$secret_hits" | sed 's/^/  - /'
    fail=1
else
    echo "OK: no suspicious *_SECRET=/*_TOKEN= assignment with a real-looking value in tracked files."
fi

echo "=========================="
if [ "$fail" -ne 0 ]; then
    echo "preflight_publish.sh: FAILED — fix the issues above before pushing to a public remote."
    exit 1
fi

echo "preflight_publish.sh: OK — nothing sensitive detected staged or tracked."
exit 0
