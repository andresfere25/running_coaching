"""
src/storage/writer.py — Escritura dual (local → Supabase).

Fase A: Lee los archivos ya generados por el pipeline local y los sube a Supabase.
No modifica ni depende de la lógica del pipeline.

Cada función retorna un dict {"ok": bool, "detail": str}.
Si el cliente Supabase no está configurado, retorna ok=True con detail="skipped".

Funciones públicas:
    push_all(cedula, athlete_dir)        → sube todo lo disponible
    push_athlete(cedula, name)           → upsert tabla athletes
    push_profile(cedula, dir)            → upsert athlete_profiles
    push_snapshot(cedula, dir)           → upsert athlete_snapshots
    push_weekly_features(cedula, dir)    → upsert weekly_features (histórico completo)
    push_plan(cedula, dir)               → upsert weekly_plans (plan actual)
    push_checkin(cedula, dir)            → upsert checkins (último check-in)
    push_coach_content(cedula, data)     → upsert coach_content (publicado del coach)
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.storage.supabase_client import get_client


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ok(detail: str = "ok") -> dict:
    return {"ok": True, "detail": detail}


def _err(detail: str) -> dict:
    return {"ok": False, "detail": detail}


def _skipped() -> dict:
    return {"ok": True, "detail": "skipped (Supabase not configured)"}


def _clean(obj: Any) -> Any:
    """Convierte tipos no serializables por supabase-py (date → str, NaN/NA → None)."""
    import math
    try:
        import pandas as pd
        if obj is pd.NA or obj is pd.NaT:
            return None
    except ImportError:
        pass
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ─── Funciones por tabla ──────────────────────────────────────────────────────

def push_athlete(cedula: str, name: str | None = None) -> dict:
    """Upsert tabla athletes (registro maestro)."""
    client = get_client()
    if not client:
        return _skipped()
    try:
        row = _clean({"cedula": cedula, "name": name})
        client.table("athletes").upsert(row, on_conflict="cedula").execute()
        return _ok("athletes upserted")
    except Exception as exc:
        return _err(f"athletes: {exc}")


def push_strava_tokens(
    cedula: str,
    access_token: str,
    refresh_token: str,
    expires_at: int,
) -> dict:
    """
    Persiste los tokens de Strava para un atleta en Supabase.
    Llamado por el portal (ar-athletes-portal) tras completar OAuth,
    y por sync_strava tras rotar el refresh_token.
    Requiere migration 005_strava_tokens.sql en Supabase.
    """
    client = get_client()
    if not client:
        return _skipped()
    try:
        client.table("athletes").upsert(
            {
                "cedula":                  cedula,
                "strava_access_token":     access_token,
                "strava_refresh_token":    refresh_token,
                "strava_token_expires_at": expires_at,
            },
            on_conflict="cedula",
        ).execute()
        return _ok(f"athletes.strava_tokens updated for cedula={cedula}")
    except Exception as exc:
        return _err(f"athletes.strava_tokens: {exc}")


def push_strava_last_sync(cedula: str) -> dict:
    """Actualiza strava_last_sync_at a NOW() en Supabase."""
    client = get_client()
    if not client:
        return _skipped()
    try:
        client.table("athletes").update(
            {"strava_last_sync_at": datetime.utcnow().isoformat()},
        ).eq("cedula", cedula).execute()
        return _ok(f"athletes.strava_last_sync_at updated for cedula={cedula}")
    except Exception as exc:
        return _err(f"athletes.strava_last_sync_at: {exc}")


def push_athlete_strava_id(cedula: str, strava_athlete_id: str) -> dict:
    """
    Persiste el strava_athlete_id en la tabla athletes.
    Necesario para que el webhook de deauthorize pueda mapear
    Strava owner_id → cedula al limpiar datos del atleta.
    Requiere migration 004_strava_athlete_id.sql en Supabase.
    """
    client = get_client()
    if not client:
        return _skipped()
    try:
        client.table("athletes").upsert(
            {"cedula": cedula, "strava_athlete_id": str(strava_athlete_id)},
            on_conflict="cedula",
        ).execute()
        return _ok(f"athletes.strava_athlete_id={strava_athlete_id}")
    except Exception as exc:
        return _err(f"athletes.strava_athlete_id: {exc}")


def push_profile(cedula: str, athlete_dir: Path) -> dict:
    """Upsert athlete_profiles desde meta/profile.json."""
    client = get_client()
    if not client:
        return _skipped()

    data = _read_json(athlete_dir / "meta" / "profile.json")
    if not data:
        return _err("profile.json not found")

    row = _clean({
        "cedula":        cedula,
        "age":           data.get("age"),
        "sex":           data.get("sex"),
        "weight_kg":     data.get("weight_kg"),
        "height_cm":     data.get("height_cm"),
        "pr_5k_sec":     data.get("pr_5k_sec"),
        "pr_10k_sec":    data.get("pr_10k_sec"),
        "pr_21k_sec":    data.get("pr_21k_sec"),
        "pr_42k_sec":    data.get("pr_42k_sec"),
        "race_distance": data.get("race_distance"),
        "race_date_raw": data.get("race_date_raw"),
        "time_goal_sec": data.get("time_goal_sec"),
        "raw":           data,
    })
    try:
        client.table("athlete_profiles").upsert(row, on_conflict="cedula").execute()
        return _ok("athlete_profiles upserted")
    except Exception as exc:
        return _err(f"athlete_profiles: {exc}")


def push_snapshot(cedula: str, athlete_dir: Path) -> dict:
    """Upsert athlete_snapshots desde features/athlete_snapshot.json."""
    client = get_client()
    if not client:
        return _skipped()

    data = _read_json(athlete_dir / "features" / "athlete_snapshot.json")
    if not data:
        return _err("athlete_snapshot.json not found")

    generated_at = data.get("generated_at")

    row = _clean({
        "cedula":               cedula,
        "generated_at":         generated_at,
        "semaforo":             data.get("semaforo_latest_checkin"),
        "readiness_score":      data.get("readiness_score"),
        "acwr_zone":            data.get("acwr_zone_latest"),
        "race_countdown_days":  data.get("race_countdown_days"),
        "data_weeks_available": data.get("data_weeks_available"),
        "raw":                  data,
    })
    try:
        client.table("athlete_snapshots").upsert(row, on_conflict="cedula").execute()
        return _ok("athlete_snapshots upserted")
    except Exception as exc:
        return _err(f"athlete_snapshots: {exc}")


def push_weekly_features(cedula: str, athlete_dir: Path) -> dict:
    """
    Upsert weekly_features desde features/weekly_features.parquet.
    Hace upsert de todas las filas (ON CONFLICT cedula+week_start → UPDATE).
    """
    client = get_client()
    if not client:
        return _skipped()

    parquet_path = athlete_dir / "features" / "weekly_features.parquet"
    if not parquet_path.exists():
        return _err("weekly_features.parquet not found")

    try:
        import duckdb
        df = duckdb.query(f"SELECT * FROM '{parquet_path.as_posix()}'").to_df()
    except Exception as exc:
        return _err(f"parquet read: {exc}")

    if df.empty:
        return _ok("weekly_features: 0 rows (empty parquet)")

    rows = []
    for _, r in df.iterrows():
        raw = {k: (None if hasattr(v, "__float__") and __import__("math").isnan(float(v)) else v)
               for k, v in r.items()
               if k not in ("__null_dask_index__",)}
        week_start = str(r.get("week_start", ""))[:10]  # DATE yyyy-mm-dd
        row = _clean({
            "cedula":           cedula,
            "week_start":       week_start,
            "km_total":         r.get("km_total"),
            "sessions":         r.get("sessions"),
            "longest_run_km":   r.get("fondo_largo_4s") or r.get("longest_run_km"),
            "avg_pace_sec_km":  r.get("avg_pace_sec_km"),
            "ctl":              r.get("ctl"),
            "atl":              r.get("atl"),
            "tsb":              r.get("tsb"),
            "acwr":             r.get("acwr"),
            "racha_semanas":    r.get("racha_semanas"),
            "km_trend":         r.get("km_trend"),
            "pace_delta_4s_sec": r.get("pace_delta_4s_sec"),
            "semana_spike":     bool(r.get("semana_spike")) if r.get("semana_spike") is not None else None,
            "raw":              raw,
        })
        rows.append(row)

    try:
        # Supabase upsert en lotes de 500
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            client.table("weekly_features").upsert(
                rows[i:i + batch_size],
                on_conflict="cedula,week_start",
            ).execute()
        return _ok(f"weekly_features: {len(rows)} rows upserted")
    except Exception as exc:
        return _err(f"weekly_features upsert: {exc}")


def push_activities(cedula: str, df: "pd.DataFrame") -> dict:
    """
    Upsert activities (tabla Supabase) desde el silver DataFrame.
    Schema alineado con reader.py: strava_id, activity_date, duration_sec, etc.
    Llamado justo después de escribir el parquet local para que Supabase
    sirva de respaldo cuando Railway redeploya y borra el disco.
    """
    client = get_client()
    if not client:
        return _skipped()

    if df is None or df.empty:
        return _ok("activities: 0 rows (empty)")

    rows = []
    for _, r in df.iterrows():
        act_id = r.get("activity_id")
        try:
            act_id = int(act_id) if act_id is not None and str(act_id) != "<NA>" else None
        except (ValueError, TypeError):
            act_id = None
        if not act_id:
            continue
        rows.append(_clean({
            "strava_id":       act_id,
            "cedula":          cedula,
            "name":            r.get("name"),
            "sport_type":      r.get("sport_type"),
            "activity_date":   r.get("start_date_local"),
            "distance_m":      r.get("distance_m"),
            "duration_sec":    r.get("moving_time_s"),
            "elevation_m":     r.get("total_elevation_gain_m"),
            "avg_pace_sec_km": r.get("pace_sec_per_km"),
            "raw": {
                "average_heartrate": r.get("average_heartrate"),
                "max_heartrate":     r.get("max_heartrate"),
                "average_cadence":   r.get("average_cadence"),
                "distance_km":       r.get("distance_km"),
                "moving_time_min":   r.get("moving_time_min"),
                "elapsed_time_s":    r.get("elapsed_time_s"),
                "average_speed_m_s": r.get("average_speed_m_s"),
            },
        }))

    if not rows:
        return _ok("activities: 0 valid rows")

    try:
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            client.table("activities").upsert(
                rows[i:i + batch_size],
                on_conflict="strava_id,cedula",
            ).execute()
        return _ok(f"activities: {len(rows)} rows upserted")
    except Exception as exc:
        return _err(f"activities upsert: {exc}")


def restore_activities_to_parquet(cedula: str, athlete_dir: Path) -> bool:
    """
    Restaura el parquet silver de actividades desde Supabase tabla 'activities'.
    Llamado al inicio del pipeline si el archivo local no existe (redeploy).
    Mapea de vuelta al schema del silver parquet (activity_id, start_date_local, etc.).
    Retorna True si se restauró algo.
    """
    client = get_client()
    if not client:
        return False

    try:
        result = (
            client.table("activities")
            .select("*")
            .eq("cedula", cedula)
            .order("activity_date", desc=False)
            .execute()
        )
        rows = result.data
        if not rows:
            return False

        import pandas as pd
        import duckdb

        # Mapear de vuelta al schema del silver parquet
        mapped = []
        for r in rows:
            raw = r.get("raw") or {}
            mapped.append({
                "cedula":                 r.get("cedula"),
                "activity_id":            r.get("strava_id"),
                "name":                   r.get("name"),
                "sport_type":             r.get("sport_type"),
                "start_date":             r.get("activity_date"),
                "start_date_local":       r.get("activity_date"),
                "distance_m":             r.get("distance_m"),
                "distance_km":            raw.get("distance_km"),
                "moving_time_s":          r.get("duration_sec"),
                "moving_time_min":        raw.get("moving_time_min"),
                "elapsed_time_s":         raw.get("elapsed_time_s"),
                "total_elevation_gain_m": r.get("elevation_m"),
                "average_speed_m_s":      raw.get("average_speed_m_s"),
                "max_speed_m_s":          None,
                "average_heartrate":      raw.get("average_heartrate"),
                "max_heartrate":          raw.get("max_heartrate"),
                "average_cadence":        raw.get("average_cadence"),
                "pace_sec_per_km":        r.get("avg_pace_sec_km"),
            })

        df = pd.DataFrame(mapped)
        silver_path = athlete_dir / "silver" / "activities.parquet"
        silver_path.parent.mkdir(parents=True, exist_ok=True)

        con = duckdb.connect(database=":memory:")
        con.register("df", df)
        con.execute(f"COPY df TO '{silver_path.as_posix()}' (FORMAT 'parquet')")
        con.close()

        print(f"[storage] ♻️  Restauradas {len(rows)} actividades desde Supabase para cedula={cedula}")
        return True
    except Exception as exc:
        print(f"[storage] ⚠️  No se pudo restaurar actividades desde Supabase: {exc}")
        return False


def push_plan(cedula: str, athlete_dir: Path) -> dict:
    """
    Upsert weekly_plans desde features/weekly_plan.json.
    Usa la semana actual (lunes) como week_start.
    """
    client = get_client()
    if not client:
        return _skipped()

    data = _read_json(athlete_dir / "features" / "weekly_plan.json")
    if not data:
        return _err("weekly_plan.json not found")

    # week_start = lunes de la semana actual (o del campo week_start en el JSON)
    week_start = data.get("week_start")
    if not week_start:
        today = date.today()
        monday = today - __import__("datetime").timedelta(days=today.weekday())
        week_start = monday.isoformat()

    targets = data.get("targets", {})
    row = _clean({
        "cedula":        cedula,
        "week_start":    str(week_start)[:10],
        "week_type":     data.get("week_type"),
        "semaforo":      data.get("semaforo"),
        "km_target":     targets.get("km_target") or targets.get("km"),
        "running_days":  targets.get("running_days") or targets.get("dias_running"),
        "strength_days": targets.get("strength_days") or targets.get("dias_fuerza"),
        "plan_json":     data,
    })
    try:
        client.table("weekly_plans").upsert(row, on_conflict="cedula,week_start").execute()
        return _ok("weekly_plans upserted")
    except Exception as exc:
        return _err(f"weekly_plans: {exc}")


def push_activities(cedula: str, athlete_dir: Path) -> dict:
    """
    Upsert activities desde silver/activities.parquet.
    Hace upsert de todas las actividades (ON CONFLICT strava_id → UPDATE).

    Mapeo de columnas parquet → Supabase:
      activity_id          → strava_id
      start_date_local     → activity_date
      moving_time_s        → duration_sec
      total_elevation_gain_m → elevation_m
      pace_sec_per_km      → avg_pace_sec_km
    """
    client = get_client()
    if not client:
        return _skipped()

    parquet_path = athlete_dir / "silver" / "activities.parquet"
    if not parquet_path.exists():
        return _err("activities.parquet not found")

    try:
        import duckdb
        df = duckdb.query(f"SELECT * FROM '{parquet_path.as_posix()}'").to_df()
    except Exception as exc:
        return _err(f"parquet read: {exc}")

    if df.empty:
        return _ok("activities: 0 rows (empty parquet)")

    rows = []
    for _, r in df.iterrows():
        # raw: fila completa sin NaN
        raw = {
            k: (None if hasattr(v, "__float__") and __import__("math").isnan(float(v)) else v)
            for k, v in r.items()
        }

        activity_id = r.get("activity_id")
        activity_date = str(r.get("start_date_local", ""))[:19]  # "2025-01-15T10:30:00"

        if not activity_id or not activity_date:
            continue

        row = _clean({
            "strava_id":       int(activity_id),
            "cedula":          cedula,
            "name":            r.get("name"),
            "sport_type":      r.get("sport_type"),
            "activity_date":   activity_date,
            "distance_m":      r.get("distance_m"),
            "duration_sec":    r.get("moving_time_s"),
            "elevation_m":     r.get("total_elevation_gain_m"),
            "avg_pace_sec_km": r.get("pace_sec_per_km"),
            "raw":             raw,
        })
        rows.append(row)

    if not rows:
        return _ok("activities: 0 valid rows")

    try:
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            client.table("activities").upsert(
                rows[i:i + batch_size],
                on_conflict="strava_id",
            ).execute()
        return _ok(f"activities: {len(rows)} rows upserted")
    except Exception as exc:
        return _err(f"activities upsert: {exc}")


def push_checkin(cedula: str, athlete_dir: Path) -> dict:
    """Upsert checkins desde meta/latest_checkin.json."""
    client = get_client()
    if not client:
        return _skipped()

    data = _read_json(athlete_dir / "meta" / "latest_checkin.json")
    if not data:
        return _err("latest_checkin.json not found")

    # El archivo tiene el check-in real anidado en data["latest_checkin"]
    payload = data.get("latest_checkin")
    if not isinstance(payload, dict):
        payload = data

    checkin_date = payload.get("date") or payload.get("checkin_date")
    if not checkin_date:
        return _err("checkin: no date/checkin_date field in latest_checkin.json")

    row = _clean({
        "cedula":       cedula,
        "checkin_date": str(checkin_date)[:10],
        "pain":         payload.get("pain") if payload.get("pain") is not None else payload.get("pain_0_10"),
        "fatigue":      payload.get("fatigue") if payload.get("fatigue") is not None else payload.get("fatigue_1_10"),
        "feeling":      payload.get("feeling") if payload.get("feeling") is not None else payload.get("feeling_1_10"),
        "sleep":        payload.get("sleep") if payload.get("sleep") is not None else payload.get("sleep_text"),
        "stress":       payload.get("stress") if payload.get("stress") is not None else payload.get("stress_text"),
        "semaforo":     payload.get("semaforo") or data.get("semaforo"),
        "is_recent":    bool(data.get("is_recent", False)),
        "raw":          data,
    })
    try:
        client.table("checkins").upsert(row, on_conflict="cedula,checkin_date").execute()
        return _ok("checkins upserted")
    except Exception as exc:
        return _err(f"checkins: {exc}")


# ─── Coach content ───────────────────────────────────────────────────────────

def push_coach_content(cedula: str, published_data: dict) -> dict:
    """
    Upsert coach_content desde el dict del publicado.

    Recibe el mismo dict que escribe publish.py en weekly_published.json.
    Requiere week_start para el upsert (cedula, week_start).
    """
    client = get_client()
    if not client:
        return _skipped()

    week_start = published_data.get("week_start")
    if not week_start:
        return _err("coach_content: week_start missing")

    row = _clean({
        "cedula":                cedula,
        "week_start":            str(week_start)[:10],
        "published_at":          published_data.get("published_at"),
        "status":                published_data.get("status", "published"),
        "coach_message":         published_data.get("coach_message"),
        "weekly_focus":          published_data.get("weekly_focus"),
        "weekly_objective":      published_data.get("weekly_objective"),
        "alerts":                published_data.get("alerts") or [],
        "restrictions":          published_data.get("restrictions") or [],
        "session_notes":         published_data.get("session_notes") or {},
        "plan_overrides":        published_data.get("plan_overrides") or {},
        # ── Plan efectivo (calculado en publish_coach_content_api) ───────────
        "week_type_override":     published_data.get("week_type_override"),
        "week_type_system":       published_data.get("week_type_system"),
        "effective_week_type":    published_data.get("effective_week_type"),
        "effective_distribution": published_data.get("effective_distribution"),
        "effective_totals":       published_data.get("effective_totals"),
        "raw":                    published_data,
    })
    try:
        client.table("coach_content").upsert(row, on_conflict="cedula,week_start").execute()
        return _ok(f"coach_content upserted (week {week_start})")
    except Exception as exc:
        return _err(f"coach_content: {exc}")


# ─── Función principal ────────────────────────────────────────────────────────

def push_all(cedula: str, athlete_dir: Path) -> dict:
    """
    Sube todos los datos locales disponibles a Supabase.
    Retorna un resumen de los resultados por tabla.
    Si Supabase no está configurado, todas las operaciones retornan 'skipped'.

    NOTA: push_activities NO se incluye intencionalmente.
    Las actividades raw de Strava se sirven desde el parquet local para reducir
    la exposición de Strava Data en almacenamiento de terceros (§2.9 / §7 Strava API Agreement).
    Para push explícito de actividades, llamar push_activities() directamente.

    Uso típico (después de que el pipeline haya corrido):
        from src.storage.writer import push_all
        from pathlib import Path
        result = push_all("1070982737", Path("data/athletes/1070982737"))
    """
    # Leer nombre desde profile.json (si existe)
    profile = _read_json(athlete_dir / "meta" / "profile.json")
    name = profile.get("name") if profile else None

    results = {
        "athletes":        push_athlete(cedula, name),
        "profile":         push_profile(cedula, athlete_dir),
        "snapshot":        push_snapshot(cedula, athlete_dir),
        "weekly_features": push_weekly_features(cedula, athlete_dir),
        "plan":            push_plan(cedula, athlete_dir),
        "checkin":         push_checkin(cedula, athlete_dir),
    }

    all_ok = all(v["ok"] for v in results.values())
    errors = {k: v["detail"] for k, v in results.items() if not v["ok"]}

    return {
        "ok":      all_ok,
        "cedula":  cedula,
        "results": {k: v["detail"] for k, v in results.items()},
        "errors":  errors,
    }
