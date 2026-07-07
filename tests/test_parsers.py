"""
test_parsers.py — Tests de app/parsers.py, foco F2 roadmap P0 (hipnograma):
parse_sleep / _segments_from_google_stages sobre payloads de Google Health.

Evidencia real inspeccionada (roadmap paso 12, ver informe final): el fixture
data/users/default/vitals_raw/sleep.json (Fitbit vía Google Health Connect)
contiene registros type="STAGES" con sleep.stages[] granular
(startTime/endTime por etapa AWAKE/LIGHT/DEEP/REM) Y registros type="CLASSIC"
cuyo stages[] trae un único tramo type="ASLEEP" (sin desglose de etapas) —
solo stagesSummary agregado. Los fixtures de este archivo replican esa forma
real (sintéticos, sin datos personales).
"""
from __future__ import annotations

from app.parsers import parse_sleep, _segments_from_google_stages, _to_local


def _dp_stages(start_time, end_time, stages, platform="FITBIT"):
    return {
        "dataSource": {"platform": platform},
        "sleep": {
            "interval": {
                "startTime": start_time, "startUtcOffset": "-21600s",
                "endTime": end_time, "endUtcOffset": "-21600s",
            },
            "type": "STAGES",
            "stages": stages,
            "summary": {
                "minutesAsleep": "90",
                "minutesInSleepPeriod": "100",
                "minutesAwake": "10",
                "stagesSummary": [
                    {"type": "LIGHT", "minutes": "50"},
                    {"type": "DEEP", "minutes": "20"},
                    {"type": "REM", "minutes": "20"},
                ],
            },
        },
    }


def _stage(stage_type, start, end):
    return {
        "startTime": start, "startUtcOffset": "-21600s",
        "endTime": end, "endUtcOffset": "-21600s",
        "type": stage_type,
    }


# ── _segments_from_google_stages ─────────────────────────────────────────────

def test_segments_from_stages_granular_timeline():
    start_local = _to_local("2026-07-05T08:32:00Z", "-21600s")
    stages = [
        _stage("AWAKE", "2026-07-05T08:32:00Z", "2026-07-05T08:35:00Z"),
        _stage("LIGHT", "2026-07-05T08:35:00Z", "2026-07-05T08:44:00Z"),
        _stage("DEEP", "2026-07-05T08:44:00Z", "2026-07-05T08:57:00Z"),
        _stage("REM", "2026-07-05T08:57:00Z", "2026-07-05T09:10:00Z"),
    ]
    segs = _segments_from_google_stages(stages, start_local)
    assert segs is not None
    assert [s["st"] for s in segs] == ["awake", "light", "deep", "rem"]
    assert segs[0]["s"] == 0
    assert segs[0]["e"] == 3
    assert segs[-1]["e"] == 38


def test_segments_from_stages_classic_single_asleep_type_returns_none():
    """type=CLASSIC: stages[] con un único tramo 'ASLEEP' (evidencia real) ->
    None, NO se inventa un segmento — 'ASLEEP' no está en el mapa de 4 etapas
    canónicas."""
    start_local = _to_local("2026-06-28T07:01:00Z", "-21600s")
    stages = [_stage("ASLEEP", "2026-06-28T07:01:00Z", "2026-06-28T13:03:00Z")]
    assert _segments_from_google_stages(stages, start_local) is None


def test_segments_from_stages_collapses_consecutive_same_stage():
    start_local = _to_local("2026-07-05T08:00:00Z", "-21600s")
    stages = [
        _stage("LIGHT", "2026-07-05T08:00:00Z", "2026-07-05T08:10:00Z"),
        _stage("LIGHT", "2026-07-05T08:10:00Z", "2026-07-05T08:20:00Z"),
        _stage("DEEP", "2026-07-05T08:20:00Z", "2026-07-05T08:30:00Z"),
    ]
    segs = _segments_from_google_stages(stages, start_local)
    assert segs == [{"s": 0, "e": 20, "st": "light"}, {"s": 20, "e": 30, "st": "deep"}]


def test_segments_from_stages_no_stages_returns_none():
    start_local = _to_local("2026-07-05T08:00:00Z", "-21600s")
    assert _segments_from_google_stages([], start_local) is None
    assert _segments_from_google_stages(None, start_local) is None


def test_segments_from_stages_no_start_local_returns_none():
    stages = [_stage("LIGHT", "2026-07-05T08:00:00Z", "2026-07-05T08:10:00Z")]
    assert _segments_from_google_stages(stages, None) is None


def test_segments_from_stages_sub_minute_stage_omitted_no_crash():
    """Un stage de <30s se colapsa a e_min<=s_min tras redondear -> se omite
    ese tramo pero no rompe el resto del parseo."""
    start_local = _to_local("2026-07-05T08:00:00Z", "-21600s")
    stages = [
        _stage("LIGHT", "2026-07-05T08:00:00Z", "2026-07-05T08:00:10Z"),
        _stage("DEEP", "2026-07-05T08:00:10Z", "2026-07-05T08:20:00Z"),
    ]
    segs = _segments_from_google_stages(stages, start_local)
    assert segs is not None
    assert segs[0]["st"] == "deep"


def test_segments_from_stages_never_raises_on_garbage():
    start_local = _to_local("2026-07-05T08:00:00Z", "-21600s")
    assert _segments_from_google_stages("garbage", start_local) is None
    assert _segments_from_google_stages([{"type": "LIGHT"}], start_local) is None
    assert _segments_from_google_stages([None], start_local) is None


# ── parse_sleep (integración) ────────────────────────────────────────────────

def test_parse_sleep_attaches_segments_for_stages_type():
    stages = [
        _stage("AWAKE", "2026-07-05T08:32:00Z", "2026-07-05T08:35:00Z"),
        _stage("LIGHT", "2026-07-05T08:35:00Z", "2026-07-05T09:44:00Z"),
        _stage("DEEP", "2026-07-05T09:44:00Z", "2026-07-05T09:57:00Z"),
        _stage("REM", "2026-07-05T09:57:00Z", "2026-07-05T10:10:00Z"),
    ]
    dp = _dp_stages("2026-07-05T08:32:00Z", "2026-07-05T10:10:00Z", stages)
    result = parse_sleep([dp])
    assert len(result) == 1
    rec = list(result.values())[0]
    assert "segments" in rec
    assert rec["segments"][0]["st"] == "awake"


def test_parse_sleep_classic_type_has_no_segments_key():
    """Registro CLASSIC (solo stagesSummary agregado, sin desglose de etapas
    en el timeline real) -> rec sin 'segments' — byte-igual a antes de F2."""
    dp = _dp_stages("2026-06-28T07:01:00Z", "2026-06-28T13:03:00Z",
                     [_stage("ASLEEP", "2026-06-28T07:01:00Z", "2026-06-28T13:03:00Z")])
    result = parse_sleep([dp])
    rec = list(result.values())[0]
    assert "segments" not in rec
    # El resto de campos (asleep/deep/rem/light desde stagesSummary) intacto.
    assert rec["asleep"] == 90


def test_parse_sleep_no_stages_field_has_no_segments_key():
    """dataPoint sin 'stages' en absoluto -> sin segments, no crashea."""
    dp = _dp_stages("2026-07-05T08:00:00Z", "2026-07-05T09:40:00Z", [])
    dp["sleep"].pop("stages")
    result = parse_sleep([dp])
    rec = list(result.values())[0]
    assert "segments" not in rec
    assert rec["asleep"] == 90


def test_parse_sleep_scores_identical_with_or_without_segments():
    """El campo segments no debe alterar NINGÚN otro campo del rec (asleep,
    deep, rem, light, eff, bedtime, waketime, bed_min) — regresión #1 del
    roadmap: segments es aditivo puro."""
    stages_with_timeline = [
        _stage("AWAKE", "2026-07-05T08:32:00Z", "2026-07-05T08:35:00Z"),
        _stage("LIGHT", "2026-07-05T08:35:00Z", "2026-07-05T09:44:00Z"),
        _stage("DEEP", "2026-07-05T09:44:00Z", "2026-07-05T09:57:00Z"),
        _stage("REM", "2026-07-05T09:57:00Z", "2026-07-05T10:10:00Z"),
    ]
    dp_with = _dp_stages("2026-07-05T08:32:00Z", "2026-07-05T10:10:00Z", stages_with_timeline)
    dp_without = _dp_stages("2026-07-05T08:32:00Z", "2026-07-05T10:10:00Z", [])
    dp_without["sleep"].pop("stages")

    rec_with = list(parse_sleep([dp_with]).values())[0]
    rec_without = list(parse_sleep([dp_without]).values())[0]

    for key in ("asleep", "inbed", "awake", "deep", "rem", "light", "eff",
                "bedtime", "waketime", "bed_min"):
        assert rec_with[key] == rec_without[key], f"campo {key} difiere"
