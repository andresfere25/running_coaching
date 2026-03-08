"""
api/routers/health.py — Healthcheck del servidor.
"""

from datetime import datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
def health():
    """
    Verifica que el servidor está activo.
    No requiere autenticación.
    """
    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
