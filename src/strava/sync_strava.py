import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
import requests
import duckdb

from src.ingest.sheets_client import open_sheet, read_worksheet_as_records, write_worksheet_from_df
from src.ingest.parsers import norm_str
from src.strava.strava_client import refresh_access_token

TOKENS_TAB = "strava_tokens"

STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


def write_parquet_duckdb(df: pd.DataFrame, path: Path) -> None:
    """Escribe Parquet usando DuckDB (no requiere pyarrow)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT 'parquet')")
    con.close()


def read_parquet_duckdb(path: Path) -> pd.DataFrame:
    """Lee Parquet usando DuckDB."""
    if not path.exists():
        return pd.DataFrame()
    return duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()


def ensure_dirs(base_dir: Path, cedula: str) -> dict:
    athlete_dir = base_dir / str(cedula)
    paths = {
        "athlete_dir": athlete_dir,
        "raw": athlete_dir / "raw",
        "silver": athlete_dir / "silver",
        "meta": athlete_dir / "meta",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def to_unix_seconds(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def parse_last_sync_date(s: str) -> datetime | None:
    s = norm_str(s)
    if not s:
        return None
    # soporta '2026-02-20T13:09:28' o '2026-02-20'
    try:
        return datetime.fromisoformat(s)
    except:
        pass
    try:
        return pd.to_datetime(s, errors="coerce").to_pydatetime()
    except:
        return None


def fetch_activities(access_token: str, after_dt: datetime, before_dt: datetime) -> list[dict]:
    """Descarga actividades entre after y before (paginado)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    after = to_unix_seconds(after_dt)
    before = to_unix_seconds(before_dt)

    all_acts = []
    page = 1
    per_page = 200

    while True:
        params = {"after": after, "before": before, "page": page, "per_page": per_page}
        r = requests.get(STRAVA_ACTIVITIES_URL, headers=headers, params=params, timeout=30)

        if r.status_code != 200:
            raise RuntimeError(f"Strava activities failed ({r.status_code}): {r.text}")

        batch = r.json()
        if not batch:
            break

        all_acts.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    return all_acts


def normalize_activities(acts: list[dict], cedula: str) -> pd.DataFrame:
    """Normaliza campos clave a una tabla estándar (silver)."""
    rows = []
    for a in acts:
        # Strava: distancia en metros, moving_time en segundos
        distance_m = a.get("distance")
        moving_time_s = a.get("moving_time")

        pace_sec_km = None
        if distance_m and moving_time_s and distance_m > 0:
            # pace = seg/km
            pace_sec_km = (moving_time_s / (distance_m / 1000.0))

        rows.append(
            {
                "cedula": str(cedula),
                "activity_id": a.get("id"),
                "name": a.get("name"),
                "sport_type": a.get("sport_type") or a.get("type"),
                "start_date": a.get("start_date"),
                "start_date_local": a.get("start_date_local"),
                "distance_m": distance_m,
                "distance_km": (distance_m / 1000.0) if distance_m is not None else None,
                "moving_time_s": moving_time_s,
                "moving_time_min": (moving_time_s / 60.0) if moving_time_s is not None else None,
                "elapsed_time_s": a.get("elapsed_time"),
                "total_elevation_gain_m": a.get("total_elevation_gain"),
                "average_speed_m_s": a.get("average_speed"),
                "max_speed_m_s": a.get("max_speed"),
                "average_heartrate": a.get("average_heartrate"),
                "max_heartrate": a.get("max_heartrate"),
                "average_cadence": a.get("average_cadence"),
                "pace_sec_per_km": pace_sec_km,
            }
        )
    df = pd.DataFrame(rows)

    # limpiar activity_id
    if "activity_id" in df.columns:
        df["activity_id"] = pd.to_numeric(df["activity_id"], errors="coerce").astype("Int64")

    return df


def upsert_by_activity_id(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Upsert lógico: concat + dedup por activity_id."""
    if existing is None or existing.empty:
        out = new.copy()
    else:
        out = pd.concat([existing, new], ignore_index=True)

    if "activity_id" in out.columns:
        out = out.drop_duplicates(subset=["activity_id"], keep="last")

    # ordenar por fecha local si existe
    if "start_date_local" in out.columns:
        out["_dt"] = pd.to_datetime(out["start_date_local"], errors="coerce")
        out = out.sort_values("_dt").drop(columns=["_dt"], errors="ignore")

    return out


def main():
    load_dotenv()
    sheet_id = os.getenv("SHEET_ID")
    sa_json = os.getenv("GOOGLE_SA_JSON")
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    sheet = open_sheet(sheet_id, sa_json)

    tokens_records = read_worksheet_as_records(sheet, TOKENS_TAB)
    if not tokens_records:
        raise RuntimeError("No hay registros en strava_tokens.")

    df_tokens = pd.DataFrame(tokens_records)

    # Validar headers mínimos
    required_cols = ["cedula", "refresh_token", "status (CONNECTED / REVOKED / ERROR)", "last_sync_date"]
    for c in required_cols:
        if c not in df_tokens.columns:
            raise RuntimeError(f"Falta columna '{c}' en strava_tokens.")

    # Solo CONNECTED con refresh_token
    df_tokens["_status"] = df_tokens["status (CONNECTED / REVOKED / ERROR)"].astype(str).str.strip().str.upper()
    df_tokens["_refresh"] = df_tokens["refresh_token"].astype(str).str.strip()
    df_connected = df_tokens[(df_tokens["_status"] == "CONNECTED") & (df_tokens["_refresh"] != "")].copy()

    if df_connected.empty:
        print("ℹ️ No hay atletas CONNECTED con refresh_token.")
        return

    now = datetime.now(tz=BOGOTA_TZ)
    updated_any = False

    for idx, row in df_connected.iterrows():
        cedula = norm_str(row.get("cedula"))
        refresh_token = norm_str(row.get("refresh_token"))
        last_sync_raw = norm_str(row.get("last_sync_date"))

        if not cedula or not refresh_token:
            continue

        # 1) Refresh access token (y capturar refresh token rotado)
        token_json = refresh_access_token(refresh_token)
        access_token = token_json.get("access_token")
        new_refresh = token_json.get("refresh_token") or refresh_token  # si rota, actualizar
        expires_at = token_json.get("expires_at")

        # 2) Ventana incremental:
        # si no hay last_sync_date -> bootstrap 365 días
        last_sync_dt = parse_last_sync_date(last_sync_raw)
        if last_sync_dt is None:
            after_dt = now - timedelta(days=365)
        else:
            # buffer 2 días por seguridad
            after_dt = last_sync_dt - timedelta(days=2)

        before_dt = now + timedelta(days=1)

        print(f"\n🔄 Sync Strava cedula={cedula} after={after_dt.date()} before={before_dt.date()}")

        acts = fetch_activities(access_token, after_dt, before_dt)
        print(f"   📥 Actividades recibidas: {len(acts)}")

        # 3) Guardar RAW (tal cual, en parquet)
        paths = ensure_dirs(data_dir, cedula)
        raw_path = paths["raw"] / "strava_activities_raw.parquet"
        df_raw_new = pd.DataFrame(acts)
        # raw upsert por 'id' si existe
        df_raw_old = read_parquet_duckdb(raw_path)
        if not df_raw_old.empty and "id" in df_raw_old.columns and "id" in df_raw_new.columns:
            df_raw_all = pd.concat([df_raw_old, df_raw_new], ignore_index=True).drop_duplicates(subset=["id"], keep="last")
        else:
            df_raw_all = df_raw_new if df_raw_old.empty else pd.concat([df_raw_old, df_raw_new], ignore_index=True)
        write_parquet_duckdb(df_raw_all, raw_path)

        # 4) Guardar SILVER (normalizado + upsert por activity_id)
        silver_path = paths["silver"] / "activities.parquet"
        df_new = normalize_activities(acts, cedula)
        df_old = read_parquet_duckdb(silver_path)
        df_all = upsert_by_activity_id(df_old, df_new)
        write_parquet_duckdb(df_all, silver_path)

        # 5) Actualizar strava_tokens: refresh token rotado + last_sync_date
        df_tokens.loc[idx, "refresh_token"] = new_refresh
        df_tokens.loc[idx, "last_sync_date"] = now.isoformat(timespec="seconds")
        updated_any = True

        # opcional: guardar expires_at (si agregas columna)
        if "expires_at" in df_tokens.columns and expires_at:
            df_tokens.loc[idx, "expires_at"] = str(expires_at)

        print(f"   ✅ Guardado RAW: {raw_path}")
        print(f"   ✅ Guardado SILVER: {silver_path}")
        print(f"   ✅ last_sync_date actualizado: {now.isoformat(timespec='seconds')}")

    # 6) Escribir strava_tokens actualizado (solo si hubo updates)
    if updated_any:
        # limpiar columnas auxiliares
        df_tokens = df_tokens.drop(columns=["_status", "_refresh"], errors="ignore")
        write_worksheet_from_df(sheet, TOKENS_TAB, df_tokens)
        print("\n✅ strava_tokens actualizado automáticamente (refresh_token/last_sync_date).")
    else:
        print("\nℹ️ No hubo actualizaciones.")

if __name__ == "__main__":
    main()