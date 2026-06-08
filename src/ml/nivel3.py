"""
src/ml/nivel3.py
================
Inferencia del modelo N3 — Personalización longitudinal RUNA.

N3 extiende N2 agregando features de carga acumulada del atleta:
CTL, ATL, TSB, ACWR, long_run_km, pace_delta_4s_sec, weeks_with_data.
Requiere ≥8 semanas de historial. Modelo: Lasso + StandardScaler.

MAE LOAO-CV: 41.87 sec/km | Atletas: 49 | Sesiones: 8,267
  (mejora +12.0% vs N2=47.59, +32.9% vs N1 OOD=62.4 sec/km)

Uso:
    from src.ml.nivel3 import predict_n3_zones, predict_n3_session
"""

from __future__ import annotations

import json
import math
import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_MODELS_DIR = Path(__file__).parent.parent.parent / "api" / "models"
_N1_PATH    = _MODELS_DIR / "nivel1_ridge_v4.json"
_N3_PKL     = _MODELS_DIR / "nivel3_v1.pkl"
_N3_JSON    = _MODELS_DIR / "nivel3_v1.json"

# Zona HR (Karvonen 5-zonas) -> % FCmax representativo del centro de zona
ZONE_PCT_HRMAX = {1: 0.60, 2: 0.70, 3: 0.80, 4: 0.88, 5: 0.95}
ZONE_LABELS    = {1: "Z1", 2: "Z2", 3: "Z3", 4: "Z4", 5: "Z5"}
ZONE_NAMES     = {
    1: "Recuperacion activa",
    2: "Aerobico base",
    3: "Tempo",
    4: "Umbral anaerobico",
    5: "VO2max",
}

_n1_cache: Optional[dict] = None
_n3_cache: Optional[dict] = None  # pkl payload
_n3_meta:  Optional[dict] = None


def _load_n1() -> dict:
    global _n1_cache
    if _n1_cache is None:
        with open(_N1_PATH, encoding="utf-8") as f:
            _n1_cache = json.load(f)
    return _n1_cache


def _load_n3() -> dict:
    global _n3_cache
    if _n3_cache is None:
        with open(_N3_PKL, "rb") as f:
            _n3_cache = pickle.load(f)
    return _n3_cache


def _load_n3_meta() -> dict:
    global _n3_meta
    if _n3_meta is None:
        with open(_N3_JSON, encoding="utf-8") as f:
            _n3_meta = json.load(f)
    return _n3_meta


def _fcmax_tanaka(age: float) -> float:
    return 208.0 - 0.7 * age


def _zona_hr(pct_hrmax: float) -> int:
    if pct_hrmax < 0.70:   return 1
    elif pct_hrmax < 0.75: return 2
    elif pct_hrmax < 0.85: return 3
    elif pct_hrmax < 0.92: return 4
    else:                  return 5


def _predict_n1_sec_km(
    n1: dict,
    *,
    sex_bin:      float,
    fcmax:        float,
    avg_hr:       float,
    pct_hrmax:    float,
    zona_hr:      int,
    hr_max_rel:   float,
    log_duration: float,
) -> float:
    duration_min = math.exp(log_duration) / 60.0
    dens_hr = (avg_hr / fcmax) / math.log(max(duration_min, 1.0))
    feat_vec = [
        float(sex_bin), float(fcmax), float(avg_hr), float(pct_hrmax),
        float(zona_hr), float(hr_max_rel), float(log_duration), float(dens_hr),
    ]
    x      = np.array(feat_vec, dtype=float)
    mean   = np.array(n1["scaler_mean"])
    scale  = np.array(n1["scaler_scale"])
    coefs  = np.array(n1["coefs"])
    intcpt = n1["intercept"]
    x_sc   = (x - mean) / scale
    return float(np.dot(x_sc, coefs) + intcpt) * 60.0   # sec/km


def predict_n3_session(
    *,
    # Perfil del atleta
    age:       float,
    sex_bin:   int,
    fcmax_obs: Optional[float] = None,
    vdot:      Optional[float] = None,
    # Sesion hipotetica / real
    avg_hr:          float,
    duration_sec:    float = 1800.0,
    log_distance_km: float = 2.302585,
    elevation_m:     float = 50.0,
    has_elevation:   int   = 1,
    cadence_filled:  float = 82.0,
    dow_sin:         float = 0.0,
    dow_cos:         float = 1.0,
    month_sin:       float = 0.0,
    month_cos:       float = 1.0,
    # Features longitudinales — obligatorias para N3
    ctl:              float = 40.0,
    atl:              float = 41.0,
    tsb:              float = -1.0,
    acwr:             float = 1.02,
    long_run_km:      float = 20.0,
    pace_delta_4s_sec: float = 0.0,
    weeks_with_data:  float = 10.0,
) -> dict:
    """
    Predice pace_sec_per_km con N3 (incluye carga longitudinal del atleta).

    Los features longitudinales (ctl, atl, tsb, acwr, long_run_km,
    pace_delta_4s_sec, weeks_with_data) deben venir del snapshot semanal
    del atleta en Supabase (weekly_features). Los defaults son medianas
    de la cohorte RUNA y se usan cuando el snapshot no está disponible.

    Returns dict equivalente a predict_n2_session + 'longitudinal_features'.
    """
    n1   = _load_n1()
    n3p  = _load_n3()
    meta = _load_n3_meta()

    model = n3p["model"]
    feats = n3p["features"]

    fcmax    = fcmax_obs if fcmax_obs else _fcmax_tanaka(age)
    vdot_val = vdot if vdot else meta.get("vdot_median_imputed", 39.28)
    pct      = avg_hr / fcmax
    zona     = _zona_hr(pct)
    log_dur  = math.log(duration_sec)
    hr_max_rel = min(1.0, pct * 1.08)

    pred_n1 = _predict_n1_sec_km(
        n1,
        sex_bin      = sex_bin,
        fcmax        = fcmax,
        avg_hr       = avg_hr,
        pct_hrmax    = pct * 100.0,
        zona_hr      = zona,
        hr_max_rel   = hr_max_rel,
        log_duration = log_dur,
    )

    feat_row = {
        # N2 features (15)
        "pred_nivel1":      pred_n1,
        "age":              float(age),
        "sex_bin":          float(sex_bin),
        "vdot":             float(vdot_val),
        "avg_hr":           float(avg_hr),
        "pct_hrmax":        float(pct * 100.0),
        "zona_hr":          float(zona),
        "log_distance_km":  float(log_distance_km),
        "elevation_m":      float(elevation_m),
        "has_elevation":    float(has_elevation),
        "cadence_filled":   float(cadence_filled),
        "dow_sin":          float(dow_sin),
        "dow_cos":          float(dow_cos),
        "month_sin":        float(month_sin),
        "month_cos":        float(month_cos),
        # Longitudinal features (7)
        "ctl":               float(ctl),
        "atl":               float(atl),
        "tsb":               float(tsb),
        "acwr":              float(acwr),
        "long_run_km":       float(long_run_km),
        "pace_delta_4s_sec": float(pace_delta_4s_sec),
        "weeks_with_data":   float(weeks_with_data),
    }

    X = np.array([[feat_row[f] for f in feats]], dtype=float)
    pace_pred = float(model.predict(X)[0])
    pace_pred = max(pace_pred, 120.0)

    mae_std = meta["mae_std_sec_km"]
    # Intervalo conformal al 80 % (cuantil de error absoluto fuera de muestra, LOSO-CV).
    # Fallback a mae_std si el artefacto no trae el cuantil conformal calibrado.
    half = float(meta.get("conformal_q80_sec_km", mae_std))

    return {
        "pace_sec_km":    round(pace_pred, 1),
        "pace_min_km":    round(pace_pred / 60, 3),
        "ci_lo_sec_km":   round(max(120.0, pace_pred - half), 1),
        "ci_hi_sec_km":   round(pace_pred + half, 1),
        "n1_pred_sec_km": round(pred_n1, 1),
        "pct_hrmax":      round(pct, 3),
        "zona_hr":        zona,
        "fcmax_used":     round(fcmax, 1),
        "fcmax_source":   "observed" if fcmax_obs else "tanaka",
        "vdot_used":      round(vdot_val, 2),
        "vdot_source":    "declared" if vdot else "imputed_median",
        "longitudinal":   {
            "ctl": ctl, "atl": atl, "tsb": tsb, "acwr": acwr,
            "long_run_km": long_run_km, "weeks_with_data": weeks_with_data,
        },
    }


def predict_n3_zones(
    *,
    age:       float,
    sex_bin:   int,
    fcmax_obs: Optional[float] = None,
    vdot:      Optional[float] = None,
    duration_sec:    float = 1800.0,
    log_distance_km: float = 2.302585,
    elevation_m:     float = 50.0,
    has_elevation:   int   = 1,
    cadence_filled:  float = 82.0,
    # Longitudinal — del snapshot actual del atleta
    ctl:               float = 40.0,
    atl:               float = 41.0,
    tsb:               float = -1.0,
    acwr:              float = 1.02,
    long_run_km:       float = 20.0,
    pace_delta_4s_sec: float = 0.0,
    weeks_with_data:   float = 10.0,
) -> list[dict]:
    """
    Prescripcion de ritmo por zona HR con N3 (5 zonas).

    Los features longitudinales son constantes por atleta — representan su
    estado de forma actual. La variacion es solo la zona HR (avg_hr hipotetica).
    """
    fcmax = fcmax_obs if fcmax_obs else _fcmax_tanaka(age)
    results = []

    for zona_num, pct in ZONE_PCT_HRMAX.items():
        avg_hr_hyp = fcmax * pct
        pred = predict_n3_session(
            age=age, sex_bin=sex_bin, fcmax_obs=fcmax_obs, vdot=vdot,
            avg_hr=avg_hr_hyp,
            duration_sec=duration_sec,
            log_distance_km=log_distance_km,
            elevation_m=elevation_m,
            has_elevation=has_elevation,
            cadence_filled=cadence_filled,
            ctl=ctl, atl=atl, tsb=tsb, acwr=acwr,
            long_run_km=long_run_km,
            pace_delta_4s_sec=pace_delta_4s_sec,
            weeks_with_data=weeks_with_data,
        )
        results.append({
            "zona":       zona_num,
            "zona_label": ZONE_LABELS[zona_num],
            "zona_name":  ZONE_NAMES[zona_num],
            "hr_bpm":     round(avg_hr_hyp, 1),
            **pred,
        })

    return results


def format_pace(pace_sec_km: float) -> str:
    pace_sec_km = max(0, pace_sec_km)
    mins = int(pace_sec_km // 60)
    secs = int(pace_sec_km % 60)
    return f"{mins}:{secs:02d} /km"
