"""
src/storage/bootstrap.py — Reconstrucción del parquet local desde Supabase.

Railway tiene disco efímero: cada deploy/restart borra los parquets locales
en `data/athletes/{cedula}/silver/activities.parquet`. Sin parquet, el step
`features` no puede computar weekly_features y el dashboard se queda vacío.

Esta función rebuilda el parquet leyendo todas las actividades del atleta
desde la tabla `activities` de Supabase. Es idempotente y rápida (sin Strava API).

Mapeo Supabase → parquet (inverso a `push_activities` en writer.py):
  strava_id           → activity_id
  activity_date       → start_date_local
  duration_sec        → moving_time_s
  elevation_m         → total_elevation_gain_m
  avg_pace_sec_km     → pace_sec_per_km
  raw (JSONB)         → flat columns (average_heartrate, max_heartrate, etc.)
"""

from pathlib import Path

from src.storage.supabase_client import get_client
from api.deps import get_data_dir


def bootstrap_parquet_from_supabase(cedula: str) -> int:
    """
    Reconstruye silver/activities.parquet desde la tabla activities de Supabase.
    Retorna número de actividades restauradas. 0 si no hay datos en Supabase
    o si el cliente no está disponible.

    Idempotente: sobrescribe el parquet con todos los datos de Supabase.
    """
    client = get_client()
    if not client:
        return 0

    res = (
        client.table("activities")
        .select("strava_id,cedula,name,sport_type,activity_date,"
                "distance_m,duration_sec,elevation_m,avg_pace_sec_km,raw")
        .eq("cedula", cedula)
        .order("activity_date", desc=False)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return 0

    import pandas as pd

    flat = []
    for r in rows:
        raw = r.get("raw") or {}
        distance_m = r.get("distance_m")
        moving_time_s = r.get("duration_sec")
        record = {
            "cedula":                 r.get("cedula"),
            "activity_id":            r.get("strava_id"),
            "name":                   r.get("name"),
            "sport_type":             r.get("sport_type"),
            "start_date":             raw.get("start_date") or r.get("activity_date"),
            "start_date_local":       r.get("activity_date"),
            "distance_m":             distance_m,
            "distance_km":            (distance_m / 1000.0) if distance_m else None,
            "moving_time_s":          moving_time_s,
            "moving_time_min":        (moving_time_s / 60.0) if moving_time_s else None,
            "elapsed_time_s":         raw.get("elapsed_time"),
            "total_elevation_gain_m": r.get("elevation_m"),
            "average_speed_m_s":      raw.get("average_speed"),
            "max_speed_m_s":          raw.get("max_speed"),
            "average_heartrate":      raw.get("average_heartrate"),
            "max_heartrate":          raw.get("max_heartrate"),
            "average_cadence":        raw.get("average_cadence"),
            "pace_sec_per_km":        r.get("avg_pace_sec_km"),
        }
        flat.append(record)

    df = pd.DataFrame(flat)
    if df.empty:
        return 0

    # activity_id como Int64 (mismo tipo que normalize_activities en sync_strava)
    if "activity_id" in df.columns:
        df["activity_id"] = pd.to_numeric(df["activity_id"], errors="coerce").astype("Int64")

    # Asegurar el directorio y escribir parquet
    data_dir = get_data_dir() / cedula / "silver"
    data_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = data_dir / "activities.parquet"

    import duckdb
    con = duckdb.connect(database=":memory:")
    con.register("df_view", df)
    con.execute(f"COPY df_view TO '{parquet_path.as_posix()}' (FORMAT PARQUET)")
    con.close()

    return len(df)
