"""
src/ml/nivel2.py
================
Inferencia del modelo N2 — Prior de cohorte RUNA.

Nivel 2 aprende de sesiones reales de la cohorte propia y usa pred_nivel1
como stacking feature (Wolpert 1992). Modelo: Lasso + StandardScaler.

MAE LOAO-CV: 33.9 sec/km | Atletas: 42 | Sesiones: 9,926

NOTA sobre dens_hr:
  N2 fue entrenado con pred_nivel1 calculado usando dens_hr = avg_hr / log(duration_sec)
  (fórmula con bug de unidades). Para inferencia consistente se usa la MISMA formula.
  Pendiente: fix dens_hr -> reentrenar NB13b -> actualizar pkl y json.

Uso:
    from src.ml.nivel2 import predict_n2_zones, predict_n2_session
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
_N2_PKL     = _MODELS_DIR / "nivel2_v1.pkl"
_N2_JSON    = _MODELS_DIR / "nivel2_lasso_v1.json"

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

# Cache de modelos para no recargar en cada peticion
_n1_cache:   Optional[dict] = None
_n2_cache:   Optional[dict] = None   # pkl payload


def _load_n1() -> dict:
    global _n1_cache
    if _n1_cache is None:
        with open(_N1_PATH, encoding="utf-8") as f:
            _n1_cache = json.load(f)
    return _n1_cache


def _load_n2() -> dict:
    """Carga el payload del pkl de N2 (contiene modelo sklearn + metadata)."""
    global _n2_cache
    if _n2_cache is None:
        with open(_N2_PKL, "rb") as f:
            _n2_cache = pickle.load(f)
    return _n2_cache


def _load_n2_meta() -> dict:
    """Carga metadata de N2 desde JSON (mae_std, vdot_median, etc.)."""
    with open(_N2_JSON, encoding="utf-8") as f:
        return json.load(f)


def _fcmax_tanaka(age: float) -> float:
    """FCmax estimada: Tanaka (2001) 208 - 0.7*edad."""
    return 208.0 - 0.7 * age


def _zona_hr(pct_hrmax: float) -> int:
    """Porcentaje FCmax (0-1) -> zona 1-5."""
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
    pct_hrmax:    float,   # porcentaje 0-100
    zona_hr:      int,
    hr_max_rel:   float,   # max_hr_session / fcmax (aprox pct * 1.05 si no hay sesion real)
    log_duration: float,   # log(duration_sec) — ej. log(1800) = 7.496
) -> float:
    """
    Aplica N1 (Ridge, Endomondo) y devuelve pred en SEC/KM.
    Usa dens_hr = avg_hr / log_duration (formula identica a la del entrenamiento de N2).
    """
    dens_hr = avg_hr / log_duration  # misma formula que en NB13b (bug intencional para consistencia)

    feat_vec = [
        float(sex_bin),
        float(fcmax),
        float(avg_hr),
        float(pct_hrmax),    # pct_fcmax en % (0-100)
        float(zona_hr),
        float(hr_max_rel),
        float(log_duration),
        float(dens_hr),
    ]
    x      = np.array(feat_vec, dtype=float)
    mean   = np.array(n1["scaler_mean"])
    scale  = np.array(n1["scaler_scale"])
    coefs  = np.array(n1["coefs"])
    intcpt = n1["intercept"]
    x_sc   = (x - mean) / scale
    pred_min_km = float(np.dot(x_sc, coefs) + intcpt)   # N1 target es min/km
    return pred_min_km * 60.0                             # -> sec/km


def predict_n2_session(
    *,
    # Perfil del atleta (formulario)
    age:       float,
    sex_bin:   int,               # 1=M, 0=F
    fcmax_obs: Optional[float],   # FCmax real; None -> Tanaka
    vdot:      Optional[float],   # VDOT de PR; None -> mediana cohorte
    # Caracteristicas de la sesion (reales o hipoteticas para prescripcion)
    avg_hr:          float,
    duration_sec:    float = 1800.0,   # duracion en segundos (default 30 min)
    log_distance_km: float = 2.302585, # log(10 km) por defecto
    elevation_m:     float = 50.0,
    has_elevation:   int   = 1,
    cadence_filled:  float = 82.0,
    dow_sin:         float = 0.0,
    dow_cos:         float = 1.0,
    month_sin:       float = 0.0,
    month_cos:       float = 1.0,
) -> dict:
    """
    Predice pace_sec_per_km para UNA sesion usando el modelo N2.

    Para prescripcion por zona (sin sesion real):
      - avg_hr = fcmax * pct_zona
      - duration_sec = duracion tipica de la sesion del atleta (o 1800 por defecto)

    Returns dict:
        pace_sec_km   : prediccion central
        ci_lo_sec_km  : limite inferior intervalo (central - mae_std)
        ci_hi_sec_km  : limite superior intervalo (central + mae_std)
        pace_min_km   : ritmo en min/km
        n1_pred_sec_km: prediccion N1 usada como stacking
        pct_hrmax, zona_hr, fcmax_used, fcmax_source
    """
    n1   = _load_n1()
    n2p  = _load_n2()     # pkl payload
    meta = _load_n2_meta()

    model  = n2p["model"]          # sklearn Pipeline(sc, model)
    feats  = n2p["features"]       # lista de 15 features en orden

    fcmax     = fcmax_obs if fcmax_obs else _fcmax_tanaka(age)
    vdot_val  = vdot if vdot else meta["vdot_median_imputed"]
    pct       = avg_hr / fcmax            # 0-1
    zona      = _zona_hr(pct)
    log_dur   = math.log(duration_sec)
    hr_max_rel = min(1.0, pct * 1.08)    # aprox si no hay sesion real

    pred_n1 = _predict_n1_sec_km(
        n1,
        sex_bin      = sex_bin,
        fcmax        = fcmax,
        avg_hr       = avg_hr,
        pct_hrmax    = pct * 100.0,       # N1 espera porcentaje 0-100
        zona_hr      = zona,
        hr_max_rel   = hr_max_rel,
        log_duration = log_dur,
    )

    feat_row = {
        "pred_nivel1":     pred_n1,
        "age":             float(age),
        "sex_bin":         float(sex_bin),
        "vdot":            float(vdot_val),
        "avg_hr":          float(avg_hr),
        "pct_hrmax":       float(pct * 100.0),   # N2 tambien en %
        "zona_hr":         float(zona),
        "log_distance_km": float(log_distance_km),
        "elevation_m":     float(elevation_m),
        "has_elevation":   float(has_elevation),
        "cadence_filled":  float(cadence_filled),
        "dow_sin":         float(dow_sin),
        "dow_cos":         float(dow_cos),
        "month_sin":       float(month_sin),
        "month_cos":       float(month_cos),
    }

    # Armar vector en orden exacto del modelo
    X = np.array([[feat_row[f] for f in feats]], dtype=float)
    pace_pred = float(model.predict(X)[0])
    pace_pred = max(pace_pred, 120.0)   # floor fisico: 2:00 min/km

    mae_std = meta["mae_std_sec_km"]

    return {
        "pace_sec_km":    round(pace_pred, 1),
        "pace_min_km":    round(pace_pred / 60, 3),
        "ci_lo_sec_km":   round(max(120.0, pace_pred - mae_std), 1),
        "ci_hi_sec_km":   round(pace_pred + mae_std, 1),
        "n1_pred_sec_km": round(pred_n1, 1),
        "pct_hrmax":      round(pct, 3),
        "zona_hr":        zona,
        "fcmax_used":     round(fcmax, 1),
        "fcmax_source":   "observed" if fcmax_obs else "tanaka",
        "vdot_used":      round(vdot_val, 2),
        "vdot_source":    "declared" if vdot else "imputed_median",
    }


def predict_n2_zones(
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
) -> list[dict]:
    """
    Prescripcion de ritmo por zona HR para un atleta.
    Genera predicciones N2 para Z1..Z5 dado el perfil del atleta.

    Ideal para el dashboard: "si este atleta corre en Z2, que ritmo deberia llevar?"

    Returns lista de 5 dicts (Z1..Z5).
    """
    fcmax = fcmax_obs if fcmax_obs else _fcmax_tanaka(age)
    results = []

    for zona_num, pct in ZONE_PCT_HRMAX.items():
        avg_hr_hyp = fcmax * pct
        pred = predict_n2_session(
            age=age,
            sex_bin=sex_bin,
            fcmax_obs=fcmax_obs,
            vdot=vdot,
            avg_hr=avg_hr_hyp,
            duration_sec=duration_sec,
            log_distance_km=log_distance_km,
            elevation_m=elevation_m,
            has_elevation=has_elevation,
            cadence_filled=cadence_filled,
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
    """Convierte sec/km a string 'MM:SS /km'."""
    pace_sec_km = max(0, pace_sec_km)
    mins = int(pace_sec_km // 60)
    secs = int(pace_sec_km % 60)
    return f"{mins}:{secs:02d} /km"
