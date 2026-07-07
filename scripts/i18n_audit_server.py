#!/usr/bin/env python3
"""
i18n_audit_server.py — gate de i18n del lado SERVIDOR.
Verifica que coach.py / insights.py / drivers.py NO tengan texto UI hardcoded en
español (todo debe ir via app.i18n.tr()), y que el dict STRINGS de app/i18n.py
tenga PARIDAD de claves en los 4 idiomas. Sale !=0 si hay problemas.
Uso: python3 scripts/i18n_audit_server.py
"""
import re, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCENT = "áéíóúñ¿¡ÁÉÍÓÚÑ"
SIGNAL = re.compile(
    r"\b(Recuperaci|Sue[nñ]|Esfuerzo|Edad|Fuerza|Dormiste|d[eé]ficit|se[nñ]ales|"
    r"enfermedad|Temperatura|estr[eé]s|Acostarte|horas|reposo|urgente|cuerpo|rinde|"
    r"semana|carga|Considera|Posibles|baja|elevada|tarde|d[ií]a siguiente)", re.I)
LOGIC = ["coach.py", "insights.py", "drivers.py"]

def scan_module(name):
    src = (ROOT / "app" / name).read_text(encoding="utf-8")
    src = re.sub(r'""".*?"""', "", src, flags=re.S)   # docstrings
    src = re.sub(r"'''.*?'''", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        l = line.strip()
        if l.startswith("#"):
            continue
        # excluir logs / excepciones / nombres de campo (no son UI)
        if any(k in line for k in ("logger", "logging.", "raise ", "print(")):
            continue
        for lit in re.finditer(r"'([^'\\]{3,})'|\"([^\"\\]{3,})\"", line):
            s = lit.group(1) or lit.group(2)
            if any(c in s for c in ACCENT) or SIGNAL.search(s):
                out.append(f"{name}: {s[:60]}")
    return out

literal_fails = []
for m in LOGIC:
    literal_fails += scan_module(m)

# parity de STRINGS en app/i18n.py
parity_fails = []
i18n_path = ROOT / "app" / "i18n.py"
if not i18n_path.exists():
    parity_fails.append("app/i18n.py NO existe todavía")
else:
    try:
        spec = importlib.util.spec_from_file_location("vi18n", i18n_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        S = mod.STRINGS
        base = set(S["es"].keys())
        for loc in ("es", "en", "fr", "pt"):
            if loc not in S:
                parity_fails.append(f"locale '{loc}' ausente en STRINGS"); continue
            miss = base - set(S[loc].keys())
            extra = set(S[loc].keys()) - base
            if miss:  parity_fails.append(f"{loc} faltan: {sorted(miss)}")
            if extra: parity_fails.append(f"{loc} sobran: {sorted(extra)}")
    except Exception as e:
        parity_fails.append(f"no se pudo cargar app/i18n.py: {e}")

print(f"(A) literales español UI en coach/insights/drivers: {len(literal_fails)}")
for f in literal_fails: print("   •", f)
print(f"(B) problemas de paridad STRINGS: {len(parity_fails)}")
for f in parity_fails: print("   •", f)
total = len(literal_fails) + len(parity_fails)
print(f"\nTOTAL: {total}")
sys.exit(0 if total == 0 else 1)
