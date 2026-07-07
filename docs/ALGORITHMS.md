# Algorithms

Vitals scores your data with plain, readable Python — no black box. This document
explains exactly how each metric is computed, and — just as important — what its
honest limitations are. If you disagree with a formula or threshold, the code
lives in `app/scoring.py`, `app/bodyage.py`, `app/healthspan.py`, `app/journal.py`,
`app/drivers.py`, and `app/insights.py`; PRs improving any of this are welcome
(see `CONTRIBUTING.md`).

None of this is medical advice or a diagnostic device. See the disclaimers
referenced throughout, especially in the illness-warning and cycle-tracking
sections.

---

## Recovery score

A weighted average of up to 3 components, each normalized to 0–100:

| Component | Weight | Normalization |
|---|---|---|
| HRV | 0.55 | `clamp((hrv - hlo) / (hhi - hlo) * 100, 0, 100)` |
| RHR | 0.25 | `clamp((rhi - rhr) / (rhi - rlo) * 100, 0, 100)` (inverted — lower RHR is better) |
| Sleep | 0.20 | `clamp(asleep / NEED * 100, 0, 100)`, `NEED` = `sleep_target_min` (default 480 min / 8h, configurable 300–600) |

```
recovery = round( Σ(value_i * weight_i) / Σ(weight_i) )
```

Weights renormalize over whatever components are actually present that day —
missing HRV, RHR, or sleep just drops that term, it doesn't zero it out.

**Rolling percentile ranges (`hlo`/`hhi`/`rlo`/`rhi`):** as of Ronda 5, these are
**rolling**, not global:

- Trailing 90-day window (past-only, to avoid look-ahead bias).
- ≥30 readings in the window → 5th/95th percentile of that window.
- 10–29 readings → 5th/95th percentile of the *entire* history up to that day.
- &lt;10 readings → fixed fallback ranges: HRV (40, 70), RHR (48, 60).
- Percentiles are computed by linear interpolation.

**Quality gate:** recovery is only computed if ≥2 of {HRV, RHR, sleep} are
present, OR exactly 1 is present but it isn't HRV/sleep clamping to an exact
extreme (a single-signal edge case that produces noisy 0s/100s).

**Nap detection:** a sleep record is discarded as a "night" (and none of its
fields are copied to the day) if `bed_min` falls outside [-300, 240] minutes
from midnight (roughly [19:00, 04:00)) or `asleep < 120` minutes.

### Limitations

- With &lt;10 rolling readings, recovery falls back to generic fixed ranges —
  it's meaningfully less personalized for new users.
- Because the ranges are rolling, the *same historical day* can recompute to a
  slightly different score depending on how much history existed at the time
  vs. now. This is a deliberate trade-off to avoid look-ahead bias, not a bug.
- The single-component clamp-extreme gate is a calibrated heuristic (tuned to
  suppress a specific class of spurious 0s/100s seen in real data), not a
  general statistical rule.

---

## Strain (v2 — hybrid TRIMP)

Daily load `L`:

```
L = trimp_day                     if a real TRIMP session exists that day
  + vigorous_min * F_VIG          ONLY if there's no trimp_day (avoids double count)
  + steps / F_STEPS                always, when steps are available (NEAT proxy)
```

`F_VIG = 2.5` (TRIMP-equivalent per "vigorous"/active-zone minute), `F_STEPS = 500.0`.

Compressed to the familiar 0–21 scale:

```
strain = round(21 * (1 - exp(-L / K)), 1),   K = 96.87
```

Requires at least one signal (TRIMP, vigorous minutes, or steps); with none,
`strain` is absent (not zero). `L ≤ 0` with a signal present → `strain = 0.0`.

TRIMP itself (Banister method, `app/load.py`) uses session duration, average
heart rate, resting HR (day's RHR → 30-day EWMA fallback → fixed 55.0), age,
and sex.

### Limitations — read this one

This is the metric with the least empirical grounding, and the code says so
directly:

- `F_VIG = 2.5` is documented in-code as a **starting assumption, not a
  measured constant** — the Doc's own real-world dataset has zero days with
  both TRIMP and Active Zone Minutes overlapping, so there was no way to
  regress this coefficient against real data.
- `K` was recalibrated once already: the original calibration (`K = 244.63`)
  was fit on a small 46-day local dataset and failed to generalize once the
  dataset grew to 394 days. The lesson captured in the code: *calibrate
  against the densest dataset available, not the local sandbox.* Expect this
  constant to keep moving as more real-world data accumulates.
- `F_STEPS = 500` is likewise labeled a roadmap starting point, not validated.

If you're relying on strain for serious training decisions, treat it as a
directional signal, not a lab-grade measurement.

---

## Sleep performance

```
sleep_perf = round(clamp(asleep / NEED * 100, 0, 100))
```

Same `NEED` (`sleep_target_min`) as recovery — a simple ratio against a single
target, with no adjustment for age, sex, or individual sleep-need variation
beyond what you set manually in your profile. Deep/REM/light breakdown is
shown in the UI but doesn't feed the score.

---

## Body age, fitness age, VO2max

**VO2max** — NTNU/Nes 2011 non-exercise regression formula:

- Men: `vo2 = 100.27 - 0.296*age + 0.226*PA - 0.369*waist - 0.155*rhr`
- Women: `vo2 = 74.736 - 0.247*age + 0.198*PA - 0.259*waist - 0.114*rhr`

**PA (Physical Activity Index) = fs + iss + ds**, each 0–5, over a trailing
28-day window:

- `fs` (frequency, unique exercise days / 4): 5 if freq≥5/wk, 4 if≥3, 3 if≥2, 2 if≥1, else 0
- `iss` (intensity, mean avg-HR): 5 if≥120bpm, 4 if≥105, 3 if≥90, 2 if&gt;0, else 0
- `ds` (duration, mean session length): 5 if≥60min, 4 if≥30, 3 if≥15, 2 if&gt;0, else 0

RHR/HRV/sleep for the formula are 14-day trailing means (RHR missing →
fallback 55.0; HRV/sleep missing → `None`, which weakens the downstream
penalty terms below).

**Fitness age:**

```
fitness_age = clamp((intercept - vo2) / 0.363, 20, 80)
intercept = 55.1 (M) / 49.0 (F)
```

**Body age** = fitness age + penalty (can only make you *older* than your
fitness age, never younger — a deliberate, conservative design choice):

- HRV penalty: if `hrv < expected_hrv` where `expected_hrv = 50 - 0.5*(age-20)`,
  add `min(5, (expected_hrv - hrv) / 5)` years.
- Sleep penalty: if `sleep_h < sleep_penalty_h` (default 7.0h, derived in
  production as `(sleep_target_min - 60) / 60` so it tracks your profile's
  sleep target), add `min(6, (sleep_penalty_h - sleep_h) * 2)` years.
- `body_age = clamp(fitness_age + hrv_penalty + sleep_penalty, 18, 90)`

**Category** (by absolute VO2max): Superior &gt;53, Excellent ≥48, Above average
≥43, Average ≥36, Below average &lt;36.

**VO2max percentile**: interpolated against Cooper Institute / ACSM Guidelines
(11th ed.) breakpoints at the 10th/25th/50th/75th/90th/95th percentile, by sex
and age decade (20s/30s/40s/50s/60s), with linear interpolation between
breakpoints and extrapolation outside the table, clamped to [1, 99].

**Confidence** (informational only, doesn't affect the score): `high` if the
minimum of {RHR days, HRV days, sleep days} in the 14-day window is ≥10,
`med` if ≥5, else `low`.

### Limitations

- This is a **non-clinical estimation formula** (submaximal, no actual exercise
  test) — a population regression from a Norwegian cohort study, not a
  substitute for a lab VO2max test.
- The percentile tables are aggregate population norms (Cooper/ACSM) bucketed
  by decade, not percentiles of your own personal history.
- `body_age` can only get worse from HRV/sleep relative to your fitness-age
  baseline, never better — this ignores real physiological upside from
  excellent HRV, by design (simplicity/conservatism over completeness).
- The HRV/sleep penalty window is only 14 days — sensitive to short streaks
  (a rough week can swing your body age meaningfully).
- `confidence` reflects data *coverage*, not the statistical validity of the
  underlying formula.

---

## Healthspan / pace of aging

Reuses `compute_body_age` **unchanged** — it's a retrospective recomputation
over historical windows, not an independent measurement.

- Trailing 90-day window, recomputed at a monthly (30-day) step.
- **Hard gate**: with less than 120 days of history, this returns nothing at
  all — the in-code rationale is that the trend wouldn't be honest with less
  data than that.
- For each monthly cutoff: recomputes chronological age at that date, calls
  `compute_body_age` on the data/exercise slice up to that cutoff, and stores
  `gap = body_age - chrono_age`.
- The most recent series point is always forced to the dataset's actual latest
  date (even if it doesn't land on the 30-day step), so the series never looks
  stale.
- **Pace of aging**: linear-regression slope of the `gap` series, annualized:
  `pace = round(1.0 + (slope_per_step / 30) * 365.25, 2)`. `pace < 1` means
  aging slower than the calendar; `pace > 1` means faster.
- **delta_quarter**: change in `gap` between the current point and the point
  ~3 steps back (~90 days), or the first point if there are fewer than 4.
- Needs ≥2 series points to return a result; never raises (degrades to `None`
  with a warning log).

### Limitations

- With &lt;120 days of history: intentionally no output — not a partial/noisy
  guess.
- The code itself flags a small bias from forcing an irregular last point when
  annualizing the slope, accepted for simplicity/auditability over a more
  complex (and less inspectable) model.
- Inherits every VO2max/body-age limitation above, multiplied across N
  historical windows — errors or noise in the base formula compound into the
  trend.

---

## Behavior Impact engine (Journal)

Finds which tracked habits actually move recovery/HRV/sleep, using Spearman
correlation (equivalent to point-biserial on ranks for a 0/1 habit variable)
with Benjamini-Hochberg (BH) multiple-comparisons correction — reused from
`app/drivers.py`, not reimplemented.

**Outcomes and lag**: `recovery` (lag 1 — next day), `hrv` (lag 1), `sleep_perf`
(lag 0 — same night).

**Gates before any test runs** (WHOOP-style): ≥5 "yes" days, ≥5 "no" days, ≥15
total paired observations. A day *with* a journal entry counts as a complete
observation — any habit not explicitly marked true that day counts as "no."
Days with no entry at all don't participate (unknown ≠ no).

**Statistics** (`app/drivers.py`):

- `_spearman`: Pearson correlation on ranks (average-rank tie handling);
  requires n≥3 and nonzero rank variance.
- `_pvalue`: normal approximation of the Student's t-test —
  `t = ρ·√((n-2)/(1-ρ²))`, `p = 2·(1-Φ(|t|))`.
- `_benjamini_hochberg(pvalues, alpha=0.05)`: real BH procedure (not a flat
  p-value cutoff) over the **m tests actually evaluated** (those that passed
  the gate) — sorts ascending, finds the largest k where
  `p_(k) ≤ (k/m)·alpha`, and only those k survive.

Findings that survive BH also report `delta = mean(outcome | yes) - mean(outcome | no)`
(the human-readable number), a strength label (`|ρ|≥0.4` strong, `≥0.3`
moderate, else weak), and an i18n headline explicitly framed as
**"association, not causation."** Sorted by `|ρ|`, top 8.

Catalog: 33 fixed habits across 5 categories (supplements, consumption,
recovery/mind, sleep routine, context) plus up to 20 user-defined custom habits.

`app/drivers.py` runs the same machinery for a separate, non-journal set of 8
built-in drivers (bedtime consistency, sleep duration, strain, steps, active
minutes, vs. HRV/recovery), with its own thresholds: `MIN_N = 25`,
`MIN_ABS_RHO = 0.2`, top 5.

### Limitations — the most important one in this document

Quoted directly from `_pvalue()`'s own docstring: the normal-approximation
t-test is *"reasonable for n≥25 but does NOT model serial autocorrelation
between consecutive observations of physiological time series — under
autocorrelation, these p-values are optimistic (they underestimate the real
uncertainty)."*

In plain terms: **correlation is not causation, and the reported p-values
likely understate how uncertain these findings really are**, because
consecutive days of physiological data are not statistically independent —
today's HRV is correlated with yesterday's HRV regardless of any habit. The
engine partially mitigates this (BH correction across multiple tests, a hard
`|ρ| ≥ 0.2` floor, keeping only the top-K), but modeling the *effective* sample
size under autocorrelation is explicitly out of scope for v1.

Other honest caveats:

- Gates are a floor, not a large-sample guarantee — 5 "yes"/5 "no"/15 total
  (journal) or n≥25 (drivers) are the minimum to run a test at all, not a
  robust sample size. With realistic usage, most habits never accumulate
  enough days to trigger analysis in the first place.
- The engine is correlational by design — headlines are deliberately worded as
  "associated with," never "causes," because there's no randomization or
  confounder control. A third, unmeasured factor could explain any finding.
- The "no" convention (unmarked = no) is a design choice, not verified absence
  — a user who forgets to log can introduce a systematic under-reporting bias
  toward "no."

---

## Insight rules (illness/overtraining/sleep-debt alerts)

13 deterministic rules, zero LLM involvement — every threshold is a plain
`if`. Highlights:

- **Illness early warning**: z-score per signal (|z| &gt; 1.5) over a 14-day
  window when enough variance data exists, falling back to fixed absolute
  thresholds otherwise (RHR &gt; baseline+5, HRV &lt; baseline×0.85, skin temp &gt;
  14-day mean+0.5°C, respiratory rate &gt; 14-day mean+1.5, SpO₂ &lt; 92%).
  `alert` severity requires elevated temperature plus ≥2 other signals;
  `watch` for temperature alone or ≥2 non-temperature signals.
- **SpO₂ low**: &lt;90% on any of the last 7 nights → `alert`.
- **Sleep debt**: a "short night" is `sleep_target_min - 60` (default 420 min
  / 7h). ≥3/7 short nights → `watch`; ≥5/7 → `alert`.
- **Overtraining**: 7-day average strain &gt; 14 **and** (7-day recovery is ≥10
  points below the 30-day average, **or** ≥2 days below 34% recovery in the
  last 7) → `watch`.
- **Recovery declining**: 30-day minus 7-day recovery ≥8 points → `watch`
  (suppressed if `overtraining` already fired, to avoid duplicate alerts).
- **Bedtime inconsistency**: standard deviation of bedtime over 21 days &gt; 75
  minutes → `watch`.
- **Strength gap**: zero real strength-training minutes in the last 7 days
  (only fires if *some* exercise was logged in that window) → `info`.
- **Positive HRV / positive sleep**: reward rules that fire on genuine
  improving trends, not just absence of problems.
- **Cycle-related rules** (phase, period approaching, delay, perimenopause
  signal): fully gated behind the opt-in cycle-tracking toggle; with it off,
  or without enough data, they never fire. All carry an explicit
  "not a diagnosis" disclaimer.

Each rule runs independently and is wrapped so a failure in one never takes
down the others; results are ranked (alert → fresh → watch → positive → info)
and capped at 5 shown at once.

### Limitations

- Z-score and absolute thresholds are tuned heuristics ("reasonable"), not
  clinically validated diagnostic cutoffs.
- With degenerate variance data (a flat window, or fewer than 3 points), rules
  fall back to the less-sensitive absolute thresholds.
- The illness-warning and female-health rules carry explicit non-diagnostic
  disclaimers in the UI and i18n strings — this is not a medical device.
