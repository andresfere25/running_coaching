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
    Decide tipo de semana según el semáforo del último check-in y métricas de carga.

    - ROJO:        descarga — hay señal clara de sobrecarga o malestar
    - SIN_CHECKIN: conservadora — no hay información suficiente; no castigar al atleta
    - AMARILLO:    conservadora
    - VERDE:       progreso, excepto si ACWR o monotonía están elevados
    """
    sem = (semaforo or "").upper()

    if sem == "ROJO":
        return "DESCARGA"

    if sem in ("SIN_CHECKIN", "AMARILLO"):
        return "CONSERVADORA"

    # VERDE — verificar métricas de carga antes de progresar
    if acwr is not None and pd.notna(acwr) and acwr >= 1.5:
        return "CONSERVADORA"
    if monotony is not None and pd.notna(monotony) and monotony >= 2.0:
        return "CONSERVADORA"

    return "PROGRESO"


def compute_robust_base_km(
    last_week_km: float | None,
    data_weeks_available: int,
    km_week_min: float | None,
) -> tuple[float, str]:
    """
    Calcula la base de km a usar para el plan, robusta ante historial escaso.

    Convención:
    - 0 semanas Strava: usa km_week_min del perfil (sin datos propios).
    - 1–3 semanas:  blend ponderado. Strava gana peso a medida que acumula.
        base = alpha * last_week_km + (1 - alpha) * km_week_min
        donde alpha = data_weeks_available / 4
        Con 1 semana: 25% Strava / 75% perfil.
        Con 2 semanas: 50% / 50%.
        Con 3 semanas: 75% / 25%.
    - 4+ semanas: usa solo last_week_km (historial suficiente para confiar en Strava).

    Esto evita que una semana atípicamente baja de Strava colapse el plan
    por debajo del nivel real del atleta, sin sobre-estimar con el perfil declarado.

    Retorna (base_km, nota_explicativa).
    """
    raw_strava = float(last_week_km) if last_week_km is not None and pd.notna(last_week_km) else 0.0
    profile_ref = float(km_week_min) if km_week_min and km_week_min > 0 else 0.0

    # Sin historial Strava
    if raw_strava == 0.0 or data_weeks_available == 0:
        base = profile_ref if profile_ref > 0 else 0.0
        nota = f"sin datos Strava → usa perfil declarado ({base:.1f} km)"
        return base, nota

    # Historial suficiente → solo Strava
    if data_weeks_available >= 4 or profile_ref == 0.0:
        nota = f"Strava ({data_weeks_available} sem) → {raw_strava:.1f} km"
        return raw_strava, nota

    # Datos escasos → blend ponderado
    alpha = data_weeks_available / 4  # peso de Strava (0.25, 0.50, 0.75)
    blended = alpha * raw_strava + (1 - alpha) * profile_ref
    nota = (
        f"blend escaso: {alpha:.0%} Strava ({raw_strava:.1f} km) + "
        f"{1-alpha:.0%} perfil ({profile_ref:.1f} km) = {blended:.1f} km"
    )
    return blended, nota


def build_running_week(
    days_target: int,
    week_type: str,
    paces: dict,
    last_week_km: float | None,
    km_week_min: float | None = None,
    km_week_max: float | None = None,
    data_weeks_available: int = 4,
):
    """
    Arma una semana de running en formato de sesiones.

    Usa compute_robust_base_km() para calcular la base cuando el historial
    de Strava es escaso (< 4 semanas), evitando planes subdimensionados
    por semanas aisladas atípicamente bajas.

    El piso de validación se eleva de 60% → 80% de km_week_min cuando hay
    datos escasos, porque con poca historia el riesgo de subestimar es mayor.
    """
    easy = paces.get("easy")
    mod = paces.get("mod")
    fast = paces.get("fast")

    # Base robusta: blend Strava/perfil según semanas disponibles
    base_km, base_nota = compute_robust_base_km(last_week_km, data_weeks_available, km_week_min)

    if week_type == "DESCARGA":
        target_km = max(0.0, base_km * 0.7)  # -30%
        quality = False
    elif week_type == "CONSERVADORA":
        target_km = max(0.0, base_km * 0.9)  # -10%
        quality = True  # pero suave
    else:  # PROGRESO
        target_km = max(0.0, base_km * 1.07)  # +7%
        quality = True

    # Sin base → fallback por perfil declarado o por días objetivo
    if target_km == 0.0:
        if km_week_min and km_week_min > 0:
            target_km = km_week_min
        else:
            target_km = {2: 12, 3: 16, 4: 20, 5: 26, 6: 32, 7: 36}.get(days_target, 20)

    # Piso de validación contra el perfil declarado.
    # Con datos escasos (< 4 semanas) el piso sube de 60% → 80%:
    # hay más riesgo de subestimar cuando el historial es corto.
    if km_week_min and km_week_min > 0:
        floor_factor = 0.80 if data_weeks_available < 4 else 0.60
        floor_km = km_week_min * floor_factor
        target_km = max(target_km, floor_km)

    # ── Distribución por sesiones ──────────────────────────────────────────
    long_km   = target_km * 0.30
    remaining = target_km - long_km

    if days_target <= 3:
        easy1 = remaining * 0.45
        easy2 = remaining * 0.55
        sessions = []
        sessions.append(("Rodaje suave", round(easy1, 1), seconds_to_pace_str(easy)))
        if quality:
            if week_type == "CONSERVADORA":
                sessions.append(("Tempo controlado", round(easy2, 1), f"{seconds_to_pace_str(mod)} (suave)"))
            else:
                sessions.append(("Intervalos cortos", round(easy2, 1), f"{seconds_to_pace_str(fast)} (series)"))
        else:
            sessions.append(("Rodaje suave", round(easy2, 1), seconds_to_pace_str(easy)))
        sessions.append(("Fondo", round(long_km, 1), seconds_to_pace_str(easy)))
        return sessions, round(target_km, 1), base_km, base_nota

    # days_target >= 4
    easy_km_each = remaining * 0.60 / (days_target - 2)
    quality_km   = remaining * 0.40

    sessions = []
    if quality:
        if week_type == "CONSERVADORA":
            sessions.append(("Tempo controlado", round(quality_km, 1), f"{seconds_to_pace_str(mod)} (bloques)"))
        else:
            sessions.append(("Intervalos / Fartlek", round(quality_km, 1), f"{seconds_to_pace_str(fast)} (series)"))
    else:
        sessions.append(("Rodaje suave", round(quality_km, 1), seconds_to_pace_str(easy)))

    for _ in range(days_target - 2):
        sessions.append(("Rodaje suave", round(easy_km_each, 1), seconds_to_pace_str(easy)))

    sessions.append(("Fondo", round(long_km, 1), seconds_to_pace_str(easy)))

    return sessions, round(target_km, 1), base_km, base_nota


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
    km_week_min = profile.get("km_week_min")
    km_week_max = profile.get("km_week_max")

    # Semanas de Strava disponibles (sin contar semanas con 0 km por gaps)
    data_weeks_available = int(weekly_sorted["km_week"].notna().sum())

    week_type = choose_week_type(semaforo, acwr, monotony)

    running_sessions, target_km, base_km, base_nota = build_running_week(
        days_target, week_type, paces, last_week_km,
        km_week_min=km_week_min, km_week_max=km_week_max,
        data_weeks_available=data_weeks_available,
    )
    strength_routines = build_strength_week(strength_days, week_type)
    plan = place_sessions_into_week(running_sessions, strength_routines, preferred_days)

    # ─── Distribución de km por tipo de sesión ──────────────────────────────
    dist_km = {"easy_km": 0.0, "fondo_km": 0.0, "quality_km": 0.0}
    for sess in running_sessions:
        name_lower = sess[0].lower()
        km_val = sess[1]
        if "fondo" in name_lower:
            dist_km["fondo_km"] += km_val
        elif any(k in name_lower for k in ("interv", "fartlek", "tempo")):
            dist_km["quality_km"] += km_val
        else:
            dist_km["easy_km"] += km_val
    dist_km = {k: round(v, 1) for k, v in dist_km.items()}

    # ─── Resumen y explicación legible ──────────────────────────────────────
    sem_labels = {
        "VERDE": "check-in VERDE (estado óptimo)",
        "AMARILLO": "check-in AMARILLO (precaución)",
        "ROJO": "check-in ROJO (reducir carga)",
        "SIN_CHECKIN": "sin check-in reciente",
    }
    type_labels = {
        "PROGRESO": "de progresión (+7%)",
        "CONSERVADORA": "conservadora (volumen estable)",
        "DESCARGA": "de descarga (-30%)",
    }
    # week_summary: frase legible para el atleta (sin jerga técnica del blend)
    sparse_note = (
        f" ({data_weeks_available} sem. de datos — usando blend con perfil declarado)"
        if data_weeks_available < 4 else ""
    )
    week_summary = (
        f"Semana {type_labels.get(week_type, week_type)} — "
        f"{sem_labels.get(semaforo, semaforo)}. "
        f"Base: {base_km:.1f} km{sparse_note} → Objetivo: {target_km:.1f} km."
    )

    # Proyección si el próximo check-in es VERDE: sobre la base robusta, no sobre last_week
    next_if_verde_km = round(base_km * 1.07, 1) if base_km > 0 else None

    focus_map = {
        "DESCARGA": "Recuperación activa — rodajes suaves y fuerza preventiva.",
        "CONSERVADORA": "Consolidación de base — mantener ritmo y consistencia.",
        "PROGRESO": "Progresión de volumen — aumentar carga gradualmente.",
    }

    out = {
        "cedula": cedula,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "week_type": week_type,
        "semaforo": semaforo,
        "weekly_focus": focus_map.get(week_type, ""),
        "week_summary": week_summary,
        "coach_explanation": {
            "trigger": f"{semaforo} → {week_type}",
            "base_km": round(base_km, 2),
            "base_method": base_nota,
            "data_weeks_strava": data_weeks_available,
            "multiplier": {"DESCARGA": 0.70, "CONSERVADORA": 0.90, "PROGRESO": 1.07}.get(week_type),
            "target_km": target_km,
            "km_week_min_profile": km_week_min,
            "km_week_max_profile": km_week_max,
            "next_week_if_verde_km": next_if_verde_km,
        },
        "targets": {
            "target_km_week": target_km,
            "days_running": days_target,
            "days_strength": strength_days
        },
        "distribution_km": dist_km,
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