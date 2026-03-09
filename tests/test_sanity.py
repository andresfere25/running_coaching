"""
test_sanity.py — Verificaciones mínimas de importación y lógica central.

Estas pruebas no requieren credenciales externas ni datos reales.
Son un smoke test para confirmar que el código carga sin errores.

Correr: pytest tests/
"""


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
