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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Body, Depends, HTTPException

from api.deps import get_data_dir, require_api_key

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
                .order("published_at", desc=True)
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


# ─── Draft: leer ─────────────────────────────────────────────────────────────

@router.get("/{cedula}/coach-draft")
def get_coach_draft(
    cedula: str,
    _: None = Depends(require_api_key),
) -> dict:
    """
    Devuelve el draft del coach para un atleta.
    Si no existe draft previo, genera una plantilla desde el snapshot actual.

    Respuesta:
    - found: True si ya había un draft guardado
    - editable: campos que el coach puede editar
    - context: datos automáticos del atleta (read-only: CTL, ATL, semáforo…)
    """
    data_dir = get_data_dir()
    draft_path = data_dir / cedula / "coach" / "weekly_draft.json"

    if draft_path.exists():
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            return {
                "found":    True,
                "cedula":   cedula,
                "editable": {k: v for k, v in draft.items()
                             if not k.startswith("_") and k not in ("status", "cedula")},
                "context":  draft.get("_auto_context") or {},
            }
        except Exception:
            pass

    # Sin draft: generar plantilla desde snapshot (Supabase-first, local fallback)
    from src.storage.reader import read_snapshot, read_plan
    from src.coach.publish import make_template

    snapshot = read_snapshot(cedula, data_dir / cedula if (data_dir / cedula).exists() else None) or {}
    plan     = read_plan(cedula,     data_dir / cedula if (data_dir / cedula).exists() else None) or {}
    template = make_template(cedula, snapshot, plan)

    return {
        "found":    False,
        "cedula":   cedula,
        "editable": {k: v for k, v in template.items()
                     if not k.startswith("_") and k not in ("status", "cedula")},
        "context":  template.get("_auto_context") or {},
    }


# ─── Draft: guardar ───────────────────────────────────────────────────────────

@router.put("/{cedula}/coach-draft")
def save_coach_draft(
    cedula: str,
    body: dict = Body(...),
    _: None = Depends(require_api_key),
) -> dict:
    """
    Guarda el draft del coach para un atleta.
    Preserva el _auto_context del draft previo (no lo sobreescribe desde el cuerpo).
    """
    data_dir = get_data_dir()
    draft_path = data_dir / cedula / "coach" / "weekly_draft.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)

    # Preservar contexto auto existente
    auto_context: dict = {}
    if draft_path.exists():
        try:
            existing = json.loads(draft_path.read_text(encoding="utf-8"))
            auto_context = existing.get("_auto_context") or {}
        except Exception:
            pass

    if not auto_context:
        from src.storage.reader import read_snapshot, read_plan
        snapshot = read_snapshot(cedula, data_dir / cedula if (data_dir / cedula).exists() else None) or {}
        plan     = read_plan(cedula,     data_dir / cedula if (data_dir / cedula).exists() else None) or {}
        lw = snapshot.get("latest_week") or {}
        auto_context = {
            "ctl":          lw.get("ctl"),
            "atl":          lw.get("atl"),
            "tsb":          lw.get("tsb"),
            "acwr":         lw.get("acwr"),
            "km_last_week": lw.get("km_week"),
            "semaforo":     snapshot.get("semaforo_latest_checkin"),
            "readiness":    snapshot.get("readiness_score"),
            "week_type":    plan.get("week_type"),
            "target_km":    (plan.get("targets") or {}).get("target_km_week"),
        }

    draft = {
        "cedula":     cedula,
        "status":     "draft",
        **{k: v for k, v in body.items()
           if k not in ("cedula", "status") and not k.startswith("_")},
        "_auto_context":  auto_context,
        "_instructions":  (
            "Edita los campos de contenido arriba. "
            "Los campos con '_' son contexto y no se publican. "
            f"Publica vía POST /athletes/{cedula}/coach-publish o desde el panel del coach."
        ),
    }

    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "detail": "draft guardado", "cedula": cedula}


# ─── Publicar desde API ───────────────────────────────────────────────────────

@router.post("/{cedula}/coach-publish")
def publish_coach_content_api(
    cedula: str,
    _: None = Depends(require_api_key),
) -> dict:
    """
    Valida y publica el draft del coach para un atleta.
    Equivalente al CLI: python -m src.coach.publish --cedula {cedula}

    Retorna el resultado de la publicación + estado del push a Supabase.
    """
    data_dir = get_data_dir()
    draft_path     = data_dir / cedula / "coach" / "weekly_draft.json"
    published_path = data_dir / cedula / "coach" / "weekly_published.json"

    if not draft_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No hay draft para cédula '{cedula}'. "
                f"Crea uno vía GET /athletes/{cedula}/coach-draft."
            ),
        )

    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Draft inválido (JSON): {exc}")

    from src.coach.publish import validate_draft
    errors = validate_draft(draft)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    # Filtrar campos privados y limpiar notas vacías
    published = {k: v for k, v in draft.items() if not k.startswith("_")}
    published["status"]       = "published"
    published["published_at"] = datetime.now(timezone.utc).isoformat()
    notes = published.get("session_notes") or {}
    published["session_notes"] = {k: v for k, v in notes.items() if v and v.strip()}

    published_path.write_text(json.dumps(published, ensure_ascii=False, indent=2), encoding="utf-8")

    from src.storage.writer import push_coach_content
    sb_result = push_coach_content(cedula, published)

    return {
        "ok":          True,
        "cedula":      cedula,
        "week_start":  published.get("week_start"),
        "published_at": published.get("published_at"),
        "supabase":    sb_result,
    }
