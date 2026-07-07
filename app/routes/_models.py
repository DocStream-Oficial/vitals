"""
app/routes/_models.py — modelos Pydantic de request compartidos entre routers
(Fase 9, paso A1). Movidos TAL CUAL desde main.py — mismo cuerpo, mismos
nombres, mismos campos (ver ROADMAP-vitals-fase9-desmonolitizar.md: refactor
estructural, no reescritura).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class CoachRequest(BaseModel):
    question: str
    history: Optional[List[dict]] = None  # deprecado: ya no se usa como contexto (ver /api/coach)
    conversation_id: Optional[str] = None


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    birthdate: Optional[str] = None
    sex: Optional[str] = None
    waist_cm: Optional[float] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    # Ronda 5: umbral único de sueño (minutos). default 480, validado 300-600.
    sleep_target_min: Optional[int] = None
    # Tarjeta de Pasos en Hoy: meta diaria de pasos. default 8000, validado 1000-50000.
    steps_target: Optional[int] = None
    locale: Optional[str] = None
    units: Optional[str] = None
    onboarded: Optional[bool] = None
    source: Optional[str] = None  # DEPRECATED: usar sources (Fase 6A). Se sigue aceptando por compat.
    sources: Optional[List[str]] = None  # Fase 6A: lista de fuentes conectadas; gana sobre 'source' si viene.
    # Ronda 4: intake clínico. Cualquier tipo raro (no-lista) se rechaza con 422
    # controlado en _clean_str_list — Any para que pydantic no rechace antes de tiempo
    # con su propio error (queremos NUESTRO mensaje, no el genérico de pydantic).
    goals: Optional[Any] = None
    injuries: Optional[Any] = None
    conditions: Optional[Any] = None
    medications: Optional[Any] = None
    # Fase 7: toggle opt-in del módulo de salud femenina. Funciona con
    # cualquier 'sex' (inclusivo, nunca forzado). bool -> pydantic ya valida el tipo.
    cycle_tracking: Optional[bool] = None
    # Fase 8C (paso C3): config de notificaciones push (ntfy/Telegram). Any
    # para validar nosotros mismos (mismo motivo que goals/injuries/etc.:
    # queremos NUESTRO mensaje 422, no el genérico de pydantic) — MERGE
    # parcial sobre el dict existente (togglear un campo no borra los demás).
    notifications: Optional[Any] = None


class CyclePeriodCreate(BaseModel):
    start: str
    end: Optional[str] = None
    flow: Optional[str] = None


class CycleSymptomCreate(BaseModel):
    date: str
    tags: Optional[Any] = None
    note: Optional[str] = None


class JournalUpdate(BaseModel):
    """Fase 8B: PUT /api/journal/{date}. habits = {key: bool|float} — MERGE
    sobre la entry existente del día (togglear un chip no borra los demás).

    Roadmap P2 (F9, paso 7, criterio 16): Union[bool, float] en vez de bool
    — Pydantic ya no rechaza números en el body para los 3 hábitos
    cuantificables (alcohol/meditation/breathwork). Validación de RANGO vive
    en app.journal.set_entry (no aquí), mismo patrón que hoy: este endpoint
    solo valida que las keys existan en el catálogo (ver abajo), la
    coerción/clamp del valor es responsabilidad del motor."""
    habits: Dict[str, Union[bool, float]]


class JournalCustomCreate(BaseModel):
    """Fase 8B: POST /api/journal/custom — alta de hábito custom por label."""
    label: str


class PlanStart(BaseModel):
    """Roadmap P1 (F4, paso 6): POST /api/plan — iniciar un programa."""
    program_id: str


class PlanCheck(BaseModel):
    """Roadmap P1 (F4, paso 6): POST /api/plan/check — marcar día cumplido
    manual. date opcional, default hoy (el endpoint valida ISO 8601)."""
    date: Optional[str] = None


class LabEntryCreate(BaseModel):
    """Fase 8D (paso D1): POST /api/labs — entrada manual de laboratorio."""
    date: str
    marker: str
    value: float
    unit: Optional[str] = None
    note: Optional[str] = None


class UserCreate(BaseModel):
    """Fase 8D (paso D3, household): POST /api/users — alta de usuario."""
    name: str
    color: Optional[str] = None


class ApiKeyCreate(BaseModel):
    """Roadmap P2 (F10, paso 2): POST /api/keys — alta de API key de lectura."""
    label: Optional[str] = None
