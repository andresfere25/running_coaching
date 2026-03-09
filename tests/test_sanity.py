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
    assert result['model'] == 'riegel_1.06'


def test_riegel_sin_prs():
    from src.ml.riegel import predict_from_profile
    assert predict_from_profile({}, target_distance='42K') is None
    assert predict_from_profile({'pr_21k_sec': 0}, target_distance='42K') is None


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
