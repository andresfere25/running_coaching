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
STRAVA_ATHLETE_URL    = "https://www.strava.com/api/v3/athlete"


def fetch_athlete_id(access_token: str) -> str:
    """Obtiene el athlete.id de Strava via GET /athlete."""
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(STRAVA_ATHLETE_URL, headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Strava GET /athlete failed ({r.status_code}): {r.text}")
    return str(r.json().get("id", ""))


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


# ─── Núcleo compartido: sincronizar un atleta ────────────────────────────────

def _sync_athlete_core(
    cedula: str,
    access_token: str,
    refresh_token_in: str,
    last_sync_dt: "datetime | None",
    data_dir: Path,
    now: datetime,
) -> dict:
    """
    Ejecuta el sync completo para un atleta dado un access_token ya válido.
    El llamador es responsable de obtener/refrescar el token ANTES de llamar
    esta función y de persistir el nuevo token si hubo rotación.
    """
    new_refresh = refresh_token_in  # se retorna para que el llamador actualice si rotó

    # 2) Ventana incremental
    if last_sync_dt is None:
        after_dt = now - timedelta(days=365)
    else:
        after_dt = last_sync_dt - timedelta(days=2)
    before_dt = now + timedelta(days=1)

    print(f"\n🔄 Sync Strava cedula={cedula} after={after_dt.date()} before={before_dt.date()}")

    # 2b) Persistir strava_athlete_id en Supabase
    try:
        strava_athlete_id = fetch_athlete_id(access_token)
        if strava_athlete_id:
            from src.storage.writer import push_athlete_strava_id
            r = push_athlete_strava_id(cedula, strava_athlete_id)
            print(f"   ✅ strava_athlete_id={strava_athlete_id} guardado en Supabase ({r})")
        else:
            print(f"   ⚠️  GET /athlete no devolvió id para cedula={cedula}")
    except Exception as _exc:
        print(f"   ⚠️  No se pudo obtener/guardar strava_athlete_id: {_exc}")

    acts = fetch_activities(access_token, after_dt, before_dt)
    print(f"   📥 Actividades recibidas: {len(acts)}")

    # 3) Guardar RAW
    paths    = ensure_dirs(data_dir, cedula)
    raw_path = paths["raw"] / "strava_activities_raw.parquet"
    df_raw_new = pd.DataFrame(acts)
    df_raw_old = read_parquet_duckdb(raw_path)
    if df_raw_new.empty:
        df_raw_all = df_raw_old  # sin actividades nuevas — conservar historial existente
    elif not df_raw_old.empty and "id" in df_raw_old.columns and "id" in df_raw_new.columns:
        df_raw_all = pd.concat([df_raw_old, df_raw_new], ignore_index=True).drop_duplicates(subset=["id"], keep="last")
    else:
        df_raw_all = df_raw_new if df_raw_old.empty else pd.concat([df_raw_old, df_raw_new], ignore_index=True)
    if not df_raw_all.empty:
        write_parquet_duckdb(df_raw_all, raw_path)

    # 4) Guardar SILVER
    silver_path = paths["silver"] / "activities.parquet"
    df_new  = normalize_activities(acts, cedula)
    df_old  = read_parquet_duckdb(silver_path)
    df_all  = upsert_by_activity_id(df_old, df_new)
    if not df_all.empty:
        write_parquet_duckdb(df_all, silver_path)

    print(f"   ✅ RAW: {raw_path}")
    print(f"   ✅ SILVER: {silver_path}")

    return {"access_token": access_token, "new_refresh": new_refresh}


# ─── Path A: Supabase (cedula-específico) ────────────────────────────────────

def _get_token_from_supabase(cedula: str) -> "dict | None":
    """Lee strava_refresh_token y strava_last_sync_at desde Supabase athletes."""
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if not client:
            return None
        resp = (
            client.table("athletes")
            .select("strava_refresh_token,strava_access_token,strava_token_expires_at,strava_last_sync_at")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = resp.data[0]
            print(f"[sync_strava] Token Supabase: refresh={'***' if row.get('strava_refresh_token') else 'NULL'} last_sync={row.get('strava_last_sync_at')}")
            if row.get("strava_refresh_token"):
                return row
    except Exception as exc:
        print(f"[sync_strava] ⚠️  No se pudo leer token desde Supabase: {exc}")
    return None


def _refresh_and_persist(cedula: str, refresh_token: str, push_fn) -> tuple[str, str, int]:
    """
    Llama a Strava para rotar el token, persiste inmediatamente en Supabase y
    retorna (access_token, refresh_token, expires_at).
    Llama a push_fn(access_token, refresh_token, expires_at) para persistir.
    """
    tok = refresh_access_token(refresh_token)
    new_access   = tok["access_token"]
    new_refresh  = tok["refresh_token"]
    new_expires  = int(tok["expires_at"])
    push_fn(access_token=new_access, refresh_token=new_refresh, expires_at=new_expires)
    print(f"[sync_strava] ✅ Tokens rotados y persistidos en Supabase (expires_at={new_expires})")
    return new_access, new_refresh, new_expires


def _sync_via_supabase(cedula: str, data_dir: Path) -> None:
    """Sync Strava para un atleta específico leyendo tokens de Supabase."""
    print(f"[sync_strava] 🗄️  Leyendo token desde Supabase para cedula={cedula}")
    token_row = _get_token_from_supabase(cedula)
    if not token_row:
        raise RuntimeError(
            f"No hay strava_refresh_token en Supabase para cedula={cedula}. "
            f"Llama primero a POST /athletes/{cedula}/strava/token con las credenciales del portal."
        )

    refresh_token = token_row["strava_refresh_token"]
    last_sync_raw = token_row.get("strava_last_sync_at")
    last_sync_dt  = parse_last_sync_date(str(last_sync_raw)) if last_sync_raw else None

    now      = datetime.now(tz=BOGOTA_TZ)
    now_unix = int(now.timestamp())

    cached_access  = token_row.get("strava_access_token")
    cached_expires = int(token_row.get("strava_token_expires_at") or 0)

    from src.storage.writer import push_strava_tokens, push_strava_last_sync

    def _persist(access_token, refresh_token, expires_at):
        push_strava_tokens(cedula, access_token=access_token,
                           refresh_token=refresh_token, expires_at=expires_at)

    print(
        f"[sync_strava] 📋 Token Supabase: "
        f"expires_at={cached_expires} | now_unix={now_unix} | "
        f"margen={cached_expires - now_unix}s | access_token={'presente' if cached_access else 'AUSENTE'}"
    )

    if cached_access and now_unix < cached_expires - 300:
        print(f"[sync_strava] ♻️  Reutilizando access_token (expira en {cached_expires - now_unix}s)")
        access_token = cached_access
    else:
        print(f"[sync_strava] 🔄 Token expirado o ausente — refrescando")
        access_token, refresh_token, _ = _refresh_and_persist(cedula, refresh_token, _persist)

    # Intentar sync; si Strava rechaza con 401 (token stale a pesar de expires_at),
    # forzar refresh y reintentar UNA sola vez.
    try:
        _sync_athlete_core(cedula, access_token, refresh_token, last_sync_dt, data_dir, now)
    except RuntimeError as exc:
        if "401" in str(exc):
            print(
                f"[sync_strava] ⚠️  access_token rechazado por Strava (401) — "
                f"el token en Supabase está stale aunque expires_at parezca válido. "
                f"Forzando refresh y reintentando..."
            )
            access_token, refresh_token, _ = _refresh_and_persist(cedula, refresh_token, _persist)
            print(f"[sync_strava] 🔁 Reintentando sync con token fresco")
            _sync_athlete_core(cedula, access_token, refresh_token, last_sync_dt, data_dir, now)
        else:
            raise

    push_strava_last_sync(cedula)
    print(f"   ✅ last_sync_at actualizado en Supabase para cedula={cedula}")


# ─── Path B: Google Sheets (sync global — pipeline semanal) ──────────────────

def _sync_via_sheets(data_dir: Path) -> None:
    """Sync Strava global leyendo tokens de Google Sheets (pipeline semanal)."""
    load_dotenv()
    sheet_id = os.getenv("SHEET_ID")
    sa_json  = os.getenv("GOOGLE_SA_JSON")

    print(f"[sync_strava] 📊 Leyendo tokens desde Google Sheets (global sync)")
    sheet = open_sheet(sheet_id, sa_json)

    tokens_records = read_worksheet_as_records(sheet, TOKENS_TAB)
    if not tokens_records:
        raise RuntimeError("No hay registros en strava_tokens.")

    df_tokens = pd.DataFrame(tokens_records)

    required_cols = ["cedula", "refresh_token", "status (CONNECTED / REVOKED / ERROR)", "last_sync_date"]
    for c in required_cols:
        if c not in df_tokens.columns:
            raise RuntimeError(f"Falta columna '{c}' en strava_tokens.")

    df_tokens["_status"]  = df_tokens["status (CONNECTED / REVOKED / ERROR)"].astype(str).str.strip().str.upper()
    df_tokens["_refresh"] = df_tokens["refresh_token"].astype(str).str.strip()
    df_connected = df_tokens[(df_tokens["_status"] == "CONNECTED") & (df_tokens["_refresh"] != "")].copy()

    if df_connected.empty:
        print("ℹ️ No hay atletas CONNECTED con refresh_token.")
        return

    now         = datetime.now(tz=BOGOTA_TZ)
    updated_any = False

    for idx, row in df_connected.iterrows():
        cedula        = norm_str(row.get("cedula"))
        refresh_token = norm_str(row.get("refresh_token"))
        last_sync_raw = norm_str(row.get("last_sync_date"))

        if not cedula or not refresh_token:
            continue

        last_sync_dt = parse_last_sync_date(last_sync_raw)

        # Refrescar token fuera del core (Sheets siempre refresca — no hay caché)
        tok          = refresh_access_token(refresh_token)
        access_token = tok["access_token"]
        new_refresh  = tok["refresh_token"]
        new_expires  = tok["expires_at"]

        _sync_athlete_core(cedula, access_token, new_refresh, last_sync_dt, data_dir, now)

        # 5) Actualizar Sheets con token rotado + last_sync_date
        df_tokens.loc[idx, "refresh_token"] = new_refresh
        df_tokens.loc[idx, "last_sync_date"] = now.isoformat(timespec="seconds")
        updated_any = True
        if "expires_at" in df_tokens.columns and new_expires:
            df_tokens.loc[idx, "expires_at"] = str(new_expires)
        print(f"   ✅ last_sync_date actualizado en Sheets: {now.isoformat(timespec='seconds')}")

    if updated_any:
        df_tokens = df_tokens.drop(columns=["_status", "_refresh"], errors="ignore")
        write_worksheet_from_df(sheet, TOKENS_TAB, df_tokens)
        print("\n✅ strava_tokens actualizado en Google Sheets.")
    else:
        print("\nℹ️ No hubo actualizaciones.")


# ─── Entrada principal ────────────────────────────────────────────────────────

def main(cedula: "str | None" = None) -> None:
    """
    Sincroniza actividades de Strava.

    Si cedula es provisto: lee tokens desde Supabase (portal OAuth path).
    Si cedula es None:     lee tokens desde Google Sheets (pipeline semanal global).
    """
    load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    if cedula:
        _sync_via_supabase(cedula, data_dir)
    else:
        _sync_via_sheets(data_dir)


if __name__ == "__main__":
    main()