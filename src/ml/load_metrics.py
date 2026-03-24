"""load_metrics.py — Métricas de carga de entrenamiento con EWMA.

Implementa CTL/ATL/TSB (Training Stress Balance) y ACWR usando
medias móviles exponenciales (EWMA), equivalente al modelo PMC
(Performance Management Chart) de TrainingPeaks.

Constantes de tiempo estándar:
    CTL (Fitness):   tau = 42 días → span = 42  (daily) | span = 6  (weekly)
    ATL (Fatigue):   tau = 7 días  → span = 7   (daily) | span = 1  (weekly)
    TSB (Form):      CTL - ATL  (positivo = fresco, negativo = fatigado)
    ACWR:            ATL / CTL  (ideal: 0.8–1.3)

Referencias:
    Bannister et al. (1975). Systems model of training for athletic performance.
    Hulin et al. (2016). Spikes in acute workload are associated with injury.
    Foster et al. (2001). A new approach to monitoring exercise training.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Literal

# ─── Constantes de tiempo ────────────────────────────────────────────────────
# EWMA: span = 2*tau/7 + 1 para granularidad semanal
CTL_SPAN_DAILY  = 42
ATL_SPAN_DAILY  = 7
CTL_SPAN_WEEKLY = 6   # ≈ 42/7
ATL_SPAN_WEEKLY = 1   # ≈ 7/7  (en weekly, ATL ≈ semana actual)


# ─── Función principal ───────────────────────────────────────────────────────

def add_load_metrics(
    df: pd.DataFrame,
    load_col: str = "distance",
    granularity: Literal["daily", "weekly"] = "weekly",
    ctl_span: int | None = None,
    atl_span: int | None = None,
) -> pd.DataFrame:
    """
    Añade CTL, ATL, TSB y ACWR a un DataFrame ordenado por fecha.

    El DataFrame debe estar ordenado por fecha y pertenecer a un único atleta
    (o estar previamente agrupado). Para múltiples atletas, usa
    `add_load_metrics_by_athlete()`.

    Args:
        df:           DataFrame con columna de carga y fechas ordenadas
        load_col:     Nombre de la columna de carga (distancia, tiempo, TRIMP, etc.)
        granularity:  'daily' o 'weekly' — determina los spans por defecto
        ctl_span:     Span EWMA para CTL (sobreescribe default por granularidad)
        atl_span:     Span EWMA para ATL (sobreescribe default por granularidad)

    Returns:
        DataFrame con columnas añadidas: ctl, atl, tsb, acwr
    """
    if granularity == "daily":
        _ctl = ctl_span or CTL_SPAN_DAILY
        _atl = atl_span or ATL_SPAN_DAILY
    else:
        _ctl = ctl_span or CTL_SPAN_WEEKLY
        _atl = atl_span or ATL_SPAN_WEEKLY

    out = df.copy()
    load = out[load_col].fillna(0.0).astype(float)

    out["ctl"]  = load.ewm(span=_ctl,  adjust=False).mean()
    out["atl"]  = load.ewm(span=_atl,  adjust=False).mean()
    out["tsb"]  = out["ctl"] - out["atl"]

    # ACWR: evitar división por cero
    out["acwr"] = np.where(out["ctl"] > 0, out["atl"] / out["ctl"], np.nan)

    return out


def add_load_metrics_by_athlete(
    df: pd.DataFrame,
    athlete_col: str = "athlete",
    date_col: str = "datetime",
    load_col: str = "distance",
    granularity: Literal["daily", "weekly"] = "weekly",
) -> pd.DataFrame:
    """
    Aplica add_load_metrics por atleta (correcto para datasets con múltiples corredores).

    El EWMA se reinicia para cada atleta — nunca mezcla series temporales
    de corredores distintos.

    Returns:
        DataFrame con columnas ctl, atl, tsb, acwr por fila.
    """
    df = df.sort_values([athlete_col, date_col]).copy()
    return (
        df.groupby(athlete_col, group_keys=False)
          .apply(lambda g: add_load_metrics(g, load_col=load_col, granularity=granularity))
    )


# ─── TRIMP (Foster session RPE method) ───────────────────────────────────────

def session_load_trimp(rpe: float, duration_min: float) -> float:
    """
    Carga de sesión por método Foster (Session RPE × duración en minutos).

    Escala RPE: 0 = reposo, 10 = esfuerzo máximo.
    Referencia: Foster et al. (2001), Medicine & Science in Sports & Exercise.

    Args:
        rpe:          RPE de la sesión (0–10)
        duration_min: Duración en minutos

    Returns:
        Carga de sesión (unidades arbitrarias, AU)
    """
    return float(rpe) * float(duration_min)


def weekly_trimp_from_srpe(df_srpe: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega la carga semanal (TRIMP Foster) desde un DataFrame de srpe.csv.

    El formato esperado (pmdata) es:
        end_date_time | activity_names | perceived_exertion | duration_min

    Returns:
        DataFrame semanal con columnas: week_start, trimp_week, n_sessions
    """
    df = df_srpe.copy()
    df["dt"] = pd.to_datetime(df["end_date_time"], utc=True, errors="coerce")
    df = df.dropna(subset=["dt"]).copy()
    df["week_start"] = (
        df["dt"].dt.to_period("W-SUN").dt.start_time.dt.tz_localize(None)
    )
    df["trimp"] = df["perceived_exertion"] * df["duration_min"]

    weekly = (
        df.groupby("week_start", as_index=False)
          .agg(
              trimp_week=("trimp",  "sum"),
              n_sessions=("trimp",  "count"),
          )
    )
    return weekly


# ─── Clasificación de ACWR ────────────────────────────────────────────────────

def acwr_zone(acwr: float | None) -> str:
    """
    Clasifica el ACWR en zonas de riesgo según literatura (Hulin et al., 2016).

    Zonas:
        BAJO:  ACWR < 0.8  — subentrenamiento, riesgo de bajo rendimiento
        OPTIMO: 0.8–1.3    — rango seguro con adaptación positiva
        PRECAUCION: 1.3–1.5 — zona amarilla, aumentar monitoreo
        ALTO:  > 1.5       — zona roja, riesgo de lesión elevado
    """
    if acwr is None or pd.isna(acwr):
        return "SIN_DATOS"
    if acwr < 0.8:
        return "BAJO"
    if acwr <= 1.3:
        return "OPTIMO"
    if acwr <= 1.5:
        return "PRECAUCION"
    return "ALTO"


# ─── Monotonía y Strain (Foster) ─────────────────────────────────────────────

def compute_monotony_strain(daily_loads: pd.Series) -> tuple[float, float]:
    """
    Calcula monotonía y strain semanal (Foster et al., 2001).

    monotony = mean(daily_load) / std(daily_load)
    strain   = weekly_total × monotony

    Un monotony > 2.0 es señal de falta de variación en el entrenamiento.

    Args:
        daily_loads: Serie con la carga de cada día de la semana (7 valores)

    Returns:
        (monotony, strain)
    """
    mean = daily_loads.mean()
    std  = daily_loads.std()
    if std == 0 or pd.isna(std):
        return np.nan, np.nan
    monotony = mean / std
    strain   = daily_loads.sum() * monotony
    return float(monotony), float(strain)
