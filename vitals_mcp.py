"""
vitals_mcp.py — MCP server de Vitals (FastMCP, transport stdio).

Expone 9 tools de solo lectura para que un agente MCP (OpenClaw, Claude, o
cualquier cliente MCP) consulte la salud del usuario sin duplicar la lógica
de la app.

Requiere Python 3.10+ y el SDK `mcp` (instalar en el box):
    pip install mcp

Cómo arrancar (en el box, Python 3.12):
    py -3 vitals_mcp.py           # Windows
    python3 vitals_mcp.py         # Linux/Mac

El server usa stdio por defecto. El agente lo spawn-eará como sub-proceso
una vez registrado en la config de MCP servers de tu cliente.

AISLAMIENTO: este es el ÚNICO archivo que importa `mcp`.
app/mcp_tools.py (las funciones puras) NO importa mcp y corre en 3.9.
"""

from mcp.server.fastmcp import FastMCP

from app import mcp_tools as _tools

mcp = FastMCP(
    name="vitals",
    instructions=(
        "Servidor de salud personal de Vitals. "
        "Usa estas 9 tools para obtener datos frescos antes de dar consejos de salud, "
        "recuperación o entrenamiento. Todas son read-only y cargan el dataset actual. "
        "vitals_cycle_summary solo trae datos si el usuario activó el seguimiento de "
        "ciclo (opt-in) — nunca asumas ni infieras ciclo si devuelve enabled:false."
    ),
)


# ── Tool 1: vitals_today ──────────────────────────────────────────────────────

@mcp.tool()
def vitals_today() -> dict:
    """
    Snapshot de salud del día más reciente.
    Devuelve: fecha, recovery (% + estado alta/media/baja), sueño (h + %),
    HRV (ms + vs base), FC reposo (bpm + vs base), esfuerzo (strain/21),
    fuerza_semana_min (minutos de ejercicio vigoroso en los últimos 7 días).

    Úsala cuando el usuario pregunte cómo está hoy, su recuperación,
    su sueño de anoche, su HRV o su ritmo cardíaco en reposo.
    """
    ds = _tools._load_dataset()
    return _tools.today_snapshot(ds)


# ── Tool 2: vitals_trends ─────────────────────────────────────────────────────

@mcp.tool()
def vitals_trends() -> dict:
    """
    Promedios 7d y 30d de recovery, HRV, FC reposo, sueño y esfuerzo.
    También incluye el número de noches con menos de 7h en los últimos 7 días.

    Úsala cuando el usuario pregunte por tendencias, evolución reciente,
    si está mejorando o empeorando, o comparativas de semana vs mes.
    """
    ds = _tools._load_dataset()
    return _tools.trends(ds)


# ── Tool 3: vitals_insights ───────────────────────────────────────────────────

@mcp.tool()
def vitals_insights() -> list:
    """
    Lista de alertas e insights actuales (máx. 5), ordenados por severidad
    (alert → watch → positive → info). Cada item incluye: id, severity,
    category, title, summary, factors (lista de factores), recommendation.

    Úsala cuando el usuario pida alertas, qué debe atender, qué está bien
    o qué dice el sistema de salud sobre su estado actual.
    Sin HTML-escapes: los textos son planos para mensajería.
    """
    ds = _tools._load_dataset()
    return _tools.insights_list(ds)


# ── Tool 4: vitals_bodyage ────────────────────────────────────────────────────

@mcp.tool()
def vitals_bodyage() -> dict:
    """
    Edad corporal y fitness del usuario: body_age, fitness_age, VO2max,
    category (Excelente/Buena/…), penalty_years (penalización por dormir < 7h),
    edad_real, y drivers (sub-métricas que componen el score: rhr, hrv, sleep_h…).

    Úsala cuando el usuario pregunte por su edad biológica, fitness age,
    VO2max, o qué tan joven/viejo está su cuerpo en papel.
    """
    ds = _tools._load_dataset()
    return _tools.bodyage_summary(ds)


# ── Tool 5: vitals_morning_brief ─────────────────────────────────────────────

@mcp.tool()
def vitals_morning_brief() -> str:
    """
    Brief mañanero determinista (sin LLM): saludo con la fecha del último
    dato, snapshot clave (recovery, sueño, HRV, FC, strain), alertas activas
    y 1 prioridad del día. Texto plano listo para enviar por WhatsApp.

    Úsala para el cron mañanero de tu agente o cuando el usuario pida
    el resumen del día / el brief de salud de la mañana.
    Incluye la fecha de `summary.updated` para saber si el dato está fresco.
    """
    ds = _tools._load_dataset()
    return _tools.morning_brief(ds)


# ── Tool 6: vitals_ask_coach ─────────────────────────────────────────────────

@mcp.tool()
def vitals_ask_coach(question: str) -> str:
    """
    Respuesta del Coach IA de Vitals a una pregunta específica.
    Invoca el claude CLI con el contexto de salud completo del usuario
    (igual que el endpoint /api/coach de la app web).

    Úsala cuando el usuario haga preguntas abiertas de entrenamiento,
    recuperación, sueño o salud que requieran razonamiento personalizado,
    no solo datos crudos (esas las dan las otras tools).

    Parámetro:
        question: la pregunta del usuario en lenguaje natural.

    Nota: esta tool invoca el claude CLI — puede tardar 10-90 segundos.
    """
    ds = _tools._load_dataset()
    return _tools.ask(question, ds=ds)


# ── Tool 7: vitals_drivers ───────────────────────────────────────────────────

@mcp.tool()
def vitals_drivers() -> list:
    """
    Palancas accionables: asociaciones estadísticas entre comportamientos del usuario
    (hora de dormir, duración de sueño, esfuerzo, pasos) y sus resultados biométricos
    (HRV, recovery) al día siguiente, usando correlación de Spearman rezagada.

    Devuelve solo findings con n>=25, significativos (p<0.05 aprox), |ρ|>=0.2,
    ordenados por |ρ| descendente (máx. 5). Si no hay suficientes datos → [].

    Cada finding incluye: driver, outcome, lag, rho, n, significant, direction
    (mejora/empeora), strength (fuerte/moderada/débil) y headline en español.

    IMPORTANTE: estas son ASOCIACIONES observadas en los datos del usuario, no causas.
    Úsalas para contextualizar consejos personalizados, nunca como diagnóstico.

    Úsala cuando el usuario pregunte qué hábitos se relacionan con mejor recovery
    o HRV, qué palancas tiene para mejorar, o qué muestra su propio historial.
    """
    ds = _tools._load_dataset()
    return _tools.drivers_list(ds)


# ── Tool 8: vitals_bedtime_brief ─────────────────────────────────────────────

@mcp.tool()
def vitals_bedtime_brief() -> str:
    """
    Brief nocturno corto (2-4 líneas): recuperación de hoy, hora media de
    acostarse de los últimos 7 días vs la meta declarada del usuario (si declaró
    una hora tipo "acostarme antes de las 23:00" en sus metas de perfil; si no,
    usa 00:00 como default), y UNA sugerencia concreta para esta noche.
    Texto plano listo para enviar por WhatsApp.

    Úsala para el cron nocturno de tu agente (recordatorio de bedtime) o cuando el
    usuario pregunte a qué hora debería acostarse hoy / cómo va con su meta de
    sueño esta semana.
    """
    ds = _tools._load_dataset()
    return _tools.bedtime_brief(ds)


# ── Tool 9: vitals_cycle_summary (Fase 7: salud femenina, opt-in) ───────────

@mcp.tool()
def vitals_cycle_summary() -> dict:
    """
    Estado del seguimiento de ciclo menstrual del usuario (fase, día de ciclo,
    predicción de próximo periodo, ventana fértil con disclaimer, retraso,
    señales de peri/menopausia si aplican).

    Devuelve {"enabled": false} si el usuario NO activó el seguimiento de ciclo
    en su perfil (opt-in, apagado por default) — en ese caso NO hay ningún dato
    de ciclo disponible ni implícito, y esta tool no debe usarse para inferir
    nada sobre el usuario.

    Úsala SOLO si el usuario pregunta explícitamente por su ciclo, periodo,
    ventana fértil o síntomas relacionados, y tiene el seguimiento activado.

    IMPORTANTE (guardrail): esta información es orientativa (calendario +
    temperatura), NUNCA un diagnóstico médico ni un método anticonceptivo.
    No la uses para diagnosticar embarazo o patología, ni la recomiendes como
    base de decisiones anticonceptivas — ante dudas, sugiere consultar a un
    profesional de salud.
    """
    ds = _tools._load_dataset()
    return _tools.cycle_summary(ds)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()  # transport stdio por default
