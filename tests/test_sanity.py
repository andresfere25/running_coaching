"""
test_sanity.py — Verificaciones mínimas de importación y lógica central.

Estas pruebas no requieren credenciales externas ni datos reales.
Son un smoke test para confirmar que el código carga sin errores.

Correr: pytest tests/
"""


def test_imports_ml():
    from src.ml.riegel import riegel, predict_from_profile
    from src.ml.load_metrics import (
        add_load_metrics, add_load_metrics_by_athlete,
        session_load_trimp, acwr_zone, compute_monotony_strain,
    )


# ─── Lógica de carga EWMA ─────────────────────────────────────────────────────

def test_load_metrics_ctl_crece_con_carga_creciente():
    """CTL debe crecer cuando la carga aumenta progresivamente."""
    import pandas as pd
    from src.ml.load_metrics import add_load_metrics
    weeks = pd.date_range('2024-01-01', periods=10, freq='W-MON')
    # Carga que sube de 10 a 50 km gradualmente
    df = pd.DataFrame({'datetime': weeks, 'distance': [10, 15, 20, 25, 30, 35, 40, 45, 50, 55]})
    out = add_load_metrics(df, load_col='distance', granularity='weekly')
    # CTL al final debe ser mayor que al inicio
    assert out['ctl'].iloc[-1] > out['ctl'].iloc[0]


def test_load_metrics_atl_cae_en_lesion():
    """ATL debe caer a 0 rápido tras una lesión (span corto)."""
    import pandas as pd
    from src.ml.load_metrics import add_load_metrics
    weeks = pd.date_range('2024-01-01', periods=6, freq='W-MON')
    df = pd.DataFrame({'datetime': weeks, 'distance': [40, 40, 40, 0, 0, 0]})
    out = add_load_metrics(df, load_col='distance', granularity='weekly')
    # Después de 3 semanas sin carga, ATL debe ser muy bajo
    assert out['atl'].iloc[-1] < 5.0


def test_acwr_zone_clasificacion():
    from src.ml.load_metrics import acwr_zone
    assert acwr_zone(0.5)  == 'BAJO'
    assert acwr_zone(1.0)  == 'OPTIMO'
    assert acwr_zone(1.4)  == 'PRECAUCION'
    assert acwr_zone(1.8)  == 'ALTO'
    assert acwr_zone(None) == 'SIN_DATOS'


def test_trimp_foster_formula():
    from src.ml.load_metrics import session_load_trimp
    assert session_load_trimp(7, 45) == 315.0
    assert session_load_trimp(10, 60) == 600.0
    assert session_load_trimp(0, 60) == 0.0


def test_riegel_basico():
    """21K -> 42K: proporcional al ratio de distancias con exponente 1.06."""
    from src.ml.riegel import riegel
    t42 = riegel(5100, 21.0975, 42.195)
    # Rango plausible: 2:50 a 3:05 para un 21K de 1:25
    assert 10200 < t42 < 11100


def test_riegel_predict_from_profile():
    """Con PR 21K válido retorna estimado; sin PRs retorna None."""
    from src.ml.riegel import predict_from_profile
    result = predict_from_profile({'pr_21k_sec': 5100}, target_distance='42K')
    assert result is not None
    assert result['from_distance'] == '21K'
    assert result['estimated_sec'] > 0
    assert '1.06' in result['model']


def test_riegel_sin_prs():
    from src.ml.riegel import predict_from_profile
    assert predict_from_profile({}, target_distance='42K') is None
    assert predict_from_profile({'pr_21k_sec': 0}, target_distance='42K') is None


def test_riegel_calibrado_segmento_correcto():
    """PR 21K 1:25 (5100s) → estimado 42K ~2:57 (10634s < 10800) → segmento sub3h."""
    from src.ml.riegel import riegel_calibrated, CALIBRATED_EXPONENTS
    t2, segment, exp = riegel_calibrated(5100, 21.0975, 42.195)
    assert segment == 'sub3h'
    assert abs(exp - CALIBRATED_EXPONENTS['sub3h']) < 0.0001
    assert 10000 < t2 < 11000  # ~2:57


def test_riegel_calibrado_segmento_3to4h():
    """PR 21K 1:40 (6000s) → estimado 42K ~3:29 (12540s) → segmento 3to4h."""
    from src.ml.riegel import riegel_calibrated, CALIBRATED_EXPONENTS
    t2, segment, exp = riegel_calibrated(6000, 21.0975, 42.195)
    assert segment == '3to4h'
    assert abs(exp - CALIBRATED_EXPONENTS['3to4h']) < 0.0001
    assert 10800 < t2 < 14400


def test_riegel_calibrado_segmento_lento():
    """PR 21K 2:00 (7200s) → estimado 42K >4h → segmento 4hplus, exp ~1.11."""
    from src.ml.riegel import riegel_calibrated, CALIBRATED_EXPONENTS
    t2, segment, exp = riegel_calibrated(7200, 21.0975, 42.195)
    assert segment == '4hplus'
    assert abs(exp - CALIBRATED_EXPONENTS['4hplus']) < 0.0001


def test_predict_calibrated_expone_segmento():
    """predict_from_profile_calibrated devuelve segment y exponent_used."""
    from src.ml.riegel import predict_from_profile_calibrated
    result = predict_from_profile_calibrated({'pr_21k_sec': 5100}, target_distance='42K')
    assert result is not None
    assert 'segment' in result
    assert 'exponent_used' in result
    assert 'expected_mae_min' in result
    assert result['segment'] == 'sub3h'


def test_imports_ingest():
    import src.ingest.ingest_forms
    import src.ingest.ingest_checkins
    import src.ingest.sheets_client
    import src.ingest.parsers


def test_imports_strava():
    import src.strava.sync_strava
    import src.strava.strava_client
    import src.strava.exchange_code


def test_imports_features():
    import src.features.build_features


def test_imports_plan():
    import src.plan.plan_builder


def test_imports_reports():
    import src.reports.generate_pdf


# ─── Lógica del semáforo ─────────────────────────────────────────────────────

def test_semaforo_sin_checkin_vacio():
    from src.features.build_features import compute_semaforo
    assert compute_semaforo({}) == "SIN_CHECKIN"


def test_semaforo_sin_checkin_no_reciente():
    """Check-in con is_recent=False debe tratarse como sin check-in."""
    from src.features.build_features import compute_semaforo
    checkin = {
        "is_recent": False,
        "latest_checkin": {
            "pain_0_10": 1,
            "fatigue_1_10": 3,
            "feeling_1_10": 9,
            "skipped_sessions": False,
            "sleep_text": "bueno",
            "stress_text": "bajo",
        }
    }
    assert compute_semaforo(checkin) == "SIN_CHECKIN"


def test_semaforo_rojo_por_dolor():
    from src.features.build_features import compute_semaforo
    checkin = {
        "is_recent": True,
        "latest_checkin": {
            "pain_0_10": 5,
            "fatigue_1_10": 3,
            "feeling_1_10": 7,
            "skipped_sessions": False,
            "sleep_text": "bueno",
            "stress_text": "bajo",
        }
    }
    assert compute_semaforo(checkin) == "ROJO"


def test_semaforo_rojo_por_sesiones_saltadas():
    from src.features.build_features import compute_semaforo
    checkin = {
        "is_recent": True,
        "latest_checkin": {
            "pain_0_10": 0,
            "fatigue_1_10": 4,
            "feeling_1_10": 7,
            "skipped_sessions": True,
            "sleep_text": "bueno",
            "stress_text": "bajo",
        }
    }
    assert compute_semaforo(checkin) == "ROJO"


def test_semaforo_amarillo_por_fatiga():
    from src.features.build_features import compute_semaforo
    checkin = {
        "is_recent": True,
        "latest_checkin": {
            "pain_0_10": 1,
            "fatigue_1_10": 6,
            "feeling_1_10": 7,
            "skipped_sessions": False,
            "sleep_text": "bueno",
            "stress_text": "bajo",
        }
    }
    assert compute_semaforo(checkin) == "AMARILLO"


def test_semaforo_verde():
    from src.features.build_features import compute_semaforo
    checkin = {
        "is_recent": True,
        "latest_checkin": {
            "pain_0_10": 1,
            "fatigue_1_10": 3,
            "feeling_1_10": 9,
            "skipped_sessions": False,
            "sleep_text": "bueno",
            "stress_text": "bajo",
        }
    }
    assert compute_semaforo(checkin) == "VERDE"


# ─── Lógica del plan ─────────────────────────────────────────────────────────

def test_choose_week_type_rojo():
    from src.plan.plan_builder import choose_week_type
    assert choose_week_type("ROJO", 1.0, 1.0) == "DESCARGA"


def test_choose_week_type_sin_checkin():
    # SIN_CHECKIN -> CONSERVADORA (no penalizar sin info; solo ROJO activa DESCARGA)
    from src.plan.plan_builder import choose_week_type
    assert choose_week_type("SIN_CHECKIN", 1.0, 1.0) == "CONSERVADORA"


def test_choose_week_type_verde_normal():
    from src.plan.plan_builder import choose_week_type
    assert choose_week_type("VERDE", 1.0, 1.0) == "PROGRESO"


def test_choose_week_type_verde_acwr_alto():
    from src.plan.plan_builder import choose_week_type
    assert choose_week_type("VERDE", 1.6, 1.0) == "CONSERVADORA"


# ─── Lógica de base robusta ───────────────────────────────────────────────────

def test_robust_base_sin_strava():
    """Sin datos Strava, la base debe ser km_week_min del perfil."""
    from src.plan.plan_builder import compute_robust_base_km
    base, _ = compute_robust_base_km(None, 0, 15.0)
    assert base == 15.0


def test_robust_base_datos_suficientes():
    """Con 4+ semanas, usa solo Strava sin mezcla."""
    from src.plan.plan_builder import compute_robust_base_km
    base, nota = compute_robust_base_km(20.0, 4, 10.0)
    assert base == 20.0
    assert "Strava" in nota


def test_robust_base_blend_2_semanas():
    """Con 2 semanas: 50% Strava + 50% perfil."""
    from src.plan.plan_builder import compute_robust_base_km
    base, nota = compute_robust_base_km(20.0, 2, 10.0)
    assert abs(base - 15.0) < 0.01  # 0.5*20 + 0.5*10 = 15
    assert "blend" in nota


def test_robust_base_semana_baja_protegida():
    """Atleta con semana muy baja (2 km) y perfil de 15 km: base sube del 2 km."""
    from src.plan.plan_builder import compute_robust_base_km
    base, _ = compute_robust_base_km(2.0, 2, 15.0)
    # 0.5*2 + 0.5*15 = 8.5  — mucho mejor que usar los 2 km crudos
    assert abs(base - 8.5) < 0.01


# ─── Predictor: sistema integrado de predicción ───────────────────────────────

def test_predictor_imports():
    from src.ml.predictor import predict_race_time_range, check_pr_consistency


def test_predict_range_basico_42k():
    """Atleta con PR 21K → predicción 42K con rango y metadatos."""
    from src.ml.predictor import predict_race_time_range
    r = predict_race_time_range({'pr_21k_sec': 5100}, '42K', age=35, gender='M')
    assert r is not None
    assert 'error' not in r
    assert 'pace_range_fmt' in r
    assert '–' in r['pace_range_fmt']
    assert r['source_pr'] == '21K'
    assert r['confidence'] == 'MEDIA'
    assert r['layers_active'] == ['Capa0', 'Capa1', 'Capa2']


def test_predict_range_sin_pr_retorna_error():
    """Sin PRs, el sistema retorna error explicativo."""
    from src.ml.predictor import predict_race_time_range
    r = predict_race_time_range({}, '42K', age=40, gender='M')
    assert r['error'] == 'SIN_PR'


def test_predict_range_todas_las_distancias():
    """Con un solo PR de 21K, el sistema predice las cuatro distancias."""
    from src.ml.predictor import predict_race_time_range
    profile = {'pr_21k_sec': 5100}
    for dist in ['5K', '10K', '21K', '42K']:
        r = predict_race_time_range(profile, dist)
        assert 'error' not in r, f"Error inesperado para {dist}: {r.get('error')}"
        assert r['estimate_sec'] > 0


def test_predict_range_confianza_menor_en_5k_con_extrapolacion():
    """
    Cuando la predicción 5K requiere extrapolación real (source ≠ 5K),
    la confianza debe degradarse respecto a la de 42K (mismo PR fuente, 1 paso).
    """
    from src.ml.predictor import predict_race_time_range
    # Solo 21K disponible: fuerza extrapolación (steps=1 para 42K, steps=2 para 5K)
    profile = {'pr_21k_sec': 5100}
    r42 = predict_race_time_range(profile, '42K', age=35, gender='M')
    r5  = predict_race_time_range(profile, '5K',  age=35, gender='M')
    confidence_order = ['ALTA', 'MEDIA', 'MEDIA-BAJA', 'BAJA']
    assert r5['extrapolation_steps'] > 0, "5K debe extrapolarse desde 21K"
    assert confidence_order.index(r42['confidence']) <= confidence_order.index(r5['confidence'])


def test_predict_range_confianza_no_degrada_cuando_mismo_pr():
    """
    Cuando source_pr == target (extrapolation_steps=0), la confianza NO debe
    degradarse para 5K/10K: la predicción es el PR directo, no una extrapolación.
    """
    from src.ml.predictor import predict_race_time_range
    # 5K PR disponible → steps=0 → confidence = MEDIA-BAJA por falta de demo, no por modelo
    r5_directo   = predict_race_time_range({'pr_5k_sec': 1140},  '5K',  age=35, gender='M')
    r10_directo  = predict_race_time_range({'pr_10k_sec': 2280}, '10K', age=35, gender='M')
    r21_directo  = predict_race_time_range({'pr_21k_sec': 5100}, '21K', age=35, gender='M')

    assert r5_directo['extrapolation_steps']  == 0
    assert r10_directo['extrapolation_steps'] == 0

    # Con mismo PR y datos demográficos, confianza base es MEDIA para todos
    assert r5_directo['confidence']  == 'MEDIA', \
        f"5K con PR directo+demo debe ser MEDIA, no {r5_directo['confidence']}"
    assert r10_directo['confidence'] == 'MEDIA', \
        f"10K con PR directo+demo debe ser MEDIA, no {r10_directo['confidence']}"
    assert r21_directo['confidence'] == 'MEDIA'


def test_consistency_ok_prs_coherentes():
    """PR 21K=1:25 y 42K=~2:57 son consistentes con Riegel."""
    from src.ml.predictor import check_pr_consistency
    # Riegel desde 21K(5100s) predice 42K ≈ 10620s → 2:57
    r = check_pr_consistency({'pr_21k_sec': 5100, 'pr_42k_sec': 10620}, '42K')
    assert r['status'] == 'OK'
    assert r['recommended_pr'] == '42K'   # el PR directo de 42K es el mejor para target 42K


def test_consistency_inconsistente_prs_contradictorios():
    """PR 5K élite + 42K muy lento → inconsistente."""
    from src.ml.predictor import check_pr_consistency
    # 5K=18min (élite) predice 42K~2:36; pero declara 42K=4:30h → Δ=~114 min
    r = check_pr_consistency({'pr_5k_sec': 1080, 'pr_42k_sec': 16200}, '42K')
    assert r['status'] in ('ADVERTENCIA', 'INCONSISTENTE')


def test_predict_range_5k_a_5k_rango_estrecho():
    """
    Predicción 5K desde PR 5K (mismo PR, 0 pasos):
    - rango debe ser notablemente más estrecho que para 2+ pasos
    - rango total < 60 s para una 5K de ~19 min (< 12 sec/km de diferencia)
    - la fracción debe ser base_fraction × step_mult[0], sin bonus de dirección
    Nota: para pr_5k=1140s, ref_42k≈10937s → segmento 3to4h →
    fracción = 0.030 × 0.85 = 0.0255 (mayor que el piso 0.020, piso no aplica).
    """
    from src.ml.predictor import (
        predict_race_time_range,
        CALIBRATED_RELATIVE_MAE,
        EXTRAPOLATION_STEP_MULTIPLIER,
    )
    r = predict_race_time_range({'pr_5k_sec': 1140}, '5K')
    assert r['source_pr'] == '5K'
    assert r['extrapolation_steps'] == 0
    assert not r['is_downward_extrapolation']

    # Fracción esperada: base × step[0], sin bonus descendente
    expected = CALIBRATED_RELATIVE_MAE[r['segment']] * EXTRAPOLATION_STEP_MULTIPLIER[0]
    assert abs(r['uncertainty_fraction'] - max(expected, 0.020)) < 0.001

    # Rango total < 60 s → pace spread < 12 sec/km en una 5K
    total_width = r['uncertainty_half_sec'] * 2
    assert total_width < 60, f"Rango demasiado amplio para 5K→5K: {total_width:.0f}s"


def test_predict_range_21k_a_21k_rango_estrecho():
    """
    Predicción 21K desde PR 21K (mismo PR, 0 pasos):
    - rango debe ser < 4 min de ancho total (coherente con variabilidad diaria)
    """
    from src.ml.predictor import predict_race_time_range
    r = predict_race_time_range({'pr_21k_sec': 5100}, '21K')
    assert r['source_pr'] == '21K'
    assert r['extrapolation_steps'] == 0
    total_width = r['uncertainty_half_sec'] * 2
    assert total_width < 240, f"Rango demasiado amplio para 21K→21K: {total_width:.0f}s"


def test_predict_range_21k_a_42k_consistente_boston():
    """
    Predicción 42K desde PR 21K (1 paso adyacente, caso calibrado):
    - incertidumbre debe equivaler al MAE de Boston para el segmento sub3h (~3.8 min)
    - rango total entre 6 y 10 min (coherente con NB03: sub3h MAE=3.7 min × 2)
    """
    from src.ml.predictor import predict_race_time_range
    r = predict_race_time_range({'pr_21k_sec': 5100}, '42K')
    assert r['source_pr'] == '21K'
    assert r['extrapolation_steps'] == 1
    assert not r['is_downward_extrapolation']
    # half_width ≈ 2.2% de ~10440s → ~230s ≈ 3.8 min
    # total width entre 6 y 10 min
    total_width_min = r['uncertainty_half_sec'] * 2 / 60
    assert 5.0 < total_width_min < 10.0, (
        f"Rango 21K→42K fuera del rango esperado por Boston: {total_width_min:.1f} min"
    )


def test_predict_range_21k_a_5k_descendente_mas_amplio():
    """
    Predicción 5K desde PR 21K (2 pasos, descendente):
    - rango debe ser más amplio que 21K→42K (1 paso, ascendente)
    - total < 3 min (razonable para una 5K de ~19 min)
    """
    from src.ml.predictor import predict_race_time_range
    r_down = predict_race_time_range({'pr_21k_sec': 5100}, '5K')
    r_up   = predict_race_time_range({'pr_21k_sec': 5100}, '42K')

    assert r_down['source_pr'] == '21K'
    assert r_down['extrapolation_steps'] == 2
    assert r_down['is_downward_extrapolation']

    # Descendente debe ser más incierto que ascendente (más pasos + bonus downward)
    assert r_down['uncertainty_fraction'] > r_up['uncertainty_fraction'], (
        "Extrapolación descendente debe tener mayor incertidumbre que ascendente"
    )
    # Rango absoluto razonable: < 3 min para una 5K
    total_width_min = r_down['uncertainty_half_sec'] * 2 / 60
    assert total_width_min < 3.0, (
        f"Rango 21K→5K demasiado amplio: {total_width_min:.1f} min"
    )


def test_predict_range_10k_a_42k_2_pasos():
    """
    Predicción 42K desde PR 10K (2 pasos, ascendente):
    - rango debe ser más amplio que 21K→42K (1 paso)
    - fraction = CALIBRATED_RELATIVE_MAE[sub3h] × STEP_MULTIPLIER[2] = 0.022 × 1.60
    """
    from src.ml.predictor import (
        predict_race_time_range,
        CALIBRATED_RELATIVE_MAE,
        EXTRAPOLATION_STEP_MULTIPLIER,
    )
    r_2step = predict_race_time_range({'pr_10k_sec': 2280}, '42K')
    r_1step = predict_race_time_range({'pr_21k_sec': 5100}, '42K')

    assert r_2step['source_pr'] == '10K'
    assert r_2step['extrapolation_steps'] == 2
    assert not r_2step['is_downward_extrapolation']

    # 2 pasos deben dar más incertidumbre que 1 paso
    assert r_2step['uncertainty_fraction'] > r_1step['uncertainty_fraction']

    # Fracción esperada = sub3h × step[2] = 0.022 × 1.60 = 0.0352
    expected = CALIBRATED_RELATIVE_MAE['sub3h'] * EXTRAPOLATION_STEP_MULTIPLIER[2]
    assert abs(r_2step['uncertainty_fraction'] - expected) < 0.001, (
        f"Fracción esperada {expected:.4f}, obtenida {r_2step['uncertainty_fraction']:.4f}"
    )


def test_predict_range_incertidumbre_escala_con_pasos():
    """
    La fracción de incertidumbre debe crecer con el número de pasos,
    manteniendo el mismo segmento (mismo PR fuente) y la misma dirección.
    Usamos un PR único para forzar distintos pasos: 21K→5K(2), 21K→10K(1), 21K→21K(0).
    """
    from src.ml.predictor import predict_race_time_range
    profile = {'pr_21k_sec': 5100}   # sin otros PRs para forzar 21K como fuente

    r0 = predict_race_time_range(profile, '21K')   # 0 pasos
    r1 = predict_race_time_range(profile, '42K')   # 1 paso (ascendente)
    r2 = predict_race_time_range(profile, '5K')    # 2 pasos (descendente + bonus)

    assert r0['extrapolation_steps'] == 0
    assert r1['extrapolation_steps'] == 1
    assert r2['extrapolation_steps'] == 2

    assert r0['uncertainty_fraction'] < r1['uncertainty_fraction'], \
        "0 pasos debe ser más estrecho que 1 paso"
    assert r1['uncertainty_fraction'] < r2['uncertainty_fraction'], \
        "1 paso debe ser más estrecho que 2 pasos (+ bonus downward)"


def test_predict_range_nuevos_campos_presentes():
    """Los campos nuevos de la API deben estar presentes en la respuesta."""
    from src.ml.predictor import predict_race_time_range
    r = predict_race_time_range({'pr_21k_sec': 5100}, '42K', age=35, gender='M')
    assert 'uncertainty_fraction' in r
    assert 'uncertainty_half_sec' in r
    assert 'extrapolation_steps' in r
    assert 'mae_effective_min' in r          # campo legado, sigue presente
    assert isinstance(r['uncertainty_fraction'], float)
    assert 0.0 < r['uncertainty_fraction'] < 0.3
    assert r['uncertainty_half_sec'] > 0


def test_piso_elevado_datos_escasos():
    """Con datos escasos el piso es 80% de km_week_min, no 60%."""
    from src.plan.plan_builder import build_running_week
    paces = {"easy": 320, "mod": 260, "fast": 225}
    # Atleta con 2 km la última semana, mínimo declarado 15 km, 2 semanas Strava
    # base = 0.5*2 + 0.5*15 = 8.5; CONSERVADORA → 8.5*0.9 = 7.65
    # piso escaso = 15 * 0.80 = 12.0 → debe ganar el piso
    _, target_km, _, _ = build_running_week(
        days_target=4, week_type="CONSERVADORA", paces=paces,
        last_week_km=2.0, km_week_min=15.0, data_weeks_available=2,
    )
    assert target_km >= 12.0  # piso 80% de 15 km


# ─── build_features: funciones auxiliares ────────────────────────────────────

def test_readiness_score_optimo():
    """TSB ideal + ACWR óptimo + VERDE → score alto."""
    from src.features.build_features import compute_readiness_score
    score = compute_readiness_score(tsb=15, acwr=1.0, semaforo="VERDE")
    # tsb_score=100 (40%) + acwr_score=100 (35%) + sem_score=100 (25%) = 100
    assert score == 100


def test_readiness_score_rojo_bajo():
    """TSB muy fatigado + ACWR alto + ROJO → score bajo."""
    from src.features.build_features import compute_readiness_score
    score = compute_readiness_score(tsb=-40, acwr=1.8, semaforo="ROJO")
    # tsb_score=20 (40%) + acwr_score=30 (35%) + sem_score=30 (25%) = 8+10.5+7.5 = 26
    assert score <= 30


def test_readiness_score_sin_datos_neutrales():
    """Sin datos (None) → componentes neutrales 50 → score ~50."""
    from src.features.build_features import compute_readiness_score
    score = compute_readiness_score(tsb=None, acwr=None, semaforo="SIN_CHECKIN")
    # 0.40*50 + 0.35*50 + 0.25*50 = 50
    assert score == 50


def test_readiness_score_rango():
    """Score siempre entre 0 y 100."""
    from src.features.build_features import compute_readiness_score
    for tsb in [-50, -15, 0, 10, 35]:
        for acwr in [0.4, 0.9, 1.4, 2.0]:
            for sem in ["VERDE", "AMARILLO", "ROJO", "SIN_CHECKIN"]:
                s = compute_readiness_score(tsb, acwr, sem)
                assert 0 <= s <= 100, f"Score fuera de rango: {s} (tsb={tsb}, acwr={acwr}, sem={sem})"


def test_racha_semanas_basico():
    """Streak se acumula con actividad y se reinicia sin actividad."""
    import pandas as pd
    from src.features.build_features import _compute_racha_semanas
    has = pd.Series([True, True, False, True, True, True])
    result = _compute_racha_semanas(has)
    assert list(result) == [1, 2, 0, 1, 2, 3]


def test_racha_semanas_todo_activo():
    import pandas as pd
    from src.features.build_features import _compute_racha_semanas
    has = pd.Series([True] * 5)
    result = _compute_racha_semanas(has)
    assert list(result) == [1, 2, 3, 4, 5]


def test_racha_semanas_todo_inactivo():
    import pandas as pd
    from src.features.build_features import _compute_racha_semanas
    has = pd.Series([False] * 4)
    result = _compute_racha_semanas(has)
    assert list(result) == [0, 0, 0, 0]


def test_km_trend_growing():
    """Serie creciente → últimas semanas deben ser 'growing'."""
    import pandas as pd
    from src.features.build_features import _compute_km_trend
    km = pd.Series([10.0, 10.0, 10.0, 10.0, 20.0])  # último >10% sobre promedio prev
    result = _compute_km_trend(km, window=4)
    assert result.iloc[-1] == "growing"


def test_km_trend_declining():
    """Serie decreciente → último valor 'declining'."""
    import pandas as pd
    from src.features.build_features import _compute_km_trend
    km = pd.Series([20.0, 20.0, 20.0, 20.0, 5.0])
    result = _compute_km_trend(km, window=4)
    assert result.iloc[-1] == "declining"


def test_km_trend_stable():
    """Serie plana → 'stable'."""
    import pandas as pd
    from src.features.build_features import _compute_km_trend
    km = pd.Series([15.0, 15.0, 15.0, 15.0, 15.5])  # <10% cambio
    result = _compute_km_trend(km, window=4)
    assert result.iloc[-1] == "stable"


def test_km_trend_insuficiente_none():
    """Con una sola semana no hay promedio previo → None."""
    import pandas as pd
    from src.features.build_features import _compute_km_trend
    km = pd.Series([20.0])
    result = _compute_km_trend(km, window=4)
    assert result.iloc[0] is None or result.iloc[0] != result.iloc[0]  # None o NaN


def test_build_race_snapshots_sin_historial():
    """Sin race_history → lista vacía."""
    import pandas as pd
    from src.features.build_features import build_race_snapshots
    profile = {"race_history": []}
    snapshots = build_race_snapshots(profile, pd.DataFrame())
    assert snapshots == []


def test_build_race_snapshots_sin_campo():
    """Perfil sin campo race_history → lista vacía."""
    import pandas as pd
    from src.features.build_features import build_race_snapshots
    snapshots = build_race_snapshots({}, pd.DataFrame())
    assert snapshots == []


def test_build_race_snapshots_sin_strava_match():
    """Carrera con fecha fuera del rango Strava → snapshot_usable=False, no error."""
    import pandas as pd
    from src.features.build_features import build_race_snapshots

    profile = {
        "race_history": [
            {"distance_key": "21K", "time_sec": 5100, "date_iso": "2023-01-15"}
        ]
    }
    # DataFrame con columnas requeridas pero sin filas que coincidan con la fecha
    weekly_df = pd.DataFrame({
        "week_start": ["2024-01-01", "2024-01-08"],
        "ctl": [30.0, 32.0],
        "atl": [28.0, 30.0],
        "tsb": [2.0, 2.0],
        "acwr": [0.95, 0.98],
        "km_week": [40.0, 42.0],
        "pace_sec_per_km_week": [310.0, 308.0],
    })
    snapshots = build_race_snapshots(profile, weekly_df)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_usable"] is False
    assert snapshots[0]["distance_key"] == "21K"
    assert snapshots[0]["time_sec"] == 5100


# ─── src.coach.publish — validación y plantilla ───────────────────────────────

def test_coach_validate_draft_ok():
    """Draft con campos requeridos completos → sin errores."""
    from src.coach.publish import validate_draft
    draft = {
        "coach_message": "Esta semana consolidamos la base aeróbica.",
        "weekly_focus":  "Base aeróbica",
        "week_start":    "2026-03-09",
        "session_notes": {"LUN": "Inicia suave", "SAB": "Sesión clave"},
        "plan_overrides": {},
    }
    assert validate_draft(draft) == []


def test_coach_validate_draft_campo_vacio():
    """coach_message vacío → error de campo requerido."""
    from src.coach.publish import validate_draft
    draft = {
        "coach_message": "",
        "weekly_focus":  "Base aeróbica",
        "week_start":    "2026-03-09",
    }
    errors = validate_draft(draft)
    assert any("coach_message" in e for e in errors)


def test_coach_validate_draft_clave_dia_invalida():
    """Clave de día no canónica → error con nombre de la clave."""
    from src.coach.publish import validate_draft
    draft = {
        "coach_message": "Mensaje OK",
        "weekly_focus":  "Focus OK",
        "week_start":    "2026-03-09",
        "session_notes": {"Lunes": "nota"},   # nombre completo no válido
    }
    errors = validate_draft(draft)
    assert any("Lunes" in e for e in errors)


def test_coach_validate_draft_claves_canonicas_validas():
    """Todas las claves canónicas deben pasar sin error."""
    from src.coach.publish import validate_draft, VALID_DAY_KEYS
    notes = {k: "nota" for k in VALID_DAY_KEYS}
    draft = {
        "coach_message": "Mensaje OK",
        "weekly_focus":  "Focus OK",
        "week_start":    "2026-03-09",
        "session_notes": notes,
    }
    assert validate_draft(draft) == []


def test_coach_make_template_estructura():
    """make_template debe incluir todos los campos esperados."""
    from src.coach.publish import make_template
    t = make_template("1070982737", snapshot={}, plan={})
    assert "coach_message" in t
    assert "weekly_focus" in t
    assert "week_start" in t
    assert "session_notes" in t
    assert "plan_overrides" in t
    assert "_auto_context" in t
    assert t["status"] == "draft"
    # Las 7 claves canónicas de día deben estar en session_notes
    for key in ("LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"):
        assert key in t["session_notes"]
