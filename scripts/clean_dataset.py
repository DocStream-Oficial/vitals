"""
scripts/clean_dataset.py — Limpia el health_compact.json existente aplicando
las mismas reglas de higiene que scoring.py (sin necesitar la API).

Reglas:
1. Recovery=0 espurio (solo HRV, sin rhr ni asleep):
   Si comps = {hrv} y len(comps) < 2 → eliminar key 'recovery' del día.
2. Siesta-como-noche:
   Si bed_min > 240 (onset > 04:00) o bed_min < -300 (onset < 19:00) O asleep < 120
   → eliminar todos los campos de sueño del día.

Reescribe data/health_compact.json en sitio de forma atómica (tmp + os.replace).
"""
import json
import os
import sys
from pathlib import Path

# ── Parámetros (idénticos a scoring.py) ──────────────────────────────────────
_NAP_BED_MIN_LO = -300
_NAP_BED_MIN_HI =  240
_NAP_MIN_ASLEEP = 120

_SLEEP_FIELDS = ("asleep", "inbed", "awake", "deep", "rem", "light", "eff",
                 "bedtime", "waketime", "bed_min", "sleep_perf")


def _is_nap(day: dict) -> bool:
    """True si el día tiene señales de siesta como noche."""
    bm = day.get("bed_min")
    asleep = day.get("asleep") or 0
    if bm is not None and (bm > _NAP_BED_MIN_HI or bm < _NAP_BED_MIN_LO):
        return True
    if asleep < _NAP_MIN_ASLEEP and any(day.get(f) is not None for f in _SLEEP_FIELDS):
        return True
    return False


def _has_recovery_only_hrv(day: dict) -> bool:
    """True si el día tiene recovery=0 espurio (solo HRV, sin rhr ni asleep)."""
    if day.get("recovery") != 0:
        return False
    # Chequear que solo tenga HRV como componente real
    has_hrv = day.get("hrv") is not None
    has_rhr = day.get("rhr") is not None
    has_asleep = day.get("asleep") is not None
    # Si recovery=0 y tiene <2 componentes de {hrv, rhr, asleep} → espurio
    comps = sum([has_hrv, has_rhr, has_asleep])
    return comps < 2


def clean_day(day: dict) -> dict:
    """Limpia un día: quita recovery espurio y/o campos de siesta."""
    day = dict(day)  # copia

    # 1. Limpiar campos de siesta
    if _is_nap(day):
        for f in _SLEEP_FIELDS:
            day.pop(f, None)

    # 2. Limpiar recovery espurio — REGLA QUIRÚRGICA (igual que scoring.py):
    #    quitar recovery SOLO si es de 1 sola señal (re-evaluado tras limpiar siesta)
    #    Y clampeó a un extremo (0 o 100). Conserva los recovery razonables de HRV-sola.
    has_hrv = day.get("hrv") is not None
    has_rhr = day.get("rhr") is not None
    has_asleep = day.get("asleep") is not None
    comps = sum([has_hrv, has_rhr, has_asleep])
    if comps == 1 and day.get("recovery") in (0, 100) and "recovery" in day:
        del day["recovery"]

    return day


def main():
    data_path = Path(__file__).resolve().parent.parent / "data" / "health_compact.json"
    if not data_path.exists():
        print(f"ERROR: {data_path} no existe.", file=sys.stderr)
        sys.exit(1)

    data = json.loads(data_path.read_text(encoding="utf-8"))
    days_orig = data.get("days", [])

    cleaned_days = []
    removed_recovery = []
    cleaned_sleep = []

    for day in days_orig:
        orig = dict(day)
        clean = clean_day(day)
        cleaned_days.append(clean)

        # Registro de cambios
        if "recovery" in orig and "recovery" not in clean:
            removed_recovery.append(clean["date"])
        sleep_removed = [f for f in _SLEEP_FIELDS if f in orig and f not in clean]
        if sleep_removed:
            cleaned_sleep.append((clean["date"], orig.get("bedtime"), orig.get("asleep"),
                                  orig.get("bed_min"), sleep_removed[:3]))

    data["days"] = cleaned_days

    # Escritura atómica
    tmp = data_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, data_path)

    print(f"health_compact.json limpiado. {len(days_orig)} días totales.")
    print(f"  recovery espurio eliminado: {len(removed_recovery)} días → {removed_recovery}")
    print(f"  campos de sueño eliminados (siesta): {len(cleaned_sleep)} días")
    for date, bt, asleep, bm, fields in cleaned_sleep:
        print(f"    {date} | bedtime={bt} | asleep={asleep} | bed_min={bm} | campos: {fields}...")

    # Verificación final
    z0 = [x for x in cleaned_days if x.get("recovery") == 0]
    atyp = [x for x in cleaned_days
            if x.get("bed_min") is not None
            and (x["bed_min"] < -240 or x["bed_min"] > 300)]
    print(f"\nVerificación post-limpieza:")
    print(f"  recovery=0 restantes: {len(z0)} (esperado: 0)")
    print(f"  bed_min atípicos (<-240 o >300) restantes: {len(atyp)} (esperado: 0)")
    if z0:
        for x in z0:
            print(f"    ALERTA {x['date']}: hrv={x.get('hrv')}, rhr={x.get('rhr')}, asleep={x.get('asleep')}")
    if atyp:
        for x in atyp:
            print(f"    ALERTA {x['date']}: bed_min={x.get('bed_min')}, bedtime={x.get('bedtime')}, asleep={x.get('asleep')}")


if __name__ == "__main__":
    main()
