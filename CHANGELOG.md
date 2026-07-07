# Changelog

All notable changes to Vitals are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Fase 9 — Des-monolitizar routing (unreleased)

Refactor estructural: sin cambios funcionales. `main.py` (2,271 → 438 líneas)
troceado en 16 routers de dominio bajo `app/routes/*.py` (pwa, export,
journal, cycle, labs, sources, ecg, profile, coach, report, insights, sync,
auth, programs, healthspan, household, keys) + `app/deps.py` (pegamento
compartido: `_data_path`, `_load_dataset`, `_KNOWN_SOURCES`,
`_clean_str_list`, etc.) + `app/routes/_models.py` (modelos Pydantic de
request). Ningún motor de cómputo (`app/cycle.py`, `app/coach.py`,
`app/sleep_*.py`, `app/journal.py`, `app/report.py`, etc.) se tocó — `git
diff` sobre esos archivos es vacío. Contrato de API congelado: OpenAPI y
golden files idénticos al baseline pre-refactor (salvo `GET /`, que cambió
por la extracción de CSS, ver abajo). 1,661 tests verdes antes y después.

`templates/vitals_ios.html`: se extrajo el bloque `<style>` completo (1,541
líneas) a `static/css/vitals.css` (10,150 → 8,609 líneas). La extracción de
los dos `<script>` inline (~7,600 líneas de JS) a `static/js/` **NO se
hizo**: la suite de tests existente (`tests/test_endpoints.py`,
`tests/test_i18n.py`) afirma contra literales de código JS (nombres de
función como `renderTend`/`sendCoach`/`ORDER_SCOPES`, y el bloque completo
`var STRINGS = {...}`) directamente sobre el HTML servido o el archivo en
disco — mover ese JS a un archivo externo rompe esas aserciones
categóricamente, no por un descuido de implementación sino porque los tests
verifican "el JS vive inline" como parte del contrato. Deshacer eso requeriría
editar tests, fuera del alcance de un refactor estructural puro. Documentado
como desviación del roadmap original (que apuntaba a ~1,500 líneas de
esqueleto); ver informe de Fase 9 para detalle completo.

## Fase 8A — GitHub launch packaging (unreleased)

- **Demo mode** (`VITALS_DEMO=1`): serves a deterministic 150-day synthetic
  dataset (recovery/strain/sleep/HRV, exercises, journal habits with a real
  injected alcohol→recovery correlation, sample labs) with zero OAuth/tokens
  required. Sync, OAuth login/callback, source connect/disconnect, and
  HealthKit/ECG ingest all short-circuit to a `{"status": "demo"}` response —
  nothing writes to real credentials or `data/`. Journal/labs/cycle writes in
  demo mode land in an ephemeral temp directory, never the real `data/`.
  Generator: `scripts/gen_demo_data.py`.
- **CI**: `.github/workflows/ci.yml` runs the full pytest suite + i18n audit
  on Python 3.9 and 3.12 on every push/PR.
- **Test hardening**: `tests/test_mcp_tools.py::TestTodaySnapshot` no longer
  depends on the Doc's real `data/health_compact.json` having a complete
  "today" row — it now uses a `real_ds_last_complete` fixture that trims the
  dataset to the last day with non-null recovery/sleep/HRV, making the suite
  robust to the time of day / sync state it runs in.
- **Docs**: README rewrite (badges, "Why Vitals" pitch, demo-first quickstart,
  supported-sources table, architecture/tech-stack section), new
  `docs/ALGORITHMS.md` (recovery/strain/sleep/body-age/healthspan/impact-engine
  formulas and their honest limitations), `SECURITY.md`, issue/PR templates,
  `scripts/preflight_publish.sh` (fails the build if `.env`/`data/` are staged
  or a real-looking secret is found in tracked files), and a completed
  `.env.example` covering every key in `app/config.py`.

## Fase 8D — Competitive moat: labs, healthspan, household, iOS hardening

- `app/labs.py`: manual blood-test tracking — 20 biomarkers with sex-specific
  reference ranges, CRUD, CSV import, and injection into the coach's context.
- `app/healthspan.py`: monthly body-age-vs-chronological-age trend computed
  over trailing 90-day windows (reuses the existing body-age formula
  unchanged), with an annualized pace metric.
- `app/userctx.py`: household / multi-profile support — data now lives under
  `data/users/<uid>/`, with an idempotent migration from the legacy
  single-user layout and a profile switcher in the UI.
- iOS: ingest token moved to Keychain (migrated from UserDefaults),
  `BGAppRefreshTask` background sync, `X-Vitals-User` header threaded
  end-to-end for household mode.
- **Security fix** (caught in validation): a destructive path-traversal bug in
  `user_dir()` / `DELETE /api/users/{uid}` — a crafted `%2e%2e` uid could have
  triggered `rmtree()` on `data/` itself. Fixed via a single sanitization
  chokepoint (`_sanitize_uid`) plus 3 regression tests.
- +95 tests (1,309 passing), i18n audit clean.

## Fase 8C — AAA feel: interactive charts, push, Sleep Coach, offline, ingest token

- Interactive chart tooltips/scrubbing as a progressive-enhancement overlay on
  the existing SVG charts (no new charting library).
- Skeleton loaders and a reusable retry toast for failed requests.
- `app/notify.py`: push notifications via ntfy or Telegram (stdlib only) — a
  daily morning brief plus insight alerts, with dedupe.
- `app/sleep_coach.py`: recommended bedtime based on today's strain, sleep
  debt, and median wake time.
- Offline-first PWA: service worker caches `/api/data`; an offline banner
  shows the timestamp of the last-known-good data.
- `INGEST_TOKEN` became mandatory: auto-generated and persisted if missing
  from `.env`; `/api/ingest` and `/api/ecg` now 401 without a matching token
  (visible/copyable from the "More" tab).
- Fix: an aliasing bug in the partial merge of `notifications` on
  `PUT /api/profile`.
- +139 tests (1,214 passing), i18n audit clean.

## Fase 8B — Journal + Behavior Impact engine + narrative reports

- Habit journal: ~33 tracked habits across 5 categories (supplements get
  first-class treatment as their own category), binary yes/no per day, atomic
  persistence.
- Behavior Impact engine (`app/journal.py::analyze_journal`): Spearman
  correlation + Benjamini-Hochberg correction (reused from `app/drivers.py`,
  not duplicated), gated at ≥5 "yes" days / ≥5 "no" days / ≥15 total
  observations, reported as a delta-of-means plus an honest
  "association, not causation" headline.
- Narrative weekly/monthly reports generated via the local `claude` CLI, with
  a signature-based cache and a deterministic fallback when the CLI never runs.
- UI: new Journal card (Today tab), Habit Impact and Report cards (Trends tab),
  fully localized (ES/EN/FR/PT).
- 61 new tests (1,075 passing), i18n audit clean.
- Fix (preexisting bug caught during this phase): `trendBadge()` was shadowing
  the global `t()` i18n helper.
