"""
app/routes/pwa.py — manifest, service-worker, ingest-token, qr (Fase 9, paso
A2). Rutas más aisladas de main.py, movidas TAL CUAL (mismo cuerpo, misma
firma, mismos nombres) — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from app.config import settings

router = APIRouter()

STATIC_DIR = settings.ROOT_DIR / "static"


@router.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/service-worker.js", include_in_schema=False)
async def pwa_service_worker():
    # Servido en la raíz para que su scope cubra toda la app.
    return FileResponse(
        STATIC_DIR / "service-worker.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/api/ingest-token", include_in_schema=False)
async def api_ingest_token():
    """Fase 8C (paso C6): expone el INGEST_TOKEN vigente (autogenerado o de
    .env) para que la sección 'Más' lo muestre copiable y lo embeba en el QR
    de conexión — sin esto, el usuario no tiene forma de saber el token que
    se autogeneró solo, y HealthKit/ECG quedarían en 401 indefinidamente.
    Mismo modelo de confianza que el resto de /api/*: la app corre en
    localhost/tailnet privado, no expuesta públicamente sin auth propia."""
    return JSONResponse(content={"token": settings.INGEST_TOKEN})


@router.get("/api/qr", include_in_schema=False)
async def qr_code(data: str = ""):
    """Servicio de exposición de QR: genera un QR (SVG) del texto dado —típicamente la
    URL pública de la instancia— para emparejar la app móvil escaneándolo. SVG = sin PIL."""
    data = (data or "").strip()
    if not data or len(data) > 512:
        raise HTTPException(status_code=400, detail="param 'data' requerido (1-512 chars)")
    import io
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )
