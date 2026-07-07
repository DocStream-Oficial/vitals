#!/usr/bin/env python3
"""
i18n_audit.py — gate determinista de i18n para vitals_ios.html.
Detecta texto visible que NO se traducirá:
  (A) nodos de texto estático SIN data-i18n (y sin ser marca/unidad)
  (B) literales de string en el JS de render con español, fuera del dict STRINGS y fuera de t(...)
Sale con código !=0 si hay fugas. Uso: python3 scripts/i18n_audit.py
"""
import re, sys
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "templates" / "vitals_ios.html"
html = HTML.read_text(encoding="utf-8")

# marcas / tokens que NO se traducen
WHITELIST = {
    "Vitals", "Vitals · iOS", "Google Health", "WHOOP", "Oura", "HealthKit",
    "ms", "lpm", "rpm", "bpm", "min", "cm", "kg", "in", "lb", "VO₂máx", "VO₂max",
    "SpO₂", "HRV", "FC", "REM", "M", "F",
}
ACCENT = "áéíóúñ¿¡ÁÉÍÓÚÑ"

def has_letters(t):
    return bool(re.search(r"[A-Za-z" + ACCENT + r"]{3,}", t))

# ---- (A) nodos estáticos sin data-i18n ----
body = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)
static_leaks = []
for m in re.finditer(r"<([a-zA-Z][^>]*)>([^<]+)", body):
    attrs, text = m.group(1), m.group(2).strip()
    if "data-i18n" in attrs:
        continue
    if not text or text in WHITELIST or not has_letters(text):
        continue
    # tokens de marca puros (SF Pro …) se permiten solo si son ASCII sin palabras señal
    static_leaks.append(text[:80])

# ---- (B) literales JS con español, fuera de STRINGS y fuera de t() ----
scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
js = max(scripts, key=len) if scripts else ""
js = re.sub(r"var STRINGS\s*=\s*\{.*?\n  \};", "", js, flags=re.S)   # quita el dict
js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)                         # quita comentarios /* */ (incl. JSDoc)
js = re.sub(r"//[^\n]*", "", js)                                      # quita comentarios //
js = re.sub(r"t\(\s*['\"][^'\"]*['\"]\s*\)", "", js)                  # quita args de t('...')
js_leaks = []
for lit in re.finditer(r"'([^'\\]{2,})'|\"([^\"\\]{2,})\"|`([^`\\]{2,})`", js):
    s = (lit.group(1) or lit.group(2) or lit.group(3)).strip()
    if any(c in s for c in ACCENT) and has_letters(s) and "http" not in s:
        # excluir CSS inline
        if re.search(r"[{};:]\s*\w", s) or ":" in s and "px" in s:
            continue
        js_leaks.append(s[:80])

def uniq(x):
    out = []
    for i in x:
        if i not in out:
            out.append(i)
    return out

sl, jl = uniq(static_leaks), uniq(js_leaks)
print(f"(A) nodos estáticos sin data-i18n: {len(sl)}")
for t in sl:
    print("   •", t)
print(f"(B) literales JS con español fuera de STRINGS/t(): {len(jl)}")
for t in jl:
    print("   •", t)
total = len(sl) + len(jl)
print(f"\nTOTAL FUGAS: {total}")
sys.exit(0 if total == 0 else 1)
