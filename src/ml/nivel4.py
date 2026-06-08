"""
src/ml/nivel4.py
================
Nivel 4 — Corrección de sesgo personal walk-forward sobre N3.

Fórmula:
    N4(z) = N3(z) + mean_i( y_i - N3(i) )      para las últimas K sesiones

Validación: NB15 Parte D — MAE 31.84 sec/km (K=15), mejora 18.3% sobre N3,
Wilcoxon signed-rank p<0.001 (todos los K ∈ {5,10,15,20,30} significativos,
80.5% atletas mejoran con K=15). 1 parámetro por atleta (el sesgo medio),
sin reentrenamiento. Interpretación bayesiana: N3 = prior poblacional +
longitudinal, sesgo = posterior personal.

Regla de activación:
    ≥15 sesiones con HR → K=15  (óptimo, validado)
    10-14 sesiones      → K=10  (fallback)
    5-9  sesiones       → K=n   (fallback estrecho)
    <5   sesiones       → N4 inactivo (servir N3 puro)

Aproximación de producción: el sesgo se computa usando el snapshot longitudinal
ACTUAL del atleta (no el snapshot histórico de cada sesión). Las features
longitudinales son lentamente variables (CTL/ATL semanales) y la cohorte de las
últimas K=15 sesiones (~4-5 semanas) tiene snapshot similar. Versión refinada
posible (v2): persistir pred_n3 por sesión al ingestar y consumirlo directo.

Uso:
    from src.ml.nivel4 import predict_n4_zones
    n4 = predict_n4_zones(age=..., sex_bin=..., snapshot=..., sessions=...,
                          n3_zones=...)
    if n4["active"]:
        # n4["zones"] = lista de 5 dicts (Z1-Z5) con pace corregido
        # n4["bias_info"] = {bias_sec_km, n_used, K, std_sec_km}
"""

from __future__ import annotations

import math
import logging
from typing import Optional

import numpy as np

from src.ml.nivel3 import predict_n3_session, ZONE_LABELS, ZONE_NAMES

log = logging.getLogger(__name__)

# ── Constantes validadas en NB15 ───────────────────────────────────────────────
K_OPT          = 15      # ventana óptima — MAE 31.84 sec/km
K_FALLBACK     = 10      # fallback intermedio
N_MIN          = 5       # mínimo absoluto de sesiones
MAE_N4_SEC_KM  = 31.84   # MAE walk-forward (Wilcoxon p<0.001)
CONFORMAL_Q80_SEC_KM = 46.9   # intervalo conformal 80% (cuantil residuos walk-forward; cobertura holdout 0.80)


def compute_personal_bias(
    *,
    age:        float,
    sex_bin:    int,
    fcmax_obs:  Optional[float],
    vdot:       Optional[float],
    snapshot:   dict,
    sessions:   list[dict],
) -> Optional[dict]:
    """
    Calcula el sesgo personal medio de N3 sobre las últimas K sesiones del atleta.

    Args:
        age, sex_bin, fcmax_obs, vdot: perfil del atleta (mismas que predict_n3_zones)
        snapshot: features longitudinales actuales del atleta —
            {ctl, atl, tsb, acwr, long_run_km, pace_delta_4s_sec, weeks_with_data}
        sessions: lista de sesiones, ORDENADAS CRONOLÓGICAMENTE ASCENDENTE,
            cada dict con: {avg_hr, duration_sec, distance_km, elevation_m,
                            cadence, pace_sec_per_km}

    Returns:
        {'bias_sec_km', 'n_used', 'K', 'std_sec_km'}  o  None si <5 sesiones válidas.
    """
    n_total = len(sessions)
    if n_total < N_MIN:
        return None

    # Selección de K según disponibilidad
    if n_total >= K_OPT:
        K = K_OPT
    elif n_total >= K_FALLBACK:
        K = K_FALLBACK
    else:
        K = n_total

    recent = sessions[-K:]
    residuals: list[float] = []
    _err_sample: Optional[str] = None  # primer error para diagnóstico

    # Parser defensivo: maneja None/NaN/string vacío (mismo patrón que N3 en athletes.py)
    def _sf(v, default: float) -> float:
        try:
            if v is None: return default
            f = float(v)
            return f if f == f else default  # NaN check
        except (TypeError, ValueError):
            return default

    # Pre-parsear snapshot UNA vez (mismas features para todas las sesiones)
    ctl_v               = _sf(snapshot.get("ctl"),               40.0)
    atl_v               = _sf(snapshot.get("atl"),               41.0)
    tsb_v               = _sf(snapshot.get("tsb"),               -1.0)
    acwr_v              = _sf(snapshot.get("acwr"),              1.02)
    long_run_v          = _sf(snapshot.get("long_run_km"),       20.0)
    pace_delta_v        = _sf(snapshot.get("pace_delta_4s_sec"), 0.0)
    weeks_with_data_v   = _sf(snapshot.get("weeks_with_data"),   10.0)

    for s in recent:
        try:
            avg_hr      = _sf(s.get("avg_hr"),          0.0)
            pace_actual = _sf(s.get("pace_sec_per_km"), 0.0)
            dist_km     = max(_sf(s.get("distance_km"), 10.0),   0.1)
            dur_sec     = _sf(s.get("duration_sec"),    1800.0)
            elev_m      = _sf(s.get("elevation_m"),     0.0)
            cad         = _sf(s.get("cadence"),         82.0)
            if avg_hr <= 0 or pace_actual <= 0:
                continue

            pred = predict_n3_session(
                age               = _sf(age,     30.0),
                sex_bin           = int(sex_bin) if sex_bin is not None else 0,
                fcmax_obs         = fcmax_obs,
                vdot              = vdot,
                avg_hr            = avg_hr,
                duration_sec      = dur_sec,
                log_distance_km   = math.log(dist_km),
                elevation_m       = elev_m,
                has_elevation     = int(elev_m > 5),
                cadence_filled    = cad,
                ctl               = ctl_v,
                atl               = atl_v,
                tsb               = tsb_v,
                acwr              = acwr_v,
                long_run_km       = long_run_v,
                pace_delta_4s_sec = pace_delta_v,
                weeks_with_data   = weeks_with_data_v,
            )
            residuals.append(pace_actual - pred["pace_sec_km"])

        except Exception as exc:
            if _err_sample is None:
                _err_sample = f"{type(exc).__name__}: {str(exc)[:160]}"
            continue

    if len(residuals) < N_MIN:
        print(f"[N4 bias] FALLO residuals={len(residuals)}/{len(recent)} "
              f"K={K} first_err={_err_sample}")
        return None

    arr = np.array(residuals, dtype=float)
    return {
        "bias_sec_km": float(arr.mean()),
        "n_used":      int(len(residuals)),
        "K":           int(K),
        "std_sec_km":  float(arr.std()),
    }


def apply_bias_to_zones(n3_zones: list[dict], bias_sec_km: float) -> list[dict]:
    """Aplica la corrección de sesgo a las 5 predicciones por zona HR de N3."""
    out: list[dict] = []
    for z in n3_zones:
        n4_pace = max(120.0, float(z["pace_sec_km"]) + bias_sec_km)
        out.append({
            "zona":         z["zona"],
            "zona_label":   z["zona_label"],
            "zona_name":    z["zona_name"],
            "hr_bpm":       z["hr_bpm"],
            "pace_sec_km":  round(n4_pace, 1),
            "pace_min_km":  round(n4_pace / 60, 3),
            "ci_lo_sec_km": round(max(120.0, n4_pace - CONFORMAL_Q80_SEC_KM), 1),
            "ci_hi_sec_km": round(n4_pace + CONFORMAL_Q80_SEC_KM, 1),
            "bias_applied": round(bias_sec_km, 1),
            "mae_sec_km":   MAE_N4_SEC_KM,
        })
    return out


def predict_n4_zones(
    *,
    age:        float,
    sex_bin:    int,
    fcmax_obs:  Optional[float],
    vdot:       Optional[float],
    snapshot:   dict,
    sessions:   list[dict],
    n3_zones:   list[dict],
) -> dict:
    """
    Combina N3 + corrección personal. Siempre retorna un dict; 'active' indica
    si N4 está disponible.

    Returns:
        {
          'active':    bool,
          'zones':     list[dict]  (5 zonas o vacío),
          'bias_info': dict | None,
          'reason':    str  (cuando active=False)
        }
    """
    if not n3_zones:
        return {"active": False, "reason": "n3_inactive", "zones": [], "bias_info": None}

    bias = compute_personal_bias(
        age=age, sex_bin=sex_bin, fcmax_obs=fcmax_obs, vdot=vdot,
        snapshot=snapshot, sessions=sessions,
    )
    if bias is None:
        return {
            "active":    False,
            "reason":    f"insufficient_sessions ({len(sessions)} < {N_MIN})",
            "zones":     [],
            "bias_info": None,
        }

    zones = apply_bias_to_zones(n3_zones, bias["bias_sec_km"])
    return {"active": True, "zones": zones, "bias_info": bias, "reason": None}


def format_pace(pace_sec_km: float) -> str:
    pace_sec_km = max(0.0, pace_sec_km)
    return f"{int(pace_sec_km // 60)}:{int(pace_sec_km % 60):02d} /km"
