"""riegel.py — Fórmula de Riegel para predicción de tiempo de carrera.

Referencia:
    Riegel, P. S. (1977). Time predicting. Runner's World, 12(8), 46.
    T2 = T1 × (D2/D1)^1.06

Uso desde la app:
    from src.ml.riegel import riegel, predict_from_profile
"""
from typing import Optional

# Distancias estándar en km
DISTANCES = {"5K": 5.0, "10K": 10.0, "21K": 21.0975, "42K": 42.195}

# PR keys en el perfil del atleta
PR_KEYS = {"5K": "pr_5k_sec", "10K": "pr_10k_sec", "21K": "pr_21k_sec", "42K": "pr_42k_sec"}


def riegel(t1_sec: float, d1_km: float, d2_km: float, exponent: float = 1.06) -> float:
    """Predice tiempo (seg) en d2_km a partir de t1_sec en d1_km."""
    if t1_sec <= 0 or d1_km <= 0 or d2_km <= 0:
        raise ValueError(f"Inputs deben ser positivos: t1={t1_sec}, d1={d1_km}, d2={d2_km}")
    return t1_sec * (d2_km / d1_km) ** exponent


def riegel_pace(t1_sec: float, d1_km: float, d2_km: float) -> float:
    """Retorna el ritmo estimado (seg/km) para la distancia objetivo."""
    return riegel(t1_sec, d1_km, d2_km) / d2_km


def predict_from_profile(
    profile: dict,
    target_distance: str = "42K",
    exponent: float = 1.06,
) -> Optional[dict]:
    """
    Dado el perfil del atleta (con PRs), predice el tiempo en la distancia objetivo.

    Busca el mejor PR disponible (preferencia: distancia más larga menor al target)
    y aplica Riegel.

    Returns:
        dict con keys: estimated_sec, estimated_fmt, from_distance, from_pr_sec,
                       pace_sec_per_km, model
        None si no hay PRs válidos disponibles.
    """
    target_km = DISTANCES.get(target_distance)
    if target_km is None:
        return None

    # Preferir el PR de mayor distancia menor al target (más representativo)
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
            t2 = riegel(t1, d1, target_km, exponent)
            pace = t2 / target_km
            h, rem = divmod(int(t2), 3600)
            m, s = divmod(rem, 60)
            fmt = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
            return {
                "estimated_sec": round(t2),
                "estimated_fmt": fmt,
                "from_distance": dist_key,
                "from_pr_sec": t1,
                "pace_sec_per_km": round(pace, 1),
                "model": "riegel_1.06",
            }
    return None
