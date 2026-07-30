# Changelog

All notable changes to Vitals are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Fusión de entrenamientos duplicados entre relojes + TRIMP recuperado (unreleased)

Bug visible en producción (usuario `default`, 2026-07-29; ver
`_dev-harness/fusion-workouts/ROADMAP.md`): cada sesión de entrenamiento
aparecía DOS veces en "Entrenamientos recientes" (una por reloj: HealthKit y
Google Health), varias con "—m" (sin duración) porque cada fuente traía solo
la MITAD del dato de la misma sesión.

1. **`_same_workout` (`app/merge.py`) reconoce más parejas como el mismo
   workout**: nueva regla_kcal (mismo `date`, `kcal` no-None e igual con
   tolerancia ±1, nombres/tipos "de la misma familia" vía `_names_equivalent`
   — normalizado de uno contiene al del otro, ej. "strengthtraining" contiene
   "strength") como OR de la regla vieja (`date` + `name` EXACTO + `dur_min`
   en ambos + |diff| <= 5, preservada sin tocar — los 4 tests que la pinnean
   siguen pasando sin modificarse).
2. **Fusión campo a campo en vez de descarte** (`_fuse_workouts`): al
   detectar match, ya NO se queda con la entrada "más completa" descartando
   la otra entera — funde cada campo (gana el valor no-None; en conflicto
   real, gana `SOURCE_PRIORITY`). Efecto de fondo: `trimp_session`
   (`app/load.py`) exige `dur_min` Y `avg_hr` juntos — hoy ninguna entrada por
   separado los trae ambos, así que el TRIMP de sesiones de fuerza NUNCA se
   computaba y el `strain` de esos días se subestimaba. Con la fusión, esas
   sesiones ahora sí producen TRIMP.
3. **`merge_info.by_metric.exercises.sources` adaptado** (criterio 9): antes
   identificaba contribución por `id(w)` de los dicts deduplicados contra las
   listas originales — dejaba de funcionar en cuanto la fusión empezó a crear
   dicts NUEVOS. `_merge_workouts` ahora devuelve también un mapa de
   procedencia explícito (qué fuentes aportaron a cada workout fundido) y
   `_contributing_sources_workouts` lo consume directo, sin re-ejecutar el
   dedup ni depender de identidad de objeto.
4. **Blindaje anti-falso-positivo**: dos sesiones REALES distintas del mismo
   tipo el mismo día (ej. las dos sesiones de fuerza del Doc del 29-jul:
   25min/83kcal y 75min/282kcal) NO se funden — el `kcal` distinto rompe la
   regla nueva. Nombres sin relación de familia (ej. "Tennis" vs "Yoga") con
   `kcal` casualmente igual tampoco se funden.
5. Fuera de alcance (sin tocar): `SOURCE_PRIORITY`, el merge de
   sleep/hrv/rhr/vo2/steps, `_finalize_exercises`/ventana de ejercicios,
   `compute_body_age`, `STRENGTH_RE`, el golden sintético
   (`tests/test_regression.py` — sus ejercicios no traen `kcal` ni `name`,
   ninguna regla nueva los toca).

## Ejercicios ya no se pierden: truncado por fecha + fuerza por presencia (unreleased)

Dos bugs verificados en producción (usuario `default`, 2026-07-29; ver
`_dev-harness/ejercicios-truncados/ROADMAP.md`): "la app dice que no tengo
ninguna sesión de fuerza esta semana, cuando hice pesas lunes, martes y
miércoles" — con 3 sesiones de fuerza REALES registradas en HealthKit.

1. **`build_dataset` ya no trunca ejercicios por posición** (`app/scoring.py`):
   `exercises[-40:]` cortaba por POSICIÓN una lista que `merge.py` concatena
   por FUENTE (`SOURCE_PRIORITY`), nunca por fecha — con ≥40 ejercicios
   recientes de una sola fuente (p.ej. Google/Fitbit), el bloque entero de
   otra fuente (HealthKit) quedaba fuera. Nuevo helper `_finalize_exercises`:
   filtra fecha ISO válida → recorta a `EXERCISE_WINDOW_DAYS = 60` días
   relativos a la fecha del ÚLTIMO `day` del dataset (nunca `date.today()`,
   motor puro) → ordena ascendente por fecha → aplica `EXERCISE_MAX_ENTRIES =
   200` como tope de seguridad DESPUÉS de ordenar (recorta lo más viejo, no
   reintroduce el bug). Afecta a todo lo que consume `dataset["exercises"]`:
   `compute_body_age`, `compute_healthspan`, `plan_store`, `changes.py`, coach
   card/chat e insights. El golden sintético (`tests/test_regression.py`) no
   se toca — sus 10 ejercicios ya están ordenados y dentro de la ventana.
2. **`strength_sessions(exercises, dates=None)`** (`app/load.py`, función
   nueva, aditiva): cuenta sesiones de fuerza (mismo `STRENGTH_RE`) SIN
   IMPORTAR si traen `dur_min` — separa PRESENCIA ("¿hubo fuerza?") de
   VOLUMEN ("¿cuántos minutos?", `strength_minutes`, sin cambio de contrato).
   Google/Fitbit mandan "Strength training" con `dur_min: None`;
   `strength_minutes` las sumaba como 0 y disparaba "cero fuerza" con
   sesiones reales registradas.
3. **Consumidores migrados a presencia** (revisados uno a uno, criterio 9):
   `rule_strength_gap` (`app/insights.py`), el recordatorio y el chip/bullet
   de fuerza de `coach.py` (build_coach + coach_card), y el gate del "← CERO"
   de `coach_chat.py` migran a `strength_sessions`. Los textos que citan
   minutos (`_goals_tracking` en `coach_chat.py`, la línea "Fuerza
   estructurada: X min" de la CARGA 7d) siguen citando minutos — no se
   inventa volumen no medido. `plan_store._auto_adherence_for_task` (kind
   `strength`) usa regla mixta: cumple si `strength_minutes >= params.min` O
   (`strength_sessions >= 1` Y `strength_minutes == 0`) — una sesión sin
   duración cuenta como cumplida, una CON duración insuficiente no se regala.

## Sin VO2máx medido NO hay edad corporal (unreleased)

Decisión explícita del dueño del producto (ver
`_dev-harness/vo2-sin-inventar/ROADMAP.md`): la regresión NTNU infla
sistemáticamente la edad fitness cuando no hay VO2 medido (Doc 37.15 medido
vs 52.7 estimado; Mariana 29.1 medido vs 41.6 estimado — ambos suben de
"Bajo" a "Excelente"). Si no se puede medir, ya NO se inventa: se muestra el
CTA para habilitarlo.

1. **Conteo por mediciones + ventana de validez** (`app/bodyage.py`): el VO2
   medido ahora cuenta por MEDICIONES deduplicadas (`round(valor, 2)`), no
   por entradas repetidas — reemplaza el umbral anterior (`>=3` entradas de
   las últimas 60, que premiaba al aparato que re-emite el mismo valor a
   diario). `MEASUREMENT_VALIDITY_DAYS = 180`: solo cuentan mediciones a
   ≤180 días de la fecha del último day del dataset (nunca `date.today()`).
   `confidence.vo2_measurements` (grupos) + `confidence.vo2_readings`
   (entradas crudas, diagnóstico) + `vo2_last_measured_date` (diagnóstico
   puro, independiente de si cuenta o no para el gate).
2. **`gate_unmeasured(ba)`** (`app/bodyage.py`, función nueva, separada de
   `compute_body_age` — el golden no se toca): con `vo2max_source !=
   "measured"` anula `vo2max`, `fitness_age`, `body_age`, `category`,
   `vo2max_percentile`, `vo2max_label`, `fitness_age_display`,
   `body_age_display`, `age_floored`, `penalty`, `body_age_stable`,
   `body_age_stable_display`, `pace` (todos `None`) + agrega
   `unavailable_reason: "no_vo2_measurement"`. Conserva `vo2max_estimated`,
   `vo2_last_measured_date`, `age`, `rhr`, `hrv`, `sleep_h`, `confidence`.
   Identidad si `vo2max_source == "measured"`.
3. **`sync.py`**: aplica el gate DESPUÉS de stable/healthspan/profile_note
   (para anular también esos campos), así todo lo persistido en
   `health_compact.json` ya viene gateado. Se elimina
   `_vo2_last_measured_date()` (lector redundante del ingest crudo de
   HealthKit) — la fecha ya sale de `days` vía `compute_body_age`.
4. **Healthspan honesto** (`app/healthspan.py`): `compute_healthspan` solo
   agrega puntos a la serie con `vo2max_source == "measured"` — con <2 puntos
   válidos, `None` (nunca inventa una tendencia con edades no respaldadas).
5. **Insight `vo2_unmeasured`** (info, `app/insights.py`): dispara cuando
   `unavailable_reason == "no_vo2_measurement"`, mutuamente excluyente con
   `fitness_age_gap` por construcción (esa regla exige `vo2max_source ==
   "measured"`). Chip sugerido `coach_q_vo2_unmeasured`.
6. **Coach** (`coach.py`, `coach_chat.py`): bullet alternativo "Edad
   corporal: no disponible" + línea explícita en el contexto del LLM para que
   RECOMIENDE la calibración en vez de inventar un número.
7. **UI** (`app-dashboard.js`, `app-i18n-helpers.js`): con
   `unavailable_reason`, `#fitnessAge`/`#bodyAge` muestran "—",
   `#baPaceRow`/`#bodyAgeBadge`/`#baPenalty` ocultos, `#baDetail` muestra el
   CTA (`ba_no_measurement` + `ba_last_measured` si hay fecha). La tarjeta
   métrica `bodyage` y `_renderFitnessDeep` ya no concatenan campos
   potencialmente `None` a pelo (antes renderizaban "undefined").
8. **Programa `vo2_boost`**: la tarea de calibración se renombra
   `task_walk_outdoor_calibrate` → `task_run_outdoor_calibrate` — caminar no
   activa la medición de VO2máx en el formato nuevo de Apple/Fitbit, hace
   falta correr/trotar 10 min al aire libre con GPS.
9. **MCP** (`app/mcp_tools.py::bodyage_summary`): propaga
   `unavailable_reason` cuando el gate está activo.

Fuera de alcance (sin cambios): la fórmula NTNU y sus constantes/percentiles
(`_VO2_NORMS`), el golden (`tests/fixtures/golden_synthetic.json`),
`merge.py`/`sources/*`/scoring de recovery-strain-sueño,
`compute_body_age_stable` (su mecánica de promediado), atribución de perfil,
motor de programas. Los 22 fails pre-existentes de `test_mcp_tools.py`
(dataset real ausente del repo) no se tocaron — verificados idénticos
(mismo conjunto de nombres, no solo el conteo) antes y después.

## Objetivo del coach: bajar la edad fitness (unreleased)

Cinco piezas aditivas (ver `_dev-harness/coach-objetivo-vo2/ROADMAP.md`) que
convierten el VO2 medido honesto-pero-duro (roadmap "edad-corporal-
credibilidad") en un objetivo accionable del coach, motivadas por el caso
real de Mariana (F/49, VO2 medido 29.1 → edad fitness 55-56).

1. **Insight `fitness_age_gap`**: dispara cuando `bodyage.vo2max_source ==
   "measured"` y la edad fitness cruda supera la real por >2 años —
   `app/insights.py::rule_fitness_age_gap`, calcado de `rule_strength_gap`.
   Factor adicional de staleness ("caminata outdoor para recalibrar") si el
   perfil conecta HealthKit y `vo2_last_measured_date` es `None` o tiene más
   de 45 días vs el último día del dataset. NO dispara con VO2 estimado, gap
   ≤2, o sin `bodyage` (dataset viejo).
2. **Chip sugerido** `coach_q_fitness_age_gap` ("¿Cómo bajo mi edad
   fitness?") vía el mismo mecanismo `INSIGHT_QUESTION_KEYS` de
   `app/coach_suggest.py` — cero lógica nueva.
3. **Programa `vo2_boost`** (28 días) en el catálogo de `app/programs.py`:
   zona 2 + 1 caminata outdoor de calibración por semana
   (`task_walk_outdoor_calibrate`) + el deporte del usuario como su día de
   intensidad (`task_play_sport`) + descansos.
4. **Contexto del coach** (`coach_chat.py::_build_context` y
   `coach.py::coach_card`, bullet de edad corporal): ahora menciona si el
   VO2 es medido (con percentil) o estimado y, si el gap fitness>real supera
   2 años, el objetivo explícito ("bajar edad fitness de X a Y"). Solo con
   VO2 medido — None-safe: datasets sin `vo2max_source` (previos a este
   roadmap) quedan byte-idénticos.
5. **`sync.py::_vo2_last_measured_date()`**: aditivo, best-effort TOTAL —
   lee la fecha de la última lectura "vo2" del ingest crudo de HealthKit
   (`app.sources.healthkit._ingest_path()`) y la escribe en
   `summary.bodyage.vo2_last_measured_date`. Sin archivo/clave/JSON válido →
   `None`, el sync nunca falla por esto.

Sin cambios en `bodyage.py`/`scoring.py`/`healthspan.py`/`trends.py`/
`merge.py`/`app/sources/*` ni en el motor de programas existente
(`task_for_day`, degradación light, `_acwr_is_caution`) — solo una entrada
nueva al catálogo. Golden y reglas de insights existentes intactos.

## Edad corporal — credibilidad (unreleased)

Cuatro mejoras aditivas al motor de edad corporal (ver
`_dev-harness/edad-corporal-credibilidad/ROADMAP.md`), disparadas por el
diagnóstico de dos usuarias reales (Mariana F/49, el Doc M/40) cuyo número
saturaba en el piso duro de 20 y perdía credibilidad:

1. **VO2 medido manda**: `build_dataset()` deja de ignorar el parámetro
   `vo2` (HealthKit/Google ya lo ingestan y `merge.py` ya lo fusiona, pero se
   tiraba). Con >=3 lecturas del reloj en las últimas 60 entradas,
   `compute_body_age()["vo2max"]` es la media medida en vez de la regresión
   NTNU (que queda de fallback etiquetado vía `vo2max_source`/
   `vo2max_estimated`/`confidence.vo2_readings`). Sin lecturas suficientes,
   comportamiento byte-idéntico a antes.
2. **Piso relativo de display**: nunca se muestra una edad >15 años menor
   que la cronológica — claves nuevas `fitness_age_display`,
   `body_age_display`, `body_age_stable_display`, `age_floored`. Los valores
   crudos (los que alimentan healthspan/series/golden) no se tocan.
3. **Pace robusto**: `app/healthspan.py`'s `pace` pasa de OLS (`linreg_slope`)
   a Theil-Sen (`app/trends.py::theil_sen_slope`, mediana de pendientes por
   pares — robusta a un solo gap ruidoso), clampeado a `[0.5, 1.5]`, `None`
   con <4 puntos de serie (antes OLS daba un valor con 3).
4. **Atribución de cambios de perfil**: si un PUT `/api/profile` mueve
   waist_cm/sex/birthdate y eso desplaza `body_age` >=2 años (recomputado
   sobre el MISMO dataset), se escribe `profile_impact.json` (TTL 14 días)
   que el siguiente sync inyecta como `summary.bodyage.profile_note` — la
   card lo muestra en vez de un salto silencioso de -9 años sin explicación.

Guard adicional en `changes.py::_check_vo2max`: si la fuente del vo2max
cambió entre syncs (estimated↔measured), se suprime el evento de mejora/
decline ese día (evita la falsa alarma "tu VO2 cayó 8 puntos" el día del
deploy). Golden (`test_regression.py`) intacto, sin regenerar el fixture.

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

`templates/vitals_ios.html`: **10,150 → 1,016 líneas** (esqueleto HTML puro,
bajo el objetivo de ~1,500). Se extrajo:
- El bloque `<style>` completo (1,541 líneas) → `static/css/vitals.css`.
- Los 3 bloques `<script>` inline restantes (~7,600 líneas de JS) →
  `static/js/sw-register.js` (registro del service-worker),
  `static/js/app-i18n-helpers.js` (`var STRINGS` ×4 locales + `t()` + helpers
  de i18n/unidades) y `static/js/app-dashboard.js` (lógica del dashboard).
  Las 9 asignaciones de datos inyectados (`var DB = __DATA__` … `var CYCLE =
  __CYCLE__`) permanecen en un bloque `<script>` inline mínimo, porque
  `render.py` las reemplaza por string; el resto del JS las lee como globales.
  Orden de carga preservado: bootstrap de datos → i18n/helpers → dashboard →
  scripts de features.

Los tests que verificaban presencia de funciones/constantes JS
(`tests/test_endpoints.py`, `tests/test_i18n.py`: `renderTend`, `sendCoach`,
`ORDER_SCOPES`, `var STRINGS`, constantes de conversión) se **adaptaron a la
nueva ubicación** — ahora verifican contra el JS externo en `static/js/`
en vez del HTML servido. Mismo contrato (paridad i18n ×4, presencia de
funciones), nueva ubicación del código; ninguna aserción se debilitó ni se
eliminó. Contrato de API sin cambios: los 7 endpoints `/api/*` del oráculo
golden quedan byte-idénticos (aislado HEAD-vs-working-tree); solo `GET /`
cambia, y solo por el `<script>`/`<link>` externalizados. 1,661 tests verdes.

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
