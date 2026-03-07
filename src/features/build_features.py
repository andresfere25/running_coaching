import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import duckdb


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT 'parquet')")
    con.close()


def safe_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except:
        return None


def load_profile(profile_json: Path) -> dict:
    if not profile_json.exists():
        return {}
    return json.loads(profile_json.read_text(encoding="utf-8"))


def load_latest_checkin(latest_json: Path) -> dict:
    if not latest_json.exists():
        return {}
    return json.loads(latest_json.read_text(encoding="utf-8"))


def compute_semaforo(latest_checkin: dict) -> str:
    """
    Reglas simples (ajustables):
    - ROJO: dolor >=4 o fatigue>=8 o skipped_sessions==True o feeling<=4
    - AMARILLO: dolor 2-3 o fatigue 6-7 o feeling 5-6 o sueño malo/estrés alto
    - VERDE: lo demás (conservador)
    """
    if not latest_checkin:
        return "SIN_CHECKIN"

    ck = latest_checkin.get("latest_checkin", {}) if "latest_checkin" in latest_checkin else latest_checkin

    pain = ck.get("pain_0_10")
    fatigue = ck.get("fatigue_1_10")
    feeling = ck.get("feeling_1_10")
    skipped = ck.get("skipped_sessions")
    sleep = str(ck.get("sleep_text", "")).lower()
    stress = str(ck.get("stress_text", "")).lower()

    def as_int(v):
        try:
            return int(v)
        except:
            return None

    pain = as_int(pain)
    fatigue = as_int(fatigue)
    feeling = as_int(feeling)

    skipped_bool = (str(skipped).strip().lower() in ("true", "1", "sí", "si", "yes"))

    # ROJO
    if (pain is not None and pain >= 4) or (fatigue is not None and fatigue >= 8) or skipped_bool or (feeling is not None and feeling <= 4):
        return "ROJO"

    # AMARILLO
    if (pain is not None and 2 <= pain <= 3) or (fatigue is not None and 6 <= fatigue <= 7) or (feeling is not None and 5 <= feeling <= 6):
        return "AMARILLO"
    if ("malo" in sleep) or (stress in ("alto", "high")):
        return "AMARILLO"

    return "VERDE"


def week_start_monday(dt: pd.Series) -> pd.Series:
    # dt: datetime series
    return (dt.dt.to_period("W-MON").dt.start_time).dt.date


def build_weekly_features(df_acts: pd.DataFrame) -> pd.DataFrame:
    """
    df_acts debe tener:
    - start_date_local
    - distance_km
    - moving_time_min
    - pace_sec_per_km
    """
    df = df_acts.copy()

    df["start_date_local"] = pd.to_datetime(df["start_date_local"], errors="coerce")
    df = df[df["start_date_local"].notna()].copy()

    df["week_start"] = week_start_monday(df["start_date_local"]).astype(str)

    # Limpieza numérica
    for c in ["distance_km", "moving_time_min", "pace_sec_per_km"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Filtrar solo Run si existe sport_type
    if "sport_type" in df.columns:
        df = df[df["sport_type"].astype(str).str.lower().str.contains("run", na=False)].copy()

    # Agregados semanales
    weekly = (
        df.groupby("week_start", as_index=False)
          .agg(
              km_week=("distance_km", "sum"),
              sessions_week=("activity_id", "count"),
              time_min_week=("moving_time_min", "sum"),
              long_run_km=("distance_km", "max"),
          )
    )

    # Ritmo promedio semanal ponderado por distancia
    # pace_week = sum(pace_sec_km * km) / sum(km)
    df["_pace_x_km"] = df["pace_sec_per_km"] * df["distance_km"]
    pace_week = df.groupby("week_start", as_index=False).agg(
        pace_sec_per_km_week=("_pace_x_km", "sum"),
        km_total=("distance_km", "sum")
    )
    pace_week["pace_sec_per_km_week"] = pace_week["pace_sec_per_km_week"] / pace_week["km_total"]
    pace_week = pace_week.drop(columns=["km_total"])

    weekly = weekly.merge(pace_week, on="week_start", how="left")

    # Rolling windows (2w, 4w) y prev semana
    weekly["week_start_dt"] = pd.to_datetime(weekly["week_start"])
    weekly = weekly.sort_values("week_start_dt")

    weekly["km_prev_week"] = weekly["km_week"].shift(1)
    weekly["km_2w"] = weekly["km_week"].rolling(2).sum()
    weekly["km_4w"] = weekly["km_week"].rolling(4).sum()

    weekly["sessions_2w"] = weekly["sessions_week"].rolling(2).sum()
    weekly["sessions_4w"] = weekly["sessions_week"].rolling(4).sum()

    # Carga simple: tiempo * intensidad proxy (si no hay HR)
    # Usamos carga = tiempo_min_week (base)
    weekly["load_week"] = weekly["time_min_week"]

    # Monotonía y strain (Foster) aproximados:
    # monotony = mean(daily_load)/std(daily_load); strain=weekly_load*monotony
    # Aquí aproximamos daily_load por cada actividad como moving_time_min (simple)
    df["_day"] = df["start_date_local"].dt.date.astype(str)
    daily = df.groupby(["week_start", "_day"], as_index=False).agg(daily_load=("moving_time_min", "sum"))
    mono = daily.groupby("week_start", as_index=False).agg(
        daily_mean=("daily_load", "mean"),
        daily_std=("daily_load", "std"),
        weekly_load=("daily_load", "sum")
    )
    mono["monotony"] = mono["daily_mean"] / mono["daily_std"].replace({0: pd.NA})
    mono["strain"] = mono["weekly_load"] * mono["monotony"]
    mono = mono[["week_start", "monotony", "strain"]]

    weekly = weekly.merge(mono, on="week_start", how="left")

    # ACWR aproximado:
    # acute = carga 1 semana, chronic = promedio 4 semanas
    weekly["acute_load"] = weekly["load_week"]
    weekly["chronic_load_4w"] = weekly["load_week"].rolling(4).mean()
    weekly["acwr"] = weekly["acute_load"] / weekly["chronic_load_4w"]

    # Limpieza final
    weekly = weekly.drop(columns=["week_start_dt"])
    return weekly


def add_zones_from_profile(df_acts: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """
    Aproxima %Z1..%Z4 usando pace promedio de cada actividad vs umbrales del Form.
    Umbrales tomados de:
      easy_pace_sec_per_km, mod_pace_sec_per_km, fast_pace_sec_per_km
    """
    easy = profile.get("easy_pace_sec_per_km")
    mod = profile.get("mod_pace_sec_per_km")
    fast = profile.get("fast_pace_sec_per_km")

    easy = safe_float(easy)
    mod = safe_float(mod)
    fast = safe_float(fast)

    df = df_acts.copy()
    df["start_date_local"] = pd.to_datetime(df["start_date_local"], errors="coerce")
    df = df[df["start_date_local"].notna()].copy()
    df["week_start"] = (df["start_date_local"].dt.to_period("W-MON").dt.start_time).dt.date.astype(str)

    df["distance_km"] = pd.to_numeric(df.get("distance_km"), errors="coerce")
    df["pace_sec_per_km"] = pd.to_numeric(df.get("pace_sec_per_km"), errors="coerce")

    if "sport_type" in df.columns:
        df = df[df["sport_type"].astype(str).str.lower().str.contains("run", na=False)].copy()

    # Si no hay umbrales, no calculamos zonas
    if not easy or not mod or not fast:
        return pd.DataFrame(columns=["week_start", "pctZ1", "pctZ2", "pctZ3", "pctZ4"])

    def zone(p):
        # p: seg/km (más bajo = más rápido)
        if pd.isna(p):
            return None
        # Z1: más lento o igual a easy
        if p >= easy:
            return "Z1"
        # Z2: entre mod y easy (más rápido que easy, más lento que mod)
        if mod <= p < easy:
            return "Z2"
        # Z3: entre fast y mod
        if fast <= p < mod:
            return "Z3"
        # Z4: más rápido que fast
        if p < fast:
            return "Z4"
        return None

    df["zone"] = df["pace_sec_per_km"].apply(zone)

    by_week_zone = (
        df.groupby(["week_start", "zone"], as_index=False)
          .agg(km=("distance_km", "sum"))
    )
    total = df.groupby("week_start", as_index=False).agg(km_total=("distance_km", "sum"))

    piv = by_week_zone.pivot(index="week_start", columns="zone", values="km").fillna(0.0).reset_index()
    out = piv.merge(total, on="week_start", how="left")

    for z in ["Z1", "Z2", "Z3", "Z4"]:
        if z not in out.columns:
            out[z] = 0.0

    out["pctZ1"] = out["Z1"] / out["km_total"]
    out["pctZ2"] = out["Z2"] / out["km_total"]
    out["pctZ3"] = out["Z3"] / out["km_total"]
    out["pctZ4"] = out["Z4"] / out["km_total"]

    return out[["week_start", "pctZ1", "pctZ2", "pctZ3", "pctZ4"]]


def main(cedula: str | None = None):
    import argparse
    load_dotenv()
    if cedula is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--cedula", required=True, help="Cédula del atleta")
        args = parser.parse_args()
        cedula = args.cedula
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    athlete_dir = data_dir / cedula
    profile_json = athlete_dir / "meta" / "profile.json"
    latest_checkin_json = athlete_dir / "meta" / "latest_checkin.json"
    activities_path = athlete_dir / "silver" / "activities.parquet"

    profile = load_profile(profile_json)
    latest_checkin = load_latest_checkin(latest_checkin_json)
    semaforo = compute_semaforo(latest_checkin)

    df_acts = read_parquet(activities_path)
    if df_acts.empty:
        raise RuntimeError(f"No hay actividades en {activities_path}. Ejecuta sync_strava primero.")

    weekly = build_weekly_features(df_acts)

    # Zonas por ritmo (si hay umbrales)
    zones = add_zones_from_profile(df_acts, profile)
    if not zones.empty:
        weekly = weekly.merge(zones, on="week_start", how="left")
    else:
        weekly["pctZ1"] = None
        weekly["pctZ2"] = None
        weekly["pctZ3"] = None
        weekly["pctZ4"] = None

    # Añadir semáforo (global al último check-in)
    weekly["semaforo_latest_checkin"] = semaforo

    # Añadir info estática del Form (para el PDF)
    weekly["goal_main"] = profile.get("goal_main")
    weekly["race_distance"] = profile.get("race_distance")
    weekly["race_date_raw"] = profile.get("race_date_raw")
    weekly["days_run_per_week_target"] = profile.get("days_run_per_week")
    weekly["strength_days_per_week_target"] = profile.get("strength_days_per_week")

    # Guardar outputs
    out_dir = athlete_dir / "features"
    out_dir.mkdir(parents=True, exist_ok=True)

    weekly_path = out_dir / "weekly_features.parquet"
    write_parquet(weekly, weekly_path)

    # Snapshot para PDF (última semana)
    last_row = weekly.tail(1).to_dict(orient="records")[0]
    snapshot = {
        "cedula": cedula,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "semaforo_latest_checkin": semaforo,
        "profile": profile,
        "latest_week": last_row,
    }
    snapshot_path = out_dir / "athlete_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Features generados:")
    print(f" - {weekly_path}")
    print(f" - {snapshot_path}")


if __name__ == "__main__":
    main()