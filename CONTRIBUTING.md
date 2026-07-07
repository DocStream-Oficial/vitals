# Contributing to Vitals

Thanks for your interest in contributing. Vitals is a self-hosted health dashboard — contributions
that make it easier to deploy on your own box are especially welcome.

## Getting started

```bash
git clone <your-fork>
cd vitals-app
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in your own CLIENT_ID / CLIENT_SECRET / profile
uvicorn main:app --host 127.0.0.1 --port 8700 --reload
```

## Running the test suite

All tests must pass before opening a PR:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt   # pytest + httpx (test-only, not runtime deps)
pytest -q
```

CI (`.github/workflows/ci.yml`) runs the same suite on Python 3.9 and 3.12 on
every push/PR — check that it's green before requesting review.

The regression tests use a **synthetic fixture** (`tests/fixtures/golden_synthetic.json`)
with fabricated health data — no real user data is shipped in the repo.
`data/health_compact.json` is your personal runtime file; it is gitignored.

## i18n audit gates

Vitals ships with four locales (ES / EN / FR / PT). Any new UI string must be added
to all locale files. Two audit scripts enforce this:

```bash
# 1. Checks app/i18n.py (Python locale dict)
python3 scripts/i18n_audit.py

# 2. Checks the server-side i18n helper (templates + main.py strings)
python3 scripts/i18n_audit_server.py
```

Both must exit with **0 missing keys** before a PR is merged. If you add a new
translatable string, add its key (with a translation or at least a placeholder)
to all four locales.

## Code style

- Python 3.9+ compatible (the venv on Mac dev uses 3.9).
- No new dependencies without a strong reason — keep `requirements.txt` minimal.
- Do not modify scoring formulas (`app/scoring.py`, `app/bodyage.py`) without
  updating `tests/test_regression.py` and regenerating `golden_synthetic.json`
  (run `python3 scripts/generate_golden_synthetic.py` — see that file for instructions).
- Keep secrets out of the repo: `.env`, `data/`, `*token*.json`, `vitals_config.json`
  are all gitignored. Do not hardcode credentials or personal paths.

## Before you publish

If you're forking this repo to make your own instance public (or open-sourcing
your own deployment), double-check the following — none of it should be
necessary if you never removed the defaults, but it's cheap insurance:

- [ ] `.env` is **not** tracked: `git ls-files | grep -E '\.env$'` must be empty.
- [ ] `data/` is **not** tracked: `git ls-files | grep -E '^data/'` must be empty.
      This directory holds your real health history, tokens, and journal/labs —
      it is gitignored by default (see `.gitignore`) and should stay that way.
- [ ] No `CLIENT_SECRET` / API key is hardcoded anywhere in tracked files —
      `.env.example` should only ever contain placeholder values.
- [ ] Run `bash scripts/preflight_publish.sh` — it fails loudly if `.env` or
      `data/` are staged for commit, or if it detects a real-looking secret in
      a tracked file.
- [ ] **Rotate your Google/Oura/WHOOP OAuth credentials** if you ever pasted a
      real `CLIENT_SECRET` into a shell command, screenshot, or chat during
      development — treat any credential that left `.env` as burned.
- [ ] Consider running `VITALS_DEMO=1` (see README) for anything you plan to
      demo publicly (a livestream, a screen-share, a hosted preview) instead of
      pointing people at an instance with your real data.

See also [`SECURITY.md`](SECURITY.md) for how to report a vulnerability found
in this project.

## Pull request checklist

- [ ] `pytest -q` passes (green)
- [ ] `python3 scripts/i18n_audit.py` → 0
- [ ] `python3 scripts/i18n_audit_server.py` → 0
- [ ] No secrets or personal data in diff (`git ls-files | xargs grep -E "tailnet|token|CLIENT_SECRET"` is clean)
- [ ] PR description explains *what* and *why*
