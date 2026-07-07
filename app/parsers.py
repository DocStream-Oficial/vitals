"""
parsers.py — portado EXACTO de vitals_sync.py.
find_date, find_number, metric_obj, _to_local, parse_daily,
parse_sleep, parse_exercises, parse_hr_zones — sin cambios de lógica.
"""
import json
import datetime


# ---------------------------------------------------------------- utilidades

def find_date(obj):
    """Busca recursivamente un {'year','month','day'} y lo vuelve 'YYYY-MM-DD'."""
    if isinstance(obj, dict):
        if {"year", "month", "day"} <= set(obj):
            return f"{obj['year']:04d}-{obj['month']:02d}-{obj['day']:02d}"
        for v in obj.values():
            r = find_date(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_date(v)
            if r:
                return r
    return None


def find_number(obj, skip_keys=("year", "month", "day", "hours", "minutes", "seconds", "nanos", "count")):
    """Devuelve el primer valor numérico 'de medida' (ignora componentes de fecha/hora)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    pass
            r = find_number(v, skip_keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_number(v, skip_keys)
            if r is not None:
                return r
    return None


def metric_obj(dp):
    """De un dataPoint, regresa el sub-objeto de medida (no name/dataSource)."""
    for k, v in dp.items():
        if k not in ("name", "dataSource") and isinstance(v, dict):
            return v
    return dp


def _to_local(iso_z, offset_str):
    """'2026-06-24T06:23:00Z' + '-21600s' -> datetime en hora local."""
    if not iso_z or "T" not in iso_z:
        return None
    try:
        dt = datetime.datetime.strptime(iso_z[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    secs = 0
    if offset_str:
        try:
            secs = int(str(offset_str).rstrip("s"))
        except ValueError:
            secs = 0
    return dt + datetime.timedelta(seconds=secs)


# ---------------------------------------------------------------- parsers

def parse_daily(datapoints, value_hint=None, prefer="FITBIT"):
    """Tipos diarios -> {date: value}. Deduplica por día prefiriendo una plataforma.

    Tu cuenta junta Fitbit (Inspire 2) y Apple Watch (HEALTH_KIT); cada noche puede
    traer 2 lecturas. Nos quedamos con UNA por día para que la tendencia sea coherente.
    """
    if prefer == "AUTO":
        from collections import Counter
        c = Counter(dp.get("dataSource", {}).get("platform") for dp in datapoints)
        prefer = c.most_common(1)[0][0] if c else "FITBIT"
    by_date = {}  # date -> (is_preferred, value)
    for dp in datapoints:
        plat = dp.get("dataSource", {}).get("platform")
        m = metric_obj(dp)
        d = find_date(m) or find_date(dp)
        val = None
        if value_hint:
            for k, v in m.items():
                if k == "date":
                    continue
                if value_hint.lower() in k.lower():
                    try:
                        val = float(v)
                        break
                    except (ValueError, TypeError):
                        pass
        if val is None:
            val = find_number(m)
        if d is None or val is None:
            continue
        pref = 1 if plat == prefer else 0
        cur = by_date.get(d)
        if cur is None or pref > cur[0]:
            by_date[d] = (pref, val)
    return {d: round(v, 2) for d, (pref, v) in by_date.items()}


# F2 roadmap P0 (hipnograma): mapa de etapas granulares de Google Health
# (sleep.stages[].type) al formato canónico de app.sleep_segments. El payload
# real (save_name="sleep", registros type="STAGES" de Fitbit) trae un timeline
# por etapa con startTime/endTime — evidencia verificada en
# data/users/default/vitals_raw/sleep.json. Tipos del sueño "CLASSIC"
# (ASLEEP/RESTLESS) u otros desconocidos NO se mapean: ante cualquier tipo
# fuera de estos 4, el record entero queda SIN segments (no se inventa nada).
_GOOGLE_STAGE_MAP = {"AWAKE": "awake", "LIGHT": "light", "DEEP": "deep", "REM": "rem"}


def _segments_from_google_stages(stages, start_local):
    """sleep.stages[] de Google Health -> segments [{s, e, st}] en minutos
    relativos a start_local (bedtime). None si no hay stages, si algún tipo no
    mapea a las 4 etapas canónicas, o si el resultado no valida — noches sin
    timeline quedan byte-igual que antes de F2. Nunca lanza."""
    try:
        if not isinstance(stages, list) or not stages or start_local is None:
            return None
        raw = []
        for st in stages:
            if not isinstance(st, dict):
                return None
            stage = _GOOGLE_STAGE_MAP.get(st.get("type"))
            if stage is None:
                return None  # tipo desconocido (CLASSIC/RESTLESS/...) -> sin segments
            s_local = _to_local(st.get("startTime", ""), st.get("startUtcOffset", "0s"))
            e_local = _to_local(st.get("endTime", ""), st.get("endUtcOffset", "0s"))
            if s_local is None or e_local is None:
                return None
            # Boundaries redondeados a minuto entero (la granularidad real de
            # Fitbit es de 30s); stages consecutivos comparten el boundary, así
            # que redondear cada uno igual nunca produce traslapes.
            s_min = int(round((s_local - start_local).total_seconds() / 60))
            e_min = int(round((e_local - start_local).total_seconds() / 60))
            if e_min <= s_min:
                continue  # stage de <30s colapsado por el redondeo -> se omite
            raw.append((s_min, e_min, stage))
        if not raw:
            return None
        raw.sort(key=lambda t: t[0])
        # Colapsar runs consecutivos de la misma etapa (Fitbit a veces parte
        # una etapa en dos stages contiguos).
        segs = []
        for s_min, e_min, stage in raw:
            if segs and segs[-1]["st"] == stage and segs[-1]["e"] == s_min:
                segs[-1]["e"] = e_min
            else:
                segs.append({"s": max(0, s_min), "e": e_min, "st": stage})
        from app.sleep_segments import validate_segments
        return validate_segments(segs)
    except Exception:
        return None


def parse_sleep(datapoints, prefer="FITBIT"):
    if prefer == "AUTO":
        from collections import Counter
        c = Counter(dp.get("dataSource", {}).get("platform") for dp in datapoints)
        prefer = c.most_common(1)[0][0] if c else "FITBIT"
    best = {}  # date -> (rank_tuple, rec)
    for dp in datapoints:
        s = dp.get("sleep") or metric_obj(dp)
        plat = dp.get("dataSource", {}).get("platform")
        interval = s.get("interval", {})
        # Convertir UTC -> hora local usando el offset (ej. "-21600s" = -6h México)
        start_local = _to_local(interval.get("startTime", ""), interval.get("startUtcOffset", "0s"))
        end_local   = _to_local(interval.get("endTime", ""),   interval.get("endUtcOffset", "0s"))
        date = end_local.strftime("%Y-%m-%d") if end_local else None
        summ = s.get("summary", {})
        stages = {x["type"]: int(x["minutes"]) for x in summ.get("stagesSummary", [])}
        asleep = int(summ.get("minutesAsleep", 0) or 0)
        inbed = int(summ.get("minutesInSleepPeriod", 0) or 0) or (asleep + int(summ.get("minutesAwake", 0) or 0))
        rec = {
            "asleep": asleep, "inbed": inbed,
            "awake": int(summ.get("minutesAwake", 0) or 0),
            "deep": stages.get("DEEP"), "rem": stages.get("REM"),
            "light": stages.get("LIGHT"),
            "eff": round(asleep / inbed * 100, 1) if inbed else None,
            "bedtime": start_local.strftime("%H:%M") if start_local else None,
            "waketime": end_local.strftime("%H:%M") if end_local else None,
        }
        if start_local:
            m = start_local.hour * 60 + start_local.minute
            rec["bed_min"] = m if m < 720 else m - 1440
        # F2 roadmap P0: segments OPCIONAL desde el timeline granular de
        # stages[] (solo records type="STAGES"; sin timeline o con tipos
        # desconocidos, el rec queda byte-igual que antes).
        segments = _segments_from_google_stages(s.get("stages"), start_local)
        if segments:
            rec["segments"] = segments
        if date:
            pref = 1 if plat == prefer else 0
            rank = (asleep, pref)   # la noche MÁS LARGA (sesión principal) gana; la plataforma solo desempata
            # (antes era (pref, asleep): un fragmento de la plataforma preferida le ganaba a la noche completa de otra)
            cur = best.get(date)
            if cur is None or rank > cur[0]:
                best[date] = (rank, rec)
    return {d: rec for d, (rank, rec) in best.items()}


def daily_rollup(datatype, token, start_date, end_date, key, subfields, save_name=None):
    """POST :dailyRollUp — delegado a health_api para evitar duplicación."""
    from app.health_api import daily_rollup as _rollup
    return _rollup(datatype, token, start_date, end_date, key, subfields, save_name)


def parse_exercises(dps):
    """Sesiones de entrenamiento -> lista de {date, name, dur_min, kcal, avg_hr, azm, start}."""
    out = []
    for dp in dps:
        e = dp.get("exercise") or metric_obj(dp)
        iv = e.get("interval", {})
        start = _to_local(iv.get("startTime", ""), iv.get("startUtcOffset", "0s"))
        date = start.strftime("%Y-%m-%d") if start else None
        if not date:
            continue
        ms = e.get("metricsSummary", {})
        dur = e.get("activeDuration", "0s")
        try:
            dur_min = round(int(str(dur).rstrip("s")) / 60)
        except (ValueError, TypeError):
            dur_min = None

        def gi(k):
            v = ms.get(k)
            try:
                return round(float(v))
            except (ValueError, TypeError):
                return None

        out.append({
            "date": date,
            "name": e.get("displayName") or e.get("exerciseType") or "Ejercicio",
            "type": e.get("exerciseType"),
            "dur_min": dur_min,
            "kcal": gi("caloriesKcal"),
            "avg_hr": gi("averageHeartRateBeatsPerMinute"),
            "azm": gi("activeZoneMinutes"),
            "start": start.strftime("%H:%M") if start else None,
        })
    out.sort(key=lambda x: (x["date"], x["start"] or ""))
    return out


def parse_hr_zones(dps, prefer="FITBIT"):
    """daily-heart-rate-zones -> {date: {fatburn, cardio, peak}} (minutos)."""
    by_date = {}
    for dp in dps:
        plat = dp.get("dataSource", {}).get("platform")
        m = metric_obj(dp)
        d = find_date(m) or find_date(dp)
        if not d:
            continue
        # busca zonas con minutos; estructura puede variar -> recorrer
        zones = {"fatburn": None, "cardio": None, "peak": None}

        def scan(o):
            if isinstance(o, dict):
                name = json.dumps(o).lower()
                for key, label in [("fat", "fatburn"), ("cardio", "cardio"), ("peak", "peak")]:
                    if key in name:
                        for k, v in o.items():
                            if "min" in k.lower():
                                try:
                                    zones[label] = round(float(v))
                                except (ValueError, TypeError):
                                    pass
                for v in o.values():
                    scan(v)
            elif isinstance(o, list):
                for v in o:
                    scan(v)

        scan(m)
        pref = 1 if plat == prefer else 0
        cur = by_date.get(d)
        if cur is None or pref > cur[0]:
            by_date[d] = (pref, zones)
    return {d: z for d, (p, z) in by_date.items()}
