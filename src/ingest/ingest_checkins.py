import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import duckdb

from src.ingest.sheets_client import open_sheet, read_worksheet_as_records
from src.ingest.parsers import norm_str, parse_yes_no, parse_int


FORM2_TAB = "Form Responses 2"


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
    Ingesta del Form 2 (check-ins semanales) desde Google Sheets.

    cedula (opcional):
      - Si se provee: escribe solo para ese atleta. Sin error si no tiene check-ins.
      - Si es None:   procesa todos los atletas con check-ins (modo global para CLI).

    Llamado desde sync.py con cedula específico.
    Llamado desde run_pipeline.py sin cedula (procesa todos).
    """
    load_dotenv()
    sheet_id = os.getenv("SHEET_ID")
    sa_json  = os.getenv("GOOGLE_SA_JSON")
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    sheet = open_sheet(sheet_id, sa_json)
    records = read_worksheet_as_records(sheet, FORM2_TAB)
    if not records:
        raise RuntimeError(f"No se encontraron registros en '{FORM2_TAB}'.")

    df_raw = pd.DataFrame(records)

    normalized = []
    for r in records:
        n = normalize_checkin_row(r)
        if n.get("cedula"):
            normalized.append(n)

    df = pd.DataFrame(normalized)
    if df.empty:
        raise RuntimeError("No se pudieron normalizar filas del check-in (¿cambió el header de Cédula?).")

    # Convertimos timestamp a datetime real para orden y filtros
    df["_ts_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # Cédulas a procesar
    all_cedulas = sorted([c for c in df["cedula"].unique() if c])
    if cedula:
        # Modo per-atleta: solo escribir para la cédula solicitada
        cedulas = [cedula] if cedula in all_cedulas else []
        if not cedulas:
            print(f"ℹ️  No hay check-ins para cédula {cedula} en '{FORM2_TAB}'. Se omite.")
            return
    else:
        cedulas = all_cedulas

    for cedula in cedulas:
        athlete_df_raw = df_raw[df_raw["Cédula"].astype(str).str.strip() == cedula].copy() if "Cédula" in df_raw.columns else pd.DataFrame()

        athlete_df = df[df["cedula"] == cedula].copy()
        athlete_df = athlete_df.sort_values("_ts_dt")

        paths = ensure_dirs(data_dir, cedula)

        # Guardar RAW snapshot (solo para auditoría)
        raw_path = paths["raw"] / "checkins_raw.parquet"
        write_parquet_duckdb(athlete_df_raw, raw_path)

        # Guardar SILVER (normalizado completo)
        silver_path = paths["silver"] / "checkins.parquet"
        write_parquet_duckdb(athlete_df.drop(columns=["_ts_dt"]), silver_path)

        # latest_checkin (último por timestamp)
        latest = athlete_df.iloc[-1].to_dict()
        latest_dt = latest.get("_ts_dt")
        latest_iso = latest_dt.isoformat() if pd.notna(latest_dt) else None

        # banderas para consumo del plan
        recent_flag = is_recent_checkin(latest_dt.to_pydatetime() if pd.notna(latest_dt) else None, lookback_days=10)

        meta = {
            "cedula": cedula,
            "latest_timestamp": latest_iso,
            "is_recent": bool(recent_flag),
            "latest_checkin": {k: v for k, v in latest.items() if k != "_ts_dt"},
        }

        meta_path = paths["meta"] / "latest_checkin.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ Check-ins procesados. Atletas encontrados: {len(cedulas)}")
    print("Ejemplo atleta:", cedulas[0] if cedulas else "N/A")


if __name__ == "__main__":
    main()