import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import duckdb


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def seconds_to_pace_str(sec_per_km: float | None) -> str:
    if sec_per_km is None or pd.isna(sec_per_km):
        return "N/A"
    sec_per_km = float(sec_per_km)
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km - m * 60))
    return f"{m}:{s:02d} min/km"


def choose_week_type(semaforo: str, acwr: float | None, monotony: float | None) -> str:
    """
    Decide tipo de semana.
    - ROJO / SIN_CHECKIN: descarga
    - AMARILLO: conservadora
    - VERDE: progreso, pero si ACWR alto o monotony alta -> conservadora
    """
    sem = (semaforo or "").upper()

    if sem in ("ROJO", "SIN_CHECKIN"):
        return "DESCARGA"

    if sem == "AMARILLO":
        return "CONSERVADORA"

    # VERDE
    # umbrales simples (ajustables)
    if acwr is not None and pd.notna(acwr) and acwr >= 1.5:
        return "CONSERVADORA"
    if monotony is not None and pd.notna(monotony) and monotony >= 2.0:
        return "CONSERVADORA"

    return "PROGRESO"


def build_running_week(days_target: int, week_type: str, paces: dict, last_week_km: float | None):
    """
    Arma una semana de running en formato de sesiones.
    """
    easy = paces.get("easy")
    mod = paces.get("mod")
    fast = paces.get("fast")

    # km base sugeridos según semana
    base_km = float(last_week_km) if last_week_km is not None and pd.notna(last_week_km) else 0.0

    if week_type == "DESCARGA":
        target_km = max(0.0, base_km * 0.7)  # -30%
        quality = False
    elif week_type == "CONSERVADORA":
        target_km = max(0.0, base_km * 0.9)  # -10%
        quality = True  # pero suave
    else:  # PROGRESO
        target_km = max(0.0, base_km * 1.07)  # +7%
        quality = True

    # Si no hay base, define un mínimo razonable por días
    if target_km == 0.0:
        target_km = {2: 12, 3: 16, 4: 20, 5: 26, 6: 32, 7: 36}.get(days_target, 20)

    # Distribución simple por tipo (rodajes + fondo + calidad)
    # Porcentaje aproximado
    long_km = target_km * 0.30
    remaining = target_km - long_km

    if days_target <= 3:
        # 2 rodajes + fondo (y una calidad opcional sustituyendo un rodaje)
        easy1 = remaining * 0.45
        easy2 = remaining * 0.55
        sessions = []
        sessions.append(("Rodaje suave", round(easy1, 1), seconds_to_pace_str(easy)))
        if quality:
            # calidad reemplaza rodaje 2
            if week_type == "CONSERVADORA":
                sessions.append(("Tempo controlado", round(easy2, 1), f"{seconds_to_pace_str(mod)} (suave)"))
            else:
                sessions.append(("Intervalos cortos", round(easy2, 1), f"{seconds_to_pace_str(fast)} (series)"))
        else:
            sessions.append(("Rodaje suave", round(easy2, 1), seconds_to_pace_str(easy)))
        sessions.append(("Fondo", round(long_km, 1), seconds_to_pace_str(easy)))
        return sessions, round(target_km, 1)

    # days_target >= 4
    easy_km_each = remaining * 0.60 / (days_target - 2)  # rodajes (sin contar calidad + fondo)
    quality_km = remaining * 0.40

    sessions = []

    # 1) Calidad
    if quality:
        if week_type == "CONSERVADORA":
            sessions.append(("Tempo controlado", round(quality_km, 1), f"{seconds_to_pace_str(mod)} (bloques)"))
        else:
            sessions.append(("Intervalos / Fartlek", round(quality_km, 1), f"{seconds_to_pace_str(fast)} (series)"))
    else:
        sessions.append(("Rodaje suave", round(quality_km, 1), seconds_to_pace_str(easy)))

    # 2) Rodajes
    for _ in range(days_target - 2):
        sessions.append(("Rodaje suave", round(easy_km_each, 1), seconds_to_pace_str(easy)))

    # 3) Fondo
    sessions.append(("Fondo", round(long_km, 1), seconds_to_pace_str(easy)))

    return sessions, round(target_km, 1)


def build_strength_week(strength_days: int, week_type: str):
    """
    Rutinas A/B simples. En descarga, más liviano.
    """
    if strength_days is None:
        strength_days = 0
    strength_days = int(strength_days)

    if strength_days <= 0:
        return []

    base_a = [
        "Sentadilla o prensa (3x8-10)",
        "Peso muerto rumano (3x8-10)",
        "Zancadas (3x10 c/pierna)",
        "Core: plancha (3x40s)",
        "Gemelos (3x12-15)"
    ]
    base_b = [
        "Hip thrust (3x8-10)",
        "Step-ups (3x10 c/pierna)",
        "Pull / remo (3x8-10)",
        "Core: dead bug (3x10 c/lado)",
        "Estabilidad tobillo/cadera (5-8 min)"
    ]

    if week_type == "DESCARGA":
        # bajar volumen
        base_a = [x.replace("3x", "2x") for x in base_a]
        base_b = [x.replace("3x", "2x") for x in base_b]

    routines = [("Fuerza A", base_a)]
    if strength_days >= 2:
        routines.append(("Fuerza B", base_b))
    if strength_days >= 3:
        routines.append(("Fuerza C (opcional)", ["Circuito liviano full body 20-25 min", "Movilidad 10 min"]))

    return routines[:strength_days]


def place_sessions_into_week(running_sessions, strength_routines, preferred_days):
    """
    Distribuye sesiones por días de semana (tabla).
    preferred_days: lista de strings ['Miércoles','Viernes','Domingo'] etc.
    """
    week_days = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    plan = {d: [] for d in week_days}

    pref = [d.strip().capitalize() for d in (preferred_days or [])]
    pref = [d for d in pref if d in week_days]

    # Selecciona días para running: primero los preferidos, luego completa
    run_days = pref.copy()
    for d in week_days:
        if len(run_days) >= len(running_sessions):
            break
        if d not in run_days:
            run_days.append(d)

    # Asignar running sessions
    for i, sess in enumerate(running_sessions):
        day = run_days[i] if i < len(run_days) else week_days[i % 7]
        plan[day].append({
            "type": "Running",
            "session": sess[0],
            "km": sess[1],
            "pace": sess[2],
        })

    # Asignar fuerza: idealmente días sin fondo (evitar mismo día del fondo)
    long_day = None
    for d in week_days:
        for it in plan[d]:
            if it.get("session") == "Fondo":
                long_day = d

    strength_days = [d for d in week_days if d != long_day]
    # poner fuerza en 2 días separados
    idx = 0
    for title, exs in strength_routines:
        day = strength_days[idx] if idx < len(strength_days) else week_days[idx % 7]
        plan[day].append({
            "type": "Fuerza",
            "session": title,
            "details": exs
        })
        idx += 2  # separación

    return plan


def main(cedula: str | None = None):
    import argparse
    load_dotenv()
    if cedula is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--cedula", required=True, help="Cédula del atleta")
        args = parser.parse_args()
        cedula = args.cedula
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    athlete_dir = data_dir / cedula
    snapshot_path = athlete_dir / "features" / "athlete_snapshot.json"
    weekly_path = athlete_dir / "features" / "weekly_features.parquet"

    snapshot = load_json(snapshot_path)
    if not snapshot:
        raise RuntimeError("No existe athlete_snapshot.json. Ejecuta build_features primero.")

    weekly = read_parquet(weekly_path)
    if weekly.empty:
        raise RuntimeError("No existe weekly_features.parquet. Ejecuta build_features primero.")

    profile = snapshot.get("profile", {})
    semaforo = snapshot.get("semaforo_latest_checkin", "SIN_CHECKIN")

    # paces desde perfil
    paces = {
        "easy": profile.get("easy_pace_sec_per_km"),
        "mod": profile.get("mod_pace_sec_per_km"),
        "fast": profile.get("fast_pace_sec_per_km"),
    }

    # última semana disponible
    weekly_sorted = weekly.copy()
    weekly_sorted["week_start_dt"] = pd.to_datetime(weekly_sorted["week_start"], errors="coerce")
    weekly_sorted = weekly_sorted.sort_values("week_start_dt")
    last = weekly_sorted.tail(1).iloc[0].to_dict()

    last_week_km = last.get("km_week")
    acwr = last.get("acwr")
    monotony = last.get("monotony")

    days_target = int(profile.get("days_run_per_week") or 4)
    strength_days = int(profile.get("strength_days_per_week") or 2)
    preferred_days = profile.get("preferred_run_days") or []

    week_type = choose_week_type(semaforo, acwr, monotony)

    running_sessions, target_km = build_running_week(days_target, week_type, paces, last_week_km)
    strength_routines = build_strength_week(strength_days, week_type)
    plan = place_sessions_into_week(running_sessions, strength_routines, preferred_days)

    out = {
        "cedula": cedula,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "week_type": week_type,
        "semaforo": semaforo,
        "targets": {
            "target_km_week": target_km,
            "days_running": days_target,
            "days_strength": strength_days
        },
        "plan_by_day": plan,
        "notes": [
            "Si aparece dolor > 3 o fatiga > 7 durante la semana: bajar intensidad y priorizar rodajes suaves.",
            "Mantén el fondo a ritmo cómodo. Si te sientes pesado, reduce 10–20% la distancia del fondo.",
        ]
    }

    out_path = athlete_dir / "features" / "weekly_plan.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Plan semanal generado:")
    print(f" - {out_path}")


if __name__ == "__main__":
    main()