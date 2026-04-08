"""
api/routers/athletes.py — Endpoints de consulta por atleta.

Todos los endpoints leen desde data/athletes/{cedula}/ (archivos ya generados
por el pipeline). No modifican datos ni corren cómputo pesado.

Endpoints:
  GET  /athletes                         → lista atletas disponibles
  GET  /athletes/{cedula}/profile        → perfil del atleta (Form 1)
  GET  /athletes/{cedula}/snapshot       → estado actual (última semana + semáforo)
  GET  /athletes/{cedula}/plan           → plan semanal (running + fuerza)
  GET  /athletes/{cedula}/features       → historial de features semanales
  GET  /athletes/{cedula}/prediction     → predicción de tiempo/ritmo por distancia
  GET  /athletes/{cedula}/checkin        → último check-in registrado
  POST /athletes/{cedula}/checkin        → registrar check-in desde la app
  GET  /athletes/{cedula}/training-data  → filas acumuladas para modelo Q2
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

import duckdb
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.deps import get_athlete_dir, get_data_dir, require_api_key, sanitize_json
from src.storage.reader import read_snapshot, read_plan, read_features, read_checkin, read_activities, read_profile


def _build_activities_summary(
    rows: list[dict],
    period: str,
    last_n: int,
    sport_type_filter: "str | None",
) -> list[dict]:
    """
    Agrupa actividades por semana o mes y calcula métricas clave.
    Retorna lista ordenada de más reciente a más antigua (máx last_n períodos).
    """
    if not rows:
        return []

    df = pd.DataFrame(rows)

    # Filtrar por sport_type antes de agregar
    if sport_type_filter:
        mask = df["sport_type"].str.lower().str.contains(sport_type_filter.lower(), na=False)
        df = df[mask]
    if df.empty:
        return []

    # Parsear fechas (activity_date puede traer timezone info)
    df["activity_date"] = pd.to_datetime(df["activity_date"], utc=True, errors="coerce")
    df = df.dropna(subset=["activity_date"])
    df["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    df["pace_sec_per_km"] = pd.to_numeric(df["pace_sec_per_km"], errors="coerce")
    df["average_heartrate"] = pd.to_numeric(df["average_heartrate"], errors="coerce")
    df["elevation_m"] = pd.to_numeric(df["elevation_m"], errors="coerce")
    df["duration_sec"] = pd.to_numeric(df["duration_sec"], errors="coerce")

    # Normalizar a UTC naive antes de asignar período (evita warning de timezone en Period)
    dt_utc = df["activity_date"].dt.tz_convert("UTC").dt.tz_localize(None)
    if period == "week":
        df["period_start"] = dt_utc.dt.to_period("W-SUN").apply(lambda p: p.start_time)
    else:
        df["period_start"] = dt_utc.dt.to_period("M").apply(lambda p: p.start_time)

    # Agregados base
    agg = (
        df.groupby("period_start")
        .agg(
            sessions=("activity_id", "count"),
            km_total=("distance_km", "sum"),
            elevation_m_total=("elevation_m", "sum"),
            duration_sec_total=("duration_sec", "sum"),
            avg_heartrate=("average_heartrate", "mean"),
        )
        .reset_index()
    )

    # Pace promedio ponderado por km (evita distorsión de actividades cortas)
    def _weighted_pace(group: pd.DataFrame) -> "float | None":
        valid = group.dropna(subset=["pace_sec_per_km", "distance_km"])
        valid = valid[valid["distance_km"] > 0]
        if valid.empty:
            return None
        return (valid["pace_sec_per_km"] * valid["distance_km"]).sum() / valid["distance_km"].sum()

    pace_by_period = (
        df.groupby("period_start")
        .apply(_weighted_pace)
        .reset_index(name="avg_pace_sec_km")
    )
    agg = agg.merge(pace_by_period, on="period_start", how="left")

    # Ordenar descendente, tomar los últimos N períodos
    agg = agg.sort_values("period_start", ascending=False).head(last_n).reset_index(drop=True)

    # Formatear para JSON
    result = []
    for _, row in agg.iterrows():
        km = round(float(row["km_total"]), 2) if pd.notna(row["km_total"]) else 0.0
        result.append({
            "period_start":       row["period_start"].strftime("%Y-%m-%d"),
            "period":             period,
            "sessions":           int(row["sessions"]),
            "km_total":           km,
            "elevation_m_total":  round(float(row["elevation_m_total"]), 1) if pd.notna(row["elevation_m_total"]) else None,
            "duration_sec_total": int(row["duration_sec_total"]) if pd.notna(row["duration_sec_total"]) else None,
            "avg_pace_sec_km":    round(float(row["avg_pace_sec_km"]), 1) if pd.notna(row["avg_pace_sec_km"]) else None,
            "avg_heartrate":      round(float(row["avg_heartrate"]), 1) if pd.notna(row["avg_heartrate"]) else None,
        })
    return result

router = APIRouter()


# ─── Lista de atletas ────────────────────────────────────────────────────────

@router.get("")
def list_athletes(_: None = Depends(require_api_key)):
    """
    Devuelve los atletas registrados.
    Prioridad: Supabase tabla athletes → fallback data/athletes/ local.
    """
    # ── 1. Supabase first (funciona en Railway sin disco local) ───────────────
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if client:
            res = client.table("athletes").select("cedula").order("cedula").execute()
            if res.data:
                cedulas = [r["cedula"] for r in res.data if r.get("cedula")]
                return {"athletes": cedulas, "count": len(cedulas)}
    except Exception as exc:
        print(f"[athletes] Supabase list error: {exc}")

    # ── 2. Fallback local ─────────────────────────────────────────────────────
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
    _: None = Depends(require_api_key),
):
    """
    Devuelve el perfil del atleta extraído del Form de ingreso.
    Incluye datos personales, objetivos, ritmos reportados y preferencias.
    Lee desde Supabase primero; fallback a archivo local.
    """
    athlete_dir = get_data_dir() / cedula
    data = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. Ejecuta primero: POST /athletes/{cedula}/sync",
        )
    return data


# ─── Actualizar perfil (carrera objetivo) ────────────────────────────────────


class RaceGoalUpdate(BaseModel):
    race_name: Optional[str] = None
    race_distance: Optional[str] = None
    race_date: Optional[str] = None  # YYYY-MM-DD


@router.patch("/{cedula}/profile/race-goal")
def update_race_goal(
    cedula: str,
    body: RaceGoalUpdate,
    _: None = Depends(require_api_key),
):
    """
    Actualiza la carrera objetivo del atleta en el perfil.
    Modifica: race_name, race_distance, race_date_raw en profile.json y Supabase.
    """
    from src.storage.supabase_client import get_client
    from src.storage.writer import push_profile

    # 1. Leer perfil actual
    athlete_dir = get_data_dir() / cedula
    data = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")

    # 2. Actualizar campos
    changed = []
    if body.race_name is not None:
        data["race_name"] = body.race_name.strip() or None
        changed.append("race_name")
    if body.race_distance is not None:
        data["race_distance"] = body.race_distance.strip() or None
        changed.append("race_distance")
    if body.race_date is not None:
        data["race_date_raw"] = body.race_date.strip() or None
        changed.append("race_date_raw")

    if not changed:
        return {"ok": True, "changed": [], "detail": "Sin cambios"}

    # 3. Guardar localmente
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    profile_path = meta_dir / "profile.json"
    import json as _json
    profile_path.write_text(
        _json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Push a Supabase
    push_result = push_profile(cedula, athlete_dir)

    return {
        "ok": True,
        "changed": changed,
        "race_name": data.get("race_name"),
        "race_distance": data.get("race_distance"),
        "race_date_raw": data.get("race_date_raw"),
        "supabase": push_result.get("detail"),
    }


# ─── Onboarding completo ─────────────────────────────────────────────────────


class OnboardingProfile(BaseModel):
    name: str
    cedula: str
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    city_country: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[int] = None
    weight_kg: Optional[float] = None
    birth_date_raw: Optional[str] = None
    goal_main: Optional[str] = None
    race_distance: Optional[str] = None
    race_date_raw: Optional[str] = None
    race_name: Optional[str] = None
    has_time_goal: Optional[bool] = None
    time_goal_sec: Optional[int] = None
    days_run_per_week: Optional[int] = None
    weekday_session_min_min: Optional[int] = None
    weekday_session_max_min: Optional[int] = None
    weekend_session_min_min: Optional[int] = None
    weekend_session_max_min: Optional[int] = None
    preferred_run_days: Optional[list[str]] = None
    training_time_pref: Optional[str] = None
    strength_access: Optional[str] = None
    strength_days_per_week: Optional[int] = None
    other_sports: Optional[str] = None
    sleep_hours_raw: Optional[str] = None
    has_pain: Optional[bool] = None
    pain_location: Optional[str] = None
    pain_level_0_10: Optional[int] = None
    had_injuries_12m: Optional[bool] = None
    medical_conditions: Optional[str] = None
    running_experience: Optional[str] = None
    km_week_min: Optional[float] = None
    km_week_max: Optional[float] = None
    avg_days_running_4w: Optional[int] = None
    long_run_recent: Optional[str] = None
    surface: Optional[str] = None
    has_recent_prs: Optional[bool] = None
    pr_5k_sec: Optional[float] = None
    pr_10k_sec: Optional[float] = None
    pr_21k_sec: Optional[float] = None
    pr_42k_sec: Optional[float] = None
    easy_pace_sec_per_km: Optional[float] = None
    mod_pace_sec_per_km: Optional[float] = None
    fast_pace_sec_per_km: Optional[float] = None
    uses_device: Optional[bool] = None
    main_app: Optional[str] = None
    uses_strava: Optional[bool] = None
    consent_plan: Optional[bool] = None
    consent_anon: Optional[bool] = None
    consent_strava: Optional[bool] = None


@router.post("/{cedula}/profile/onboarding")
def create_onboarding_profile(
    cedula: str,
    body: OnboardingProfile,
    _: None = Depends(require_api_key),
):
    """
    Recibe el perfil completo de onboarding y lo persiste.
    Merge: los campos enviados como None no sobreescriben valores existentes.
    Escribe en profile.json local y sincroniza a Supabase.
    """
    from src.storage.writer import push_profile

    if body.cedula != cedula:
        raise HTTPException(
            status_code=400,
            detail="La cédula en el body no coincide con la URL",
        )

    # 1. Leer perfil existente (si hay)
    athlete_dir = get_data_dir() / cedula
    existing: dict = {}
    profile_path = athlete_dir / "meta" / "profile.json"
    if profile_path.exists():
        try:
            existing = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    else:
        # Try Supabase
        data_from_reader = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
        if data_from_reader:
            existing = data_from_reader

    # 2. Merge: new values override, but None values don't overwrite existing
    incoming = body.model_dump(exclude_none=True)
    merged = {**existing, **incoming}

    # 3. Write locally
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Push to Supabase
    push_result = push_profile(cedula, athlete_dir)

    return {
        "ok": True,
        "cedula": cedula,
        "supabase": push_result.get("detail"),
    }


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


# ─── Registrar check-in desde la app ─────────────────────────────────────────

class WeeklyCheckinIn(BaseModel):
    type: Literal["weekly"] = "weekly"
    sleep_1_5: int = Field(..., ge=1, le=5)
    energy_1_5: int = Field(..., ge=1, le=5)
    has_pain: bool = False
    pain_location: Optional[str] = None
    checkin_date: Optional[date] = None  # default: hoy


class RaceCheckinIn(BaseModel):
    type: Literal["race"] = "race"
    race_distance_km: float = Field(..., gt=0)
    race_time_sec: int = Field(..., gt=0)
    sensation_1_5: int = Field(..., ge=1, le=5)
    is_official: bool = False
    race_date: Optional[date] = None  # default: hoy
    avg_heartrate: Optional[float] = Field(None, ge=40, le=250, description="FC promedio (bpm)")
    max_heartrate: Optional[float] = Field(None, ge=40, le=250, description="FC máxima (bpm)")


@router.post("/{cedula}/checkin")
def post_checkin(
    cedula: str,
    body: WeeklyCheckinIn | RaceCheckinIn,
    _: None = Depends(require_api_key),
):
    """
    Registra un check-in enviado desde la app (semanal o post-carrera).

    - type=weekly   : sueño, energía, dolor/lesión
    - type=race     : resultado de carrera + sensación

    Escribe en Supabase (checkins table) y en latest_checkin.json local.
    Si Supabase no está disponible, solo escribe localmente.
    """
    from src.storage.supabase_client import get_client

    today = date.today().isoformat()

    if body.type == "weekly":
        checkin_date = body.checkin_date.isoformat() if body.checkin_date else today
        raw = {
            "cedula": cedula,
            "source": "app_weekly",
            "checkin_date": checkin_date,
            "checkin_week_start": checkin_date,
            "timestamp": datetime.utcnow().isoformat(),
            "is_recent": True,
            # Mapeo a campos existentes para compatibilidad con el dashboard
            "feeling_1_10": body.energy_1_5 * 2,       # 1-5 → 2-10
            "fatigue_1_10": (6 - body.energy_1_5) * 2, # inverso
            "sleep_1_5": body.sleep_1_5,
            "energy_1_5": body.energy_1_5,
            "pain_0_10": 5 if body.has_pain else 0,
            "has_pain": body.has_pain,
            "pain_location": body.pain_location or "",
            "pain_where": body.pain_location or "",  # compat legacy
            "sessions_completed": None,
            "skipped_sessions": None,
            "comments": "",
        }
    else:  # race
        race_date = body.race_date.isoformat() if body.race_date else today
        raw = {
            "cedula": cedula,
            "source": "app_race",
            "checkin_date": race_date,
            "checkin_week_start": race_date,
            "timestamp": datetime.utcnow().isoformat(),
            "is_recent": True,
            "race_distance_km": body.race_distance_km,
            "race_time_sec": body.race_time_sec,
            "sensation_1_5": body.sensation_1_5,
            "is_official": body.is_official,
            "race_date": race_date,
            "avg_heartrate": body.avg_heartrate,
            "max_heartrate": body.max_heartrate,
            # Compatibilidad con dashboard
            "feeling_1_10": body.sensation_1_5 * 2,
            "fatigue_1_10": None,
            "pain_0_10": None,
            "sessions_completed": None,
            "skipped_sessions": None,
            "comments": "",
        }

    # 1. Escribir en Supabase
    supabase_ok = False
    supabase_error = None
    client = get_client()
    if client:
        try:
            # Ensure athlete row exists (FK constraint on checkins)
            client.table("athletes").upsert(
                {"cedula": cedula}, on_conflict="cedula"
            ).execute()
            client.table("checkins").upsert({
                "cedula": cedula,
                "checkin_date": raw["checkin_date"],
                "raw": raw,
            }, on_conflict="cedula,checkin_date").execute()
            supabase_ok = True
        except Exception as exc:
            supabase_error = str(exc)

    # 2. Escribir localmente
    athlete_dir = get_data_dir() / cedula
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    local_path = meta_dir / "latest_checkin.json"
    payload = {"cedula": cedula, "is_recent": True, "latest_checkin": raw}
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # 3. Si es carrera, guardar snapshot de features para dataset Q2
    snapshot_saved = False
    if body.type == "race":
        snapshot_saved = _save_training_snapshot(
            cedula=cedula,
            race_date=raw["checkin_date"],
            race_distance_km=body.race_distance_km,
            race_time_sec=body.race_time_sec,
            sensation_1_5=body.sensation_1_5,
            is_official=body.is_official,
            avg_heartrate=body.avg_heartrate,
            max_heartrate=body.max_heartrate,
        )

    result = {
        "status": "ok",
        "type": body.type,
        "checkin_date": raw["checkin_date"],
        "supabase": supabase_ok,
        "local": True,
    }
    if supabase_error:
        result["supabase_error"] = supabase_error
    if body.type == "race":
        result["training_snapshot_saved"] = snapshot_saved
    return result


def _save_training_snapshot(
    cedula: str,
    race_date: str,
    race_distance_km: float,
    race_time_sec: int,
    sensation_1_5: int,
    is_official: bool,
    avg_heartrate: Optional[float] = None,
    max_heartrate: Optional[float] = None,
) -> bool:
    """
    Guarda una fila de entrenamiento para el modelo Q2.

    Combina:
      - Resultado (distancia, tiempo, sensación, FC)
      - Features de carga de la semana previa (CTL, ATL, TSB, ACWR, km_week, etc.)
      - Perfil del atleta (edad, género)
      - Último check-in semanal disponible (sueño, energía)

    FC (avg_heartrate, max_heartrate) disponible cuando viene de Strava o check-in manual.
    Permite calcular eficiencia aeróbica (pace/FC) en NB09.

    Escribe en:
      - Local:    data/{cedula}/training_data/q2_rows.jsonl  (una fila JSON por línea)
      - Supabase: tabla training_snapshots (si está disponible)

    Retorna True si se guardó correctamente.
    """
    try:
        athlete_dir = get_data_dir() / cedula

        # ── 1. Features de la semana previa a la carrera ──────────────────────
        load_features: dict = {}
        features_path = athlete_dir / "features" / "weekly_features.parquet"
        if features_path.exists():
            df = pd.read_parquet(features_path)
            if not df.empty and "week_start" in df.columns:
                df["week_start"] = pd.to_datetime(df["week_start"])
                race_dt = pd.to_datetime(race_date)
                # Tomar la semana más reciente antes o igual a la fecha de carrera
                prev = df[df["week_start"] <= race_dt].sort_values("week_start")
                if not prev.empty:
                    row = prev.iloc[-1]
                    keep_cols = [
                        "km_week", "sessions", "long_run_km",
                        "ctl", "atl", "tsb", "acwr",
                        "pace_delta_4s_sec", "racha_semanas", "km_trend",
                        "fondo_largo_4s", "semana_spike",
                    ]
                    for col in keep_cols:
                        if col in row.index:
                            val = row[col]
                            load_features[col] = None if pd.isna(val) else float(val)

        # ── 2. Perfil del atleta (edad, género, PRs) ──────────────────────────
        profile_features: dict = {}
        profile_path = athlete_dir / "meta" / "profile.json"
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_features["age"] = profile.get("age")
            profile_features["gender"] = profile.get("gender")
            profile_features["pr_5k_sec"]  = profile.get("pr_5k_sec")
            profile_features["pr_10k_sec"] = profile.get("pr_10k_sec")
            profile_features["pr_21k_sec"] = profile.get("pr_21k_sec")
            profile_features["pr_42k_sec"] = profile.get("pr_42k_sec")

        # ── 3. Último check-in semanal disponible (sueño, energía) ───────────
        subjective_features: dict = {}
        latest_checkin_path = athlete_dir / "meta" / "latest_checkin.json"
        if latest_checkin_path.exists():
            lc = json.loads(latest_checkin_path.read_text(encoding="utf-8"))
            lc_data = lc.get("latest_checkin", {})
            if lc_data.get("source") == "app_weekly":
                subjective_features["sleep_1_5"]  = lc_data.get("sleep_1_5")
                subjective_features["energy_1_5"] = lc_data.get("energy_1_5")
                subjective_features["has_pain"]   = lc_data.get("has_pain")

        # ── 4. Target y metadata ──────────────────────────────────────────────
        pace_sec_km = race_time_sec / race_distance_km if race_distance_km > 0 else None
        dist_bucket = (
            "5K"  if race_distance_km <= 6 else
            "10K" if race_distance_km <= 12 else
            "21K" if race_distance_km <= 23 else
            "42K"
        )

        # Eficiencia aeróbica: pace / FC (útil para Q2)
        # Menor valor = más eficiente (menor ritmo por latido)
        aerobic_efficiency = None
        if avg_heartrate and avg_heartrate > 0 and pace_sec_km:
            aerobic_efficiency = round(pace_sec_km / avg_heartrate, 4)

        row_data = {
            # Identificadores
            "cedula":            cedula,
            "race_date":         race_date,
            "recorded_at":       datetime.utcnow().isoformat(),
            # Target
            "race_distance_km":  race_distance_km,
            "race_time_sec":     race_time_sec,
            "pace_sec_km":       round(pace_sec_km, 2) if pace_sec_km else None,
            "dist_bucket":       dist_bucket,
            # Sensación subjetiva post-carrera
            "sensation_1_5":     sensation_1_5,
            "is_official":       is_official,
            # FC — disponible desde Strava o check-in manual con monitor
            "avg_heartrate":     avg_heartrate,
            "max_heartrate":     max_heartrate,
            "aerobic_efficiency": aerobic_efficiency,  # pace_sec_km / avg_hr
            # Features de carga (semana previa)
            **load_features,
            # Perfil
            **profile_features,
            # Subjetivo semanal
            **subjective_features,
        }

        # ── 5. Escribir localmente en JSONL ───────────────────────────────────
        td_dir = athlete_dir / "training_data"
        td_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = td_dir / "q2_rows.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row_data, ensure_ascii=False, default=str) + "\n")

        # ── 6. Escribir en Supabase ───────────────────────────────────────────
        from src.storage.supabase_client import get_client
        client = get_client()
        if client:
            try:
                client.table("training_snapshots").upsert(
                    {"cedula": cedula, "race_date": race_date, "data": row_data},
                    on_conflict="cedula,race_date",
                ).execute()
            except Exception:
                pass  # local ya guardado, Supabase es best-effort

        return True

    except Exception:
        return False  # silencioso — no rompemos el check-in por esto


# ─── Historial de check-ins ─────────────────────────────────────────────────

@router.get("/{cedula}/checkin-history")
def get_checkin_history(
    cedula: str,
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_api_key),
):
    """
    Retorna los últimos N check-ins del atleta (semanales + carreras),
    ordenados por fecha descendente. Lee de Supabase.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        return {"cedula": cedula, "checkins": [], "error": "Supabase no disponible"}

    try:
        resp = (
            sb.table("checkins")
            .select("checkin_date, raw")
            .eq("cedula", cedula)
            .order("checkin_date", desc=True)
            .limit(limit)
            .execute()
        )
        rows = []
        for r in resp.data or []:
            raw = r.get("raw") or {}
            rows.append({
                "checkin_date": r["checkin_date"],
                "type": raw.get("source", "unknown"),
                "sleep_1_5": raw.get("sleep_1_5"),
                "energy_1_5": raw.get("energy_1_5"),
                "has_pain": raw.get("has_pain"),
                "pain_location": raw.get("pain_location"),
                "feeling_1_10": raw.get("feeling_1_10"),
                "fatigue_1_10": raw.get("fatigue_1_10"),
                "pain_0_10": raw.get("pain_0_10"),
                # Race fields
                "race_distance_km": raw.get("race_distance_km"),
                "race_time_sec": raw.get("race_time_sec"),
                "sensation_1_5": raw.get("sensation_1_5"),
                "is_official": raw.get("is_official"),
            })
        return {"cedula": cedula, "count": len(rows), "checkins": rows}
    except Exception as e:
        return {"cedula": cedula, "checkins": [], "error": str(e)}


# ─── Dataset Q2: leer filas acumuladas ────────────────────────────────────────

@router.get("/{cedula}/training-data")
def get_training_data(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Retorna todas las filas del dataset Q2 acumuladas para este atleta.
    Cada fila = una carrera reportada + features de carga de esa semana.
    Útil para NB09 (personalización walk-forward).
    """
    athlete_dir = get_data_dir() / cedula
    jsonl_path = athlete_dir / "training_data" / "q2_rows.jsonl"

    if not jsonl_path.exists():
        return {"cedula": cedula, "n_rows": 0, "rows": []}

    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    return {
        "cedula":  cedula,
        "n_rows":  len(rows),
        "rows":    rows,
    }


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


# ─── Summary de actividades ──────────────────────────────────────────────────

@router.get("/{cedula}/activities/summary")
def get_activities_summary(
    cedula: str,
    period: str = Query(default="week", description="Agrupar por 'week' o 'month'"),
    last_n: int = Query(default=8, ge=1, le=52, description="Últimos N períodos a devolver"),
    sport_type: str | None = Query(default=None, description="Filtrar por tipo, ej: 'run'"),
    _: None = Depends(require_api_key),
):
    """
    Resumen de actividades agrupado por semana o mes. Listo para gráficas.

    Lee todas las actividades disponibles (Supabase → local) y las agrega
    por el período solicitado.

    Parámetros:
    - period: 'week' (lunes como inicio) o 'month'
    - last_n: cuántos períodos devolver (default 8, max 52)
    - sport_type: filtrar antes de agregar, ej: 'run', 'Ride'

    Campos por período:
    - period_start: fecha de inicio del período (YYYY-MM-DD)
    - sessions: número de actividades
    - km_total: km totales
    - elevation_m_total: desnivel acumulado
    - duration_sec_total: tiempo total en segundos
    - avg_pace_sec_km: pace promedio ponderado por km (None si no hay runs)
    - avg_heartrate: FC promedio (None si no hay datos de HR)
    """
    if period not in ("week", "month"):
        raise HTTPException(
            status_code=422,
            detail="El parámetro 'period' debe ser 'week' o 'month'.",
        )

    athlete_dir = get_data_dir() / cedula
    # Leer todas las actividades disponibles para poder agrupar
    rows = read_activities(
        cedula,
        athlete_dir if athlete_dir.exists() else None,
        limit=1000,  # suficiente para cualquier atleta real
    )
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail=f"Activities no disponibles. Ejecuta primero: POST /athletes/{cedula}/sync",
        )

    summary = _build_activities_summary(rows, period, last_n, sport_type)
    return sanitize_json({
        "cedula":   cedula,
        "period":   period,
        "count":    len(summary),
        "data":     summary,
    })


# ─── Dashboard / Home del atleta ─────────────────────────────────────────────

@router.get("/{cedula}/dashboard")
def get_dashboard(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Resumen agregado para la vista principal (home) del atleta.

    Reúne en una sola llamada los datos más relevantes para el dashboard:
    - athlete:           nombre, objetivo, días para la carrera
    - status:            semáforo, readiness, ACWR zone, estado check-in
    - week:              km hechos vs objetivo, tipo semana, foco semanal
    - load:              CTL, TSB, racha_semanas, km_trend, semana_spike
    - recent_activities: últimas 5 actividades (schema canónico)
    - volume_trend:      resumen semanal de las últimas 4 semanas

    Fuentes: Supabase primero, fallback local para cada sección.
    Las secciones se incluyen aunque falten datos (null en lugar de 404).
    """
    athlete_dir = get_data_dir() / cedula
    adir = athlete_dir if athlete_dir.exists() else None

    # ── Lecturas paralelas (todas tolerantes a None) ──────────────────────────
    snap     = read_snapshot(cedula, adir) or {}
    plan     = read_plan(cedula, adir) or {}
    chk_wrap = read_checkin(cedula, adir) or {}
    acts     = read_activities(cedula, adir, limit=100) or []

    chk = chk_wrap.get("latest_checkin") or {}
    profile = snap.get("profile") or {}
    lw      = snap.get("latest_week") or {}

    # Separar actividades: running vs. cross-training
    runs  = [a for a in acts if "run" in (a.get("sport_type") or "").lower()]
    cross = [a for a in acts if "run" not in (a.get("sport_type") or "").lower()]

    # ── athlete ───────────────────────────────────────────────────────────────
    athlete_section = {
        "name":               profile.get("name"),
        "race_distance":      profile.get("race_distance"),
        "race_date_raw":      profile.get("race_date_raw"),
        "race_countdown_days": snap.get("race_countdown_days"),
        "time_goal_formatted": snap.get("time_goal_formatted") or profile.get("time_goal_formatted"),
        "pr_21k_sec":         profile.get("pr_21k_sec"),
        "pr_42k_sec":         profile.get("pr_42k_sec"),
    }

    # ── status ────────────────────────────────────────────────────────────────
    status_section = {
        "semaforo":        snap.get("semaforo_latest_checkin"),
        "readiness_score": snap.get("readiness_score"),
        "acwr_zone":       snap.get("acwr_zone_latest"),
        "data_weeks_available": snap.get("data_weeks_available"),
        "checkin_is_recent":    chk_wrap.get("is_recent", False),
        "last_checkin_date":    chk.get("checkin_date"),
        "last_checkin_feeling": chk.get("feeling_1_10"),
        "last_checkin_fatigue": chk.get("fatigue_1_10"),
        "last_checkin_pain":    chk.get("pain_0_10"),
    }

    # ── week ──────────────────────────────────────────────────────────────────
    targets = plan.get("targets") or {}
    week_section = {
        "week_start":    lw.get("week_start"),
        "week_type":     plan.get("week_type"),
        "weekly_focus":  plan.get("weekly_focus"),
        "week_summary":  plan.get("week_summary"),
        "km_done":       round(lw.get("km_week") or 0, 2),
        "km_target":     targets.get("target_km_week"),
        "sessions_done": lw.get("sessions_week"),
        "days_running":  targets.get("days_running"),
        "days_strength": targets.get("days_strength"),
        "notes":         (plan.get("notes") or [None])[0],
    }

    # ── load ──────────────────────────────────────────────────────────────────
    load_section = {
        "ctl":           round(lw.get("ctl") or 0, 2),
        "atl":           round(lw.get("atl") or 0, 2),
        "tsb":           round(lw.get("tsb") or 0, 2),
        "acwr":          round(lw.get("acwr") or 0, 3),
        "racha_semanas": lw.get("racha_semanas"),
        "km_trend":      lw.get("km_trend"),
        "semana_spike":  lw.get("semana_spike", False),
        "pctZ1":         lw.get("pctZ1"),
        "pctZ2":         lw.get("pctZ2"),
        "pctZ3":         lw.get("pctZ3"),
        "pctZ4":         lw.get("pctZ4"),
    }

    # ── recent_runs (últimas 5 carreras) — núcleo del coaching ───────────────
    recent_runs = runs[:5]

    # ── running_trend (4 semanas, solo runs) — para gráfica de volumen ───────
    running_trend = _build_activities_summary(runs, "week", 4, None)

    # ── recent_cross_training (últimas 5 actividades no running) ─────────────
    # Ordenadas por fecha desc; sin métricas de carga (no contaminan el plan)
    recent_cross = [
        {
            "activity_id":  a.get("activity_id"),
            "name":         a.get("name"),
            "sport_type":   a.get("sport_type"),
            "activity_date": a.get("activity_date"),
            "distance_km":  a.get("distance_km"),
            "duration_sec": a.get("duration_sec"),
        }
        for a in cross[:5]
    ]

    return sanitize_json({
        "cedula":               cedula,
        "generated_at":         snap.get("generated_at"),
        "athlete":              athlete_section,
        "status":               status_section,
        "week":                 week_section,
        "load":                 load_section,
        "recent_runs":          recent_runs,
        "running_trend":        running_trend,
        "recent_cross_training": recent_cross,
    })


# ─── Predicción de rendimiento ───────────────────────────────────────────────

@router.get("/{cedula}/prediction")
def get_prediction(
    cedula: str,
    target: str = Query(default="42K", description="Distancia objetivo: 5K, 10K, 21K o 42K"),
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

    athlete_dir_pred = get_data_dir() / cedula
    profile = read_profile(cedula, athlete_dir_pred if athlete_dir_pred.exists() else None)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. Ejecuta primero: POST /athletes/{cedula}/sync",
        )

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


# ─── Strava token status (read-only — para validar que Supabase tiene tokens) ──

@router.get("/{cedula}/strava/token-status")
def strava_token_status(cedula: str, _: None = Depends(require_api_key)):
    """
    Lee el estado de los tokens de Strava desde Supabase.
    Solo muestra sufijos (últimos 6 chars) — nunca expone tokens completos.
    Usado para validar que POST /strava/token realmente escribió en Supabase.
    """
    from src.storage.supabase_client import get_client
    client = get_client()
    if not client:
        return {"ok": False, "detail": "Supabase not configured"}
    try:
        resp = (
            client.table("athletes")
            .select("cedula,strava_access_token,strava_refresh_token,strava_token_expires_at,strava_last_sync_at")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return {"ok": False, "detail": f"Athlete {cedula} not found in Supabase"}
        row = resp.data[0]
        at  = row.get("strava_access_token")  or ""
        rt  = row.get("strava_refresh_token") or ""
        return {
            "ok":      True,
            "cedula":  cedula,
            "access_token_suffix":   f"…{at[-6:]}"  if at  else None,
            "refresh_token_suffix":  f"…{rt[-6:]}"  if rt  else None,
            "expires_at":            row.get("strava_token_expires_at"),
            "last_sync_at":          row.get("strava_last_sync_at"),
            "is_test_token":         at.startswith("BRIDGE_TEST_") if at else False,
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# ─── Strava token ingestion (called by ar-athletes-portal after OAuth) ────────

class StravaTokenBody(BaseModel):
    access_token:  str
    refresh_token: str
    expires_at:    int  # unix timestamp


@router.post("/{cedula}/strava/token")
def store_strava_token(
    cedula:     str,
    body:       StravaTokenBody,
    background: BackgroundTasks,
    _:          None = Depends(require_api_key),
):
    """
    Recibe y persiste los tokens de Strava en Supabase para un atleta.
    Llamado por ar-athletes-portal tras completar el flujo OAuth.
    Requiere migration 005_strava_tokens.sql aplicada en Supabase.

    Después de llamar este endpoint, el step strava del sync leerá
    los tokens desde Supabase en lugar de Google Sheets.
    """
    import time
    print(
        f"[/strava/token] Recibido para cedula={cedula}: "
        f"expires_at={body.expires_at} (margen={(body.expires_at - int(time.time()))}s) | "
        f"access_token=…{body.access_token[-6:]} | refresh_token=…{body.refresh_token[-6:]}"
    )
    from src.storage.writer import push_strava_tokens
    result = push_strava_tokens(cedula, body.access_token, body.refresh_token, body.expires_at)
    print(f"[/strava/token] push_strava_tokens result: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("detail", "Error writing tokens"))

    # Auto-trigger pipeline en background: ingest → strava → features → plan → push Supabase
    # ingest es necesario para que athlete_profiles se llene desde Google Sheets
    from api.routers.sync import _run_pipeline_and_push
    background.add_task(
        _run_pipeline_and_push,
        cedula,
        ["ingest", "strava", "features", "plan"],
        False,   # skip_strava=False
        True,    # push_to_supabase=True
    )
    print(f"[/strava/token] Pipeline auto-trigger encolado para cedula={cedula}")

    return {"ok": True, "cedula": cedula, "detail": result.get("detail"), "pipeline": "enqueued"}


# ─── Backfill: Strava activities → check-ins ────────────────────────────────

@router.get("/{cedula}/strava-runs")
def get_strava_runs_without_checkin(
    cedula: str,
    days: int = Query(default=90, ge=1, le=365),
    _: None = Depends(require_api_key),
):
    """
    Lista runs de Strava que NO tienen un check-in correspondiente.
    Útil para que el coach vea qué actividades puede importar.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Cutoff: febrero 2026 (inicio del proyecto) — no importar datos antiguos
    project_start = "2026-02-01"
    cutoff = max(
        project_start,
        (date.today() - __import__("datetime").timedelta(days=days)).isoformat(),
    )

    # 1. Traer runs de Strava desde el inicio del proyecto
    activities = (
        sb.table("activities")
        .select("strava_id,activity_date,name,sport_type,distance_m,duration_sec,avg_pace_sec_km,raw")
        .eq("cedula", cedula)
        .in_("sport_type", ["Run", "TrailRun"])
        .gte("activity_date", project_start)
        .order("activity_date", desc=True)
        .execute()
    ).data or []

    # 2. Traer check-ins existentes (solo fechas)
    checkins = (
        sb.table("checkins")
        .select("checkin_date")
        .eq("cedula", cedula)
        .gte("checkin_date", cutoff)
        .execute()
    ).data or []
    checkin_dates = {r["checkin_date"] for r in checkins}

    # 3. Filtrar actividades sin check-in
    missing = []
    for act in activities:
        act_date = (act.get("activity_date") or "")[:10]
        if act_date not in checkin_dates:
            dist_km = round((act.get("distance_m") or 0) / 1000, 2)
            dur_sec = act.get("duration_sec") or 0
            raw = act.get("raw") or {}
            missing.append({
                "strava_id": act["strava_id"],
                "date": act_date,
                "name": act.get("name", ""),
                "sport_type": act.get("sport_type"),
                "distance_km": dist_km,
                "duration_sec": dur_sec,
                "pace_sec_km": round(dur_sec / max(dist_km, 0.01), 1) if dist_km > 0.3 else None,
                "avg_heartrate": raw.get("average_heartrate"),
                "max_heartrate": raw.get("max_heartrate"),
            })

    return {
        "cedula": cedula,
        "days": days,
        "total_runs": len(activities),
        "already_have_checkin": len(activities) - len(missing),
        "missing_checkin": len(missing),
        "runs": missing,
    }


class BackfillRequest(BaseModel):
    """
    Importa actividades seleccionadas de Strava como check-ins.

    El coach selecciona manualmente qué actividades importar desde el panel.
    Cada check-in queda marcado con source='coach_from_strava' para
    transparencia total sobre el origen de los datos.
    """
    strava_ids: list[str] = Field(..., min_length=1, description="IDs de Strava a importar (selección manual del coach)")
    mark_as_official: list[str] = Field(default_factory=list, description="IDs que el coach marca como carrera oficial")
    min_distance_km: float = Field(default=2.0, ge=0, description="Distancia mínima para importar")


@router.post("/{cedula}/backfill-strava")
def backfill_strava_to_checkins(
    cedula: str,
    body: BackfillRequest,
    _: None = Depends(require_api_key),
):
    """
    Importa actividades de Strava seleccionadas por el coach como check-ins.

    Proceso transparente:
      1. El coach ve las actividades en el panel y selecciona cuáles importar
      2. El coach decide cuáles marcar como carrera oficial vs entrenamiento
      3. Cada check-in queda marcado con source='coach_from_strava'
      4. Se genera training_snapshot para el modelo Q2

    NO consulta la API de Strava — usa datos ya almacenados con consentimiento.
    Requiere strava_ids explícitos — no hay importación masiva automática.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Solo traer las actividades específicas seleccionadas por el coach
    id_set = set(body.strava_ids)
    official_set = set(body.mark_as_official)

    activities = (
        sb.table("activities")
        .select("strava_id,activity_date,name,sport_type,distance_m,duration_sec,raw")
        .eq("cedula", cedula)
        .in_("sport_type", ["Run", "TrailRun"])
        .order("activity_date", desc=True)
        .execute()
    ).data or []

    # Filtrar solo los IDs seleccionados manualmente
    activities = [a for a in activities if a["strava_id"] in id_set]

    # Filtrar por distancia mínima
    activities = [a for a in activities if (a.get("distance_m") or 0) / 1000 >= body.min_distance_km]

    # Crear check-ins
    imported = []
    errors = []
    snapshots_saved = 0

    # Ensure athlete exists in Supabase (FK constraint)
    try:
        sb.table("athletes").upsert(
            {"cedula": cedula}, on_conflict="cedula"
        ).execute()
    except Exception:
        pass

    for act in activities:
        act_date = (act.get("activity_date") or "")[:10]
        dist_km = round((act.get("distance_m") or 0) / 1000, 2)
        dur_sec = act.get("duration_sec") or 0
        raw_strava = act.get("raw") or {}
        is_official = act["strava_id"] in official_set

        # Build check-in payload — transparente sobre el origen
        raw = {
            "source": "coach_from_strava",
            "checkin_date": act_date,
            "checkin_type": "race",
            "race_distance_km": dist_km,
            "race_time_sec": dur_sec,
            "sensation_1_5": 3,  # neutral — no disponible desde Strava
            "is_official": is_official,
            "strava_id": act["strava_id"],
            "strava_name": act.get("name", ""),
            "avg_heartrate": raw_strava.get("average_heartrate"),
            "max_heartrate": raw_strava.get("max_heartrate"),
        }

        try:
            sb.table("checkins").upsert({
                "cedula": cedula,
                "checkin_date": act_date,
                "raw": raw,
            }, on_conflict="cedula,checkin_date").execute()

            imported.append({
                "date": act_date,
                "distance_km": dist_km,
                "duration_sec": dur_sec,
                "name": act.get("name", ""),
                "is_official": is_official,
            })

            # Save training snapshot for Q2 model (with FC from Strava)
            try:
                ok = _save_training_snapshot(
                    cedula=cedula,
                    race_date=act_date,
                    race_distance_km=dist_km,
                    race_time_sec=dur_sec,
                    sensation_1_5=3,
                    is_official=is_official,
                    avg_heartrate=raw_strava.get("average_heartrate"),
                    max_heartrate=raw_strava.get("max_heartrate"),
                )
                if ok:
                    snapshots_saved += 1
            except Exception:
                pass

        except Exception as exc:
            errors.append({"date": act_date, "error": str(exc)})

    return {
        "cedula": cedula,
        "imported": len(imported),
        "snapshots_saved": snapshots_saved,
        "errors": len(errors),
        "error_details": errors[:5] if errors else [],
        "runs": imported,
    }
