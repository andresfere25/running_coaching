"""
api/routers/health.py — Healthcheck del servidor.
"""

import os
from datetime import datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
def health():
    """
    Verifica que el servidor está activo y muestra el estado de configuración clave.
    No requiere autenticación.
    """
    from src.storage.supabase_client import is_configured as _sb_ok
    webhook_token_set = bool(os.getenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "").strip())
    strava_ok = bool(os.getenv("STRAVA_CLIENT_ID") and os.getenv("STRAVA_CLIENT_SECRET"))

    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "supabase":              _sb_ok(),
            "strava_credentials":    strava_ok,
            "strava_webhook_token":  webhook_token_set,
        },
    }
