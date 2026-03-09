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

    return out


def main():
    load_dotenv()

    sheet_id = os.getenv("SHEET_ID")
    sa_json = os.getenv("GOOGLE_SA_JSON")
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

    # 4) Por ahora, seleccionamos tu fila por cédula (si existe) o la primera
    target_cedula = "1070982737"
    if (df_norm["cedula"] == target_cedula).any():
        cedula = target_cedula
    else:
        cedula = df_norm.iloc[0]["cedula"]

    # 5) Crear carpeta atleta
    paths = ensure_dirs(data_dir, cedula)

    # 6) Guardar RAW snapshot (solo por auditoría)
    raw_path = paths["raw"] / "form_ingreso_raw.parquet"
    write_parquet_duckdb(df_raw, raw_path)

    # 7) Guardar SILVER (perfil normalizado del atleta)
    silver_profile_path = paths["silver"] / "profile.parquet"
    write_parquet_duckdb(df_norm[df_norm["cedula"] == cedula], silver_profile_path)

    # 8) Guardar JSON meta
    profile_json_path = paths["meta"] / "profile.json"
    profile = df_norm[df_norm["cedula"] == cedula].iloc[0].to_dict()
    profile_json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ Listo. Carpeta creada/actualizada para cédula {cedula}")
    print(f"RAW: {raw_path}")
    print(f"SILVER: {silver_profile_path}")
    print(f"META: {profile_json_path}")


if __name__ == "__main__":
    main()
