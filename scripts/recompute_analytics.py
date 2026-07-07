"""
recompute_analytics.py — Regenera los campos Tier 1 y Tier 2 en data/health_compact.json
sin necesitar OAuth ni re-sync completo.

Qué hace (en orden):
  (a) recovery_n por día: conteo de componentes reales que formaron el recovery,
      derivado de los campos presentes en cada día (hrv/rhr/asleep).
  (b) EWMA bases recientes: hrv_base_recent / rhr_base_recent en summary,
      calculados desde las series de días presentes.
  (c) bodyage extendido: llama a compute_body_age para añadir confidence,
      vo2max_percentile, vo2max_label al summary.bodyage existente.
  (d) [Tier 2] Rolling SDs: hrv_sd, rhr_sd, resp_sd, skin_temp_sd en summary
      (pstdev de las últimas ~30 lecturas no-None).
  (e) [Tier 2] TRIMP por día: TRIMP de Banister agregado de sesiones del día,
      guardado en day["trimp"] (solo días con ejercicios).
  (f) [Tier 2] ACWR en summary: ratio agudo:crónico 7d:28d sobre serie strain.
      summary["acwr"] y summary["acwr_zone"] (None si <14 días con strain).

Uso:
    cd ~/vitals-app
    python scripts/recompute_analytics.py [--data data/health_compact.json]
    # o para producción:
    python scripts/recompute_analytics.py --data /path/to/health_compact.json

    # Solo mostrar sin escribir:
    python scripts/recompute_analytics.py --dry-run

Después de correr, copiar el JSON a tu box de producción y reiniciar Vitals.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Asegurar que el path del proyecto esté en sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.scoring import _ewma_recent, _rolling_sd
from app.bodyage import compute_body_age
from app.load import trimp_session, acwr, acwr_zone


def _recompute_recovery_n(days: list) -> int:
    """
    Añade recovery_n a cada día que tenga recovery, derivado de los campos presentes.
    La lógica es: se cuentan los componentes que scoring.py usó para computar el recovery:
      - hrv presente → 1 comp
      - rhr presente → 1 comp
      - asleep presente → 1 comp
    Sólo añade recovery_n si el día ya tiene recovery (no infiere recovery nuevo).
    Devuelve el número de días modificados.
    """
    modified = 0
    for day in days:
        if "recovery" not in day:
            continue
        n = 0
        if day.get("hrv") is not None:
            n += 1
        if day.get("rhr") is not None:
            n += 1
        if day.get("asleep") is not None:
            n += 1
        # n == 0 no debería pasar (recovery requiere >=1 comp), pero lo manejamos
        if n > 0:
            day["recovery_n"] = n
            modified += 1
    return modified


def _build_series_from_days(days: list, field: str) -> dict:
    """Extrae {date: value} para un campo desde la lista de días."""
    return {
        d["date"]: d[field]
        for d in days
        if d.get(field) is not None and d.get("date")
    }


def _recompute_trimp(days: list, exercises: list, age: int, sex: str,
                     rhr_base_recent: Optional[float]) -> int:
    """
    Agrega TRIMP de Banister a cada día con ejercicios.

    Para cada día con sesiones:
      hr_rest = rhr del día → fallback rhr_base_recent → fallback 55.0
    Devuelve el número de días con trimp calculado.
    """
    rhr_by_date = {d["date"]: d["rhr"] for d in days if d.get("rhr") is not None}
    hr_rest_fallback = rhr_base_recent if rhr_base_recent is not None else 55.0

    # Agrupar ejercicios por fecha
    ex_by_date: dict = {}
    for ex in exercises:
        ex_date = ex.get("date")
        if ex_date:
            ex_by_date.setdefault(ex_date, []).append(ex)

    days_with_trimp = 0
    for day in days:
        day_date = day["date"]
        sessions = ex_by_date.get(day_date)
        if not sessions:
            continue
        hr_rest_today = rhr_by_date.get(day_date, hr_rest_fallback)
        total_trimp = 0.0
        valid = False
        for sess in sessions:
            t = trimp_session(
                dur_min=sess.get("dur_min"),
                avg_hr=sess.get("avg_hr"),
                hr_rest=hr_rest_today,
                age=age,
                sex=sex,
            )
            if t is not None:
                total_trimp += t
                valid = True
        if valid:
            day["trimp"] = round(total_trimp, 2)
            days_with_trimp += 1

    return days_with_trimp


def _recompute_acwr(days: list, summary: dict) -> None:
    """
    Calcula ACWR sobre los últimos 28 días de strain y actualiza summary.
    """
    strain_series = [d.get("strain") for d in days[-28:]]
    acwr_val = acwr(strain_series)
    acwr_zone_val = acwr_zone(acwr_val)
    summary["acwr"] = round(acwr_val, 3) if acwr_val is not None else None
    summary["acwr_zone"] = acwr_zone_val


def main():
    parser = argparse.ArgumentParser(
        description="Recomputa campos Tier 1 y Tier 2 en health_compact.json"
    )
    parser.add_argument(
        "--data",
        default=str(_REPO_ROOT / "data" / "health_compact.json"),
        help="Ruta al archivo health_compact.json (default: data/health_compact.json)",
    )
    parser.add_argument(
        "--age",  default=40, type=int,  help="Edad del usuario (default: 40)")
    parser.add_argument(
        "--waist", default=82, type=int, help="Cintura cm (default: 82)")
    parser.add_argument(
        "--sex",  default="M",          help="Sexo M/F (default: M)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Solo muestra los valores calculados, no escribe el archivo.",
    )
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: No se encontró el archivo: {data_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando: {data_path}")
    with open(data_path, encoding="utf-8") as f:
        dataset = json.load(f)

    days = dataset.get("days", [])
    summary = dataset.get("summary", {})
    exercises = dataset.get("exercises", [])

    if not days:
        print("ERROR: El dataset no tiene días. Verifica el archivo.", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(days)} días cargados, {len(exercises)} ejercicios.")

    # ── (a) recovery_n ─────────────────────────────────────────────────────────
    n_rec = _recompute_recovery_n(days)
    print(f"  (a) recovery_n: {n_rec} días actualizados.")

    # ── (b) EWMA bases recientes ────────────────────────────────────────────────
    hrv_series = _build_series_from_days(days, "hrv")
    rhr_series = _build_series_from_days(days, "rhr")

    hrv_base_recent = _ewma_recent(hrv_series)
    rhr_base_recent = _ewma_recent(rhr_series)

    print(f"  (b) hrv_base_recent: {hrv_base_recent}  (all-time: {summary.get('hrv_base')})")
    print(f"      rhr_base_recent: {rhr_base_recent}  (all-time: {summary.get('rhr_base')})")

    summary["hrv_base_recent"] = hrv_base_recent
    summary["rhr_base_recent"] = rhr_base_recent

    # ── (c) bodyage extendido ───────────────────────────────────────────────────
    bodyage_new = compute_body_age(days, exercises, age=args.age, waist=args.waist, sex=args.sex)

    print(f"  (c) vo2max_percentile: {bodyage_new.get('vo2max_percentile')} "
          f"({bodyage_new.get('vo2max_label')})")
    conf = bodyage_new.get("confidence", {})
    print(f"      confidence.level: {conf.get('level')} "
          f"(rhr={conf.get('rhr_days')}d / hrv={conf.get('hrv_days')}d / "
          f"sleep={conf.get('sleep_days')}d / ex={conf.get('exercise_sessions')})")

    summary["bodyage"] = bodyage_new

    # ── (d) [Tier 2] Rolling SDs ────────────────────────────────────────────────
    resp_series = _build_series_from_days(days, "resp")
    skin_series = _build_series_from_days(days, "skin_temp")

    hrv_sd = _rolling_sd(hrv_series)
    rhr_sd = _rolling_sd(rhr_series)
    resp_sd = _rolling_sd(resp_series)
    skin_temp_sd = _rolling_sd(skin_series)

    summary["hrv_sd"] = round(hrv_sd, 2) if hrv_sd is not None else None
    summary["rhr_sd"] = round(rhr_sd, 2) if rhr_sd is not None else None
    summary["resp_sd"] = round(resp_sd, 2) if resp_sd is not None else None
    summary["skin_temp_sd"] = round(skin_temp_sd, 2) if skin_temp_sd is not None else None

    print(f"  (d) SDs: hrv={summary['hrv_sd']} rhr={summary['rhr_sd']} "
          f"resp={summary['resp_sd']} skin_temp={summary['skin_temp_sd']}")

    # ── (e) [Tier 2] TRIMP por día ──────────────────────────────────────────────
    n_trimp = _recompute_trimp(days, exercises, age=args.age, sex=args.sex,
                               rhr_base_recent=rhr_base_recent)
    print(f"  (e) TRIMP: {n_trimp} días con TRIMP calculado.")

    # Mostrar muestra de TRIMPs
    trimp_days = [(d["date"], d["trimp"]) for d in days if d.get("trimp") is not None]
    if trimp_days:
        print(f"      Muestra (últimos 3): {trimp_days[-3:]}")

    # ── (f) [Tier 2] ACWR en summary ───────────────────────────────────────────
    _recompute_acwr(days, summary)
    print(f"  (f) ACWR: {summary.get('acwr')} → zona: {summary.get('acwr_zone')}")

    if args.dry_run:
        print("\n[dry-run] No se escribió el archivo. Campos calculados mostrados arriba.")
        return

    # ── Guardar ─────────────────────────────────────────────────────────────────
    dataset["summary"] = summary
    dataset["days"] = days

    out_text = json.dumps(dataset, ensure_ascii=False, indent=2)
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(out_text)

    print(f"\nGuardado: {data_path} ({len(out_text)//1024} KB)")
    print("Listo. Ahora puedes scp este JSON a tu box de producción y reiniciar Vitals.")


if __name__ == "__main__":
    main()
