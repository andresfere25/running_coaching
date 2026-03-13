import os
import json
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
import duckdb


from src.ingest.sheets_client import open_sheet, read_worksheet_as_records
from src.ingest.parsers import (
    norm_str, parse_yes_no, parse_int, parse_float,
    parse_pace_to_seconds, parse_duration_to_seconds,
    parse_minutes_range, parse_range_km_week, parse_multi_select
)

FORM1_TAB = "Form Responses 1"


def write_parquet_duckdb(df: pd.DataFrame, path: Path) -> None:
    """
    Escribe Parquet usando DuckDB (no requiere pyarrow).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    # COPY escribe Parquet nativo
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT 'parquet')")
    con.close()


def ensure_dirs(base_dir: Path, cedula: str) -> dict:
    athlete_dir = base_dir / str(cedula)
    paths = {
        "athlete_dir": athlete_dir,
        "raw": athlete_dir / "raw",
        "silver": athlete_dir / "silver",
        "meta": athlete_dir / "meta",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def normalize_form1_row(row: dict) -> dict:
    """
    Normaliza 1 fila del Form 1 (Ingreso).
    A prueba de:
    - "No sé"
    - rangos (10–20)
    - tiempos mm:ss / hh:mm:ss
    - multi-select separados por coma
    - sí/no con variantes
    """
    out = {}

    out["timestamp"] = norm_str(row.get("Timestamp"))

    out["consent_plan"] = parse_yes_no(
        row.get("Acepto que Andrés use mis respuestas para construir y ajustar mi plan de entrenamiento.")
    )
    out["consent_anon"] = parse_yes_no(
        row.get("Acepto que mis datos se usen de forma anónima para análisis/estudio (sin nombre ni cédula visible).")
    )

    out["cedula"] = norm_str(row.get("Cedula o documento de identidad"))
    out["name"] = norm_str(row.get("Nombre y apellido"))
    out["whatsapp"] = norm_str(row.get("Celular (WhatsApp)"))
    out["email"] = norm_str(row.get("Correo"))
    out["city_country"] = norm_str(row.get("Ciudad / País (opcional)"))
    out["age"] = parse_int(row.get("Edad"))
    out["sex"] = norm_str(row.get("Sexo (opcional)"))

    out["goal_main"] = norm_str(row.get("¿Cuál es tu objetivo principal?"))
    out["race_distance"] = norm_str(row.get("Distancia de la carrera objetivo"))
    out["race_date_raw"] = norm_str(row.get("Fecha de la carrera objetivo"))

    out["has_time_goal"] = parse_yes_no(row.get("¿Tienes un tiempo objetivo?"))
    out["time_goal_raw"] = norm_str(
        row.get('Si tienes tiempo objetivo: escríbelo (hh:mm:ss o mm:ss) / Si no sabes escribe "No sé"')
    )
    out["time_goal_sec"] = parse_duration_to_seconds(out["time_goal_raw"])

    out["days_run_per_week"] = parse_int(row.get("¿Cuántos días a la semana puedes entrenar running?"))

    w_min, w_max = parse_minutes_range(row.get("Tiempo máximo entre semana por sesión"))
    out["weekday_session_min_min"] = w_min
    out["weekday_session_max_min"] = w_max

    we_min, we_max = parse_minutes_range(row.get("Tiempo máximo el fin de semana por sesión"))
    out["weekend_session_min_min"] = we_min
    out["weekend_session_max_min"] = we_max

    out["preferred_run_days"] = parse_multi_select(
        row.get("Días que prefieres correr (elige los que normalmente puedes)")
    )
    out["training_time_pref"] = norm_str(row.get("¿En qué horario sueles entrenar?"))

    out["strength_access"] = norm_str(row.get("¿Tienes acceso a gimnasio o puedes hacer fuerza en casa?"))
    out["strength_days_per_week"] = parse_int(row.get("¿Cuántos días puedes hacer fuerza por semana?"))

    out["other_sports"] = norm_str(row.get("¿Haces otro deporte aparte de correr?"))
    out["sleep_hours_raw"] = norm_str(row.get("Sueño promedio por noche"))

    out["has_pain"] = parse_yes_no(row.get("¿Tienes dolor actualmente?"))
    out["pain_location"] = norm_str(row.get("¿Dónde sientes dolor? (elige todo lo que aplique)"))
    out["pain_level_0_10"] = parse_int(row.get("En una escala 0–10, ¿qué tan fuerte es el dolor?"))

    out["had_injuries_12m"] = parse_yes_no(row.get("¿Has tenido lesiones en los últimos 12 meses?"))
    out["medical_conditions"] = norm_str(row.get("Condiciones médicas relevantes (opcional)"))

    out["running_experience"] = norm_str(row.get("¿Hace cuánto corres?"))
    km_min, km_max = parse_range_km_week(row.get("En las últimas 4 semanas, ¿cuántos km por semana aprox.?"))
    out["km_week_min"] = km_min
    out["km_week_max"] = km_max

    out["avg_days_running_4w"] = parse_int(row.get("Días promedio corriendo por semana (últimas 4 semanas)"))
    out["long_run_recent"] = norm_str(row.get("Fondo más largo reciente"))
    out["surface"] = norm_str(row.get("Superficie donde corres la mayoría del tiempo"))

    out["has_recent_prs"] = parse_yes_no(
        row.get("¿Tienes marcas recientes en carrera o test (últimos 12 meses)? Por ejemplo mejor tiempo en 5 km")
    )
    out["pr_5k_sec"] = parse_duration_to_seconds(row.get('Mejor tiempo reciente 5K (mm:ss) o "No sé"'))
    out["pr_10k_sec"] = parse_duration_to_seconds(row.get('Mejor tiempo reciente 10K (mm:ss) o "No sé"'))
    out["pr_21k_sec"] = parse_duration_to_seconds(row.get('Mejor tiempo reciente 21K (hh:mm:ss) o "No sé"'))
    out["pr_42k_sec"] = parse_duration_to_seconds(row.get('Mejor tiempo reciente 42K (hh:mm:ss) o "No sé"'))

    out["easy_pace_sec_per_km"] = parse_pace_to_seconds(
        row.get('Ritmo cómodo (suave) aproximado (min/km) o "No sé"')
    )
    out["mod_pace_sec_per_km"] = parse_pace_to_seconds(
        row.get('Ritmo moderado aproximado (min/km) o "No sé"')
    )
    out["fast_pace_sec_per_km"] = parse_pace_to_seconds(
        row.get('Ritmo rápido aproximado (min/km) o "No sé"')
    )

    out["uses_device"] = parse_yes_no(row.get("¿Usas reloj o app para registrar entrenos?"))
    out["main_app"] = norm_str(row.get("¿Cuál App usas principalmente?"))

    out["height_cm"] = parse_int(row.get("Estatura (cm)"))
    out["weight_kg"] = parse_float(row.get("Peso (kg) "))
    out["birth_date_raw"] = norm_str(row.get("Fecha de nacimiento"))

    out["has_target_race"] = parse_yes_no(row.get("Tienes carrera objetivo?"))
    out["uses_strava"] = parse_yes_no(row.get("Usas Strava para registrar tus entrenamientos?"))
    out["consent_strava"] = parse_yes_no(
        row.get("Darías tu consentimiento para usar Strava y mejorar de una manera grandiosa el diseño de tu plan de entrenamiento y una mayor personalización y seguimiento ?")
    )

    # ── Historial de carreras pasadas (hasta 3) ───────────────────────────
    # Campos esperados en el Google Form (sección nueva, todos opcionales):
    #   "Carrera reciente 1 — Distancia"       → 5K / 10K / 21K / 42K
    #   "Carrera reciente 1 — Tiempo (hh:mm:ss o mm:ss)"
    #   "Carrera reciente 1 — Fecha (yyyy-mm-dd)"
    # La clave distance_key normaliza el texto a uno de los 4 valores estándar.
    # Este bloque solo almacena datos — NO activa ningún modelo todavía.

    _dist_map = {
        "5k": "5K", "5km": "5K",
        "10k": "10K", "10km": "10K",
        "21k": "21K", "21km": "21K", "media": "21K", "media maratón": "21K",
        "42k": "42K", "42km": "42K", "maratón": "42K", "maraton": "42K",
    }

    race_history = []
    for i in range(1, 4):
        raw_dist = norm_str(row.get(f"Carrera reciente {i} — Distancia"))
        raw_time = norm_str(row.get(f"Carrera reciente {i} — Tiempo (hh:mm:ss o mm:ss)"))
        raw_date = norm_str(row.get(f"Carrera reciente {i} — Fecha (yyyy-mm-dd)"))

        dist_key = _dist_map.get((raw_dist or "").lower().strip()) if raw_dist else None
        time_sec = parse_duration_to_seconds(raw_time) if raw_time else None

        # Solo incluir si al menos distancia y tiempo están presentes
        if dist_key and time_sec and time_sec > 0:
            race_history.append({
                "distance_key": dist_key,
                "time_sec":     time_sec,
                "date_iso":     raw_date,   # puede ser None si no se declaró
            })

    out["race_history"] = race_history   # lista vacía si no hay carreras declaradas

    return out


def main(cedula: str | None = None):
    """
    Ingesta del Form 1 (perfil de atletas) desde Google Sheets.

    cedula (opcional):
      - Si se provee: procesa solo ese atleta. Error si no está en el Form.
      - Si es None:   procesa todos los atletas del Form (modo global para CLI).

    Llamado desde sync.py con cedula específico.
    Llamado desde run_pipeline.py sin cedula (procesa todos).
    """
    load_dotenv()

    sheet_id = os.getenv("SHEET_ID")
    sa_json  = os.getenv("GOOGLE_SA_JSON")
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    # 1) Abrir Google Sheet
    sheet = open_sheet(sheet_id, sa_json)

    # 2) Leer Form Responses 1
    records = read_worksheet_as_records(sheet, FORM1_TAB)
    if not records:
        raise RuntimeError(f"No se encontraron registros en la pestaña '{FORM1_TAB}'.")

    df_raw = pd.DataFrame(records)

    # 3) Normalizar filas
    normalized = []
    for r in records:
        n = normalize_form1_row(r)
        if n.get("cedula"):
            normalized.append(n)

    df_norm = pd.DataFrame(normalized)
    if df_norm.empty:
        raise RuntimeError("No se pudieron normalizar filas. Revisa el header de cédula y campos.")

    # 4) Determinar qué cédula(s) procesar
    if cedula:
        if not (df_norm["cedula"] == cedula).any():
            raise RuntimeError(
                f"Cédula '{cedula}' no encontrada en '{FORM1_TAB}'. "
                f"Verifica que el atleta haya enviado el formulario."
            )
        cedulas_to_process = [cedula]
    else:
        # Modo global: todos los atletas del Form
        cedulas_to_process = sorted(df_norm["cedula"].unique().tolist())

    # 5) Procesar cada cédula
    for ced in cedulas_to_process:
        paths = ensure_dirs(data_dir, ced)

        # RAW snapshot (auditoría)
        raw_path = paths["raw"] / "form_ingreso_raw.parquet"
        write_parquet_duckdb(df_raw, raw_path)

        # SILVER — perfil normalizado de este atleta
        silver_profile_path = paths["silver"] / "profile.parquet"
        write_parquet_duckdb(df_norm[df_norm["cedula"] == ced], silver_profile_path)

        # META — JSON consumido por build_features / push_profile
        profile_json_path = paths["meta"] / "profile.json"
        profile = df_norm[df_norm["cedula"] == ced].iloc[0].to_dict()
        profile_json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"✅ Forms ingest OK para cédula {ced}")
        print(f"   RAW:    {raw_path}")
        print(f"   SILVER: {silver_profile_path}")
        print(f"   META:   {profile_json_path}")


if __name__ == "__main__":
    main()
