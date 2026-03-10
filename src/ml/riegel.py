"""riegel.py — Fórmula de Riegel para predicción de tiempo de carrera.

Referencia original:
    Riegel, P. S. (1977). Time predicting. Runner's World, 12(8), 46.
    T2 = T1 x (D2/D1)^1.06

Calibración empírica:
    Los exponentes en CALIBRATED_EXPONENTS fueron derivados minimizando el MAE
    sobre el dataset Boston Marathon 2015-2018 (n=102,729 corredores),
    usando el tiempo de media maratón para predecir el tiempo completo del mismo corredor.
    Ver: ml/notebooks/03_calibracion_riegel_boston.ipynb

    Hallazgo central: el exponente 1.06 es adecuado para corredores de 3-4h,
    pero subestima la degradación de corredores recreativos lentos (>4h, exp optimo ~1.11)
    y sobreestima la de corredores de élite (<3h, exp optimo ~1.033-1.037).

Uso desde la app:
    from src.ml.riegel import riegel, predict_from_profile
    from src.ml.riegel import predict_from_profile_calibrated
"""
from typing import Optional

# Distancias estándar en km
DISTANCES = {"5K": 5.0, "10K": 10.0, "21K": 21.0975, "42K": 42.195}

# PR keys en el perfil del atleta
PR_KEYS = {"5K": "pr_5k_sec", "10K": "pr_10k_sec", "21K": "pr_21k_sec", "42K": "pr_42k_sec"}

# ─── Exponentes calibrados por segmento (Boston 2015-2018) ───────────────────
# El segmento se define por el tiempo estimado en la distancia objetivo.
# Fuente: minimización de MAE sobre 102,729 corredores reales.
#
# Resultados:
#   Elite (<2:30h)    exp=1.0366  MAE: 3.3 min → opt: 2.5 min  (mejora: 24.2%)
#   Sub-3h (2:30-3h)  exp=1.0332  MAE: 4.5 min → opt: 3.7 min  (mejora: 17.8%)
#   3-4h              exp=1.0613  MAE: 6.4 min → opt: 6.4 min  (mejora:  0.2%)
#   4h+               exp=1.1100  MAE: 14.9 min → opt: 12.0 min (mejora: 19.5%)
CALIBRATED_EXPONENTS: dict[str, float] = {
    "elite":    1.0366,   # tiempo objetivo < 9000 seg (< 2:30h en maraton)
    "sub3h":    1.0332,   # 9000 - 10800 seg (2:30h - 3:00h)
    "3to4h":    1.0613,   # 10800 - 14400 seg (3:00h - 4:00h)  <- mayoria de usuarios
    "4hplus":   1.1100,   # > 14400 seg (> 4:00h)
    "default":  1.06,     # cuando no hay estimacion previa del tiempo objetivo
}

# Precision esperada (MAE en minutos) por segmento con exponente calibrado
CALIBRATED_MAE_MIN: dict[str, float] = {
    "elite":   2.5,
    "sub3h":   3.7,
    "3to4h":   6.4,
    "4hplus": 12.0,
    "default": 9.2,
}


def _segment_from_full_sec(full_sec: float) -> str:
    """
    Clasifica al corredor en un segmento a partir del tiempo de maratón (segundos).

    Expuesta para uso en predictor.py al normalizar PRs de otras distancias.
    Los umbrales corresponden a 2:30h, 3:00h y 4:00h en maratón.
    """
    if full_sec < 9000:   return "elite"
    if full_sec < 10800:  return "sub3h"
    if full_sec < 14400:  return "3to4h"
    return "4hplus"


def riegel(t1_sec: float, d1_km: float, d2_km: float, exponent: float = 1.06) -> float:
    """
    Predice tiempo (seg) en d2_km a partir de t1_sec en d1_km.

    Args:
        t1_sec:   tiempo conocido en segundos
        d1_km:    distancia conocida en km
        d2_km:    distancia objetivo en km
        exponent: exponente de fatiga (original Riegel = 1.06)

    Returns:
        tiempo estimado en segundos para d2_km
    """
    if t1_sec <= 0 or d1_km <= 0 or d2_km <= 0:
        raise ValueError(f"Inputs deben ser positivos: t1={t1_sec}, d1={d1_km}, d2={d2_km}")
    return t1_sec * (d2_km / d1_km) ** exponent


def riegel_pace(t1_sec: float, d1_km: float, d2_km: float) -> float:
    """Retorna el ritmo estimado (seg/km) para la distancia objetivo."""
    return riegel(t1_sec, d1_km, d2_km) / d2_km


def riegel_calibrated(
    t1_sec: float,
    d1_km: float,
    d2_km: float,
    rough_estimate_sec: Optional[float] = None,
) -> tuple[float, str, float]:
    """
    Aplica Riegel con el exponente calibrado para el segmento correspondiente.

    Si no hay estimacion previa (rough_estimate_sec=None), usa el exponente 1.06
    para un primer pasaje y clasifica el segmento con esa estimacion.

    Args:
        t1_sec:              tiempo conocido (seg)
        d1_km:               distancia conocida (km)
        d2_km:               distancia objetivo (km)
        rough_estimate_sec:  estimacion previa del tiempo en d2 (para clasificar segmento)

    Returns:
        (tiempo_estimado_seg, segmento, exponente_usado)
    """
    # Paso 1: estimar el segmento
    if rough_estimate_sec is None:
        rough = riegel(t1_sec, d1_km, d2_km, exponent=1.06)
    else:
        rough = float(rough_estimate_sec)

    segment = _segment_from_full_sec(rough)
    exponent = CALIBRATED_EXPONENTS[segment]
    t2 = riegel(t1_sec, d1_km, d2_km, exponent=exponent)
    return t2, segment, exponent


def predict_from_profile(
    profile: dict,
    target_distance: str = "42K",
    exponent: float = 1.06,
) -> Optional[dict]:
    """
    Dado el perfil del atleta (con PRs), predice el tiempo en la distancia objetivo
    usando el exponente especificado (por defecto: Riegel original 1.06).

    Returns:
        dict con keys: estimated_sec, estimated_fmt, from_distance, from_pr_sec,
                       pace_sec_per_km, model, expected_mae_min
        None si no hay PRs validos disponibles.
    """
    target_km = DISTANCES.get(target_distance)
    if target_km is None:
        return None

    preference = {
        "42K": ["21K", "10K", "5K"],
        "21K": ["10K", "5K"],
        "10K": ["5K"],
        "5K":  [],
    }.get(target_distance, [])

    for dist_key in preference:
        pr_sec = profile.get(PR_KEYS[dist_key])
        if pr_sec and float(pr_sec) > 0:
            t1   = float(pr_sec)
            d1   = DISTANCES[dist_key]
            t2   = riegel(t1, d1, target_km, exponent)
            pace = t2 / target_km
            h, rem = divmod(int(t2), 3600)
            m, s   = divmod(rem, 60)
            fmt    = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
            return {
                "estimated_sec":    round(t2),
                "estimated_fmt":    fmt,
                "from_distance":    dist_key,
                "from_pr_sec":      t1,
                "pace_sec_per_km":  round(pace, 1),
                "model":            f"riegel_{exponent:.4f}",
                "expected_mae_min": CALIBRATED_MAE_MIN.get("default"),
            }
    return None


def predict_from_profile_calibrated(
    profile: dict,
    target_distance: str = "42K",
) -> Optional[dict]:
    """
    Igual que predict_from_profile pero selecciona automáticamente el exponente
    calibrado según el segmento de velocidad del atleta.

    Expone adicionalmente: segment, exponent_used, expected_mae_min.

    Uso recomendado en la app para predicciones finales.
    """
    target_km = DISTANCES.get(target_distance)
    if target_km is None:
        return None

    preference = {
        "42K": ["21K", "10K", "5K"],
        "21K": ["10K", "5K"],
        "10K": ["5K"],
        "5K":  [],
    }.get(target_distance, [])

    for dist_key in preference:
        pr_sec = profile.get(PR_KEYS[dist_key])
        if pr_sec and float(pr_sec) > 0:
            t1 = float(pr_sec)
            d1 = DISTANCES[dist_key]

            t2, segment, exponent = riegel_calibrated(t1, d1, target_km)
            pace = t2 / target_km
            h, rem = divmod(int(t2), 3600)
            m, s   = divmod(rem, 60)
            fmt    = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

            return {
                "estimated_sec":    round(t2),
                "estimated_fmt":    fmt,
                "from_distance":    dist_key,
                "from_pr_sec":      t1,
                "pace_sec_per_km":  round(pace, 1),
                "model":            f"riegel_calibrated_{exponent:.4f}",
                "segment":          segment,
                "exponent_used":    round(exponent, 4),
                "expected_mae_min": CALIBRATED_MAE_MIN.get(segment, 9.2),
            }
    return None
