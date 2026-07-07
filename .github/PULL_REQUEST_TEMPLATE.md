## What and why

Briefly describe the change and the motivation behind it.

## How was this tested

- [ ] `pytest -q` passes locally (paste the final summary line below)
- [ ] `python scripts/i18n_audit.py` → 0 leaks
- [ ] `python scripts/i18n_audit_server.py` → 0 leaks
- [ ] Manually verified in the running app (describe what you clicked/checked)

```
paste pytest summary here, e.g. "1,312 passed in 45s"
```

## Scoring / algorithm changes (delete this section if N/A)

If this touches `app/scoring.py`, `app/bodyage.py`, `app/healthspan.py`,
`app/journal.py`, or `app/drivers.py`:

- [ ] `tests/test_regression.py` still passes
- [ ] `tests/fixtures/golden_synthetic.json` was regenerated
      (`python scripts/gen_golden.py`) and the diff is reviewed/intentional
- [ ] `docs/ALGORITHMS.md` was updated to match the new formula/threshold

## Security / secrets checklist

- [ ] No secrets, tokens, or personal data in this diff
      (`git diff --staged | grep -iE "client_secret|token|tailnet"` is clean)
- [ ] `.env` and `data/` are not touched
      (`git diff --staged --name-only | grep -E '\.env$|^data/'` is empty)
- [ ] `bash scripts/preflight_publish.sh` passes

## Screenshots (if UI change)

Before/after, or a short GIF.
