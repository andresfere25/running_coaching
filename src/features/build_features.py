"""build_features.py — Pipeline de features semanales por atleta.

Métricas de carga: CTL/ATL/TSB/ACWR vía EWMA (load_metrics.py).
  ⚠️  El campo `acwr` en weekly_features.parquet es la versión EWMA
      (ATL/CTL con spans semanales 1 y 6), NO el ratio simple agudo/crónico
      de 4 semanas que se usaba antes. Cambio introducido en sesión 2026-03-11.
"""
import os
import json
import math
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import duckdb

from src.ml.load_metrics import add_load_metrics, acwr_zone


# ─── Helpers de I/O ───────────────────────────────────────────────────────────

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
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return None


def load_profile(profile_json: Path) -> dict:
    if not profile_json.exists():
        return {}
    return json.loads(profile_json.read_text(encoding="utf-8"))


def load_latest_checkin(latest_json: Path) -> dict:
    if not latest_json.exists():
        return {}
    return json.loads(latest_json.read_text(encoding="utf-8"))


# ─── Semáforo ────────────────────────────────────────────────────────────────

def compute_semaforo(latest_checkin: dict) -> str:
    """
    Reglas simples (ajustables):
    - ROJO: dolor >=4 o fatigue>=8 o skipped_sessions==True o feeling<=4
    - AMARILLO: dolor 2-3 o fatigue 6-7 o feeling 5-6 o sueño malo/estrés alto
    - VERDE: lo demás (conservador)
    - SIN_CHECKIN: no hay checkin o el checkin no es reciente (>10 días)
    """
    if not latest_checkin:
        return "SIN_CHECKIN"

    if not latest_checkin.get("is_recent", True):
        return "SIN_CHECKIN"

    ck = latest_checkin.get("latest_checkin", {}) if "latest_checkin" in latest_checkin else latest_checkin

    pain    = ck.get("pain_0_10")
    fatigue = ck.get("fatigue_1_10")
    feeling = ck.get("feeling_1_10")
    skipped = ck.get("skipped_sessions")
    sleep   = str(ck.get("sleep_text", "")).lower()
    stress  = str(ck.get("stress_text", "")).lower()

    def as_int(v):
        try:
            return int(v)
        except Exception:
            return None

    pain    = as_int(pain)
    fatigue = as_int(fatigue)
    feeling = as_int(feeling)
    skipped_bool = str(skipped).strip().lower() in ("true", "1", "sí", "si", "yes")

    if (pain is not None and pain >= 4) or (fatigue is not None and fatigue >= 8) or skipped_bool or (feeling is not None and feeling <= 4):
        return "ROJO"
    if (pain is not None and 2 <= pain <= 3) or (fatigue is not None and 6 <= fatigue <= 7) or (feeling is not None and 5 <= feeling <= 6):
        return "AMARILLO"
    if ("malo" in sleep) or (stress in ("alto", "high")):
        return "AMARILLO"
    return "VERDE"


# ─── Helpers de análisis ──────────────────────────────────────────────────────

def week_start_monday(dt: pd.Series) -> pd.Series:
    return (dt.dt.to_period("W-MON").dt.start_time).dt.date


def _compute_racha_semanas(has_activity: pd.Series) -> pd.Series:
    """
    Semanas consecutivas con ≥1 actividad (streak acumulado hacia adelante).
    Se reinicia a 0 en semanas sin actividad.
    """
    streaks = []
    current = 0
    for has in has_activity:
        if has:
            current += 1
        else:
            current = 0
        streaks.append(current)
    return pd.Series(streaks, index=has_activity.index, dtype=int)


def _compute_km_trend(km_series: pd.Series, window: int = 4) -> pd.Series:
    """
    Tendencia de volumen semanal respecto al promedio de las N semanas anteriores.
    Valores: 'growing' | 'stable' | 'declining' | None
    Umbral ±10%: cambios menores se clasifican como 'stable'.
    """
    rolling_prev = km_series.shift(1).rolling(window, min_periods=2).mean()
    pct_change   = (km_series - rolling_prev) / (rolling_prev + 0.001)

    def classify(p):
        if p is None or (isinstance(p, float) and math.isnan(p)):
            return None
        if p > 0.10:
            return "growing"
        if p < -0.10:
            return "declining"
        return "stable"

    return pct_change.apply(classify)


def compute_readiness_score(tsb, acwr, semaforo: str) -> int:
    """
    Score de readiness 0–100. Heurístico — no es modelo ML.

    Compuesto de tres señales con pesos fijos:
      TSB  (40%): balance forma-fatiga — ideal 5-25 (fresco pero entrenado)
      ACWR (35%): ratio agudo/crónico  — ideal 0.8-1.3
      Semáforo (25%): check-in subjetivo

    NO corrige la predicción de tiempo; solo informa el estado del atleta.
    """
    # TSB score
    if tsb is None or (isinstance(tsb, float) and math.isnan(tsb)):
        tsb_score = 50
    elif tsb > 30:
        tsb_score = 60   # muy fresco → posiblemente desentrenado
    elif 5 <= tsb <= 25:
        tsb_score = 100
    elif 0 <= tsb < 5:
        tsb_score = 80
    elif -15 <= tsb < 0:
        tsb_score = 65
    elif -30 <= tsb < -15:
        tsb_score = 40
    else:
        tsb_score = 20   # tsb < -30: muy fatigado

    # ACWR score
    if acwr is None or (isinstance(acwr, float) and math.isnan(acwr)):
        acwr_score = 50
    elif 0.8 <= acwr <= 1.3:
        acwr_score = 100
    elif 0.6 <= acwr < 0.8:
        acwr_score = 70
    elif 1.3 < acwr <= 1.5:
        acwr_score = 60
    elif acwr > 1.5:
        acwr_score = 30
    else:
        acwr_score = 50   # < 0.6

    # Semáforo score
    sem_score = {"VERDE": 100, "AMARILLO": 70, "ROJO": 30, "SIN_CHECKIN": 50}.get(semaforo, 50)

    return round(0.40 * tsb_score + 0.35 * acwr_score + 0.25 * sem_score)


# ─── Build features ───────────────────────────────────────────────────────────

def build_weekly_features(df_acts: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega actividades Strava a nivel semanal y computa métricas de carga.

    Métricas EWMA (via load_metrics.add_load_metrics):
      ctl  — Chronic Training Load (fitness): EWMA km, span=6 semanas
      atl  — Acute Training Load  (fatiga):  EWMA km, span=1 semana
      tsb  — Training Stress Balance:         CTL - ATL
      acwr — Acute/Chronic Ratio (EWMA):      ATL / CTL
             ⚠️ Este es el ACWR EWMA, no el ratio simple agudo/crónico de 4 semanas.

    Métricas analíticas adicionales (sin ML):
      fondo_largo_4s     — max long_run_km de las últimas 4 semanas (contexto mensual)
      racha_semanas      — semanas consecutivas con ≥1 actividad (streak)
      km_trend           — tendencia de volumen vs prom. 4 semanas anteriores
      pace_delta_4s_sec  — ritmo esta semana vs prom. 4 sem. anteriores (seg/km)
      semana_spike       — True si acwr > 1.3 (posible pico de carga)
      pico_semana_ratio  — long_run_km / km_week (distribución de la carga semanal)
    """
    df = df_acts.copy()

    df["start_date_local"] = pd.to_datetime(df["start_date_local"], errors="coerce")
    df = df[df["start_date_local"].notna()].copy()
    df["week_start"] = week_start_monday(df["start_date_local"]).astype(str)

    for c in ["distance_km", "moving_time_min", "pace_sec_per_km"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "sport_type" in df.columns:
        df = df[df["sport_type"].astype(str).str.lower().str.contains("run", na=False)].copy()

    # ── Agregados semanales base ───────────────────────────────────────────
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
    df["_pace_x_km"] = df["pace_sec_per_km"] * df["distance_km"]
    pace_week = df.groupby("week_start", as_index=False).agg(
        pace_num=("_pace_x_km", "sum"),
        km_total=("distance_km", "sum"),
    )
    pace_week["pace_sec_per_km_week"] = pace_week["pace_num"] / pace_week["km_total"]
    pace_week = pace_week.drop(columns=["pace_num", "km_total"])
    weekly = weekly.merge(pace_week, on="week_start", how="left")

    # ── Rollings básicos ──────────────────────────────────────────────────
    weekly["week_start_dt"] = pd.to_datetime(weekly["week_start"])
    weekly = weekly.sort_values("week_start_dt").reset_index(drop=True)

    weekly["km_prev_week"]  = weekly["km_week"].shift(1)
    weekly["km_2w"]         = weekly["km_week"].rolling(2).sum()
    weekly["km_4w"]         = weekly["km_week"].rolling(4).sum()
    weekly["sessions_2w"]   = weekly["sessions_week"].rolling(2).sum()
    weekly["sessions_4w"]   = weekly["sessions_week"].rolling(4).sum()

    # ── Métricas de carga EWMA (reemplaza el ratio simple anterior) ───────
    # load_col = 'km_week'; granularity = 'weekly' → CTL span=6, ATL span=1
    weekly = add_load_metrics(weekly, load_col="km_week", granularity="weekly")
    # Resultado: columnas ctl, atl, tsb, acwr (EWMA-based)

    # ── Monotonía y strain (Foster) ───────────────────────────────────────
    df["_day"] = df["start_date_local"].dt.date.astype(str)
    daily = df.groupby(["week_start", "_day"], as_index=False).agg(
        daily_load=("moving_time_min", "sum")
    )
    mono = daily.groupby("week_start", as_index=False).agg(
        daily_mean=("daily_load", "mean"),
        daily_std=("daily_load", "std"),
        weekly_load=("daily_load", "sum"),
    )
    mono["monotony"] = mono["daily_mean"] / mono["daily_std"].replace({0: pd.NA})
    mono["strain"]   = mono["weekly_load"] * mono["monotony"]
    mono = mono[["week_start", "monotony", "strain"]]
    weekly = weekly.merge(mono, on="week_start", how="left")

    # ── Métricas analíticas adicionales (no-ML) ───────────────────────────

    # Fondo largo en contexto mensual (últimas 4 semanas)
    weekly["fondo_largo_4s"] = weekly["long_run_km"].rolling(4, min_periods=1).max()

    # Streak de semanas consecutivas activas
    weekly["racha_semanas"] = _compute_racha_semanas(weekly["sessions_week"] > 0)

    # Tendencia de volumen vs prom. 4 semanas anteriores
    weekly["km_trend"] = _compute_km_trend(weekly["km_week"], window=4)

    # Diferencia de ritmo vs prom. 4 semanas anteriores (seg/km)
    # Negativo = va más rápido; positivo = va más lento
    pace_roll_prev = weekly["pace_sec_per_km_week"].shift(1).rolling(4, min_periods=2).mean()
    weekly["pace_delta_4s_sec"] = weekly["pace_sec_per_km_week"] - pace_roll_prev

    # Spike de carga (ACWR EWMA > 1.3 — zona de precaución)
    weekly["semana_spike"] = weekly["acwr"].apply(
        lambda v: bool(v is not None and not (isinstance(v, float) and math.isnan(v)) and v > 1.3)
    )

    # Distribución de la carga semanal: ¿qué fracción es el fondo largo?
    weekly["pico_semana_ratio"] = weekly["long_run_km"] / (weekly["km_week"] + 0.001)

    # Limpiar columna auxiliar
    weekly = weekly.drop(columns=["week_start_dt"])
    return weekly


def add_zones_from_profile(df_acts: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """
    Aproxima %Z1..%Z4 usando pace promedio de cada actividad vs umbrales del Form.
    """
    easy = safe_float(profile.get("easy_pace_sec_per_km"))
    mod  = safe_float(profile.get("mod_pace_sec_per_km"))
    fast = safe_float(profile.get("fast_pace_sec_per_km"))

    df = df_acts.copy()
    df["start_date_local"] = pd.to_datetime(df["start_date_local"], errors="coerce")
    df = df[df["start_date_local"].notna()].copy()
    df["week_start"] = (df["start_date_local"].dt.to_period("W-MON").dt.start_time).dt.date.astype(str)
    df["distance_km"]       = pd.to_numeric(df.get("distance_km"), errors="coerce")
    df["pace_sec_per_km"]   = pd.to_numeric(df.get("pace_sec_per_km"), errors="coerce")

    if "sport_type" in df.columns:
        df = df[df["sport_type"].astype(str).str.lower().str.contains("run", na=False)].copy()

    if not easy or not mod or not fast:
        return pd.DataFrame(columns=["week_start", "pctZ1", "pctZ2", "pctZ3", "pctZ4"])

    def zone(p):
        if pd.isna(p):
            return None
        if p >= easy:
            return "Z1"
        if mod <= p < easy:
            return "Z2"
        if fast <= p < mod:
            return "Z3"
        if p < fast:
            return "Z4"
        return None

    df["zone"] = df["pace_sec_per_km"].apply(zone)
    by_week_zone = df.groupby(["week_start", "zone"], as_index=False).agg(km=("distance_km", "sum"))
    total = df.groupby("week_start", as_index=False).agg(km_total=("distance_km", "sum"))
    piv   = by_week_zone.pivot(index="week_start", columns="zone", values="km").fillna(0.0).reset_index()
    out   = piv.merge(total, on="week_start", how="left")

    for z in ["Z1", "Z2", "Z3", "Z4"]:
        if z not in out.columns:
            out[z] = 0.0

    out["pctZ1"] = out["Z1"] / out["km_total"]
    out["pctZ2"] = out["Z2"] / out["km_total"]
    out["pctZ3"] = out["Z3"] / out["km_total"]
    out["pctZ4"] = out["Z4"] / out["km_total"]

    return out[["week_start", "pctZ1", "pctZ2", "pctZ3", "pctZ4"]]


# ─── Race snapshots silenciosos (semilla Capa individual) ─────────────────────

def build_race_snapshots(profile: dict, weekly_df: pd.DataFrame) -> list[dict]:
    """
    Para cada carrera en profile['race_history'], captura el contexto de carga
    de la semana correspondiente en el historial Strava.

    NO influye en ninguna predicción todavía.
    Semilla para la Capa de personalización individual (Tier 3) cuando el
    atleta tenga ≥ 3 carreras con datos de carga suficientes.

    Campos de metadatos para auditoría futura:
      source             — origen del dato ('form_race_history')
      week_found         — si se encontró la semana en el historial Strava
      history_weeks_at_race — semanas de historial disponibles en la fecha de la carrera
      snapshot_usable    — True si week_found y ≥ 8 semanas de historial (CTL estable)
      notes              — lista de flags/advertencias metodológicas
      k_factor           — tiempo_real / predicción_base (guardado, no usado aún)
    """
    from src.ml.predictor import predict_race_time_range

    race_history = profile.get("race_history", [])
    if not race_history:
        return []

    wdf = weekly_df.copy()
    wdf["week_start_dt"] = pd.to_datetime(wdf["week_start"])
    n_weeks_total = len(wdf)

    snapshots = []
    for race in race_history:
        dist_key = race.get("distance_key")
        time_sec = race.get("time_sec")
        date_iso = race.get("date_iso")

        if not dist_key or not time_sec or not date_iso:
            continue

        snap = {
            # Identidad
            "distance_key":    dist_key,
            "time_sec":        time_sec,
            "date_iso":        date_iso,
            "source":          "form_race_history",
            # Metadatos de calidad
            "week_found":             False,
            "history_weeks_at_race":  0,
            "snapshot_usable":        False,
            "notes":                  [],
            # Contexto de carga (relleno si week_found)
            "ctl_race_week":          None,
            "atl_race_week":          None,
            "tsb_race_week":          None,
            "acwr_race_week":         None,
            "acwr_zone_race_week":    None,
            "km_8w_total":            None,
            "pace_race_week":         None,
            # PRs vigentes en el perfil al momento del registro (mejor aproximación)
            "pr_5k_sec":   profile.get("pr_5k_sec"),
            "pr_10k_sec":  profile.get("pr_10k_sec"),
            "pr_21k_sec":  profile.get("pr_21k_sec"),
            "pr_42k_sec":  profile.get("pr_42k_sec"),
            # Predicción base y factor de corrección (no expuesto en app todavía)
            "riegel_pred_sec": None,
            "k_factor":        None,
        }

        # Encontrar la semana de la carrera en el historial Strava
        try:
            race_dt = pd.to_datetime(date_iso)
            # Lunes de la semana de la carrera
            days_since_monday = race_dt.dayofweek
            race_week_monday  = (race_dt - pd.Timedelta(days=days_since_monday)).normalize()
            race_week_str     = race_week_monday.strftime("%Y-%m-%d")

            # Cuántas semanas de historial existían antes de esa carrera
            weeks_before = wdf[wdf["week_start_dt"] <= race_week_monday]
            snap["history_weeks_at_race"] = len(weeks_before)

            week_row = wdf[wdf["week_start"] == race_week_str]
            if week_row.empty:
                snap["notes"].append(
                    f"Semana {race_week_str} no encontrada en historial Strava "
                    f"(carrera anterior al inicio del sync, o semana sin actividades)"
                )
            else:
                snap["week_found"] = True
                row = week_row.iloc[0]
                snap["ctl_race_week"]       = safe_float(row.get("ctl"))
                snap["atl_race_week"]       = safe_float(row.get("atl"))
                snap["tsb_race_week"]       = safe_float(row.get("tsb"))
                snap["acwr_race_week"]      = safe_float(row.get("acwr"))
                snap["acwr_zone_race_week"] = acwr_zone(safe_float(row.get("acwr")))
                snap["pace_race_week"]      = safe_float(row.get("pace_sec_per_km_week"))

                # km acumulados en las 8 semanas previas (bloque de preparación)
                idx_loc  = wdf.index.get_loc(week_row.index[0])
                start_8w = max(0, idx_loc - 7)
                snap["km_8w_total"] = round(float(wdf.iloc[start_8w : idx_loc + 1]["km_week"].sum()), 1)

        except Exception as exc:
            snap["notes"].append(f"Error procesando fecha '{date_iso}': {exc}")

        # Evaluar usabilidad para el futuro modelo individual
        if snap["history_weeks_at_race"] < 8:
            snap["notes"].append(
                f"CTL puede ser impreciso: solo {snap['history_weeks_at_race']} semanas "
                f"de historial disponibles en la fecha de la carrera (mínimo recomendado: 8)"
            )

        snap["snapshot_usable"] = (
            snap["week_found"]
            and snap["ctl_race_week"] is not None
            and snap["history_weeks_at_race"] >= 8
        )

        if not snap["snapshot_usable"] and not snap["notes"]:
            snap["notes"].append("Snapshot no usable: semana no encontrada o historial insuficiente")

        # Calcular predicción base y k_factor (guardado, no expuesto en app)
        if time_sec and dist_key in ("5K", "10K", "21K", "42K"):
            try:
                pred = predict_race_time_range(
                    profile=profile,
                    target_distance=dist_key,
                    age=float(profile.get("age") or 0) or None,
                    gender=profile.get("sex"),
                )
                if pred and pred.get("estimated_sec"):
                    pred_sec = pred["estimated_sec"]
                    snap["riegel_pred_sec"] = pred_sec
                    snap["k_factor"]        = round(time_sec / pred_sec, 4)
                    if snap["k_factor"] < 0.80 or snap["k_factor"] > 1.25:
                        snap["notes"].append(
                            f"k_factor={snap['k_factor']:.3f} fuera del rango esperado "
                            f"(0.80–1.25). Posible: carrera atípica, PRs desactualizados, "
                            f"o condiciones extremas (calor/altitud/desnivel)."
                        )
            except Exception as exc:
                snap["notes"].append(f"No se pudo calcular predicción base: {exc}")

        snapshots.append(snap)

    return snapshots


# ─── Main ─────────────────────────────────────────────────────────────────────

def main(cedula: str | None = None):
    import argparse
    load_dotenv()
    if cedula is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--cedula", required=True, help="Cédula del atleta")
        args   = parser.parse_args()
        cedula = args.cedula

    data_dir    = Path(os.getenv("DATA_DIR", "data/athletes"))
    athlete_dir = data_dir / cedula
    local_dir   = athlete_dir if athlete_dir.exists() else None

    # ── Inputs: Supabase-first + fallback local ───────────────────────────
    from src.storage.reader import read_profile, read_checkin, read_activities

    profile        = read_profile(cedula, local_dir) or {}
    latest_checkin = read_checkin(cedula, local_dir) or {}
    semaforo       = compute_semaforo(latest_checkin)

    acts = read_activities(cedula, local_dir, limit=2000)
    if not acts:
        raise RuntimeError(
            f"No hay actividades para cedula={cedula}. "
            f"Ejecuta primero POST /athletes/{cedula}/sync con steps=strava"
        )

    # Map canonical schema → columnas que esperan build_weekly_features() y add_zones_from_profile()
    #   activity_date → start_date_local
    #   duration_sec  → moving_time_s  (+ compute moving_time_min)
    df_acts = pd.DataFrame(acts)
    df_acts = df_acts.rename(columns={
        "activity_date": "start_date_local",
        "duration_sec":  "moving_time_s",
    })
    df_acts["moving_time_min"] = df_acts["moving_time_s"] / 60

    weekly = build_weekly_features(df_acts)

    # Zonas por ritmo (si hay umbrales en el formulario)
    zones = add_zones_from_profile(df_acts, profile)
    if not zones.empty:
        weekly = weekly.merge(zones, on="week_start", how="left")
    else:
        for z in ["pctZ1", "pctZ2", "pctZ3", "pctZ4"]:
            weekly[z] = None

    # Campos del perfil para contexto
    weekly["semaforo_latest_checkin"]       = semaforo
    weekly["goal_main"]                     = profile.get("goal_main")
    weekly["race_distance"]                 = profile.get("race_distance")
    weekly["race_date_raw"]                 = profile.get("race_date_raw")
    weekly["days_run_per_week_target"]      = profile.get("days_run_per_week")
    weekly["strength_days_per_week_target"] = profile.get("strength_days_per_week")

    # Guardar weekly_features.parquet
    out_dir      = athlete_dir / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    weekly_path  = out_dir / "weekly_features.parquet"
    write_parquet(weekly, weekly_path)

    # ── Snapshot ──────────────────────────────────────────────────────────
    last_row = weekly.tail(1).to_dict(orient="records")[0]

    # Días para la carrera objetivo
    race_countdown_days = None
    race_date_raw = profile.get("race_date_raw", "")
    if race_date_raw:
        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                from datetime import date
                race_dt = datetime.strptime(race_date_raw, fmt).date()
                race_countdown_days = (race_dt - date.today()).days
                break
            except ValueError:
                continue

    # Tiempo objetivo formateado
    time_goal_sec = profile.get("time_goal_sec")
    time_goal_formatted = None
    if time_goal_sec and time_goal_sec > 0:
        tg = int(time_goal_sec)
        h, rem = divmod(tg, 3600)
        m, s   = divmod(rem, 60)
        time_goal_formatted = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

    # Readiness score (compuesto: TSB + ACWR + semáforo)
    readiness_score = compute_readiness_score(
        tsb=safe_float(last_row.get("tsb")),
        acwr=safe_float(last_row.get("acwr")),
        semaforo=semaforo,
    )

    # Zona ACWR de la última semana
    acwr_zone_latest = acwr_zone(safe_float(last_row.get("acwr")))

    snapshot = {
        "cedula":                 cedula,
        "generated_at":          datetime.now().isoformat(timespec="seconds"),
        "semaforo_latest_checkin": semaforo,
        "race_countdown_days":    race_countdown_days,
        "data_weeks_available":   len(weekly),
        "time_goal_formatted":    time_goal_formatted,
        "readiness_score":        readiness_score,
        "acwr_zone_latest":       acwr_zone_latest,
        "profile":                profile,
        "latest_week":            last_row,
    }

    snapshot_path = out_dir / "athlete_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # ── Race snapshots silenciosos ────────────────────────────────────────
    race_snapshots = build_race_snapshots(profile, weekly)
    if race_snapshots:
        race_snaps_path = out_dir / "race_snapshots.json"
        race_snaps_path.write_text(
            json.dumps(
                {
                    "cedula":          cedula,
                    "generated_at":    datetime.now().isoformat(timespec="seconds"),
                    "n_races":         len(race_snapshots),
                    "usable_count":    sum(1 for r in race_snapshots if r.get("snapshot_usable")),
                    "race_snapshots":  race_snapshots,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    print("✅ Features generados:")
    print(f"   {weekly_path}")
    print(f"   {snapshot_path}")
    if race_snapshots:
        n_usable = sum(1 for r in race_snapshots if r.get("snapshot_usable"))
        print(f"   {out_dir / 'race_snapshots.json'}  ({len(race_snapshots)} carreras, {n_usable} usables)")


if __name__ == "__main__":
    main()
