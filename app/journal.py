"""
journal.py — Journal de hábitos + Behavior Impact engine (Fase 8B, pasos B1/B2)
+ Journal cuantitativo (Roadmap P2, F9).

Journal BINARIO (v1): sí/no por hábito por día, catálogo fijo (HABITS, ~33
specs en 5 categorías con los suplementos como categoría de primera clase) +
hábitos custom del usuario.

Journal CUANTITATIVO (v2, F9): 3 hábitos existentes (alcohol/meditation/
breathwork) ganan spec de cantidad opcional (`"quantity": {"unit_key", "max"}`
en su dict de HABITS, ver QUANTIFIABLE_HABITS) — el VALOR guardado pasa a ser
un número (copas/minutos) en vez de solo bool. Backward-compat DELIBERADO: el
gate sí/no del impact engine existente (analyze_journal) sigue funcionando
SIN NINGÚN cambio, porque la truthiness de Python ya trata `bool(0)`=False y
`bool(N>0)`=True — un valor numérico guardado en vez de un bool binario no
altera en absoluto cómo _pair_habit_outcome lee `entry.get(habit)`. Ver
`analyze_journal_dose_response` para el motor NUEVO y aditivo que sí usa la
cantidad (pool de corrección estadística SEPARADO del de analyze_journal).

Persistencia: data/journal_log.json — patrón EXACTO de cycle.py:
    {entries: {"YYYY-MM-DD": {key: bool|float}}, custom: [{key,label}], updated}
load/save NUNCA lanzan; escritura atómica vía fsutil.atomic_write_text.

Impact engine (analyze_journal): correlación honesta hábito→biometría.
Estadística IMPORTADA de app/drivers.py (_spearman, _pvalue,
_benjamini_hochberg) — NO duplicada. Spearman sobre x binario (0/1) equivale a
point-biserial de rangos. Se reporta además `delta` = media(outcome|sí) −
media(outcome|no), que es lo legible. Gate WHOOP-style: ≥5 días "sí" Y ≥5 "no"
Y n_total ≥ 15. BH global sobre los tests efectivamente evaluados (patrón
analyze_drivers). Lag=1 para outcomes de mañana (recovery/hrv), lag=0 para
sleep_perf de esa noche. Headlines i18n ×4, tono "ASOCIACIÓN, no causa".

Semántica de "no": un día CON entry en el journal es una observación completa —
todo hábito no marcado true ese día cuenta como "no" (0). Días sin entry no
participan (desconocido ≠ no). Documentado también en la UI (empty state).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from app.drivers import _spearman, _pvalue, _benjamini_hochberg
from app.i18n import tr

logger = logging.getLogger("vitals.journal")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_JOURNAL_LOG_FILE = _DATA_DIR / "journal_log.json"

# ── Catálogo fijo (B1) ───────────────────────────────────────────────────────
# Labels: claves i18n `habit_<key>` ×4 en app/i18n.py. La UI agrupa por
# categoría (suplementos como sección visible, no mezclados).

CATEGORIES = ("supplements", "consumption", "recovery_mind", "sleep_routine", "context")

HABITS: list[dict] = [
    # Suplementos (primera clase)
    {"key": "creatine",      "category": "supplements"},
    {"key": "magnesium",     "category": "supplements"},
    {"key": "melatonin",     "category": "supplements"},
    {"key": "omega3",        "category": "supplements"},
    {"key": "vitamin_d_supp","category": "supplements"},
    {"key": "zinc",          "category": "supplements"},
    {"key": "ashwagandha",   "category": "supplements"},
    {"key": "protein_supp",  "category": "supplements"},
    {"key": "multivitamin",  "category": "supplements"},
    {"key": "electrolytes",  "category": "supplements"},
    {"key": "collagen",      "category": "supplements"},
    {"key": "probiotics",    "category": "supplements"},
    # Consumo
    {"key": "alcohol",       "category": "consumption",
     "quantity": {"unit_key": "unit_drinks", "max": 20}},
    {"key": "alcohol_heavy", "category": "consumption"},  # binario, SIN cambio (criterio 14)
    {"key": "caffeine_late", "category": "consumption"},
    {"key": "late_meal",     "category": "consumption"},
    {"key": "big_dinner",    "category": "consumption"},
    {"key": "hydration_low", "category": "consumption"},
    {"key": "fasting",       "category": "consumption"},
    # Recuperación / mente
    {"key": "meditation",    "category": "recovery_mind",
     "quantity": {"unit_key": "unit_minutes", "max": 240}},
    {"key": "breathwork",    "category": "recovery_mind",
     "quantity": {"unit_key": "unit_minutes", "max": 120}},
    {"key": "sauna",         "category": "recovery_mind"},
    {"key": "cold_exposure", "category": "recovery_mind"},
    {"key": "stretching",    "category": "recovery_mind"},
    {"key": "nap_today",     "category": "recovery_mind"},
    {"key": "stress_high",   "category": "recovery_mind"},
    # Sueño / rutina
    {"key": "screen_bed",    "category": "sleep_routine"},
    {"key": "reading_bed",   "category": "sleep_routine"},
    {"key": "late_workout",  "category": "sleep_routine"},
    {"key": "shared_bed",    "category": "sleep_routine"},
    {"key": "sunlight_am",   "category": "sleep_routine"},
    # Contexto
    {"key": "travel",        "category": "context"},
    {"key": "sick",          "category": "context"},
]

HABIT_KEYS = {h["key"] for h in HABITS}

# ── Journal cuantitativo (Roadmap P2, F9, paso 6) ────────────────────────────
# QUANTIFIABLE_HABITS: {key: {"unit_key": ..., "max": N}} derivado de HABITS al
# cargar el módulo — los 3 hábitos con spec de cantidad (alcohol/meditation/
# breathwork). `alcohol_heavy` NO está aquí: sigue binario a propósito (no hay
# conflicto de producto con `alcohol` cuantitativo — son preguntas distintas:
# "¿cuántas copas?" vs "¿tomaste en exceso (binge) hoy?").
QUANTIFIABLE_HABITS: dict = {
    h["key"]: h["quantity"] for h in HABITS if isinstance(h.get("quantity"), dict)
}

# ── Impact engine — constantes (B2) ──────────────────────────────────────────

# outcome → lag. recovery/hrv son "de mañana" (lag 1); sleep_perf es de ESA
# noche (lag 0: el hábito de hoy afecta el sueño de hoy-noche, que el dataset
# registra en el MISMO día calendario del despertar... ver nota en informe).
OUTCOMES: list[tuple[str, int]] = [
    ("recovery", 1),
    ("hrv", 1),
    ("sleep_perf", 0),
]

MIN_YES = 5      # ≥5 días "sí"
MIN_NO = 5       # ≥5 días "no"
MIN_TOTAL = 15   # Y n_total ≥ 15
TOP_K = 8        # TOP 8 por |ρ|


def _journal_log_path() -> Path:
    """Ruta a journal_log.json del usuario activo (Fase 8D, paso D3:
    household). Fuera de un request household-aware (is_context_active()=
    False — tests preexistentes que monkeypatchean _JOURNAL_LOG_FILE
    directamente, scripts), usa _JOURNAL_LOG_FILE tal cual: comportamiento
    idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "journal_log.json"
    except Exception:
        pass
    return _JOURNAL_LOG_FILE


# ── Persistencia atómica (patrón cycle.py — nunca lanza) ────────────────────

def _empty_journal() -> dict:
    return {"entries": {}, "custom": [], "updated": None}


def load_journal() -> dict:
    """Lee data/journal_log.json → {entries, custom, updated}.
    Si no existe o está corrupto → estructura vacía (nunca lanza)."""
    empty = _empty_journal()
    try:
        path = _journal_log_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("journal_log.json no es dict; usando estructura vacía.")
            return empty
        data.setdefault("entries", {})
        data.setdefault("custom", [])
        data.setdefault("updated", None)
        if not isinstance(data.get("entries"), dict):
            data["entries"] = {}
        if not isinstance(data.get("custom"), list):
            data["custom"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("journal_log.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo journal_log.json: %s", exc)
        return empty


def save_journal(d: dict) -> None:
    """Guarda journal_log.json con escritura ATÓMICA (fsutil.atomic_write_text).
    Nunca lanza excepción (loguea en error)."""
    try:
        from app.fsutil import atomic_write_text
        path = _journal_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("entries", {})
        d.setdefault("custom", [])
        d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando journal_log.json: %s", exc)


# ── set/get de entries ───────────────────────────────────────────────────────

def _valid_date(s: Any) -> Optional[datetime.date]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def valid_habit_keys(journal: Optional[dict] = None) -> set:
    """Claves válidas: catálogo fijo + customs del journal dado (o del disco)."""
    j = journal if journal is not None else load_journal()
    keys = set(HABIT_KEYS)
    for c in j.get("custom") or []:
        if isinstance(c, dict) and isinstance(c.get("key"), str):
            keys.add(c["key"])
    return keys


def _coerce_quantity_value(v: Any, max_val: float) -> float:
    """Coerción de un valor entrante para una clave de QUANTIFIABLE_HABITS
    (criterio 15): acepta número (clamp a [0, max]) O bool legacy (True->1,
    False->0, para clientes viejos que sigan mandando booleano — backward-
    compat con cualquier cliente que no sepa todavía de cantidades). Nunca
    lanza — cualquier tipo raro degrada a 0.0 (equivalente a 'no marcado')."""
    try:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        num = float(v)
        if num < 0:
            return 0.0
        if num > max_val:
            return max_val
        return num
    except (TypeError, ValueError):
        return 0.0


def set_entry(date: str, habits: dict) -> dict:
    """Fusiona `habits` {key: bool|float} en la entry de esa fecha y persiste.
    MERGE (no replace): togglear un chip no borra los demás del día.

    Roadmap P2 (F9, paso 6/criterio 15): para claves en QUANTIFIABLE_HABITS
    (alcohol/meditation/breathwork) — acepta número (clamp a [0, max] de su
    spec) O bool legacy (True->1, False->0). Para TODAS las demás claves —
    comportamiento IDÉNTICO al de antes (`bool(v)`, sin excepción): este es
    el criterio de backward-compat — entradas viejas (solo bool, en
    cualquier hábito) siguen leyéndose y escribiéndose exactamente igual.

    Claves no validadas aquí (el endpoint valida contra el catálogo).
    Devuelve la entry resultante. Nunca lanza."""
    try:
        j = load_journal()
        entries = j.get("entries") or {}
        entry = dict(entries.get(date) or {})
        for k, v in (habits or {}).items():
            if not isinstance(k, str):
                continue
            quantity_spec = QUANTIFIABLE_HABITS.get(k)
            if quantity_spec is not None:
                entry[k] = _coerce_quantity_value(v, quantity_spec["max"])
            else:
                entry[k] = bool(v)  # comportamiento IDÉNTICO al actual, sin excepción
        entries[date] = entry
        j["entries"] = entries
        save_journal(j)
        return entry
    except Exception as exc:
        logger.error("set_entry falló: %s", exc)
        return {}


def get_entry(date: str) -> dict:
    """Entry {key: bool} de esa fecha ({} si no hay). Nunca lanza."""
    try:
        j = load_journal()
        entry = (j.get("entries") or {}).get(date)
        return dict(entry) if isinstance(entry, dict) else {}
    except Exception as exc:
        logger.error("get_entry falló: %s", exc)
        return {}


# ── Hábitos custom ───────────────────────────────────────────────────────────

_CUSTOM_MAX = 20
_CUSTOM_LABEL_MAX = 40


def _slugify(label: str) -> str:
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:32] or "habit"


def add_custom_habit(label: str) -> Optional[dict]:
    """Alta de hábito custom {key, label}. key = 'custom_<slug>' (única).
    Devuelve el hábito creado, o el existente si el label ya estaba.
    None si el label es inválido o se alcanzó el tope. Nunca lanza."""
    try:
        label = (label or "").strip()[:_CUSTOM_LABEL_MAX]
        if not label:
            return None
        j = load_journal()
        custom = [c for c in (j.get("custom") or []) if isinstance(c, dict)]
        for c in custom:
            if (c.get("label") or "").lower() == label.lower():
                return c  # idempotente
        if len(custom) >= _CUSTOM_MAX:
            return None
        base = "custom_" + _slugify(label)
        key = base
        i = 2
        existing = {c.get("key") for c in custom} | HABIT_KEYS
        while key in existing:
            key = f"{base}_{i}"
            i += 1
        habit = {"key": key, "label": label}
        custom.append(habit)
        j["custom"] = custom
        save_journal(j)
        return habit
    except Exception as exc:
        logger.error("add_custom_habit falló: %s", exc)
        return None


# ── Catálogo localizado (para GET /api/journal) ──────────────────────────────

def catalog(journal: Optional[dict] = None, locale: str = "es") -> list[dict]:
    """Catálogo completo [{key, category, label, custom, quantity?}] con
    labels localizados (habit_<key> ×4) + customs (label libre del usuario).

    Roadmap P2 (F9, paso 9): los 3 hábitos de QUANTIFIABLE_HABITS ganan la
    clave ADITIVA `quantity` con la unidad YA TRADUCIDA (`unit_key` resuelto
    vía tr()) + `max` — así el frontend no necesita su propio mapa de
    unit_key->texto, solo pinta lo que ya viene localizado."""
    j = journal if journal is not None else load_journal()
    out = []
    for h in HABITS:
        entry = {
            "key": h["key"],
            "category": h["category"],
            "label": tr("habit_" + h["key"], locale),
            "custom": False,
        }
        quantity_spec = QUANTIFIABLE_HABITS.get(h["key"])
        if quantity_spec is not None:
            entry["quantity"] = {
                "unit": tr(quantity_spec["unit_key"], locale),
                "max": quantity_spec["max"],
            }
        out.append(entry)
    for c in j.get("custom") or []:
        if isinstance(c, dict) and isinstance(c.get("key"), str):
            out.append({
                "key": c["key"],
                "category": "custom",
                "label": str(c.get("label") or c["key"]),
                "custom": True,
            })
    return out


# ── Impact engine (B2) — PURO, sin I/O ───────────────────────────────────────

def _pair_habit_outcome(days: list, entries: dict, habit: str,
                        outcome: str, lag: int) -> list[tuple[float, float]]:
    """Pares (0/1, outcome) alineados por FECHA (helper propio inspirado en
    drivers.pair_lagged — índice por fecha, robusto a huecos, no por posición).

    Un día participa si: tiene entry en el journal (observación completa;
    hábito no marcado = 0 "no") Y el día fecha+lag existe en el dataset con
    outcome no-None."""
    date_to_day = {}
    for d in days or []:
        dt = d.get("date")
        if dt:
            date_to_day[dt] = d

    pairs: list[tuple[float, float]] = []
    for date_str, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            continue
        t_date = _valid_date(date_str)
        if t_date is None:
            continue
        try:
            lag_date = (t_date + datetime.timedelta(days=lag)).isoformat()
        except Exception:
            continue
        lag_day = date_to_day.get(lag_date)
        if lag_day is None:
            continue
        y = lag_day.get(outcome)
        if y is None:
            continue
        x = 1.0 if entry.get(habit) else 0.0
        try:
            pairs.append((x, float(y)))
        except (TypeError, ValueError):
            continue
    return pairs


def _habit_label(habit_key: str, journal: dict, locale: str) -> str:
    """Label localizado del hábito (catálogo) o label libre (custom)."""
    if habit_key in HABIT_KEYS:
        return tr("habit_" + habit_key, locale)
    for c in (journal or {}).get("custom") or []:
        if isinstance(c, dict) and c.get("key") == habit_key:
            return str(c.get("label") or habit_key)
    return habit_key


def _outcome_name(outcome: str, locale: str) -> str:
    if outcome == "hrv":
        return tr("outcome_hrv", locale)
    if outcome == "recovery":
        return tr("outcome_recovery", locale)
    if outcome == "sleep_perf":
        return tr("outcome_sleep_perf", locale)
    return outcome


def _journal_headline(habit_key: str, outcome: str, lag: int, rho: float,
                      n: int, journal: dict, locale: str) -> str:
    """Headline i18n reusando el patrón de drivers._build_headline (tono
    ASOCIACIÓN, no causa): «hábito (sí)» se asocia con {outcome más alto/bajo}
    {lag} (ρ=…, n=…)."""
    rho_sign = "+" if rho > 0 else "−"
    rho_str = f"ρ={rho_sign}{abs(rho):.2f}, n={n}"

    outcome_name = _outcome_name(outcome, locale)
    if rho > 0:
        outcome_dir = tr("outcome_higher", locale, outcome_name=outcome_name)
    else:
        outcome_dir = tr("outcome_lower", locale, outcome_name=outcome_name)

    lag_str = tr("lag_same_day", locale) if lag == 0 else tr("lag_next_day", locale)

    dl = tr("journal_habit_dl", locale, habit=_habit_label(habit_key, journal, locale))
    return tr("headline_pattern", locale, dl=dl, outcome_dir=outcome_dir,
              lag_str=lag_str, rho_str=rho_str)


def analyze_journal(days: list, journal: Optional[dict], locale: str = "es") -> list[dict]:
    """Impact engine: para cada (hábito, outcome) con gate ≥5 sí / ≥5 no /
    n≥15, Spearman (+p) importados de drivers, BH global sobre los tests
    efectivamente evaluados, delta de medias, headline i18n.

    Devuelve SOLO los findings que sobreviven BH (con ruido puro → []),
    ordenados por |ρ| desc, TOP 8. Cada finding:
        {habit, outcome, lag, n_yes, n_no, delta, rho, p, significant,
         headline, strength}
    PURO (sin I/O). Nunca lanza — degrada a []."""
    try:
        journal = journal or {}
        entries = journal.get("entries") or {}
        days = days or []
        if not entries or not days:
            return []

        habit_keys: list[str] = [h["key"] for h in HABITS]
        for c in journal.get("custom") or []:
            if isinstance(c, dict) and isinstance(c.get("key"), str):
                habit_keys.append(c["key"])

        # ── Paso 1: candidatos (gate + rho/p calculables) ────────────────────
        candidates = []
        for habit in habit_keys:
            for outcome, lag in OUTCOMES:
                pairs = _pair_habit_outcome(days, entries, habit, outcome, lag)
                n_yes = sum(1 for x, _ in pairs if x == 1.0)
                n_no = sum(1 for x, _ in pairs if x == 0.0)
                n_total = len(pairs)
                if n_yes < MIN_YES or n_no < MIN_NO or n_total < MIN_TOTAL:
                    continue
                result = _spearman(pairs)
                if result is None:
                    continue
                rho, n = result
                p = _pvalue(rho, n)
                if p is None:
                    continue
                yes_vals = [y for x, y in pairs if x == 1.0]
                no_vals = [y for x, y in pairs if x == 0.0]
                delta = (sum(yes_vals) / len(yes_vals)) - (sum(no_vals) / len(no_vals))
                candidates.append({
                    "habit": habit, "outcome": outcome, "lag": lag,
                    "n_yes": n_yes, "n_no": n_no, "n": n,
                    "rho": rho, "p": p, "delta": delta,
                })

        # ── Paso 2: BH global sobre los m tests EFECTIVAMENTE evaluados ──────
        pvalues = [c["p"] for c in candidates]
        survives_bh = _benjamini_hochberg(pvalues, alpha=0.05)

        findings = []
        for cand, survived in zip(candidates, survives_bh):
            if not survived:
                continue
            rho, n, p = cand["rho"], cand["n"], cand["p"]

            abs_rho = abs(rho)
            if abs_rho >= 0.4:
                strength = tr("strength_strong", locale)
            elif abs_rho >= 0.3:
                strength = tr("strength_moderate", locale)
            else:
                strength = tr("strength_weak", locale)

            findings.append({
                "habit": cand["habit"],
                "habit_label": _habit_label(cand["habit"], journal, locale),
                "outcome": cand["outcome"],
                "lag": cand["lag"],
                "n_yes": cand["n_yes"],
                "n_no": cand["n_no"],
                "n": n,
                "delta": round(cand["delta"], 1),
                "rho": round(rho, 2),
                "p": round(p, 4),
                "significant": True,  # sobrevivió BH
                "headline": _journal_headline(cand["habit"], cand["outcome"],
                                              cand["lag"], rho, n, journal, locale),
                "strength": strength,
            })

        findings.sort(key=lambda f: abs(f["rho"]), reverse=True)
        return findings[:TOP_K]
    except Exception as exc:
        logger.warning("analyze_journal falló (degradando a []): %s", exc)
        return []


# ── Dosis-respuesta (Roadmap P2, F9, paso 8) — PURO, pool de BH SEPARADO ─────
# Motor NUEVO y ADITIVO: "¿la CANTIDAD del hábito importa?" — pregunta
# conceptualmente distinta de analyze_journal ("¿el hábito en sí afecta?").
# Se corre en un pool de corrección BH propio (documentado en el roadmap
# §Arquitectura F9: mezclar ambos pools infla artificialmente el número de
# comparaciones y puede ocultar señal real en cualquiera de los dos grupos).

DOSE_MIN_TOTAL = 15         # n>=15 (mismo umbral de tamaño que analyze_journal)
DOSE_MIN_DISTINCT_VALUES = 3  # >=3 valores distintos de cantidad (si todos
                              # pusieron siempre "2 copas", no hay variación
                              # que correlacionar)


def _pair_habit_outcome_dose(days: list, entries: dict, habit: str,
                              outcome: str, lag: int) -> list:
    """Pares (cantidad, outcome) alineados por fecha — mismo mecanismo de
    alineación por fecha+lag que _pair_habit_outcome, pero SOLO cuenta días
    donde el valor guardado es NUMÉRICO REAL (int/float que NO sea bool):
    un bool legacy True/False (cliente viejo, o hábito togglead antes de F9)
    no aporta cantidad — esos días NO participan en el pool de dosis (criterio
    18), aunque sí cuenten para el gate sí/no de analyze_journal más arriba.

    isinstance(v, bool) se chequea ANTES que isinstance(v, (int, float)) —
    en Python `bool` es subclase de `int`, así que sin este orden un True/
    False colaría como 1/0 "numérico real", contaminando el pool de dosis con
    observaciones que en realidad no tienen cantidad conocida."""
    date_to_day = {}
    for d in days or []:
        dt = d.get("date")
        if dt:
            date_to_day[dt] = d

    pairs = []
    for date_str, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            continue
        t_date = _valid_date(date_str)
        if t_date is None:
            continue
        raw_val = entry.get(habit)
        if raw_val is None or isinstance(raw_val, bool):
            continue  # ausente o bool legacy -> sin cantidad real, no cuenta
        if not isinstance(raw_val, (int, float)):
            continue
        try:
            lag_date = (t_date + datetime.timedelta(days=lag)).isoformat()
        except Exception:
            continue
        lag_day = date_to_day.get(lag_date)
        if lag_day is None:
            continue
        y = lag_day.get(outcome)
        if y is None:
            continue
        try:
            pairs.append((float(raw_val), float(y)))
        except (TypeError, ValueError):
            continue
    return pairs


def _dose_headline(habit_key: str, outcome: str, lag: int, rho: float,
                    n: int, journal: dict, locale: str) -> str:
    """Headline de dosis-respuesta — MISMO idioma que _journal_headline
    (dirección + fuerza + ρ/n), pero con el "dl" (descripción de la
    izquierda) hablando de CANTIDAD, no de sí/no (criterio 18: nunca "por
    cada copa baja X" — Spearman no da pendiente por unidad)."""
    rho_sign = "+" if rho > 0 else "−"
    rho_str = f"ρ={rho_sign}{abs(rho):.2f}, n={n}"

    outcome_name = _outcome_name(outcome, locale)
    if rho > 0:
        outcome_dir = tr("outcome_higher", locale, outcome_name=outcome_name)
    else:
        outcome_dir = tr("outcome_lower", locale, outcome_name=outcome_name)

    lag_str = tr("lag_same_day", locale) if lag == 0 else tr("lag_next_day", locale)

    dl = tr("journal_habit_dose_dl", locale, habit=_habit_label(habit_key, journal, locale))
    return tr("headline_pattern", locale, dl=dl, outcome_dir=outcome_dir,
              lag_str=lag_str, rho_str=rho_str)


def analyze_journal_dose_response(days: list, journal: Optional[dict],
                                   locale: str = "es") -> list[dict]:
    """Motor de dosis-respuesta: para cada hábito en QUANTIFIABLE_HABITS ×
    outcome (misma lista OUTCOMES de analyze_journal), evalúa si la CANTIDAD
    reportada (no solo sí/no) correlaciona con el outcome.

    Gate (criterio 18): n>=15 días con cantidad numérica real Y >=3 valores
    distintos de cantidad (evita "correlacionar" una serie constante, ej.
    todos los días con "2 copas" — sin varianza en x, Spearman ya degrada a
    None, pero el gate explícito de valores distintos es más legible/rápido
    de razonar que esperar a que _spearman() rechace varianza cero).

    Spearman+p+BH en un POOL DE CORRECCIÓN SEPARADO del de analyze_journal
    (ver docstring de la sección arriba). Devuelve SOLO los findings que
    sobreviven BH, ordenados por |ρ| desc, TOP 8. Cada finding:
        {habit, habit_label, outcome, lag, n, n_distinct_values, rho, p,
         significant, headline, strength}
    PURO (sin I/O). Nunca lanza — degrada a []."""
    try:
        journal = journal or {}
        entries = journal.get("entries") or {}
        days = days or []
        if not entries or not days or not QUANTIFIABLE_HABITS:
            return []

        candidates = []
        for habit in QUANTIFIABLE_HABITS.keys():
            for outcome, lag in OUTCOMES:
                pairs = _pair_habit_outcome_dose(days, entries, habit, outcome, lag)
                n_total = len(pairs)
                distinct_values = {x for x, _ in pairs}
                if n_total < DOSE_MIN_TOTAL or len(distinct_values) < DOSE_MIN_DISTINCT_VALUES:
                    continue
                result = _spearman(pairs)
                if result is None:
                    continue
                rho, n = result
                p = _pvalue(rho, n)
                if p is None:
                    continue
                candidates.append({
                    "habit": habit, "outcome": outcome, "lag": lag,
                    "n": n, "n_distinct_values": len(distinct_values),
                    "rho": rho, "p": p,
                })

        # BH en pool SEPARADO del de analyze_journal (criterio del roadmap).
        pvalues = [c["p"] for c in candidates]
        survives_bh = _benjamini_hochberg(pvalues, alpha=0.05)

        findings = []
        for cand, survived in zip(candidates, survives_bh):
            if not survived:
                continue
            rho, n, p = cand["rho"], cand["n"], cand["p"]

            abs_rho = abs(rho)
            if abs_rho >= 0.4:
                strength = tr("strength_strong", locale)
            elif abs_rho >= 0.3:
                strength = tr("strength_moderate", locale)
            else:
                strength = tr("strength_weak", locale)

            findings.append({
                "habit": cand["habit"],
                "habit_label": _habit_label(cand["habit"], journal, locale),
                "outcome": cand["outcome"],
                "lag": cand["lag"],
                "n": n,
                "n_distinct_values": cand["n_distinct_values"],
                "rho": round(rho, 2),
                "p": round(p, 4),
                "significant": True,  # sobrevivió BH
                "headline": _dose_headline(cand["habit"], cand["outcome"],
                                           cand["lag"], rho, n, journal, locale),
                "strength": strength,
            })

        findings.sort(key=lambda f: abs(f["rho"]), reverse=True)
        return findings[:TOP_K]
    except Exception as exc:
        logger.warning("analyze_journal_dose_response falló (degradando a []): %s", exc)
        return []
