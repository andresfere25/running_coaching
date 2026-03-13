"""
api/routers/coach.py — Endpoint de contenido editorial del coach.

GET /athletes/{cedula}/coach-content
  → Lee weekly_published.json si existe.
  → Si no existe, retorna auto_fallback (campos vacíos, source="auto_fallback").

La app solo muestra contenido con source="published".
El draft nunca se expone por este endpoint.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter

load_dotenv()

router = APIRouter(tags=["coach"])

DATA_DIR = Path(os.getenv("DATA_DIR", "data/athletes"))


def _current_week_monday() -> str:
    """Retorna la fecha del lunes de la semana actual en formato YYYY-MM-DD."""
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()

# Respuesta vacía para el caso de fallback automático
_AUTO_FALLBACK: dict = {
    "source":           "auto_fallback",
    "week_start":       None,
    "published_at":     None,
    "status":           "auto",
    "coach_message":    None,
    "weekly_focus":     None,
    "weekly_objective": None,
    "alerts":           [],
    "restrictions":     [],
    "session_notes":    {},
    "plan_overrides":   {},
}


@router.get("/{cedula}/coach-content")
def get_coach_content(cedula: str) -> dict:
    """
    Devuelve el contenido publicado del coach para un atleta.

    - source="published": hay contenido editorial activo.
    - source="auto_fallback": no hay publicado — la app usa el contenido automático.

    El draft (weekly_draft.json) nunca se sirve por este endpoint.
    """
    published_path = DATA_DIR / cedula / "coach" / "weekly_published.json"

    if not published_path.exists():
        return _AUTO_FALLBACK.copy()

    try:
        data = json.loads(published_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _AUTO_FALLBACK.copy()

    week_start = data.get("week_start")
    return {
        "source":           "published",
        "week_start":       week_start,
        "published_at":     data.get("published_at"),
        "status":           data.get("status", "published"),
        "is_stale":         week_start != _current_week_monday(),
        "coach_message":    data.get("coach_message") or None,
        "weekly_focus":     data.get("weekly_focus") or None,
        "weekly_objective": data.get("weekly_objective") or None,
        "alerts":           data.get("alerts") or [],
        "restrictions":     data.get("restrictions") or [],
        "session_notes":    data.get("session_notes") or {},
        "plan_overrides":   data.get("plan_overrides") or {},
    }
