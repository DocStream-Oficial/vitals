#!/usr/bin/env python3
"""Diagnóstico: dump crudo de registros de sueño del API para los últimos días."""
import sys
from app import auth, health_api
from app.parsers import _to_local

def main():
    token = auth.access_token()
    dps = health_api.list_all("sleep", token)
    print(f"TOTAL sleep dataPoints del API: {len(dps)}")
    targets = {"2026-06-24", "2026-06-25", "2026-06-26"}
    rows = []
    for dp in dps:
        s = dp.get("sleep") or {}
        plat = dp.get("dataSource", {}).get("platform")
        iv = s.get("interval", {})
        start = _to_local(iv.get("startTime", ""), iv.get("startUtcOffset", "0s"))
        end = _to_local(iv.get("endTime", ""), iv.get("endUtcOffset", "0s"))
        date = end.strftime("%Y-%m-%d") if end else None
        if date not in targets:
            continue
        summ = s.get("summary", {})
        asleep = int(summ.get("minutesAsleep", 0) or 0)
        inbed = summ.get("minutesInSleepPeriod")
        stages = {x.get("type"): x.get("minutes") for x in summ.get("stagesSummary", [])}
        rows.append((date, plat, asleep, inbed,
                     start.strftime("%m-%d %H:%M") if start else "-",
                     end.strftime("%m-%d %H:%M") if end else "-",
                     stages))
    rows.sort()
    print(f"\nRegistros para {sorted(targets)}:\n")
    for date, plat, asleep, inbed, st, en, stages in rows:
        print(f"  {date} | {str(plat):10} | asleep={str(asleep).rjust(4)}min "
              f"({asleep//60}h{asleep%60:02d}) | inbed={inbed} | {st} -> {en} | {stages}")
    if not rows:
        print("  (ningún registro para esas fechas)")

if __name__ == "__main__":
    main()
