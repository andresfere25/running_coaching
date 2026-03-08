"""
api/routers/athletes.py — Endpoints de consulta por atleta.

Todos los endpoints leen desde data/athletes/{cedula}/ (archivos ya generados
por el pipeline). No modifican datos ni corren cómputo pesado.

Endpoints:
  GET /athletes                         → lista atletas disponibles
  GET /athletes/{cedula}/profile        → perfil del atleta (Form 1)
  GET /athletes/{cedula}/snapshot       → estado actual (última semana + semáforo)
  GET /athletes/{cedula}/plan           → plan semanal (running + fuerza)
  GET /athletes/{cedula}/features       → historial de features semanales
"""

import json
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_athlete_dir, get_data_dir, require_api_key, sanitize_json

router = APIRouter()


# ─── Lista de atletas ────────────────────────────────────────────────────────

@router.get("")
def list_athletes(_: None = Depends(require_api_key)):
    """
    Devuelve los atletas con datos locales en data/athletes/.
    Solo muestra cédulas (carpetas numéricas existentes).
    """
    data_dir = get_data_dir()
    if not data_dir.exists():
        return {"athletes": [], "count": 0}

    cedulas = sorted(
        d.name for d in data_dir.iterdir()
        if d.is_dir() and d.name.isdigit()
    )
    return {"athletes": cedulas, "count": len(cedulas)}


# ─── Perfil ──────────────────────────────────────────────────────────────────

@router.get("/{cedula}/profile")
def get_profile(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el perfil del atleta extraído del Form de ingreso.
    Incluye datos personales, objetivos, ritmos reportados y preferencias.
    """
    path = athlete_dir / "meta" / "profile.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. El paso 'ingest' del pipeline no ha corrido.",
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ─── Snapshot (estado actual) ────────────────────────────────────────────────

@router.get("/{cedula}/snapshot")
def get_snapshot(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el snapshot del atleta: última semana de features + semáforo.
    Es el dato principal para el dashboard del atleta.

    Campos clave en la respuesta:
    - semaforo_latest_checkin: VERDE / AMARILLO / ROJO / SIN_CHECKIN
    - latest_week: km, sessions, ACWR, monotonía, zonas de la última semana
    - profile: datos del atleta (nombre, objetivo, ritmos)
    - generated_at: timestamp del último pipeline ejecutado
    """
    path = athlete_dir / "features" / "athlete_snapshot.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Snapshot no disponible. "
                f"Ejecuta primero: POST /athletes/{cedula}/pipeline"
            ),
        )
    return sanitize_json(json.loads(path.read_text(encoding="utf-8")))


# ─── Plan semanal ────────────────────────────────────────────────────────────

@router.get("/{cedula}/plan")
def get_plan(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el plan semanal (running + fuerza) del atleta.

    Campos clave:
    - week_type: DESCARGA / CONSERVADORA / PROGRESO
    - semaforo: estado del check-in que determinó el week_type
    - targets: km objetivo, días de running, días de fuerza
    - plan_by_day: sesiones por día (Lunes a Domingo)
    - notes: recomendaciones adicionales
    """
    path = athlete_dir / "features" / "weekly_plan.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Plan no disponible. "
                f"Ejecuta primero: POST /athletes/{cedula}/pipeline"
            ),
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ─── Historial de features ───────────────────────────────────────────────────

@router.get("/{cedula}/features")
def get_features(
    cedula: str,
    weeks: int = Query(default=12, ge=1, le=104, description="Últimas N semanas de historial"),
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el historial semanal de features del atleta.
    Usado para gráficos de evolución (km, ritmo, ACWR, zonas).

    Parámetros:
    - weeks: cuántas semanas incluir (default 12, max 104)
    """
    path = athlete_dir / "features" / "weekly_features.parquet"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Features no disponibles. Ejecuta primero el pipeline.",
        )

    df = duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()
    df = df.sort_values("week_start").tail(weeks).reset_index(drop=True)

    # Convertir NaN a None para JSON válido
    return sanitize_json({
        "cedula": cedula,
        "weeks_returned": len(df),
        "weeks_requested": weeks,
        "data": df.to_dict(orient="records"),
    })


# ─── Último check-in ─────────────────────────────────────────────────────────

@router.get("/{cedula}/checkin")
def get_latest_checkin(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el último check-in registrado del atleta.
    Incluye el flag is_recent (True si tiene menos de 10 días).
    """
    path = athlete_dir / "meta" / "latest_checkin.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Check-in no disponible. El paso 'ingest' del pipeline no ha corrido.",
        )
    return json.loads(path.read_text(encoding="utf-8"))
