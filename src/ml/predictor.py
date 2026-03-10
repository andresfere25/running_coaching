"""predictor.py — Sistema de predicción multi-distancia con rango de ritmo.

Integra las capas de la arquitectura de predicción del proyecto:

    Capa 0: Verificación de consistencia y selección del PR fuente
    Capa 1: Riegel calibrado por segmento (src/ml/riegel.py)
    Capa 2: Corrección demográfica (age × gender)
    Capa 3: Corrección por carga  [pendiente — requiere dataset real]
    Capa 4: Ajuste de bienestar   [pendiente — requiere datos de la app]

Salida principal: rango de ritmo (pace_low–pace_high min/km) + metadatos.

Uso desde la app:
    from src.ml.predictor import predict_race_time_range, check_pr_consistency

    result = predict_race_time_range(
        profile         = {'pr_21k_sec': 5100, 'pr_10k_sec': 2700},
        target_distance = '42K',
        age             = 35,
        gender          = 'M',
    )
    print(result['pace_range_fmt'])   # '5:11 – 5:29 min/km'
    print(result['time_range_fmt'])   # '3:38:50 – 3:52:10'
    print(result['confidence'])       # 'MEDIA'
"""
from __future__ import annotations

import math
from typing import Optional

from src.ml.riegel import (
    riegel_calibrated,
    DISTANCES,
    PR_KEYS,
    CALIBRATED_MAE_MIN,
)

# ─── Preferencia de PR fuente por distancia objetivo ─────────────────────────
# Regla: usar el PR más cercano a la distancia objetivo.
# Extrapolación hacia arriba (corto→largo) es más fiable que hacia abajo.
PREFERENCE_ORDER: dict[str, list[str]] = {
    '42K': ['42K', '21K', '10K', '5K'],   # PR exacto de la misma distancia es el mejor
    '21K': ['21K', '10K', '5K', '42K'],
    '10K': ['10K', '5K', '21K', '42K'],
    '5K':  ['5K', '10K', '21K', '42K'],
}

# ─── Penalización de MAE según distancia objetivo ────────────────────────────
# Justificación: la validación empírica es sólida para 42K (Boston 103K +
# Results.csv 429K). Para distancias más cortas, la cobertura disminuye.
MAE_PENALTY_FACTOR: dict[str, float] = {
    '42K': 1.0,
    '21K': 1.2,   # +20%: soporte parcial (Boston Half como fuente, no target)
    '10K': 1.5,   # +50%: solo Riegel extrapolado sin validación directa
    '5K':  1.8,   # +80%: mayor extrapolación + sensibilidad a velocidad
}

# Penalización adicional por extrapolación descendente (PR fuente > target)
# Riegel calibrado para upward extrapolation; hacia abajo es menos natural
DOWNWARD_EXTRAPOLATION_PENALTY = 1.3

# Soporte empírico declarado por distancia (para reportar al usuario)
EMPIRICAL_SUPPORT: dict[str, str] = {
    '42K': 'ALTA — Boston 2015-2018 (103K) + Results.csv (429K maratonistas)',
    '21K': 'MEDIA — Boston Half splits como PR fuente; sin dataset 21K standalone',
    '10K': 'BAJA — Riegel extrapolado; sin validación empírica directa a escala',
    '5K':  'BAJA — Riegel extrapolado; mayor sensibilidad a velocidad vs resistencia',
}

# ─── Corrección demográfica: residuales post-calibración (Capa 2b) ───────────
# Derivados del análisis empírico Boston 2015-2018 (NB04).
# Representan el sesgo RESIDUAL de Riegel calibrado por edad y género.
# Positivo = Riegel optimista (sumar tiempo al estimado).
# Escala: 42K. Se ajusta proporcionalmente para otras distancias.
#
# Nota: para producción, serializar el Ridge model de NB04 y cargarlo aquí.
# Estos valores son aproximaciones lineales de los coeficientes ajustados.
_AGE_CORRECTION_42K_SEC: list[tuple[float, float]] = [
    # (edad_max, corrección_segundos)
    (30,  -60),   # <30:  -1.0 min — jóvenes llegan ligeramente antes de lo predicho
    (40,  -15),   # 30-40: -0.25 min — casi neutro
    (45,   30),   # 40-45: +0.5 min
    (50,   90),   # 45-50: +1.5 min
    (55,  150),   # 50-55: +2.5 min
    (60,  240),   # 55-60: +4.0 min
    (65,  360),   # 60-65: +6.0 min
    (200, 480),   # 65+:   +8.0 min — positive split más pronunciado con la edad
]
_GENDER_CORRECTION_F_42K_SEC = -60   # -1.0 min: mujeres tienen ritmo más uniforme


# ─── Helpers internos ────────────────────────────────────────────────────────

def _fmt_pace(sec_per_km: float) -> str:
    """Formatea seg/km como 'M:SS'. Ej: 322.0 → '5:22'."""
    if not sec_per_km or sec_per_km <= 0 or math.isnan(sec_per_km):
        return '--'
    mins, secs = divmod(int(round(sec_per_km)), 60)
    return f'{mins}:{secs:02d}'


def _fmt_time(total_sec: float) -> str:
    """Formatea segundos totales como 'H:MM:SS'. Ej: 13545 → '3:45:45'."""
    if not total_sec or total_sec <= 0 or math.isnan(total_sec):
        return '--'
    h, rem = divmod(int(round(total_sec)), 3600)
    m, s   = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}'


def _age_group(age: float) -> str:
    """Grupo de edad estándar del proyecto."""
    for upper, label in [(25,'18-24'),(30,'25-29'),(35,'30-34'),(40,'35-39'),
                         (45,'40-44'),(50,'45-49'),(55,'50-54'),(60,'55-59'),
                         (65,'60-64')]:
        if age < upper:
            return label
    return '65+'


def _is_downward(source_dist: str, target_dist: str) -> bool:
    """True si se extrapola de una distancia mayor a una menor."""
    return DISTANCES.get(source_dist, 0) > DISTANCES.get(target_dist, 0)


def _demographic_correction_sec(
    age: float,
    gender: str,
    target_km: float,
    segment: str,
) -> float:
    """
    Retorna la corrección demográfica en segundos para sumar al estimado Riegel.

    Positiva = Riegel era optimista (añadir tiempo).
    Negativa = Riegel era pesimista (reducir tiempo).

    La corrección base es para 42K y se escala a la distancia objetivo.
    Fuente: análisis empírico Boston 2015-2018 (n=103K), NB04.
    """
    # Corrección por edad (efecto principal del positive split con la edad)
    age_correction_42k = _AGE_CORRECTION_42K_SEC[-1][1]  # fallback: 65+
    for age_max, correction in _AGE_CORRECTION_42K_SEC:
        if age < age_max:
            age_correction_42k = correction
            break

    # Corrección por género
    gender_correction_42k = (
        _GENDER_CORRECTION_F_42K_SEC
        if str(gender).upper() in ('F', 'FEMALE', 'MUJER')
        else 0
    )

    # Escalar a la distancia objetivo (el positive split escala con la distancia)
    D_REF = 42.195
    scale = (target_km / D_REF) ** 1.06

    return (age_correction_42k + gender_correction_42k) * scale


# ─── Capa 0: selección y consistencia de PR ──────────────────────────────────

def _select_source_pr(
    profile: dict,
    target_distance: str,
) -> tuple[str, float] | tuple[None, None]:
    """
    Selecciona el PR fuente más adecuado para la distancia objetivo.
    Sigue el PREFERENCE_ORDER definido en el módulo.
    Descarta PRs inválidos (0, None, negative).
    """
    order = PREFERENCE_ORDER.get(target_distance, [])
    for dist_key in order:
        pr_key = PR_KEYS.get(dist_key, '')
        raw    = profile.get(pr_key)
        if raw is not None:
            try:
                val = float(raw)
                if val > 0:
                    return dist_key, val
            except (TypeError, ValueError):
                continue
    return None, None


def check_pr_consistency(
    profile: dict,
    target_distance: str = '42K',
) -> dict:
    """
    Verifica la consistencia mutua de todos los PRs declarados en el perfil.

    Para cada par (source → other), aplica Riegel calibrado y compara
    con el PR declarado. Discrepancia > 2×MAE_segmento indica PR desactualizado,
    error de reporte o carrera atípica.

    Returns:
        {
          'status':             'OK' | 'ADVERTENCIA' | 'INCONSISTENTE' | 'SIN_DATOS_MULTIPLES',
          'checks':             lista de comparaciones par a par,
          'recommended_pr':     distancia fuente recomendada,
          'recommended_pr_sec': tiempo del PR recomendado,
          'notes':              lista de mensajes descriptivos,
        }
    """
    # Recopilar PRs válidos
    available: dict[str, float] = {}
    for dist in ['5K', '10K', '21K', '42K']:
        raw = profile.get(PR_KEYS.get(dist, ''))
        if raw is not None:
            try:
                val = float(raw)
                if val > 0:
                    available[dist] = val
            except (TypeError, ValueError):
                pass

    rec_source, rec_sec = _select_source_pr(profile, target_distance)

    if len(available) < 2:
        return {
            'status':             'SIN_DATOS_MULTIPLES',
            'checks':             [],
            'recommended_pr':     rec_source,
            'recommended_pr_sec': rec_sec,
            'notes': ['Solo hay un PR disponible — no es posible verificar consistencia.'],
        }

    checks   = []
    warnings = []
    errors   = []

    for src_dist, src_sec in available.items():
        for tgt_dist, tgt_sec in available.items():
            if src_dist == tgt_dist:
                continue
            src_km = DISTANCES[src_dist]
            tgt_km = DISTANCES[tgt_dist]

            pred_sec, segment, _ = riegel_calibrated(src_sec, src_km, tgt_km)
            mae_threshold = CALIBRATED_MAE_MIN.get(segment, 9.2) * 2

            delta_min  = abs(pred_sec - tgt_sec) / 60
            consistent = delta_min <= mae_threshold

            checks.append({
                'from':              src_dist,
                'to':                tgt_dist,
                'reported_fmt':      _fmt_time(tgt_sec),
                'riegel_pred_fmt':   _fmt_time(pred_sec),
                'delta_min':         round(delta_min, 1),
                'threshold_min':     round(mae_threshold, 1),
                'consistent':        consistent,
                'segment':           segment,
            })

            if not consistent:
                msg = (
                    f"PR {src_dist} ({_fmt_time(src_sec)}) predice {tgt_dist} en "
                    f"{_fmt_time(pred_sec)}, pero se reportó {_fmt_time(tgt_sec)} "
                    f"(Δ={delta_min:.1f} min, umbral={mae_threshold:.1f} min). "
                    f"Posible causa: PR desactualizado o carrera atípica."
                )
                if delta_min > mae_threshold * 1.5:
                    errors.append(msg)
                else:
                    warnings.append(msg)

    status = 'OK'
    if errors:
        status = 'INCONSISTENTE'
    elif warnings:
        status = 'ADVERTENCIA'

    notes = warnings + errors
    if status == 'OK':
        notes = ['Todos los PRs son mutuamente consistentes (dentro de 2×MAE esperado).']

    return {
        'status':             status,
        'checks':             checks,
        'recommended_pr':     rec_source,
        'recommended_pr_sec': int(rec_sec) if rec_sec else None,
        'notes':              notes,
    }


# ─── Función principal: predicción multi-distancia con rango de ritmo ─────────

def predict_race_time_range(
    profile: dict,
    target_distance: str = '42K',
    age: Optional[float] = None,
    gender: Optional[str] = None,
    load_metrics: Optional[dict] = None,
    checkin: Optional[dict] = None,
) -> dict:
    """
    Predice el rango de ritmo esperado para la distancia objetivo.

    Integra Capas 0 + 1 + 2 de la arquitectura de predicción.
    Capas 3 (carga) y 4 (bienestar) están reservadas: se anotan si los datos
    están presentes pero no modifican el estimado todavía.

    Args:
        profile:         Perfil del atleta. Debe contener pr_5k_sec, pr_10k_sec,
                         pr_21k_sec y/o pr_42k_sec (cualquier subconjunto válido).
        target_distance: '5K', '10K', '21K' o '42K'.
        age:             Edad numérica (activa Capa 2b).
        gender:          'M' o 'F' (activa Capa 2b).
        load_metrics:    dict con CTL, ATL, TSB, ACWR (Capa 3, reservada).
        checkin:         dict con fatigue, sleep, stress, pain (Capa 4, reservada).

    Returns:
        dict completo con predicción, rango de ritmo y metadatos de confianza.
        Si no hay PR disponible, retorna dict con key 'error'.
    """
    if target_distance not in DISTANCES:
        return {'error': 'DISTANCIA_INVALIDA',
                'message': f"Distancia '{target_distance}' no soportada. Use: 5K, 10K, 21K, 42K."}

    target_km    = DISTANCES[target_distance]
    layers_active: list[str] = []

    # ─── Capa 0: selección y verificación del PR ──────────────────────────────
    source_dist, source_sec = _select_source_pr(profile, target_distance)
    has_pr      = source_dist is not None
    is_downward = has_pr and _is_downward(source_dist, target_distance)

    if has_pr:
        layers_active.append('Capa0')

    # ─── Capa 1: Riegel calibrado ─────────────────────────────────────────────
    if has_pr:
        source_km = DISTANCES[source_dist]
        estimate_sec, segment, exponent = riegel_calibrated(
            source_sec, source_km, target_km
        )
        mae_base_min = CALIBRATED_MAE_MIN.get(segment, 9.2)
        layers_active.append('Capa1')
    else:
        return {
            'target_distance': target_distance,
            'error':   'SIN_PR',
            'message': ('No hay marcas personales disponibles. '
                        'Registra al menos un tiempo de carrera (5K, 10K, 21K o 42K).'),
            'layers_active': layers_active,
            'empirical_support': EMPIRICAL_SUPPORT[target_distance],
        }

    # ─── Capa 2: corrección demográfica ───────────────────────────────────────
    has_demo          = age is not None and gender is not None
    demo_correction_sec = 0.0

    if has_demo:
        demo_correction_sec = _demographic_correction_sec(
            age, gender, target_km, segment
        )
        layers_active.append('Capa2')

    # ─── Capa 3: corrección por carga [reservada] ─────────────────────────────
    load_correction_sec = 0.0
    if load_metrics:
        layers_active.append('Capa3[pendiente]')
        # TODO NB05+: entrenar corrector con dataset real (no Run Club — sintético)

    # ─── Capa 4: ajuste de bienestar [reservada] ──────────────────────────────
    wellness_correction_sec = 0.0
    if checkin:
        layers_active.append('Capa4[pendiente]')

    # ─── Estimación final ─────────────────────────────────────────────────────
    final_sec = estimate_sec + demo_correction_sec + load_correction_sec + wellness_correction_sec

    # ─── MAE efectivo ─────────────────────────────────────────────────────────
    distance_penalty = MAE_PENALTY_FACTOR.get(target_distance, 1.0)
    downward_penalty = DOWNWARD_EXTRAPOLATION_PENALTY if is_downward else 1.0
    mae_effective_min = mae_base_min * distance_penalty * downward_penalty

    # ─── Rango de ritmo ───────────────────────────────────────────────────────
    half_width_sec = mae_effective_min * 60

    time_low  = max(final_sec - half_width_sec, target_km * 120)  # mínimo 2:00/km
    time_high = final_sec + half_width_sec

    pace_center = final_sec / target_km
    pace_low    = time_low  / target_km
    pace_high   = time_high / target_km

    # ─── Nivel de confianza ───────────────────────────────────────────────────
    n_layers = len([l for l in layers_active if '[pendiente]' not in l])

    if n_layers >= 3 and load_metrics:
        confidence        = 'ALTA'
        confidence_reason = 'PR + demografía + historial de entrenamiento'
    elif has_demo and has_pr:
        confidence        = 'MEDIA'
        confidence_reason = f'PR de {source_dist} + corrección demográfica'
    else:
        confidence        = 'MEDIA-BAJA'
        confidence_reason = f'PR de {source_dist} sin corrección demográfica'

    # Penalización por distancias con menor soporte empírico
    if target_distance in ('5K', '10K') and confidence != 'BAJA':
        _down = {'ALTA': 'MEDIA', 'MEDIA': 'MEDIA-BAJA', 'MEDIA-BAJA': 'BAJA'}
        confidence        = _down.get(confidence, confidence)
        confidence_reason += f'; menor soporte empírico para {target_distance}'

    # Nota sobre extrapolación descendente
    direction_note = ''
    if is_downward:
        direction_note = (
            f'Extrapolación descendente ({source_dist}→{target_distance}): '
            f'PR en distancia más larga aplicado a distancia más corta. '
            f'El MAE puede ser mayor de lo indicado.'
        )

    return {
        # ─── Rango de ritmo ──────────────────────────────────────────────────
        'pace_center_fmt':    _fmt_pace(pace_center),
        'pace_low_fmt':       _fmt_pace(pace_low),
        'pace_high_fmt':      _fmt_pace(pace_high),
        'pace_range_fmt':     f'{_fmt_pace(pace_low)} – {_fmt_pace(pace_high)} min/km',
        'pace_center_sec_km': round(pace_center, 1),
        'pace_low_sec_km':    round(pace_low, 1),
        'pace_high_sec_km':   round(pace_high, 1),
        # ─── Tiempo total ────────────────────────────────────────────────────
        'time_center_fmt':    _fmt_time(final_sec),
        'time_low_fmt':       _fmt_time(time_low),
        'time_high_fmt':      _fmt_time(time_high),
        'time_range_fmt':     f'{_fmt_time(time_low)} – {_fmt_time(time_high)}',
        'estimate_sec':       round(final_sec),
        # ─── Métricas del modelo ─────────────────────────────────────────────
        'mae_effective_min':  round(mae_effective_min, 1),
        'segment':            segment,
        'exponent_used':      round(exponent, 4),
        'target_distance':    target_distance,
        'target_km':          target_km,
        # ─── Origen del PR ───────────────────────────────────────────────────
        'source_pr':          source_dist,
        'source_pr_sec':      int(source_sec),
        'source_pr_fmt':      _fmt_time(source_sec),
        'is_downward_extrapolation': is_downward,
        # ─── Correcciones aplicadas ──────────────────────────────────────────
        'demo_correction_sec':    round(demo_correction_sec),
        'demo_correction_min':    round(demo_correction_sec / 60, 2),
        'age_group':              _age_group(age) if has_demo else None,
        'gender':                 str(gender).upper() if has_demo else None,
        # ─── Metadatos de confianza ──────────────────────────────────────────
        'layers_active':          layers_active,
        'confidence':             confidence,
        'confidence_reason':      confidence_reason,
        'direction_note':         direction_note,
        'empirical_support':      EMPIRICAL_SUPPORT[target_distance],
    }
