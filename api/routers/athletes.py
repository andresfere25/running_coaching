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
  GET /athletes/{cedula}/prediction     → predicción de tiempo/ritmo por distancia
"""

import json
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.deps import get_athlete_dir, get_data_dir, require_api_key, sanitize_json
from src.storage.reader import read_snapshot, read_plan, read_features, read_checkin, read_activities

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
    _: None = Depends(require_api_key),
):
    """
    Devuelve el snapshot del atleta: última semana de features + semáforo.
    Es el dato principal para el dashboard del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Campos clave en la respuesta:
    - semaforo_latest_checkin: VERDE / AMARILLO / ROJO / SIN_CHECKIN
    - latest_week: km, sessions, ACWR, monotonía, zonas de la última semana
    - profile: datos del atleta (nombre, objetivo, ritmos)
    - generated_at: timestamp del último pipeline ejecutado
    """
    athlete_dir = get_data_dir() / cedula
    data = read_snapshot(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot no disponible. Ejecuta primero: POST /athletes/{cedula}/pipeline",
        )
    return sanitize_json(data)


# ─── Plan semanal ────────────────────────────────────────────────────────────

@router.get("/{cedula}/plan")
def get_plan(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el plan semanal (running + fuerza) del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Campos clave:
    - week_type: DESCARGA / CONSERVADORA / PROGRESO
    - semaforo: estado del check-in que determinó el week_type
    - targets: km objetivo, días de running, días de fuerza
    - plan_by_day: sesiones por día (Lunes a Domingo)
    - notes: recomendaciones adicionales
    """
    athlete_dir = get_data_dir() / cedula
    data = read_plan(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan no disponible. Ejecuta primero: POST /athletes/{cedula}/pipeline",
        )
    return data


# ─── Historial de features ───────────────────────────────────────────────────

@router.get("/{cedula}/features")
def get_features(
    cedula: str,
    weeks: int = Query(default=12, ge=1, le=104, description="Últimas N semanas de historial"),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el historial semanal de features del atleta.
    Usado para gráficos de evolución (km, ritmo, ACWR, zonas).

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Parámetros:
    - weeks: cuántas semanas incluir (default 12, max 104)
    """
    athlete_dir = get_data_dir() / cedula
    rows = read_features(cedula, athlete_dir if athlete_dir.exists() else None, weeks)
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail="Features no disponibles. Ejecuta primero el pipeline.",
        )
    return sanitize_json({
        "cedula": cedula,
        "weeks_returned": len(rows),
        "weeks_requested": weeks,
        "data": rows,
    })


# ─── Último check-in ─────────────────────────────────────────────────────────

@router.get("/{cedula}/checkin")
def get_latest_checkin(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el último check-in registrado del atleta.
    Incluye el flag is_recent (True si tiene menos de 10 días).

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.
    """
    athlete_dir = get_data_dir() / cedula
    data = read_checkin(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Check-in no disponible. El paso 'ingest' del pipeline no ha corrido.",
        )
    return data


# ─── Actividades Strava ──────────────────────────────────────────────────────

@router.get("/{cedula}/activities")
def get_activities(
    cedula: str,
    limit: int = Query(default=50, ge=1, le=500, description="Máximo de actividades a devolver"),
    from_date: str | None = Query(default=None, description="Fecha inicio ISO, ej: 2025-01-01"),
    to_date: str | None = Query(default=None, description="Fecha fin ISO, ej: 2025-03-12"),
    sport_type: str | None = Query(default=None, description="Filtrar por tipo, ej: 'run'"),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el historial de actividades Strava del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a silver/activities.parquet.

    Parámetros:
    - limit: máximo de actividades (default 50, max 500), ordenadas de más reciente a más antigua
    - from_date: filtrar actividades desde esta fecha (YYYY-MM-DD)
    - to_date: filtrar actividades hasta esta fecha (YYYY-MM-DD)
    - sport_type: filtrar por tipo de actividad (ej: "run", "VirtualRun")

    Campos por actividad:
    - strava_id: ID único de Strava (desde Supabase) o activity_id (desde local)
    - name: nombre de la actividad
    - sport_type: tipo (Run, VirtualRun, etc.)
    - activity_date / start_date_local: fecha/hora local
    - distance_m, duration_sec, elevation_m, avg_pace_sec_km
    """
    athlete_dir = get_data_dir() / cedula
    rows = read_activities(
        cedula,
        athlete_dir if athlete_dir.exists() else None,
        limit=limit,
        from_date=from_date,
        to_date=to_date,
        sport_type=sport_type,
    )
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Activities no disponibles. "
                f"Ejecuta primero: POST /athletes/{cedula}/sync"
            ),
        )
    return sanitize_json({
        "cedula":   cedula,
        "count":    len(rows),
        "limit":    limit,
        "data":     rows,
    })


# ─── Predicción de rendimiento ───────────────────────────────────────────────

@router.get("/{cedula}/prediction")
def get_prediction(
    cedula: str,
    target: str = Query(default="42K", description="Distancia objetivo: 5K, 10K, 21K o 42K"),
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Predice el rango de ritmo esperado para la distancia objetivo.

    Integra Capas 0 (selección PR) + 1 (Riegel calibrado) + 2 (corrección
    demográfica) del sistema de predicción. Los PRs se leen desde profile.json.

    Parámetros:
    - target: distancia objetivo (default 42K)

    Campos clave en la respuesta:
    - pace_range_fmt:  rango de ritmo, p.ej. "5:11 – 5:29 min/km"
    - time_range_fmt:  rango de tiempo total, p.ej. "3:38:50 – 3:52:10"
    - confidence:      ALTA / MEDIA / MEDIA-BAJA / BAJA
    - layers_active:   lista de capas aplicadas (Capa0, Capa1, Capa2)
    - source_pr:       distancia del PR usado como fuente
    - empirical_support: nivel de soporte empírico para esta distancia
    - error: 'SIN_PR' si no hay marcas personales disponibles
    """
    if target not in ("5K", "10K", "21K", "42K"):
        raise HTTPException(
            status_code=422,
            detail=f"Distancia '{target}' no válida. Use: 5K, 10K, 21K, 42K.",
        )

    profile_path = athlete_dir / "meta" / "profile.json"
    if not profile_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. El paso 'ingest' del pipeline no ha corrido.",
        )

    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    from src.ml.predictor import predict_race_time_range

    age    = profile.get("age")
    gender = profile.get("sex")

    result = predict_race_time_range(
        profile=profile,
        target_distance=target,
        age=float(age) if age else None,
        gender=gender,
    )
    return sanitize_json(result)


# ─── Reporte PDF ──────────────────────────────────────────────────────────────

@router.get("/{cedula}/report.pdf")
def get_report_pdf(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Descarga el último reporte PDF generado para el atleta.
    El PDF se genera en el paso 'pdf' del pipeline (legado/fallback).

    Retorna el archivo más reciente en data/athletes/{cedula}/outputs/*.pdf.
    """
    outputs_dir = athlete_dir / "outputs"
    if not outputs_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Directorio outputs/ no encontrado. Ejecuta el pipeline con step 'pdf'.",
        )

    pdfs = sorted(outputs_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        raise HTTPException(
            status_code=404,
            detail=(
                "No hay reportes PDF disponibles. "
                f"Ejecuta: POST /athletes/{cedula}/pipeline?steps=pdf"
            ),
        )

    latest_pdf = pdfs[0]
    return FileResponse(
        path=str(latest_pdf),
        media_type="application/pdf",
        filename=f"coaching_report_{cedula}_{latest_pdf.stem}.pdf",
    )
