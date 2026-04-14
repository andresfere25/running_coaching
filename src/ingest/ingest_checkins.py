import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import duckdb

from src.ingest.parsers import norm_str, parse_yes_no, parse_int


def write_parquet_duckdb(df: pd.DataFrame, path: Path) -> None:
    """Escribe Parquet usando DuckDB (no requiere pyarrow)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT 'parquet')")
    con.close()


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


def parse_timestamp_flexible(x) -> datetime | None:
    """
    Google Forms suele traer '2/20/2026 11:44:27'
    Esta función intenta parsearlo robusto.
    """
    s = norm_str(x)
    if not s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except:
            pass
    # fallback (pandas)
    try:
        return pd.to_datetime(s, errors="coerce").to_pydatetime()
    except:
        return None


def monday_of_week(dt: datetime) -> datetime:
    """Devuelve el lunes de la semana de dt."""
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def normalize_checkin_row(row: dict) -> dict:
    out = {}
    ts = parse_timestamp_flexible(row.get("Timestamp"))
    out["timestamp_raw"] = norm_str(row.get("Timestamp"))
    out["timestamp"] = ts.isoformat() if ts else None
    out["cedula"] = norm_str(row.get("Cédula"))

    out["sessions_completed"] = parse_int(row.get("¿Cuántos entrenamientos completaste esta semana?"))
    out["feeling_1_10"] = parse_int(row.get("¿Cómo te sentiste esta semana? (1–10)"))

    out["sleep_text"] = norm_str(row.get("Sueño")).lower()          # ej: bueno / regular / malo / 6h
    out["stress_text"] = norm_str(row.get("Estrés")).lower()        # ej: bajo / medio / alto
    out["pain_0_10"] = parse_int(row.get("Dolor (0–10)"))
    out["pain_where"] = norm_str(row.get("¿Dónde sientes el dolor?"))
    out["fatigue_1_10"] = parse_int(row.get("Fatiga (1–10)"))

    out["skipped_sessions"] = parse_yes_no(row.get("¿Tuviste que saltarte sesiones por cansancio/dolor?"))
    out["comments"] = norm_str(row.get("Comentarios (opcional)"))

    # flags de calidad
    out["missing_core_fields"] = (
        out["sessions_completed"] is None
        or out["feeling_1_10"] is None
        or out["fatigue_1_10"] is None
    )

    # Derivados de semana (si hay timestamp)
    if ts:
        out["checkin_date"] = ts.date().isoformat()
        ws = monday_of_week(ts)
        out["checkin_week_start"] = ws.date().isoformat()
    else:
        out["checkin_date"] = None
        out["checkin_week_start"] = None

    return out


def is_recent_checkin(ts: datetime | None, lookback_days: int = 10) -> bool:
    if not ts:
        return False
    return ts >= (datetime.now() - timedelta(days=lookback_days))


def main(cedula: str | None = None):
    """
    Ingesta de check-ins desde Supabase.

    Los check-ins llegan via POST /athletes/{cedula}/checkin y se guardan
    en Supabase tabla `checkins`. Esta función lee los más recientes y
    escribe latest_checkin.json para consumo del pipeline.

    cedula (opcional):
      - Si se provee: procesa solo ese atleta.
      - Si es None:   procesa todos los atletas con check-ins.
    """
    load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    try:
        from src.storage.supabase_client import get_client
        client = get_client()
    except Exception:
        client = None

    if not client:
        print("[ingest_checkins] Supabase no configurado. Omitiendo ingesta de check-ins.")
        return

    # Leer check-ins desde Supabase
    try:
        query = client.table("checkins").select("cedula, checkin_date, raw").order("checkin_date", desc=True)
        if cedula:
            query = query.eq("cedula", cedula)
        res = query.limit(500).execute()
    except Exception as exc:
        print(f"[ingest_checkins] Error leyendo checkins de Supabase: {exc}")
        return

    if not res.data:
        if cedula:
            print(f"[ingest_checkins] No hay check-ins para cedula {cedula}. Se omite.")
        else:
            print("[ingest_checkins] No hay check-ins en Supabase.")
        return

    # Agrupar por cédula y escribir latest_checkin.json
    from collections import defaultdict
    by_cedula = defaultdict(list)
    for row in res.data:
        ced = row.get("cedula")
        if ced:
            by_cedula[ced].append(row)

    count = 0
    for ced, rows in by_cedula.items():
        # Más reciente primero (ya ordenado por desc)
        latest_row = rows[0]
        raw = latest_row.get("raw") or {}

        # Determinar si es reciente
        checkin_date_str = latest_row.get("checkin_date")
        recent_flag = False
        if checkin_date_str:
            try:
                checkin_dt = datetime.fromisoformat(checkin_date_str.replace("Z", "+00:00"))
                recent_flag = checkin_dt >= (datetime.now() - timedelta(days=10))
            except Exception:
                pass

        meta = {
            "cedula": ced,
            "latest_timestamp": checkin_date_str,
            "is_recent": bool(recent_flag),
            "latest_checkin": raw,
        }

        paths = ensure_dirs(data_dir, ced)
        meta_path = paths["meta"] / "latest_checkin.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1

    print(f"[ingest_checkins] Check-ins procesados: {count} atletas desde Supabase.")


if __name__ == "__main__":
    main()