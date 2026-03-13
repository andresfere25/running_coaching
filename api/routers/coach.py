"""
api/routers/coach.py — Endpoint de contenido editorial del coach.

GET /athletes/{cedula}/coach-content
  → 1. Busca en Supabase tabla coach_content (más reciente por cedula).
  → 2. Si no hay Supabase o no hay fila, lee weekly_published.json local.
  → 3. Si tampoco existe local, retorna auto_fallback.

La app solo muestra contenido con source="published".
El draft (weekly_draft.json) nunca se sirve por este endpoint.
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


def _build_response(data: dict) -> dict:
    """Construye el dict de respuesta normalizado desde cualquier fuente."""
    week_start = data.get("week_start")
    if week_start:
        week_start = str(week_start)[:10]  # DATE puede venir como 'YYYY-MM-DD' o datetime
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


# Respuesta vacía para el caso de fallback automático
_AUTO_FALLBACK: dict = {
    "source":           "auto_fallback",
    "week_start":       None,
    "published_at":     None,
    "status":           "auto",
    "is_stale":         False,
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

    Prioridad de lectura:
      1. Supabase tabla coach_content (más reciente por cedula)
      2. data/athletes/{cedula}/coach/weekly_published.json (fallback local)
      3. auto_fallback (sin contenido publicado)

    Campos clave en la respuesta:
    - source: "published" | "auto_fallback"
    - is_stale: True si el week_start no coincide con la semana actual
    - coach_message, weekly_focus, session_notes, plan_overrides
    """
    # ── 1. Supabase first ─────────────────────────────────────────────────────
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if client:
            res = (
                client.table("coach_content")
                .select("*")
                .eq("cedula", cedula)
                .order("week_start", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return _build_response(res.data[0])
    except Exception as exc:
        print(f"[coach] Supabase read error para {cedula}: {exc}")

    # ── 2. Fallback local ─────────────────────────────────────────────────────
    published_path = DATA_DIR / cedula / "coach" / "weekly_published.json"
    if published_path.exists():
        try:
            data = json.loads(published_path.read_text(encoding="utf-8"))
            return _build_response(data)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 3. Sin contenido publicado ────────────────────────────────────────────
    return _AUTO_FALLBACK.copy()
