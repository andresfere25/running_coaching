"""
src/storage/reader.py — Lectura híbrida: Supabase primero, fallback local.

Para cada tipo de dato:
1. Intenta leer desde Supabase (si está configurado y hay datos).
2. Si Supabase no tiene la fila (o no está configurado), lee desde archivo local.
3. Si tampoco hay datos locales, retorna None.

Funciones públicas:
  read_snapshot(cedula, athlete_dir)                              → dict | None
  read_plan(cedula, athlete_dir)                                  → dict | None
  read_features(cedula, athlete_dir, weeks)                       → list[dict] | None
  read_activities(cedula, athlete_dir, limit, from_date, to_date, sport_type)
                                                                  → list[dict] | None
  read_checkin(cedula, athlete_dir)                               → dict | None

Schema canónico de actividad (read_activities siempre devuelve este shape):
  activity_id, cedula, name, sport_type, activity_date,
  distance_m, distance_km, duration_sec, elevation_m,
  pace_sec_per_km, average_heartrate, average_cadence
"""

import json
from pathlib import Path

from src.storage.supabase_client import get_client

# Incluye raw para extraer HR/cadencia que no tienen columna propia en Supabase
_ACTIVITY_COLS = (
    "strava_id,cedula,name,sport_type,activity_date,"
    "distance_m,duration_sec,elevation_m,avg_pace_sec_km,raw"
)


# ─── Normalización de activities ─────────────────────────────────────────────

def _normalize_activity(row: dict, source: str) -> dict:
    """
    Normaliza una fila de actividad al schema canónico de 12 campos.
    Independiente de si viene de Supabase o del parquet local.
    """
    if source == "supabase":
        raw = row.get("raw") or {}
        dist_m = row.get("distance_m") or 0
        return {
            "activity_id":       row.get("strava_id"),
            "cedula":            row.get("cedula"),
            "name":              row.get("name"),
            "sport_type":        row.get("sport_type"),
            "activity_date":     row.get("activity_date"),
            "distance_m":        dist_m,
            "distance_km":       round(dist_m / 1000, 3) if dist_m else None,
            "duration_sec":      row.get("duration_sec"),
            "elevation_m":       row.get("elevation_m"),
            "pace_sec_per_km":   row.get("avg_pace_sec_km"),
            "average_heartrate": raw.get("average_heartrate"),
            "average_cadence":   raw.get("average_cadence"),
        }
    else:  # local parquet
        return {
            "activity_id":       row.get("activity_id"),
            "cedula":            row.get("cedula"),
            "name":              row.get("name"),
            "sport_type":        row.get("sport_type"),
            "activity_date":     row.get("start_date_local"),
            "distance_m":        row.get("distance_m"),
            "distance_km":       row.get("distance_km"),
            "duration_sec":      row.get("moving_time_s"),
            "elevation_m":       row.get("total_elevation_gain_m"),
            "pace_sec_per_km":   row.get("pace_sec_per_km"),
            "average_heartrate": row.get("average_heartrate"),
            "average_cadence":   row.get("average_cadence"),
        }


# ─── profile ─────────────────────────────────────────────────────────────────

def read_profile(cedula: str, athlete_dir: "Path | None") -> "dict | None":
    """
    Lee profile.json (perfil del atleta del Form 1).
    Fuente 1: Supabase athlete_profiles (raw JSON + columnas PR/demográficas)
    Fuente 2: meta/profile.json

    Las columnas de la tabla (pr_5k_sec, age, sex, etc.) se mergean sobre
    el JSON raw para que ediciones manuales en Supabase se reflejen.
    """
    PR_COLUMNS = ("pr_5k_sec", "pr_10k_sec", "pr_21k_sec", "pr_42k_sec")
    MERGE_COLUMNS = PR_COLUMNS + ("age", "sex", "height_cm", "weight_kg")

    client = get_client()
    if client:
        try:
            fields = "raw," + ",".join(MERGE_COLUMNS)
            res = (
                client.table("athlete_profiles")
                .select(fields)
                .eq("cedula", cedula)
                .limit(1)
                .execute()
            )
            if res.data:
                row = res.data[0]
                profile = row.get("raw") or {}
                # Mergear columnas de la tabla sobre el JSON raw
                # (las columnas tienen prioridad — permiten edición manual)
                for col in MERGE_COLUMNS:
                    val = row.get(col)
                    if val is not None and val != 0:
                        profile[col] = val
                return profile
        except Exception as exc:
            print(f"[reader] Supabase profile error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "meta" / "profile.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None


# ─── snapshot ────────────────────────────────────────────────────────────────

def read_snapshot(cedula: str, athlete_dir: "Path | None") -> "dict | None":
    """
    Lee athlete_snapshot.json.
    Fuente 1: Supabase athlete_snapshots.raw
    Fuente 2: features/athlete_snapshot.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("athlete_snapshots")
                .select("raw")
                .eq("cedula", cedula)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["raw"]
        except Exception as exc:
            print(f"[reader] Supabase snapshot error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "athlete_snapshot.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None


# ─── plan ─────────────────────────────────────────────────────────────────────

def read_plan(cedula: str, athlete_dir: "Path | None") -> "dict | None":
    """
    Lee weekly_plan.json.
    Fuente 1: Supabase weekly_plans.plan_json (más reciente)
    Fuente 2: features/weekly_plan.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("weekly_plans")
                .select("plan_json")
                .eq("cedula", cedula)
                .order("week_start", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["plan_json"]
        except Exception as exc:
            print(f"[reader] Supabase plan error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "weekly_plan.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None


# ─── features ────────────────────────────────────────────────────────────────

def read_features(cedula: str, athlete_dir: "Path | None", weeks: int = 12) -> "list[dict] | None":
    """
    Lee historial de weekly_features.
    Fuente 1: Supabase weekly_features.raw (últimas N semanas, orden ascendente)
    Fuente 2: features/weekly_features.parquet
    Retorna lista de dicts compatible con df.to_dict(orient='records').
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("weekly_features")
                .select("raw, week_start")
                .eq("cedula", cedula)
                .order("week_start", desc=True)
                .limit(weeks)
                .execute()
            )
            if res.data:
                return [r["raw"] for r in reversed(res.data)]
        except Exception as exc:
            print(f"[reader] Supabase features error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "weekly_features.parquet"
        if path.exists():
            import duckdb
            df = duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()
            df = df.sort_values("week_start").tail(weeks).reset_index(drop=True)
            return df.to_dict(orient="records")

    return None


# ─── activities ──────────────────────────────────────────────────────────────

def read_activities(
    cedula: str,
    athlete_dir: "Path | None",
    limit: int = 50,
    from_date: "str | None" = None,
    to_date: "str | None" = None,
    sport_type: "str | None" = None,
) -> "list[dict] | None":
    """
    Lee activities (historial de actividades Strava).
    Fuente 1: Supabase activities — normalizado al schema canónico
    Fuente 2: silver/activities.parquet — normalizado al schema canónico

    Siempre devuelve el mismo shape de 12 campos independientemente de la fuente.
    """
    client = get_client()
    if client:
        try:
            q = (
                client.table("activities")
                .select(_ACTIVITY_COLS)
                .eq("cedula", cedula)
                .order("activity_date", desc=True)
                .limit(limit)
            )
            if from_date:
                q = q.gte("activity_date", from_date)
            if to_date:
                q = q.lte("activity_date", to_date + "T23:59:59")
            if sport_type:
                q = q.ilike("sport_type", f"%{sport_type}%")
            res = q.execute()
            if res.data:
                return [_normalize_activity(r, "supabase") for r in res.data]
        except Exception as exc:
            print(f"[reader] Supabase activities error para {cedula}: {exc}")

    # Fallback local — filtros aplicados en pandas (sin SQL dinámico)
    if athlete_dir:
        path = athlete_dir / "silver" / "activities.parquet"
        if path.exists():
            import duckdb
            df = duckdb.query(
                f"SELECT * FROM '{path.as_posix()}' ORDER BY start_date_local DESC"
            ).to_df()
            if from_date:
                df = df[df["start_date_local"].astype(str) >= from_date]
            if to_date:
                df = df[df["start_date_local"].astype(str) <= to_date + "T23:59:59"]
            if sport_type:
                mask = df["sport_type"].str.lower().str.contains(sport_type.lower(), na=False)
                df = df[mask]
            df = df.head(limit).reset_index(drop=True)
            return [_normalize_activity(r, "local") for r in df.to_dict(orient="records")]

    return None


# ─── checkin ─────────────────────────────────────────────────────────────────

def read_checkin(cedula: str, athlete_dir: "Path | None") -> "dict | None":
    """
    Lee latest_checkin.json.
    Fuente 1: Supabase checkins.raw (más reciente por checkin_date)
    Fuente 2: meta/latest_checkin.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("checkins")
                .select("raw")
                .eq("cedula", cedula)
                .order("checkin_date", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["raw"]
        except Exception as exc:
            print(f"[reader] Supabase checkin error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "meta" / "latest_checkin.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None
