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
