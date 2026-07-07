"""
sleep_segments.py — Formato canónico + helpers de segmentos de sueño
(hipnograma). Roadmap P0-launch-gaps, F2.

Formato de un segmento: {"s": <min desde bedtime>, "e": <min>, "st": etapa},
con st en {"awake", "light", "rem", "deep"}. Minutos relativos a bedtime (no
timestamps ISO absolutos): compacto en JSON, inmune a cruces de medianoche,
directo de mapear a coordenadas SVG (ver roadmap, trade-off descartado).

Módulo PURO (sin I/O). Usado por los parsers de fuente (Oura, HealthKit,
Google Health) para sanear/validar segments antes de adjuntarlos al rec de
sueño de una noche, y por la UI (espejo en JS de `awakenings()`).
"""
from __future__ import annotations

from typing import Any, Optional

_VALID_STAGES = frozenset({"awake", "light", "rem", "deep"})


def validate_segments(raw: Any) -> Optional[list]:
    """Sanea una lista candidata de segmentos crudos.

    Reglas (cualquier violación -> None, se descarta TODO el campo segments,
    nunca una lista parcial silenciosa que podría malinterpretarse como
    completa):
      - `raw` debe ser una lista no vacía.
      - Cada elemento debe ser un dict con claves "s", "e", "st".
      - "s" y "e" deben ser int (o float entero) >= 0, con e > s.
      - "st" debe ser una de las 4 etapas válidas.
      - Los segmentos, ordenados por "s", no deben traslaparse (el "s" de
        cada segmento debe ser >= "e" del anterior).

    Devuelve una NUEVA lista (ordenada por "s", con s/e normalizados a int)
    en caso válido, o None si algo no cuadra. Nunca lanza.
    """
    try:
        if not isinstance(raw, list) or not raw:
            return None

        cleaned = []
        for item in raw:
            if not isinstance(item, dict):
                return None
            if "s" not in item or "e" not in item or "st" not in item:
                return None

            s = item["s"]
            e = item["e"]
            st = item["st"]

            if isinstance(s, bool) or isinstance(e, bool):
                return None
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                return None

            s_int = int(s)
            e_int = int(e)
            if s_int != s or e_int != e:
                return None  # no enteros "de verdad" (ej. 1.5) -> formato inválido
            if s_int < 0 or e_int <= s_int:
                return None
            if st not in _VALID_STAGES:
                return None

            cleaned.append({"s": s_int, "e": e_int, "st": st})

        cleaned.sort(key=lambda seg: seg["s"])

        for prev, cur in zip(cleaned, cleaned[1:]):
            if cur["s"] < prev["e"]:
                return None  # traslape

        return cleaned
    except Exception:
        return None


def awakenings(segments: Optional[list]) -> int:
    """Cuenta los segmentos "awake" que ocurren DESPUÉS del primer segmento
    no-awake (el "awake" inicial antes de conciliar el sueño no cuenta como
    un despertar). None/lista vacía -> 0. Nunca lanza."""
    try:
        if not segments:
            return 0
        asleep_started = False
        count = 0
        for seg in segments:
            st = seg.get("st") if isinstance(seg, dict) else None
            if not asleep_started:
                if st != "awake":
                    asleep_started = True
                continue
            if st == "awake":
                count += 1
        return count
    except Exception:
        return 0
