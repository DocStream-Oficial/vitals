"""
config.py — carga .env + perfil de usuario.
Expone `settings` con todos los valores que necesita la app.
"""
import json
import logging
import os
import secrets
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("vitals.config")

# Busca .env en la raíz del proyecto (un nivel arriba de este archivo)
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _load_or_generate_ingest_token(data_dir: Path) -> str:
    """Fase 8C (paso C6): INGEST_TOKEN pasa a ser OBLIGATORIO. Si no viene en
    el .env (INGEST_TOKEN=""), se autogenera y persiste en
    data/ingest_token.json — {token, generated_at} — para que sobreviva
    reinicios sin volver a pedir re-parear la app iOS en cada arranque.

    Escritura ATÓMICA (.tmp + os.replace), patrón cycle.py. Nunca lanza: si
    algo falla al leer/escribir el archivo, degrada a un token generado
    en memoria (válido solo para este proceso) en vez de tumbar el arranque —
    peor caso es pedir re-parear una vez más, nunca un 500 al boot."""
    path = data_dir / "ingest_token.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token") if isinstance(data, dict) else None
            if isinstance(token, str) and token:
                return token
    except Exception as exc:
        logger.warning("ingest_token.json ilegible (%s); se regenerará.", exc)

    token = secrets.token_urlsafe(32)
    try:
        import datetime as _dt
        data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"token": token, "generated_at": _dt.datetime.now().isoformat(timespec="seconds")}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        logger.warning(
            "INGEST_TOKEN no estaba en .env: se autogeneró y persistió en "
            "%s. Cópialo desde la sección 'Más' de la app "
            "para conectar HealthKit/ECG (visible también en el QR).",
            path,
        )
    except Exception as exc:
        logger.error("No pude persistir ingest_token.json (%s); token válido solo en memoria.", exc)
    return token


def _parse_bool_env(name: str, default: str = "0") -> bool:
    """Parseo tolerante de flags booleanos por env var: acepta 1/true/yes/on
    (case-insensitive); cualquier otra cosa (incluido vacío/ausente) -> False.
    Nunca lanza."""
    try:
        return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _parse_int_env(name: str, default: int = 0) -> int:
    """Parseo tolerante de enteros por env var: vacío/ausente/no-numérico ->
    default, sin crashear el arranque. Acepta espacios alrededor del valor."""
    try:
        raw = os.getenv(name, "")
        if raw is None or not raw.strip():
            return default
        return int(raw.strip())
    except Exception:
        return default


class Settings:
    # Fase 8A (paso A1): modo DEMO — sirve el dataset sintético (tests/fixtures/
    # demo_dataset.json, generado por scripts/gen_demo_data.py) SIN requerir
    # OAuth/tokens, y corta en seco cualquier endpoint que escriba en data/ real
    # o dispare sync/ingest reales. Ver main.py::_load_dataset y los guards
    # `_demo_guard()` en los endpoints de escritura sensibles. Default OFF —
    # comportamiento normal 100% intacto si no se define la env var.
    VITALS_DEMO: bool = _parse_bool_env("VITALS_DEMO")

    # OAuth
    CLIENT_ID: str = os.getenv("CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("CLIENT_SECRET", "")
    REDIRECT_URI: str = os.getenv("REDIRECT_URI", "http://localhost:8700/auth/callback")

    SCOPES = [
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    ]

    # Perfil del usuario
    BIRTHDATE: str = os.getenv("BIRTHDATE", "1990-01-01")
    WAIST_CM: float = float(os.getenv("WAIST_CM", "80"))
    SEX: str = os.getenv("SEX", "M")
    PREFER_PLATFORM: str = os.getenv("PREFER_PLATFORM", "AUTO")

    # Scheduler
    SYNC_HOUR: int = int(os.getenv("SYNC_HOUR", "9"))

    # Aviso de token Google (fix "nag" — ver ROADMAP-vitals-token-nag.md).
    # Con la app OAuth PUBLICADA (In production) el refresh_token de Google no
    # caduca por edad — solo por invalid_grant real (que mark_expired() marca).
    # Default 0 = permanente: auth_state() ignora la edad del token y solo
    # reporta 'expired' si el flag real lo dice. Poner >0 (ej. 7) únicamente
    # si tu app OAuth sigue en modo Testing de Google (límite real de 7 días).
    # Parseo tolerante: vacío/no-numérico -> 0, nunca crashea el arranque.
    GOOGLE_TOKEN_EXPIRY_DAYS: int = _parse_int_env("GOOGLE_TOKEN_EXPIRY_DAYS", 0)

    # Claude CLI — para coach conversacional vía subprocess (sin API key)
    # Resolución cross-platform con shutil.which; override por .env/CLAUDE_CLI.
    # En Windows, which encuentra el .cmd de npm automáticamente.
    # Si no está instalado, cae a "claude" y el coach degrada con _FALLBACK (no 500).
    _default_cli: str = shutil.which("claude") or "claude"
    CLAUDE_CLI: str = os.getenv("CLAUDE_CLI", _default_cli)

    # Backend de LLM intercambiable (app/llm.py) — Fase P0 F3. "claude_cli"
    # (default) preserva el comportamiento actual byte-idéntico. "openai_compat"
    # habilita un endpoint OpenAI-compatible (Ollama/LM Studio/llama.cpp) para
    # el coach, headline y reportes, vía urllib (sin dependencia pip nueva).
    # Valor no reconocido -> app.llm.generate loguea y cae a claude_cli.
    COACH_BACKEND: str = os.getenv("COACH_BACKEND", "claude_cli")
    COACH_API_BASE: str = os.getenv("COACH_API_BASE", "http://localhost:11434/v1")
    COACH_MODEL: str = os.getenv("COACH_MODEL", "llama3.1")
    COACH_API_KEY: str = os.getenv("COACH_API_KEY", "")

    # Directorios
    ROOT_DIR: Path = _ROOT
    # Fase 8A (paso A1): en modo demo, DATA_DIR NUNCA apunta a data/ real —
    # apunta a un directorio EFÍMERO bajo tempfile (borrado por el SO en algún
    # momento, nunca escrito por este repo fuera de un proceso demo). TODOS los
    # módulos de persistencia (journal.py, labs.py, cycle.py, userctx.py,
    # ingest_token) derivan su ruta de settings.DATA_DIR — este único punto
    # blinda que ninguno de ellos pueda tocar data/ real mientras VITALS_DEMO=1,
    # sin tener que tocar cada módulo uno por uno. mkdir aquí mismo (no al
    # primer write) para que /api/journal, /api/labs, etc. no truene por
    # directorio inexistente en la primera escritura de una demo fresca.
    if VITALS_DEMO:
        import tempfile as _tempfile
        DATA_DIR: Path = Path(_tempfile.mkdtemp(prefix="vitals_demo_"))
    else:
        DATA_DIR: Path = _ROOT / "data"
    TEMPLATES_DIR: Path = _ROOT / "templates"

    # API URLs (constantes de vitals_sync.py)
    API_BASE: str = "https://health.googleapis.com/v4/users/me"
    TOKEN_URL: str = "https://oauth2.googleapis.com/token"
    AUTH_URL: str = "https://accounts.google.com/o/oauth2/v2/auth"

    # Oura OAuth credentials (BYO — crear app en https://cloud.ouraring.com/oauth/applications)
    OURA_CLIENT_ID: str = os.getenv("OURA_CLIENT_ID", "")
    OURA_CLIENT_SECRET: str = os.getenv("OURA_CLIENT_SECRET", "")

    # Oura API URLs (v2, jun 2026) + scopes
    OURA_AUTH_URL: str = "https://cloud.ouraring.com/oauth/authorize"
    OURA_TOKEN_URL: str = "https://api.ouraring.com/oauth/token"
    OURA_API_BASE: str = "https://api.ouraring.com/v2/usercollection"
    OURA_SCOPES = ["personal", "daily", "heartrate", "workout", "spo2", "session"]

    # WHOOP OAuth credentials (BYO — crear app en https://developer.whoop.com)
    WHOOP_CLIENT_ID: str = os.getenv("WHOOP_CLIENT_ID", "")
    WHOOP_CLIENT_SECRET: str = os.getenv("WHOOP_CLIENT_SECRET", "")

    # WHOOP API URLs (v2, jun 2026) + scopes
    # 🔴 'offline' es obligatorio para recibir refresh_token (WHOOP lo rota en cada refresh).
    WHOOP_AUTH_URL: str = "https://api.prod.whoop.com/oauth/oauth2/auth"
    WHOOP_TOKEN_URL: str = "https://api.prod.whoop.com/oauth/oauth2/token"
    WHOOP_API_BASE: str = "https://api.prod.whoop.com/developer/v2"
    WHOOP_SCOPES = [
        "read:recovery", "read:sleep", "read:workout", "read:cycles",
        "read:profile", "read:body_measurement", "offline",
    ]

    # Secreto compartido para POST /api/ingest y /api/ecg (HealthKit push).
    # Fase 8C (paso C6): AHORA OBLIGATORIO. Si no viene en .env, se autogenera
    # y persiste en data/ingest_token.json — /api/ingest y /api/ecg exigen
    # header X-Vitals-Token == este valor SIEMPRE (401 si falta o no coincide;
    # ya no existe el modo permisivo "sin auth" de fases anteriores).
    # Fase 8A (paso A1): en modo demo, persiste bajo DATA_DIR (ya efímero,
    # ver arriba) en vez de ROOT_DIR/"data" — nunca escribe ingest_token.json
    # en data/ real. Además /api/ingest y /api/ecg quedan cortados en seco en
    # demo (ver _demo_guard en main.py), así que este token ni siquiera se usa
    # ahí; se genera igual por si algún test/script lo referencia directo.
    INGEST_TOKEN: str = os.getenv("INGEST_TOKEN", "") or _load_or_generate_ingest_token(
        DATA_DIR if VITALS_DEMO else (ROOT_DIR / "data")
    )

    # R2 pre-publicación: auth OPT-IN del dashboard web (cookie o Bearer).
    # Vacío (default) = OFF, comportamiento 100% igual a hoy — a diferencia de
    # INGEST_TOKEN, este NUNCA se autogenera: es una decisión consciente del
    # usuario, no un secreto que la app necesite para funcionar sola. Ver
    # middleware en main.py (definido después de _userctx_middleware).
    DASHBOARD_TOKEN: str = os.getenv("DASHBOARD_TOKEN", "")


settings = Settings()
