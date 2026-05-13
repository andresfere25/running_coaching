"""
api/routers/athletes.py — Endpoints de consulta por atleta.

Todos los endpoints leen desde data/athletes/{cedula}/ (archivos ya generados
por el pipeline). No modifican datos ni corren cómputo pesado.

Endpoints:
  GET  /athletes                         → lista atletas disponibles
  GET  /athletes/{cedula}/profile        → perfil del atleta (Form 1)
  GET  /athletes/{cedula}/snapshot       → estado actual (última semana + semáforo)
  GET  /athletes/{cedula}/plan           → plan semanal (running + fuerza)
  GET  /athletes/{cedula}/features       → historial de features semanales
  GET  /athletes/{cedula}/prediction     → predicción de tiempo/ritmo por distancia
  GET  /athletes/{cedula}/checkin        → último check-in registrado
  POST /athletes/{cedula}/checkin        → registrar check-in desde la app
  GET  /athletes/{cedula}/training-data  → filas acumuladas para modelo Q2
"""

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

import duckdb
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.deps import get_athlete_dir, get_data_dir, require_api_key, sanitize_json
from src.storage.reader import read_snapshot, read_plan, read_features, read_checkin, read_activities, read_profile


def _build_activities_summary(
    rows: list[dict],
    period: str,
    last_n: int,
    sport_type_filter: "str | None",
) -> list[dict]:
    """
    Agrupa actividades por semana o mes y calcula métricas clave.
    Retorna lista ordenada de más reciente a más antigua (máx last_n períodos).
    """
    if not rows:
        return []

    df = pd.DataFrame(rows)

    # Filtrar por sport_type antes de agregar
    if sport_type_filter:
        mask = df["sport_type"].str.lower().str.contains(sport_type_filter.lower(), na=False)
        df = df[mask]
    if df.empty:
        return []

    # Parsear fechas (activity_date puede traer timezone info)
    df["activity_date"] = pd.to_datetime(df["activity_date"], utc=True, errors="coerce")
    df = df.dropna(subset=["activity_date"])
    df["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    df["pace_sec_per_km"] = pd.to_numeric(df["pace_sec_per_km"], errors="coerce")
    df["average_heartrate"] = pd.to_numeric(df["average_heartrate"], errors="coerce")
    df["elevation_m"] = pd.to_numeric(df["elevation_m"], errors="coerce")
    df["duration_sec"] = pd.to_numeric(df["duration_sec"], errors="coerce")

    # Normalizar a UTC naive antes de asignar período (evita warning de timezone en Period)
    dt_utc = df["activity_date"].dt.tz_convert("UTC").dt.tz_localize(None)
    if period == "week":
        df["period_start"] = dt_utc.dt.to_period("W-SUN").apply(lambda p: p.start_time)
    else:
        df["period_start"] = dt_utc.dt.to_period("M").apply(lambda p: p.start_time)

    # Agregados base
    agg = (
        df.groupby("period_start")
        .agg(
            sessions=("activity_id", "count"),
            km_total=("distance_km", "sum"),
            elevation_m_total=("elevation_m", "sum"),
            duration_sec_total=("duration_sec", "sum"),
            avg_heartrate=("average_heartrate", "mean"),
        )
        .reset_index()
    )

    # Pace promedio ponderado por km (evita distorsión de actividades cortas)
    def _weighted_pace(group: pd.DataFrame) -> "float | None":
        valid = group.dropna(subset=["pace_sec_per_km", "distance_km"])
        valid = valid[valid["distance_km"] > 0]
        if valid.empty:
            return None
        return (valid["pace_sec_per_km"] * valid["distance_km"]).sum() / valid["distance_km"].sum()

    pace_by_period = (
        df.groupby("period_start")
        .apply(_weighted_pace)
        .reset_index(name="avg_pace_sec_km")
    )
    agg = agg.merge(pace_by_period, on="period_start", how="left")

    # Ordenar descendente, tomar los últimos N períodos
    agg = agg.sort_values("period_start", ascending=False).head(last_n).reset_index(drop=True)

    # Formatear para JSON
    result = []
    for _, row in agg.iterrows():
        km = round(float(row["km_total"]), 2) if pd.notna(row["km_total"]) else 0.0
        result.append({
            "period_start":       row["period_start"].strftime("%Y-%m-%d"),
            "period":             period,
            "sessions":           int(row["sessions"]),
            "km_total":           km,
            "elevation_m_total":  round(float(row["elevation_m_total"]), 1) if pd.notna(row["elevation_m_total"]) else None,
            "duration_sec_total": int(row["duration_sec_total"]) if pd.notna(row["duration_sec_total"]) else None,
            "avg_pace_sec_km":    round(float(row["avg_pace_sec_km"]), 1) if pd.notna(row["avg_pace_sec_km"]) else None,
            "avg_heartrate":      round(float(row["avg_heartrate"]), 1) if pd.notna(row["avg_heartrate"]) else None,
        })
    return result

router = APIRouter()


# ─── Lista de atletas ────────────────────────────────────────────────────────

@router.get("")
def list_athletes(_: None = Depends(require_api_key)):
    """
    Devuelve los atletas registrados.
    Prioridad: Supabase tabla athletes → fallback data/athletes/ local.
    """
    # ── 1. Supabase first (funciona en Railway sin disco local) ───────────────
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if client:
            # Una sola query: cedula + name desde la tabla athletes
            res = (
                client.table("athletes")
                .select("cedula,name")
                .order("cedula")
                .execute()
            )
            if res.data:
                names_by_cedula: dict[str, str] = {}
                cedulas: list[str] = []
                for r in res.data:
                    ced = r.get("cedula")
                    if not ced:
                        continue
                    cedulas.append(ced)
                    raw_name = (r.get("name") or "").strip()
                    # Filtrar nombres autogenerados legacy:
                    # - "Atleta {cedula}"
                    # - solo la cédula como string ("1070985513")
                    # - vacío
                    if (
                        raw_name
                        and raw_name != str(ced)
                        and not raw_name.lower().startswith(f"atleta {ced}")
                    ):
                        names_by_cedula[str(ced)] = raw_name

                # Fallback: si no hay name en athletes, intentar athlete_profiles.raw->>name
                missing = [c for c in cedulas if str(c) not in names_by_cedula]
                if missing:
                    try:
                        prof_res = (
                            client.table("athlete_profiles")
                            .select("cedula,raw")
                            .in_("cedula", missing)
                            .execute()
                        )
                        for r in (prof_res.data or []):
                            ced = r.get("cedula")
                            raw = r.get("raw") or {}
                            nm = (raw.get("name") or "").strip() if isinstance(raw, dict) else ""
                            if (
                                ced
                                and nm
                                and nm != str(ced)
                                and not nm.lower().startswith(f"atleta {ced}")
                            ):
                                names_by_cedula[str(ced)] = nm
                    except Exception as exc:
                        print(f"[athletes] athlete_profiles fallback read error: {exc}")

                athletes_with_names = [
                    {"cedula": ced, "name": names_by_cedula.get(str(ced)) or ced}
                    for ced in cedulas
                ]
                return {"athletes": athletes_with_names, "count": len(athletes_with_names)}
    except Exception as exc:
        print(f"[athletes] Supabase list error: {exc}")

    # ── 2. Fallback local ─────────────────────────────────────────────────────
    data_dir = get_data_dir()
    if not data_dir.exists():
        return {"athletes": [], "count": 0}

    cedulas = sorted(
        d.name for d in data_dir.iterdir()
        if d.is_dir() and d.name.isdigit()
    )
    # Enriquecer con nombres desde profile.json local (best-effort)
    athletes_with_names = []
    for ced in cedulas:
        name = ced
        prof_path = data_dir / ced / "meta" / "profile.json"
        if prof_path.exists():
            try:
                prof = json.loads(prof_path.read_text(encoding="utf-8"))
                name = prof.get("name") or ced
            except Exception:
                pass
        athletes_with_names.append({"cedula": ced, "name": name})
    return {"athletes": athletes_with_names, "count": len(athletes_with_names)}


# ─── Perfil ──────────────────────────────────────────────────────────────────

@router.get("/{cedula}/profile")
def get_profile(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el perfil del atleta extraído del Form de ingreso.
    Incluye datos personales, objetivos, ritmos reportados y preferencias.
    Lee desde Supabase primero; fallback a archivo local.
    """
    athlete_dir = get_data_dir() / cedula
    data = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. Ejecuta primero: POST /athletes/{cedula}/sync",
        )
    return data


# ─── Diagnóstico de nombres de atletas ───────────────────────────────────────


def _is_legacy_name(name: "str | None", cedula: str) -> bool:
    """True si el nombre es autogenerado/inválido y no debería mostrarse."""
    import re as _re
    n = (name or "").strip()
    if not n:
        return True
    if n == str(cedula):
        return True
    if _re.match(rf"^Atleta\s+{cedula}$", n):
        return True
    return False


@router.get("/diagnostics/names")
def diagnose_names(_: None = Depends(require_api_key)):
    """
    Reporte de nombres por atleta con flag de legacy/válido.
    Útil para auditar quién necesita edición manual desde el panel.
    """
    from src.storage.supabase_client import get_client

    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    res = client.table("athletes").select("cedula,name").order("cedula").execute()
    athletes = res.data or []

    cedulas = [a["cedula"] for a in athletes if a.get("cedula")]
    profiles_by_ced: dict[str, str] = {}
    if cedulas:
        try:
            prof_res = (
                client.table("athlete_profiles")
                .select("cedula,raw")
                .in_("cedula", cedulas)
                .execute()
            )
            for r in (prof_res.data or []):
                raw = r.get("raw") or {}
                if isinstance(raw, dict):
                    profiles_by_ced[str(r.get("cedula"))] = (raw.get("name") or "").strip()
        except Exception as exc:
            print(f"[diagnose] error: {exc}")

    report = []
    needs_fix = 0
    for a in athletes:
        ced = a.get("cedula")
        portal_name = (a.get("name") or "").strip()
        form_name = profiles_by_ced.get(str(ced), "")
        portal_legacy = _is_legacy_name(portal_name, ced)
        form_legacy = _is_legacy_name(form_name, ced)
        # El "nombre efectivo" prioriza form > portal
        effective = form_name if not form_legacy else (portal_name if not portal_legacy else None)
        is_ok = effective is not None
        if not is_ok:
            needs_fix += 1
        report.append({
            "cedula":          ced,
            "athletes_name":   portal_name or None,
            "form_name":       form_name or None,
            "effective_name":  effective,
            "needs_fix":       not is_ok,
            "source":          "form" if (effective and effective == form_name) else ("portal" if effective else None),
        })

    return {
        "total":     len(report),
        "needs_fix": needs_fix,
        "ok":        len(report) - needs_fix,
        "report":    report,
    }


@router.post("/diagnostics/cleanup-names")
def cleanup_names(_: None = Depends(require_api_key)):
    """
    Pasa por todos los atletas y NORMALIZA los nombres legacy:
    - Si el form tiene nombre real → ese gana (también baja a athletes.name).
    - Si solo athletes.name tiene nombre real → ese se preserva.
    - Si Supabase no tiene name pero D1 (Panel Admin) sí → propagar de D1.
    - Si ambos son legacy → no toca nada (queda pendiente de edición coach).

    NO modifica nombres que ya son válidos. Idempotente: corre cuantas veces quieras.
    """
    from src.storage.supabase_client import get_client

    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Pre-fetch nombres en D1 para auto-corrección desde el Panel Admin
    d1_names_by_cedula: dict[str, str] = {}
    worker_url = os.getenv("WORKER_URL", "https://app.arathleteslab.com").rstrip("/")
    shared_secret = os.getenv("INTERNAL_SHARED_SECRET", "").strip()
    if shared_secret:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{worker_url}/api/internal/athletes/list-cedulas",
                headers={
                    "X-Internal-Secret": shared_secret,
                    "User-Agent": "running-coaching-backend/1.0 (+https://runningcoaching-production.up.railway.app)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                d1_data = json.loads(resp.read().decode())
            for a in d1_data.get("athletes", []):
                ced = str(a.get("external_athlete_id") or "")
                nm = (a.get("name") or "").strip()
                if ced and nm and not _is_legacy_name(nm, ced):
                    d1_names_by_cedula[ced] = nm
        except Exception as exc:
            print(f"[cleanup-names] D1 fetch failed: {exc}")

    res = client.table("athletes").select("cedula,name").execute()
    athletes = res.data or []

    fixed = []
    pending = []

    for a in athletes:
        ced = a.get("cedula")
        if not ced:
            continue
        portal_name = (a.get("name") or "").strip()

        # Buscar form_name desde athlete_profiles
        form_name = ""
        try:
            pr = (
                client.table("athlete_profiles")
                .select("raw")
                .eq("cedula", ced)
                .limit(1)
                .execute()
            )
            if pr.data and pr.data[0].get("raw") and isinstance(pr.data[0]["raw"], dict):
                form_name = (pr.data[0]["raw"].get("name") or "").strip()
        except Exception:
            pass

        portal_legacy = _is_legacy_name(portal_name, ced)
        form_legacy = _is_legacy_name(form_name, ced)
        d1_name = d1_names_by_cedula.get(str(ced), "")

        # Decisión: prioridad form > portal Supabase > D1 (Panel Admin) > pendiente
        if not form_legacy:
            real_name = form_name
            source = "form"
        elif not portal_legacy:
            real_name = portal_name
            source = "portal"
        elif d1_name:
            real_name = d1_name
            source = "d1"
        else:
            pending.append({"cedula": ced, "reason": "no real name in form, portal, or D1"})
            continue

        # Sincronizar a TODOS los lugares
        actions = []
        if portal_name != real_name:
            try:
                client.table("athletes").update({"name": real_name}).eq("cedula", ced).execute()
                actions.append("athletes.name")
            except Exception as exc:
                actions.append(f"athletes.name FAILED: {exc}")

        # Si el form tiene legacy pero hay un name real (portal o D1) → propagarlo
        # también al raw del profile (que es lo que lee el snapshot generation).
        if form_legacy and source in ("portal", "d1"):
            try:
                pr2 = client.table("athlete_profiles").select("raw").eq("cedula", ced).limit(1).execute()
                if pr2.data:
                    raw = pr2.data[0].get("raw") or {}
                    if isinstance(raw, dict):
                        raw["name"] = real_name
                        client.table("athlete_profiles").update({"raw": raw}).eq("cedula", ced).execute()
                        actions.append("athlete_profiles.raw.name")
            except Exception as exc:
                actions.append(f"athlete_profiles FAILED: {exc}")

        # Actualizar snapshot también
        try:
            sr = client.table("athlete_snapshots").select("raw").eq("cedula", ced).limit(1).execute()
            if sr.data and sr.data[0].get("raw") and isinstance(sr.data[0]["raw"], dict):
                snap = sr.data[0]["raw"]
                prof = snap.get("profile") or {}
                if prof.get("name") != real_name:
                    prof["name"] = real_name
                    snap["profile"] = prof
                    client.table("athlete_snapshots").upsert(
                        {"cedula": ced, "raw": snap}, on_conflict="cedula"
                    ).execute()
                    actions.append("athlete_snapshots.raw.profile.name")
        except Exception as exc:
            actions.append(f"snapshot FAILED: {exc}")

        if actions:
            fixed.append({"cedula": ced, "name": real_name, "source": source, "actions": actions})

    return {
        "fixed_count":   len(fixed),
        "pending_count": len(pending),
        "fixed":         fixed,
        "pending":       pending,
    }


# ─── Actualizar perfil (nombre del atleta) ───────────────────────────────────


class AthleteNameUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.patch("/{cedula}/profile/name")
def update_athlete_name(
    cedula: str,
    body: AthleteNameUpdate,
    _: None = Depends(require_api_key),
):
    """
    Actualiza el nombre del atleta. Usado por el coach desde el panel cuando el
    formulario o el portal no trajo nombre y quedó como "Atleta {cedula}" o vacío.

    Persiste en TODOS los lugares de lectura del dashboard, en una sola operación:
      - profile.json local
      - Supabase athlete_profiles.raw.name
      - Supabase athletes.name
      - Supabase athlete_snapshots.raw.profile.name  (← crítico: el dashboard lee de aquí)
      - features/athlete_snapshot.json local
    """
    from src.storage.supabase_client import get_client
    from src.storage.writer import push_profile

    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")

    athlete_dir = get_data_dir() / cedula
    data = read_profile(cedula, athlete_dir if athlete_dir.exists() else None) or {}
    data["name"] = new_name
    data.setdefault("cedula", cedula)

    # 1. Guardar profile.json localmente
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    profile_path = meta_dir / "profile.json"
    profile_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2. Push a Supabase athlete_profiles (raw + columna name)
    push_result = push_profile(cedula, athlete_dir)

    # 3. Actualizar tabla athletes (list_athletes lo lee desde aquí)
    client = get_client()
    updates_done = ["profile.json"]
    if push_result.get("ok"):
        updates_done.append("athlete_profiles")

    if client:
        try:
            client.table("athletes").upsert(
                {"cedula": cedula, "name": new_name}, on_conflict="cedula"
            ).execute()
            updates_done.append("athletes")
        except Exception as exc:
            print(f"[athletes] update name on athletes table failed: {exc}")

        # 4. CRÍTICO: actualizar athlete_snapshots.raw.profile.name
        # El dashboard del atleta lee desde aquí — sin esto, el cambio no se ve
        # hasta que corra el pipeline.
        try:
            snap_res = (
                client.table("athlete_snapshots")
                .select("raw")
                .eq("cedula", cedula)
                .limit(1)
                .execute()
            )
            if snap_res.data and snap_res.data[0].get("raw"):
                snap_raw = snap_res.data[0]["raw"]
                if isinstance(snap_raw, dict):
                    prof = snap_raw.get("profile") or {}
                    prof["name"] = new_name
                    snap_raw["profile"] = prof
                    client.table("athlete_snapshots").upsert(
                        {"cedula": cedula, "raw": snap_raw}, on_conflict="cedula"
                    ).execute()
                    updates_done.append("athlete_snapshots")
        except Exception as exc:
            print(f"[athletes] update name on athlete_snapshots failed: {exc}")

    # 5. Actualizar snapshot local también (si existe)
    local_snap_path = athlete_dir / "features" / "athlete_snapshot.json"
    if local_snap_path.exists():
        try:
            snap_local = json.loads(local_snap_path.read_text(encoding="utf-8"))
            if isinstance(snap_local, dict):
                prof = snap_local.get("profile") or {}
                prof["name"] = new_name
                snap_local["profile"] = prof
                local_snap_path.write_text(
                    json.dumps(snap_local, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                updates_done.append("athlete_snapshot.json")
        except Exception as exc:
            print(f"[athletes] update local snapshot failed: {exc}")

    return {
        "ok": True,
        "cedula": cedula,
        "name": new_name,
        "updated": updates_done,
        "supabase": push_result.get("detail"),
    }


# ─── Actualizar perfil (carrera objetivo) ────────────────────────────────────


class RaceGoalUpdate(BaseModel):
    race_name: Optional[str] = None
    race_distance: Optional[str] = None
    race_date: Optional[str] = None  # YYYY-MM-DD


@router.patch("/{cedula}/profile/race-goal")
def update_race_goal(
    cedula: str,
    body: RaceGoalUpdate,
    _: None = Depends(require_api_key),
):
    """
    Actualiza la carrera objetivo del atleta en el perfil.
    Modifica: race_name, race_distance, race_date_raw en profile.json y Supabase.
    """
    from src.storage.supabase_client import get_client
    from src.storage.writer import push_profile

    # 1. Leer perfil actual
    athlete_dir = get_data_dir() / cedula
    data = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")

    # 2. Actualizar campos
    changed = []
    if body.race_name is not None:
        data["race_name"] = body.race_name.strip() or None
        changed.append("race_name")
    if body.race_distance is not None:
        data["race_distance"] = body.race_distance.strip() or None
        changed.append("race_distance")
    if body.race_date is not None:
        data["race_date_raw"] = body.race_date.strip() or None
        changed.append("race_date_raw")

    if not changed:
        return {"ok": True, "changed": [], "detail": "Sin cambios"}

    # 3. Guardar localmente
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    profile_path = meta_dir / "profile.json"
    import json as _json
    profile_path.write_text(
        _json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Push a Supabase
    push_result = push_profile(cedula, athlete_dir)

    return {
        "ok": True,
        "changed": changed,
        "race_name": data.get("race_name"),
        "race_distance": data.get("race_distance"),
        "race_date_raw": data.get("race_date_raw"),
        "supabase": push_result.get("detail"),
    }


# ─── Onboarding completo ─────────────────────────────────────────────────────


class OnboardingProfile(BaseModel):
    name: str
    cedula: str
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    city_country: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[int] = None
    weight_kg: Optional[float] = None
    birth_date_raw: Optional[str] = None
    goal_main: Optional[str] = None
    race_distance: Optional[str] = None
    race_date_raw: Optional[str] = None
    race_name: Optional[str] = None
    has_time_goal: Optional[bool] = None
    time_goal_sec: Optional[int] = None
    days_run_per_week: Optional[int] = None
    weekday_session_min_min: Optional[int] = None
    weekday_session_max_min: Optional[int] = None
    weekend_session_min_min: Optional[int] = None
    weekend_session_max_min: Optional[int] = None
    preferred_run_days: Optional[list[str]] = None
    training_time_pref: Optional[str] = None
    strength_access: Optional[str] = None
    strength_days_per_week: Optional[int] = None
    other_sports: Optional[str] = None
    sleep_hours_raw: Optional[str] = None
    has_pain: Optional[bool] = None
    pain_location: Optional[str] = None
    pain_level_0_10: Optional[int] = None
    had_injuries_12m: Optional[bool] = None
    medical_conditions: Optional[str] = None
    running_experience: Optional[str] = None
    km_week_min: Optional[float] = None
    km_week_max: Optional[float] = None
    avg_days_running_4w: Optional[int] = None
    long_run_recent: Optional[str] = None
    surface: Optional[str] = None
    has_recent_prs: Optional[bool] = None
    pr_5k_sec: Optional[float] = None
    pr_10k_sec: Optional[float] = None
    pr_21k_sec: Optional[float] = None
    pr_42k_sec: Optional[float] = None
    easy_pace_sec_per_km: Optional[float] = None
    mod_pace_sec_per_km: Optional[float] = None
    fast_pace_sec_per_km: Optional[float] = None
    uses_device: Optional[bool] = None
    main_app: Optional[str] = None
    uses_strava: Optional[bool] = None
    consent_plan: Optional[bool] = None
    consent_anon: Optional[bool] = None
    consent_strava: Optional[bool] = None


@router.post("/{cedula}/profile/onboarding")
def create_onboarding_profile(
    cedula: str,
    body: OnboardingProfile,
    _: None = Depends(require_api_key),
):
    """
    Recibe el perfil completo de onboarding y lo persiste.
    Merge: los campos enviados como None no sobreescriben valores existentes.
    Escribe en profile.json local y sincroniza a Supabase.

    Garantía FK: hace upsert en athletes (tabla maestra) ANTES de
    insertar en athlete_profiles para evitar violación de FK constraint.
    """
    from src.storage.writer import push_profile, push_athlete

    if body.cedula != cedula:
        raise HTTPException(
            status_code=400,
            detail="La cédula en el body no coincide con la URL",
        )

    # 0. Garantizar fila en tabla athletes (FK parent de athlete_profiles)
    #    Upsert idempotente — si ya existe, solo actualiza el nombre.
    push_athlete(cedula, body.name)

    # 1. Leer perfil existente (si hay)
    athlete_dir = get_data_dir() / cedula
    existing: dict = {}
    profile_path = athlete_dir / "meta" / "profile.json"
    if profile_path.exists():
        try:
            existing = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    else:
        # Try Supabase
        data_from_reader = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
        if data_from_reader:
            existing = data_from_reader

    # 2. Merge: new values override, but None values don't overwrite existing
    incoming = body.model_dump(exclude_none=True)
    merged = {**existing, **incoming}

    # 3. Write locally
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Push to Supabase
    push_result = push_profile(cedula, athlete_dir)

    return {
        "ok": True,
        "cedula": cedula,
        "supabase": push_result.get("detail"),
    }


# ─── Snapshot (estado actual) ────────────────────────────────────────────────

@router.get("/{cedula}/snapshot")
def get_snapshot(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el snapshot del atleta: última semana de features + semáforo.
    Es el dato principal para el dashboard del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Campos clave en la respuesta:
    - semaforo_latest_checkin: VERDE / AMARILLO / ROJO / SIN_CHECKIN
    - latest_week: km, sessions, ACWR, monotonía, zonas de la última semana
    - profile: datos del atleta (nombre, objetivo, ritmos)
    - generated_at: timestamp del último pipeline ejecutado
    """
    athlete_dir = get_data_dir() / cedula
    data = read_snapshot(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot no disponible. Ejecuta primero: POST /athletes/{cedula}/pipeline",
        )
    return sanitize_json(data)


# ─── Plan semanal ────────────────────────────────────────────────────────────

@router.get("/{cedula}/plan")
def get_plan(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el plan semanal (running + fuerza) del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Campos clave:
    - week_type: DESCARGA / CONSERVADORA / PROGRESO
    - semaforo: estado del check-in que determinó el week_type
    - targets: km objetivo, días de running, días de fuerza
    - plan_by_day: sesiones por día (Lunes a Domingo)
    - notes: recomendaciones adicionales
    """
    athlete_dir = get_data_dir() / cedula
    data = read_plan(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan no disponible. Ejecuta primero: POST /athletes/{cedula}/pipeline",
        )
    return data


# ─── Historial de features ───────────────────────────────────────────────────

@router.get("/{cedula}/features")
def get_features(
    cedula: str,
    weeks: int = Query(default=12, ge=1, le=104, description="Últimas N semanas de historial"),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el historial semanal de features del atleta.
    Usado para gráficos de evolución (km, ritmo, ACWR, zonas).

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.

    Parámetros:
    - weeks: cuántas semanas incluir (default 12, max 104)
    """
    athlete_dir = get_data_dir() / cedula
    rows = read_features(cedula, athlete_dir if athlete_dir.exists() else None, weeks)
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail="Features no disponibles. Ejecuta primero el pipeline.",
        )
    return sanitize_json({
        "cedula": cedula,
        "weeks_returned": len(rows),
        "weeks_requested": weeks,
        "data": rows,
    })


# ─── Último check-in ─────────────────────────────────────────────────────────

@router.get("/{cedula}/checkin")
def get_latest_checkin(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Devuelve el último check-in registrado del atleta.
    Incluye el flag is_recent (True si tiene menos de 10 días).

    Lee desde Supabase primero; si no hay datos, fallback a archivo local.
    """
    athlete_dir = get_data_dir() / cedula
    data = read_checkin(cedula, athlete_dir if athlete_dir.exists() else None)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Check-in no disponible. El paso 'ingest' del pipeline no ha corrido.",
        )
    return data


# ─── Registrar check-in desde la app ─────────────────────────────────────────

class WeeklyCheckinIn(BaseModel):
    type: Literal["weekly"] = "weekly"
    sleep_1_5: int = Field(..., ge=1, le=5)
    energy_1_5: int = Field(..., ge=1, le=5)
    has_pain: bool = False
    pain_location: Optional[str] = None
    checkin_date: Optional[date] = None  # default: hoy


class RaceCheckinIn(BaseModel):
    type: Literal["race"] = "race"
    race_distance_km: float = Field(..., gt=0)
    race_time_sec: int = Field(..., gt=0)
    sensation_1_5: int = Field(..., ge=1, le=5)
    is_official: bool = False
    race_date: Optional[date] = None  # default: hoy
    avg_heartrate: Optional[float] = Field(None, ge=40, le=250, description="FC promedio (bpm)")
    max_heartrate: Optional[float] = Field(None, ge=40, le=250, description="FC máxima (bpm)")


@router.post("/{cedula}/checkin")
def post_checkin(
    cedula: str,
    body: WeeklyCheckinIn | RaceCheckinIn,
    _: None = Depends(require_api_key),
):
    """
    Registra un check-in enviado desde la app (semanal o post-carrera).

    - type=weekly   : sueño, energía, dolor/lesión
    - type=race     : resultado de carrera + sensación

    Escribe en Supabase (checkins table) y en latest_checkin.json local.
    Si Supabase no está disponible, solo escribe localmente.
    """
    from src.storage.supabase_client import get_client

    today = date.today().isoformat()

    if body.type == "weekly":
        checkin_date = body.checkin_date.isoformat() if body.checkin_date else today
        raw = {
            "cedula": cedula,
            "source": "app_weekly",
            "checkin_date": checkin_date,
            "checkin_week_start": checkin_date,
            "timestamp": datetime.utcnow().isoformat(),
            "is_recent": True,
            # Mapeo a campos existentes para compatibilidad con el dashboard
            "feeling_1_10": body.energy_1_5 * 2,       # 1-5 → 2-10
            "fatigue_1_10": (6 - body.energy_1_5) * 2, # inverso
            "sleep_1_5": body.sleep_1_5,
            "energy_1_5": body.energy_1_5,
            "pain_0_10": 5 if body.has_pain else 0,
            "has_pain": body.has_pain,
            "pain_location": body.pain_location or "",
            "pain_where": body.pain_location or "",  # compat legacy
            "sessions_completed": None,
            "skipped_sessions": None,
            "comments": "",
        }
    else:  # race
        race_date = body.race_date.isoformat() if body.race_date else today
        raw = {
            "cedula": cedula,
            "source": "app_race",
            "checkin_date": race_date,
            "checkin_week_start": race_date,
            "timestamp": datetime.utcnow().isoformat(),
            "is_recent": True,
            "race_distance_km": body.race_distance_km,
            "race_time_sec": body.race_time_sec,
            "sensation_1_5": body.sensation_1_5,
            "is_official": body.is_official,
            "race_date": race_date,
            "avg_heartrate": body.avg_heartrate,
            "max_heartrate": body.max_heartrate,
            # Compatibilidad con dashboard
            "feeling_1_10": body.sensation_1_5 * 2,
            "fatigue_1_10": None,
            "pain_0_10": None,
            "sessions_completed": None,
            "skipped_sessions": None,
            "comments": "",
        }

    # 1. Escribir en Supabase
    supabase_ok = False
    supabase_error = None
    client = get_client()
    if client:
        try:
            # Ensure athlete row exists (FK constraint on checkins)
            client.table("athletes").upsert(
                {"cedula": cedula}, on_conflict="cedula"
            ).execute()
            client.table("checkins").upsert({
                "cedula": cedula,
                "checkin_date": raw["checkin_date"],
                "raw": raw,
            }, on_conflict="cedula,checkin_date").execute()
            supabase_ok = True
        except Exception as exc:
            supabase_error = str(exc)

    # 2. Escribir localmente
    athlete_dir = get_data_dir() / cedula
    meta_dir = athlete_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    local_path = meta_dir / "latest_checkin.json"
    payload = {"cedula": cedula, "is_recent": True, "latest_checkin": raw}
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # 3. Si es carrera, guardar snapshot de features para dataset Q2
    snapshot_saved = False
    if body.type == "race":
        snapshot_saved = _save_training_snapshot(
            cedula=cedula,
            race_date=raw["checkin_date"],
            race_distance_km=body.race_distance_km,
            race_time_sec=body.race_time_sec,
            sensation_1_5=body.sensation_1_5,
            is_official=body.is_official,
            avg_heartrate=body.avg_heartrate,
            max_heartrate=body.max_heartrate,
        )

    result = {
        "status": "ok",
        "type": body.type,
        "checkin_date": raw["checkin_date"],
        "supabase": supabase_ok,
        "local": True,
    }
    if supabase_error:
        result["supabase_error"] = supabase_error
    if body.type == "race":
        result["training_snapshot_saved"] = snapshot_saved
    return result


def _save_training_snapshot(
    cedula: str,
    race_date: str,
    race_distance_km: float,
    race_time_sec: int,
    sensation_1_5: int,
    is_official: bool,
    avg_heartrate: Optional[float] = None,
    max_heartrate: Optional[float] = None,
) -> bool:
    """
    Guarda una fila de entrenamiento para el modelo Q2.

    Combina:
      - Resultado (distancia, tiempo, sensación, FC)
      - Features de carga de la semana previa (CTL, ATL, TSB, ACWR, km_week, etc.)
      - Perfil del atleta (edad, género)
      - Último check-in semanal disponible (sueño, energía)

    FC (avg_heartrate, max_heartrate) disponible cuando viene de Strava o check-in manual.
    Permite calcular eficiencia aeróbica (pace/FC) en NB09.

    Escribe en:
      - Local:    data/{cedula}/training_data/q2_rows.jsonl  (una fila JSON por línea)
      - Supabase: tabla training_snapshots (si está disponible)

    Retorna True si se guardó correctamente.
    """
    try:
        athlete_dir = get_data_dir() / cedula

        # ── 1. Features de la semana previa a la carrera ──────────────────────
        load_features: dict = {}
        features_path = athlete_dir / "features" / "weekly_features.parquet"
        if features_path.exists():
            df = pd.read_parquet(features_path)
            if not df.empty and "week_start" in df.columns:
                df["week_start"] = pd.to_datetime(df["week_start"])
                race_dt = pd.to_datetime(race_date)
                # Tomar la semana más reciente antes o igual a la fecha de carrera
                prev = df[df["week_start"] <= race_dt].sort_values("week_start")
                if not prev.empty:
                    row = prev.iloc[-1]
                    keep_cols = [
                        "km_week", "sessions", "long_run_km",
                        "ctl", "atl", "tsb", "acwr",
                        "pace_delta_4s_sec", "racha_semanas", "km_trend",
                        "fondo_largo_4s", "semana_spike",
                    ]
                    for col in keep_cols:
                        if col in row.index:
                            val = row[col]
                            load_features[col] = None if pd.isna(val) else float(val)

        # ── 2. Perfil del atleta (edad, género, PRs) ──────────────────────────
        profile_features: dict = {}
        profile_path = athlete_dir / "meta" / "profile.json"
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_features["age"] = profile.get("age")
            profile_features["gender"] = profile.get("gender")
            profile_features["pr_5k_sec"]  = profile.get("pr_5k_sec")
            profile_features["pr_10k_sec"] = profile.get("pr_10k_sec")
            profile_features["pr_21k_sec"] = profile.get("pr_21k_sec")
            profile_features["pr_42k_sec"] = profile.get("pr_42k_sec")

        # ── 3. Último check-in semanal disponible (sueño, energía) ───────────
        subjective_features: dict = {}
        latest_checkin_path = athlete_dir / "meta" / "latest_checkin.json"
        if latest_checkin_path.exists():
            lc = json.loads(latest_checkin_path.read_text(encoding="utf-8"))
            lc_data = lc.get("latest_checkin", {})
            if lc_data.get("source") == "app_weekly":
                subjective_features["sleep_1_5"]  = lc_data.get("sleep_1_5")
                subjective_features["energy_1_5"] = lc_data.get("energy_1_5")
                subjective_features["has_pain"]   = lc_data.get("has_pain")

        # ── 4. Target y metadata ──────────────────────────────────────────────
        pace_sec_km = race_time_sec / race_distance_km if race_distance_km > 0 else None
        dist_bucket = (
            "5K"  if race_distance_km <= 6 else
            "10K" if race_distance_km <= 12 else
            "21K" if race_distance_km <= 23 else
            "42K"
        )

        # Eficiencia aeróbica: pace / FC (útil para Q2)
        # Menor valor = más eficiente (menor ritmo por latido)
        aerobic_efficiency = None
        if avg_heartrate and avg_heartrate > 0 and pace_sec_km:
            aerobic_efficiency = round(pace_sec_km / avg_heartrate, 4)

        row_data = {
            # Identificadores
            "cedula":            cedula,
            "race_date":         race_date,
            "recorded_at":       datetime.utcnow().isoformat(),
            # Target
            "race_distance_km":  race_distance_km,
            "race_time_sec":     race_time_sec,
            "pace_sec_km":       round(pace_sec_km, 2) if pace_sec_km else None,
            "dist_bucket":       dist_bucket,
            # Sensación subjetiva post-carrera
            "sensation_1_5":     sensation_1_5,
            "is_official":       is_official,
            # FC — disponible desde Strava o check-in manual con monitor
            "avg_heartrate":     avg_heartrate,
            "max_heartrate":     max_heartrate,
            "aerobic_efficiency": aerobic_efficiency,  # pace_sec_km / avg_hr
            # Features de carga (semana previa)
            **load_features,
            # Perfil
            **profile_features,
            # Subjetivo semanal
            **subjective_features,
        }

        # ── 5. Escribir localmente en JSONL ───────────────────────────────────
        td_dir = athlete_dir / "training_data"
        td_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = td_dir / "q2_rows.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row_data, ensure_ascii=False, default=str) + "\n")

        # ── 6. Escribir en Supabase ───────────────────────────────────────────
        from src.storage.supabase_client import get_client
        client = get_client()
        if client:
            try:
                client.table("training_snapshots").upsert(
                    {"cedula": cedula, "race_date": race_date, "data": row_data},
                    on_conflict="cedula,race_date",
                ).execute()
            except Exception:
                pass  # local ya guardado, Supabase es best-effort

        return True

    except Exception:
        return False  # silencioso — no rompemos el check-in por esto


# ─── Historial de check-ins ─────────────────────────────────────────────────

@router.get("/{cedula}/checkin-history")
def get_checkin_history(
    cedula: str,
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_api_key),
):
    """
    Retorna los últimos N check-ins del atleta (semanales + carreras),
    ordenados por fecha descendente. Lee de Supabase.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        return {"cedula": cedula, "checkins": [], "error": "Supabase no disponible"}

    try:
        resp = (
            sb.table("checkins")
            .select("checkin_date, raw")
            .eq("cedula", cedula)
            .order("checkin_date", desc=True)
            .limit(limit)
            .execute()
        )
        rows = []
        for r in resp.data or []:
            raw = r.get("raw") or {}
            rows.append({
                "checkin_date": r["checkin_date"],
                "type": raw.get("source", "unknown"),
                "sleep_1_5": raw.get("sleep_1_5"),
                "energy_1_5": raw.get("energy_1_5"),
                "has_pain": raw.get("has_pain"),
                "pain_location": raw.get("pain_location"),
                "feeling_1_10": raw.get("feeling_1_10"),
                "fatigue_1_10": raw.get("fatigue_1_10"),
                "pain_0_10": raw.get("pain_0_10"),
                # Race fields
                "race_distance_km": raw.get("race_distance_km"),
                "race_time_sec": raw.get("race_time_sec"),
                "sensation_1_5": raw.get("sensation_1_5"),
                "is_official": raw.get("is_official"),
            })
        return {"cedula": cedula, "count": len(rows), "checkins": rows}
    except Exception as e:
        return {"cedula": cedula, "checkins": [], "error": str(e)}


# ─── Dataset Q2: leer filas acumuladas ────────────────────────────────────────

@router.get("/{cedula}/training-data")
def get_training_data(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Retorna todas las filas del dataset Q2 acumuladas para este atleta.
    Cada fila = una carrera reportada + features de carga de esa semana.
    Útil para NB09 (personalización walk-forward).
    """
    athlete_dir = get_data_dir() / cedula
    jsonl_path = athlete_dir / "training_data" / "q2_rows.jsonl"

    if not jsonl_path.exists():
        return {"cedula": cedula, "n_rows": 0, "rows": []}

    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    return {
        "cedula":  cedula,
        "n_rows":  len(rows),
        "rows":    rows,
    }


# ─── Borrar datos de entrenamiento (mantiene perfil/registro) ────────────────

@router.delete("/{cedula}/data")
def delete_athlete_data(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Borra todos los datos de entrenamiento del atleta.
    Mantiene intactos: athletes (tokens Strava) y athlete_profiles (formulario).

    Elimina:
      - activities          → historial Strava
      - weekly_features     → CTL/ATL/TSB/ACWR calculado
      - athlete_snapshots   → estado actual del dashboard
      - weekly_plans        → planes semanales generados
      - training_snapshots  → datos históricos para modelo ML
      - checkins            → check-ins semanales
      - coach_content       → contenido publicado por el coach

    También:
      - Resetea strava_last_sync_at → próximo sync re-descarga historial completo
      - Borra archivos locales (parquet) si existen en el disco de Railway
    """
    import shutil
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        return {"cedula": cedula, "error": "Supabase no disponible"}

    TABLES = [
        "activities",
        "weekly_features",
        "athlete_snapshots",
        "weekly_plans",
        "training_snapshots",
        "checkins",
        "coach_content",
    ]

    deleted: dict = {}
    errors:  dict = {}

    for table in TABLES:
        try:
            result = sb.table(table).delete().eq("cedula", cedula).execute()
            deleted[table] = len(result.data or [])
        except Exception as e:
            errors[table] = str(e)
            print(f"[delete_data] ERROR en tabla {table} para cedula={cedula}: {e}")

    # Resetear last_sync_at para que próximo sync sea historial completo
    try:
        sb.table("athletes").update({"strava_last_sync_at": None}).eq("cedula", cedula).execute()
        deleted["strava_last_sync_at"] = "reset"
    except Exception as e:
        errors["reset_sync"] = str(e)

    # Borrar archivos locales si existen (Railway ephemeral disk)
    athlete_dir = Path(os.getenv("DATA_DIR", "data/athletes")) / cedula
    if athlete_dir.exists():
        shutil.rmtree(athlete_dir, ignore_errors=True)
        deleted["local_dir"] = str(athlete_dir)

    return {
        "status":  "deleted",
        "cedula":  cedula,
        "deleted": deleted,
        "errors":  errors,
    }


# ─── Stats bulk (panel del coach — carga toda la cohorte en ~8 queries) ────────

@router.get("/stats/bulk")
def bulk_stats(_: None = Depends(require_api_key)):
    """
    Retorna estadísticas de participación para TODOS los atletas en ~8 queries de Supabase.
    Reemplaza el patron anterior: 91 atletas × 10 queries/atleta = 910 queries → ~20s.
    Con este endpoint: 8 queries de agregación en Python → ~2s.

    Responde: dict keyed by cedula → mismo schema que /{cedula}/stats.
    """
    from collections import defaultdict
    from datetime import date as _date, timedelta as _td
    from src.storage.supabase_client import get_client

    # Ventana rodante 90 días — "activo en el programa" = corrió en los últimos 3 meses.
    # Era hardcodeado ("2026-02-01") lo que hacía el KPI obsoleto con el tiempo.
    PROJECT_START = (_date.today() - _td(days=90)).isoformat()
    sb = get_client()
    if not sb:
        raise HTTPException(503, "Supabase no disponible")

    # 1. Todos los cedulas registrados
    all_ceds = [r["cedula"] for r in (sb.table("athletes").select("cedula").execute().data or [])]
    if not all_ceds:
        return {}

    # LÍMITE EXPLÍCITO en todas las queries que bajan filas.
    # supabase-py usa PostgREST cuyo default es 1000 filas. Con 91 atletas × ~200 acts
    # = ~18K filas, sin límite explícito se trunca y weeks_span / hr_count quedan mal.
    _BIG = 200_000  # suficiente para cualquier cohorte razonable

    # 2. Runs desde PROJECT_START (para strava_runs + last_activity)
    runs_res = (
        sb.table("activities")
        .select("cedula,activity_date")
        .in_("sport_type", ["Run", "TrailRun"])
        .gte("activity_date", PROJECT_START)
        .limit(_BIG)
        .execute()
    ).data or []

    runs_count: dict = defaultdict(int)
    last_act: dict   = {}
    for row in runs_res:
        ced = row["cedula"]
        runs_count[ced] += 1
        d = row["activity_date"][:10]
        if ced not in last_act or d > last_act[ced]:
            last_act[ced] = d

    # 3. TODAS las fechas de runs (para weeks_span — sin filtro PROJECT_START)
    all_runs_dates = (
        sb.table("activities")
        .select("cedula,activity_date")
        .in_("sport_type", ["Run", "TrailRun"])
        .limit(_BIG)
        .execute()
    ).data or []

    first_run: dict = {}
    last_run_all: dict = {}
    for row in all_runs_dates:
        ced = row["cedula"]
        d = row["activity_date"][:10]
        if ced not in first_run or d < first_run[ced]:
            first_run[ced] = d
        if ced not in last_run_all or d > last_run_all[ced]:
            last_run_all[ced] = d

    # 4. Runs con HR (solo cedula — filtro JSONB server-side)
    hr_runs = (
        sb.table("activities")
        .select("cedula")
        .in_("sport_type", ["Run", "TrailRun"])
        .not_.is_("raw->>average_heartrate", "null")
        .limit(_BIG)
        .execute()
    ).data or []
    hr_count: dict = defaultdict(int)
    for row in hr_runs:
        hr_count[row["cedula"]] += 1

    # 5. Perfiles, snapshots y planes — solo existencia (filas pequeñas, no necesitan _BIG)
    profiles_set  = {r["cedula"] for r in (sb.table("athlete_profiles") .select("cedula").limit(5000).execute().data or [])}
    snapshots_set = {r["cedula"] for r in (sb.table("athlete_snapshots").select("cedula").limit(5000).execute().data or [])}
    plans_set     = {r["cedula"] for r in (sb.table("weekly_plans")     .select("cedula").limit(5000).execute().data or [])}

    # 6. Check-ins (count + última fecha)
    checkins_res = (
        sb.table("checkins")
        .select("cedula,checkin_date")
        .limit(50_000)
        .execute()
    ).data or []
    checkins_count: dict = defaultdict(int)
    last_checkin: dict   = {}
    for row in checkins_res:
        ced = row["cedula"]
        checkins_count[ced] += 1
        d = row["checkin_date"]
        if ced not in last_checkin or d > last_checkin[ced]:
            last_checkin[ced] = d

    # 7. Training snapshots — total + manual (source=app_race)
    ml_all = (
        sb.table("training_snapshots")
        .select("cedula")
        .limit(50_000)
        .execute()
    ).data or []
    ml_manual_rows = (
        sb.table("training_snapshots")
        .select("cedula")
        .filter("data->>source", "eq", "app_race")
        .limit(50_000)
        .execute()
    ).data or []
    ml_count: dict  = defaultdict(int)
    ml_manual: dict = defaultdict(int)
    for row in ml_all:
        ml_count[row["cedula"]] += 1
    for row in ml_manual_rows:
        ml_manual[row["cedula"]] += 1

    # ── Construir respuesta ───────────────────────────────────────────────────
    result = {}
    for ced in all_ceds:
        runs      = runs_count[ced]
        hr        = hr_count[ced]
        ml        = ml_count[ced]
        ml_m      = ml_manual[ced]
        has_prof  = ced in profiles_set
        has_snap  = ced in snapshots_set
        has_plan  = ced in plans_set

        # weeks_span: días entre primera y última carrera all-time
        weeks_span = 0
        if ced in first_run and ced in last_run_all:
            d0 = _date.fromisoformat(first_run[ced])
            d1 = _date.fromisoformat(last_run_all[ced])
            weeks_span = max(1, (d1 - d0).days // 7)

        # last_activity: más reciente de runs since PROJECT_START o all-time
        last_activity = last_act.get(ced) or last_run_all.get(ced)

        # N2 eligibility: ≥5 runs con HR + ≥4 semanas de historial.
        # Criterio relajado (antes ≥10/≥8) para capturar toda la cohorte útil.
        # Nota: la validación final del modelo usa LOAO-CV → el umbral aquí es
        # solo para decidir qué atletas entran al entrenamiento, no para garantizar
        # suficientes datos de entrenamiento (eso lo controla el propio fold de CV).
        features = {
            "prediccion_n1":    has_prof,
            "dashboard_activo": has_snap,
            "plan_semanal":     has_plan,
            "carga_training":   runs > 0,
            "zonas_hr":         hr >= 5,
            "elegible_n2":      weeks_span >= 4 and hr >= 5,
        }

        result[ced] = {
            "ml_snapshots":  ml,
            "ml_manual":     ml_m,
            "ml_strava":     ml - ml_m,
            "checkins_total": checkins_count[ced],
            "last_checkin":  last_checkin.get(ced),
            "strava_runs":   runs,
            "strava_pending": 0,
            "last_activity": last_activity,
            "runs_with_hr":  hr,
            "weeks_span":    weeks_span,
            "has_profile":   has_prof,
            "has_snapshot":  has_snap,
            "has_plan":      has_plan,
            "features":      features,
            "n_rows":        ml,
        }

    return result


# ─── Stats resumen por atleta (para panel del coach) ─────────────────────────

@router.get("/{cedula}/stats")
def get_athlete_stats(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Métricas de participación del atleta para el panel del coach.
    Lee todo desde Supabase — funciona en Railway sin archivos locales.

    Retorna:
      - ml_snapshots:     filas en training_snapshots (modelo Q2)
        - manual:         source=app_race (check-in manual del atleta)
        - strava:         source=coach_from_strava (importado por el coach)
      - strava_runs:      actividades de running desde feb 2026
      - strava_pending:   runs sin cobertura (approx: runs - snapshots strava)
      - checkins_total:   total check-ins en tabla checkins
      - last_checkin:     fecha del último check-in
      - last_activity:    fecha de la última actividad Strava
    """
    from datetime import date as _date2, timedelta as _td2
    from src.storage.supabase_client import get_client
    PROJECT_START = (_date2.today() - _td2(days=90)).isoformat()  # ventana rodante 90 días

    sb = get_client()
    if not sb:
        return {"cedula": cedula, "error": "Supabase no disponible"}

    try:
        # 1. Training snapshots — COUNT server-side por fuente (sin descargar JSONB pesado).
        # Antes: SELECT race_date,data → descargaba toda la columna data JSONB por atleta.
        # Ahora: 3 queries COUNT + 1 query de fechas (sin JSONB) → dramáticamente más rápido.
        snap_total_res = (
            sb.table("training_snapshots")
            .select("race_date", count="exact")
            .eq("cedula", cedula)
            .execute()
        )
        snap_manual_res = (
            sb.table("training_snapshots")
            .select("race_date", count="exact")
            .eq("cedula", cedula)
            .filter("data->>source", "eq", "app_race")
            .execute()
        )
        ml_snapshots = snap_total_res.count or 0
        snap_manual  = snap_manual_res.count or 0
        snap_strava  = ml_snapshots - snap_manual
        snap_dates   = {s["race_date"] for s in (snap_total_res.data or [])}

        # 2. Check-ins — COUNT + última fecha sin descargar raw JSONB.
        checkins_res = (
            sb.table("checkins")
            .select("checkin_date", count="exact")
            .eq("cedula", cedula)
            .execute()
        )
        last_checkin_res = (
            sb.table("checkins")
            .select("checkin_date")
            .eq("cedula", cedula)
            .order("checkin_date", desc=True)
            .limit(1)
            .execute()
        )
        checkins_total = checkins_res.count or 0
        last_checkin   = last_checkin_res.data[0]["checkin_date"] if last_checkin_res.data else None

        # 3. Actividades Strava — HEAD + count=exact (sin descargar filas)
        # head=True → HTTP HEAD request: PostgREST devuelve solo Content-Range con el conteo,
        # sin body. Más confiable que select("strava_id", count="exact") que puede retornar
        # count=None si el SDK no parsea el header correctamente.
        runs_count_res = (
            sb.table("activities")
            .select("*", count="exact", head=True)
            .eq("cedula", cedula)
            .in_("sport_type", ["Run", "TrailRun"])
            .gte("activity_date", PROJECT_START)
            .execute()
        )
        strava_runs = runs_count_res.count or 0

        last_activity_res = (
            sb.table("activities")
            .select("activity_date")
            .eq("cedula", cedula)
            .in_("sport_type", ["Run", "TrailRun"])
            .order("activity_date", desc=True)
            .limit(1)
            .execute()
        ).data or []
        last_activity  = last_activity_res[0]["activity_date"][:10] if last_activity_res else None
        strava_pending = 0  # simplificado — era comparación de sets, no crítico para display

        # 4a. weeks_span — solo necesitamos primera y última fecha (2 filas, sin raw)
        first_run = (
            sb.table("activities")
            .select("activity_date")
            .eq("cedula", cedula)
            .in_("sport_type", ["Run", "TrailRun"])
            .order("activity_date", desc=False)
            .limit(1)
            .execute()
        ).data or []
        last_run = (
            sb.table("activities")
            .select("activity_date")
            .eq("cedula", cedula)
            .in_("sport_type", ["Run", "TrailRun"])
            .order("activity_date", desc=True)
            .limit(1)
            .execute()
        ).data or []

        weeks_span = 0
        if first_run and last_run:
            from datetime import date
            d0 = first_run[0]["activity_date"][:10]
            d1 = last_run[0]["activity_date"][:10]
            delta = date.fromisoformat(d1) - date.fromisoformat(d0)
            weeks_span = max(1, delta.days // 7)

        # 4b. runs_with_hr — count server-side con filtro JSONB correcto.
        # PostgREST soporta path filters sobre JSONB:
        #   raw->>average_heartrate=not.is.null
        # → cuenta solo runs donde average_heartrate existe en el raw de Strava.
        # Esto es semánticamente correcto (atletas sin sensor HR tienen raw pero sin
        # el campo average_heartrate) y es rápido: COUNT server-side, cero transferencia.
        # Validado: 592 para Forero, 0 para Johan Castro (sin sensor HR).
        hr_count_res = (
            sb.table("activities")
            .select("*", count="exact", head=True)
            .eq("cedula", cedula)
            .in_("sport_type", ["Run", "TrailRun"])
            .not_.is_("raw->>average_heartrate", "null")
            .execute()
        )
        runs_with_hr = hr_count_res.count or 0

        # 5. Perfil (age/sex) — señala si el onboarding llegó a Supabase
        profile_rows = (
            sb.table("athlete_profiles")
            .select("cedula")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        ).data or []
        has_profile = len(profile_rows) > 0

        # 6. Snapshot — confirma que el pipeline corrió exitosamente
        snap_rows = (
            sb.table("athlete_snapshots")
            .select("cedula")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        ).data or []
        has_snapshot = len(snap_rows) > 0

        # 7. Plan semanal generado
        plan_rows = (
            sb.table("weekly_plans")
            .select("cedula")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        ).data or []
        has_plan = len(plan_rows) > 0

        # ── Feature flags ─────────────────────────────────────────────────────
        # N2 eligibility: criterio relajado (≥5 HR runs, ≥4 semanas) — ver bulk stats.
        features = {
            "prediccion_n1":    has_profile,
            "dashboard_activo": has_snapshot,
            "plan_semanal":     has_plan,
            "carga_training":   strava_runs > 0,
            "zonas_hr":         runs_with_hr >= 5,
            "elegible_n2":      weeks_span >= 4 and runs_with_hr >= 5,
        }

        return {
            "cedula":          cedula,
            "ml_snapshots":    ml_snapshots,
            "ml_manual":       snap_manual,
            "ml_strava":       snap_strava,
            "checkins_total":  checkins_total,
            "last_checkin":    last_checkin,
            "strava_runs":     strava_runs,
            "strava_pending":  strava_pending,
            "last_activity":   last_activity,
            # Feature flags para el panel del coach y el portal
            "runs_with_hr":    runs_with_hr,
            "weeks_span":      weeks_span,
            "has_profile":     has_profile,
            "has_snapshot":    has_snapshot,
            "has_plan":        has_plan,
            "features":        features,
            # Compatibilidad con campo antiguo
            "n_rows":          ml_snapshots,
        }

    except Exception as e:
        return {"cedula": cedula, "error": str(e), "n_rows": 0}


# ─── Actividades Strava ──────────────────────────────────────────────────────

@router.get("/{cedula}/activities")
def get_activities(
    cedula: str,
    limit: int = Query(default=50, ge=1, le=500, description="Máximo de actividades a devolver"),
    from_date: str | None = Query(default=None, description="Fecha inicio ISO, ej: 2025-01-01"),
    to_date: str | None = Query(default=None, description="Fecha fin ISO, ej: 2025-03-12"),
    sport_type: str | None = Query(default=None, description="Filtrar por tipo, ej: 'run'"),
    _: None = Depends(require_api_key),
):
    """
    Devuelve el historial de actividades Strava del atleta.

    Lee desde Supabase primero; si no hay datos, fallback a silver/activities.parquet.

    Parámetros:
    - limit: máximo de actividades (default 50, max 500), ordenadas de más reciente a más antigua
    - from_date: filtrar actividades desde esta fecha (YYYY-MM-DD)
    - to_date: filtrar actividades hasta esta fecha (YYYY-MM-DD)
    - sport_type: filtrar por tipo de actividad (ej: "run", "VirtualRun")

    Campos por actividad:
    - strava_id: ID único de Strava (desde Supabase) o activity_id (desde local)
    - name: nombre de la actividad
    - sport_type: tipo (Run, VirtualRun, etc.)
    - activity_date / start_date_local: fecha/hora local
    - distance_m, duration_sec, elevation_m, avg_pace_sec_km
    """
    athlete_dir = get_data_dir() / cedula
    rows = read_activities(
        cedula,
        athlete_dir if athlete_dir.exists() else None,
        limit=limit,
        from_date=from_date,
        to_date=to_date,
        sport_type=sport_type,
    )
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Activities no disponibles. "
                f"Ejecuta primero: POST /athletes/{cedula}/sync"
            ),
        )
    return sanitize_json({
        "cedula":   cedula,
        "count":    len(rows),
        "limit":    limit,
        "data":     rows,
    })


# ─── Summary de actividades ──────────────────────────────────────────────────

@router.get("/{cedula}/activities/summary")
def get_activities_summary(
    cedula: str,
    period: str = Query(default="week", description="Agrupar por 'week' o 'month'"),
    last_n: int = Query(default=8, ge=1, le=52, description="Últimos N períodos a devolver"),
    sport_type: str | None = Query(default=None, description="Filtrar por tipo, ej: 'run'"),
    _: None = Depends(require_api_key),
):
    """
    Resumen de actividades agrupado por semana o mes. Listo para gráficas.

    Lee todas las actividades disponibles (Supabase → local) y las agrega
    por el período solicitado.

    Parámetros:
    - period: 'week' (lunes como inicio) o 'month'
    - last_n: cuántos períodos devolver (default 8, max 52)
    - sport_type: filtrar antes de agregar, ej: 'run', 'Ride'

    Campos por período:
    - period_start: fecha de inicio del período (YYYY-MM-DD)
    - sessions: número de actividades
    - km_total: km totales
    - elevation_m_total: desnivel acumulado
    - duration_sec_total: tiempo total en segundos
    - avg_pace_sec_km: pace promedio ponderado por km (None si no hay runs)
    - avg_heartrate: FC promedio (None si no hay datos de HR)
    """
    if period not in ("week", "month"):
        raise HTTPException(
            status_code=422,
            detail="El parámetro 'period' debe ser 'week' o 'month'.",
        )

    athlete_dir = get_data_dir() / cedula
    # Leer todas las actividades disponibles para poder agrupar
    rows = read_activities(
        cedula,
        athlete_dir if athlete_dir.exists() else None,
        limit=1000,  # suficiente para cualquier atleta real
    )
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail=f"Activities no disponibles. Ejecuta primero: POST /athletes/{cedula}/sync",
        )

    summary = _build_activities_summary(rows, period, last_n, sport_type)
    return sanitize_json({
        "cedula":   cedula,
        "period":   period,
        "count":    len(summary),
        "data":     summary,
    })


# ─── Dashboard / Home del atleta ─────────────────────────────────────────────

@router.get("/{cedula}/dashboard")
def get_dashboard(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Resumen agregado para la vista principal (home) del atleta.

    Reúne en una sola llamada los datos más relevantes para el dashboard:
    - athlete:           nombre, objetivo, días para la carrera
    - status:            semáforo, readiness, ACWR zone, estado check-in
    - week:              km hechos vs objetivo, tipo semana, foco semanal
    - load:              CTL, TSB, racha_semanas, km_trend, semana_spike
    - recent_activities: últimas 5 actividades (schema canónico)
    - volume_trend:      resumen semanal de las últimas 4 semanas

    Fuentes: Supabase primero, fallback local para cada sección.
    Las secciones se incluyen aunque falten datos (null en lugar de 404).
    """
    athlete_dir = get_data_dir() / cedula
    adir = athlete_dir if athlete_dir.exists() else None

    # ── Lecturas paralelas (todas tolerantes a None) ──────────────────────────
    snap     = read_snapshot(cedula, adir) or {}
    plan     = read_plan(cedula, adir) or {}
    chk_wrap = read_checkin(cedula, adir) or {}
    acts     = read_activities(cedula, adir, limit=100) or []

    chk = chk_wrap.get("latest_checkin") or {}
    profile = snap.get("profile") or {}
    lw      = snap.get("latest_week") or {}

    # Separar actividades: running vs. cross-training
    runs  = [a for a in acts if "run" in (a.get("sport_type") or "").lower()]
    cross = [a for a in acts if "run" not in (a.get("sport_type") or "").lower()]

    # ── athlete ───────────────────────────────────────────────────────────────
    athlete_section = {
        "name":               profile.get("name"),
        "race_distance":      profile.get("race_distance"),
        "race_date_raw":      profile.get("race_date_raw"),
        "race_countdown_days": snap.get("race_countdown_days"),
        "time_goal_formatted": snap.get("time_goal_formatted") or profile.get("time_goal_formatted"),
        "pr_21k_sec":         profile.get("pr_21k_sec"),
        "pr_42k_sec":         profile.get("pr_42k_sec"),
    }

    # ── status ────────────────────────────────────────────────────────────────
    status_section = {
        "semaforo":        snap.get("semaforo_latest_checkin"),
        "readiness_score": snap.get("readiness_score"),
        "acwr_zone":       snap.get("acwr_zone_latest"),
        "data_weeks_available": snap.get("data_weeks_available"),
        "checkin_is_recent":    chk_wrap.get("is_recent", False),
        "last_checkin_date":    chk.get("checkin_date"),
        "last_checkin_feeling": chk.get("feeling_1_10"),
        "last_checkin_fatigue": chk.get("fatigue_1_10"),
        "last_checkin_pain":    chk.get("pain_0_10"),
    }

    # ── week ──────────────────────────────────────────────────────────────────
    targets = plan.get("targets") or {}
    week_section = {
        "week_start":    lw.get("week_start"),
        "week_type":     plan.get("week_type"),
        "weekly_focus":  plan.get("weekly_focus"),
        "week_summary":  plan.get("week_summary"),
        "km_done":       round(lw.get("km_week") or 0, 2),
        "km_target":     targets.get("target_km_week"),
        "sessions_done": lw.get("sessions_week"),
        "days_running":  targets.get("days_running"),
        "days_strength": targets.get("days_strength"),
        "notes":         (plan.get("notes") or [None])[0],
    }

    # ── load ──────────────────────────────────────────────────────────────────
    load_section = {
        "ctl":           round(lw.get("ctl") or 0, 2),
        "atl":           round(lw.get("atl") or 0, 2),
        "tsb":           round(lw.get("tsb") or 0, 2),
        "acwr":          round(lw.get("acwr") or 0, 3),
        "racha_semanas": lw.get("racha_semanas"),
        "km_trend":      lw.get("km_trend"),
        "semana_spike":  lw.get("semana_spike", False),
        "pctZ1":         lw.get("pctZ1"),
        "pctZ2":         lw.get("pctZ2"),
        "pctZ3":         lw.get("pctZ3"),
        "pctZ4":         lw.get("pctZ4"),
    }

    # ── recent_runs (últimas 5 carreras) — núcleo del coaching ───────────────
    recent_runs = runs[:5]

    # ── running_trend (4 semanas, solo runs) — para gráfica de volumen ───────
    running_trend = _build_activities_summary(runs, "week", 4, None)

    # ── recent_cross_training (últimas 5 actividades no running) ─────────────
    # Ordenadas por fecha desc; sin métricas de carga (no contaminan el plan)
    recent_cross = [
        {
            "activity_id":  a.get("activity_id"),
            "name":         a.get("name"),
            "sport_type":   a.get("sport_type"),
            "activity_date": a.get("activity_date"),
            "distance_km":  a.get("distance_km"),
            "duration_sec": a.get("duration_sec"),
        }
        for a in cross[:5]
    ]

    return sanitize_json({
        "cedula":               cedula,
        "generated_at":         snap.get("generated_at"),
        "athlete":              athlete_section,
        "status":               status_section,
        "week":                 week_section,
        "load":                 load_section,
        "recent_runs":          recent_runs,
        "running_trend":        running_trend,
        "recent_cross_training": recent_cross,
    })


# ─── Jerarquía ML — predicción por zona de FC ───────────────────────────────

@router.get("/{cedula}/ml-hierarchy")
def get_ml_hierarchy(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Retorna la predicción del modelo jerárquico ML para este atleta.

    Nivel 1 (Prior poblacional): Ridge sobre FitRec/Endomondo (20 710 sesiones).
    Predice pace_min_km para cada zona de FC (Z1–Z5) usando FCmax observada.

    Nivel 2 (Cohorte RUNA): pendiente — retorna placeholder.
    Nivel 3 (Bayesiano individual): pendiente — retorna placeholder.

    Campos clave:
    - fcmax: FCmax observada (de Strava) o estimada (220−edad)
    - fcmax_source: 'strava' | 'formula'
    - zones: Z1–Z5 con rangos de FC y ritmo estimado por nivel
    - distances: ritmo estimado para 5K/10K/21K/42K (solo Nivel 1)
    - model_info: metadatos del modelo activo
    - levels: estado de cada nivel (active/pending/future)
    """
    import math

    # ── Cargar modelo desde api/models/nivel1_ridge_v4.json ──────────────────
    # El JSON está commiteado al repo → Railway siempre lo tiene.
    # Para actualizar el modelo: reentrenar, exportar nuevo JSON, commitear.
    _MODEL_PATH = Path(__file__).parent.parent / "models" / "nivel1_ridge_v4.json"
    try:
        _m = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=f"Modelo no encontrado: {_MODEL_PATH}. Asegúrate de commitear api/models/nivel1_ridge_v4.json",
        )

    RIDGE_COEFS     = _m["coefs"]
    RIDGE_INTERCEPT = _m["intercept"]
    SCALER_MEAN     = _m["scaler_mean"]
    SCALER_SCALE    = _m["scaler_scale"]
    FEATURES        = _m["features"]
    CONFORMAL_Q     = _m["conformal_q"]
    MAE_SEC_KM      = _m["mae_sec_km"]

    def _ridge_predict(X_raw: list[float]) -> float:
        """StandardScaler + Ridge predict sin sklearn."""
        scaled = [(x - m) / s for x, m, s in zip(X_raw, SCALER_MEAN, SCALER_SCALE)]
        return sum(c * x for c, x in zip(RIDGE_COEFS, scaled)) + RIDGE_INTERCEPT

    # ── 1. Leer perfil del atleta ────────────────────────────────────────────
    athlete_dir = get_data_dir() / cedula
    profile = read_profile(cedula, athlete_dir if athlete_dir.exists() else None)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. Ejecuta primero: POST /athletes/{cedula}/sync",
        )

    age = profile.get("age")
    gender = profile.get("sex") or profile.get("gender")
    gender_bin = 1 if gender and gender.strip().upper() in ("M", "MALE", "MASCULINO", "HOMBRE") else 0

    # ── 2. Determinar FCmax ──────────────────────────────────────────────────
    fcmax_obs = None
    fcmax_source = "formula"

    try:
        acts = read_activities(cedula, athlete_dir if athlete_dir.exists() else None, limit=200)
        if acts:
            max_hrs = [
                a.get("max_heartrate") or (a.get("raw") or {}).get("max_heartrate") or 0
                for a in acts
                if a.get("max_heartrate") or (a.get("raw") or {}).get("max_heartrate")
            ]
            if max_hrs:
                fcmax_obs = max(max_hrs)
                if fcmax_obs > 100:
                    fcmax_source = "strava"
    except Exception:
        pass

    if not fcmax_obs or fcmax_obs <= 100:
        if age and age > 10:
            fcmax_obs = 220 - int(age)
        else:
            fcmax_obs = 190

    # ── 3. Definir zonas y predecir ritmo por zona ───────────────────────────
    zone_defs = [
        {"zone": 1, "name": "Z1 · Recuperación",   "pct_min": 0.00, "pct_max": 0.60, "color": "#3B82F6"},
        {"zone": 2, "name": "Z2 · Aeróbico fácil",  "pct_min": 0.60, "pct_max": 0.70, "color": "#22C55E"},
        {"zone": 3, "name": "Z3 · Tempo",           "pct_min": 0.70, "pct_max": 0.80, "color": "#EAB308"},
        {"zone": 4, "name": "Z4 · Umbral",          "pct_min": 0.80, "pct_max": 0.90, "color": "#F97316"},
        {"zone": 5, "name": "Z5 · VO₂max",          "pct_min": 0.90, "pct_max": 1.00, "color": "#EF4444"},
    ]
    typical_duration_sec = {1: 3600, 2: 3600, 3: 2400, 4: 1800, 5: 1200}

    zones = []
    for zd in zone_defs:
        z = zd["zone"]
        hr_mid = fcmax_obs * (zd["pct_min"] + zd["pct_max"]) / 2
        hr_min = round(fcmax_obs * zd["pct_min"])
        hr_max = round(fcmax_obs * zd["pct_max"])
        pct_mid = (zd["pct_min"] + zd["pct_max"]) / 2 * 100
        dur_sec = typical_duration_sec[z]

        hr_max_rel = hr_mid / fcmax_obs
        log_dur = math.log(dur_sec)
        dens_hr = 1.0 / (fcmax_obs * 0.10 + 1)

        X_raw = [gender_bin, fcmax_obs, hr_mid, pct_mid, z, hr_max_rel, log_dur, dens_hr]
        pred_min_km = _ridge_predict(X_raw)

        pred_lower = pred_min_km - CONFORMAL_Q
        pred_upper = pred_min_km + CONFORMAL_Q

        pred_min_km = max(pred_min_km, 2.5)
        pred_lower = max(pred_lower, 2.0)
        pred_upper = max(pred_upper, pred_min_km)

        zones.append({
            "zone": z,
            "name": zd["name"],
            "color": zd["color"],
            "hr_min": hr_min,
            "hr_max": hr_max,
            "pct_fcmax_range": f"{int(zd['pct_min']*100)}–{int(zd['pct_max']*100)}%",
            "nivel1": {
                "pace_min_km": round(pred_min_km, 2),
                "pace_sec_km": round(pred_min_km * 60, 0),
                "pace_fmt": f"{int(pred_min_km)}:{int((pred_min_km % 1) * 60):02d} /km",
                "interval_lower_fmt": f"{int(pred_lower)}:{int((pred_lower % 1) * 60):02d}",
                "interval_upper_fmt": f"{int(pred_upper)}:{int((pred_upper % 1) * 60):02d}",
                "conformal_width_sec": round(CONFORMAL_Q * 60, 0),
            },
            "nivel2": None,
            "nivel3": None,
        })

    # ── 4. Estimación de tiempos por distancia (Riegel desde zona 3) ─────────
    z3_pace = next((z["nivel1"]["pace_min_km"] for z in zones if z["zone"] == 3), 5.5)
    distances = []
    for dist_label, dist_km in [("5K", 5.0), ("10K", 10.0), ("21K", 21.0975), ("42K", 42.195)]:
        riegel_factor = (dist_km / 10.0) ** 0.06
        adj_pace = z3_pace * riegel_factor
        time_sec = adj_pace * 60 * dist_km

        h = int(time_sec // 3600)
        m = int((time_sec % 3600) // 60)
        s = int(time_sec % 60)
        time_fmt = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

        distances.append({
            "distance": dist_label,
            "km": dist_km,
            "estimated_pace_min_km": round(adj_pace, 2),
            "estimated_pace_fmt": f"{int(adj_pace)}:{int((adj_pace % 1) * 60):02d} /km",
            "estimated_time_sec": round(time_sec),
            "estimated_time_fmt": time_fmt,
            "source_level": 1,
        })

    # ── 5. Info del modelo ───────────────────────────────────────────────────
    model_info = {
        "name": "Ridge Regression",
        "alpha": 1.0,
        "features": FEATURES,
        "n_features": len(FEATURES),
        "mae_sec_km": round(MAE_SEC_KM, 2),
        "mae_min_km": round(MAE_SEC_KM / 60, 2),
        "conformal_width_min_km": round(CONFORMAL_Q, 2),
        "conformal_coverage": 0.80,
        "n_train_sessions": 20710,
        "n_users": 356,
        "dataset": "FitRec/Endomondo (Ni et al., 2019)",
        "cv_protocol": "GroupKFold K=10",
        "feature_set": "v4 (+dens_hr)",
    }

    # ── 6. Estado de niveles ─────────────────────────────────────────────────
    levels = [
        {
            "level": 1,
            "name": "Prior poblacional",
            "status": "active",
            "description": "Modelo entrenado con 20 710 sesiones de 356 usuarios de Endomondo",
            "icon": "globe",
            "accuracy": f"MAE = {round(MAE_SEC_KM, 1)} seg/km",
        },
        {
            "level": 2,
            "name": "Cohorte RUNA",
            "status": "pending",
            "description": "AutoML + LOAO-CV sobre la cohorte de atletas RUNA (en desarrollo)",
            "icon": "users-three",
            "accuracy": "Pendiente — se espera mejorar MAE en ~15–25%",
        },
        {
            "level": 3,
            "name": "Personalización individual",
            "status": "future",
            "description": "Actualización bayesiana con las carreras y entrenamientos del atleta",
            "icon": "user-focus",
            "accuracy": "Futuro — mejora progresiva con cada actividad registrada",
        },
    ]

    return sanitize_json({
        "cedula": cedula,
        "fcmax": fcmax_obs,
        "fcmax_source": fcmax_source,
        "gender": "M" if gender_bin else "F",
        "age": age,
        "zones": zones,
        "distances": distances,
        "model_info": model_info,
        "levels": levels,
    })


# ─── Predicción de rendimiento ───────────────────────────────────────────────

def _fmt_pace_local(sec_per_km: float) -> str:
    """Formatea seg/km como 'M:SS'. Ej: 322.0 → '5:22'."""
    import math
    if not sec_per_km or sec_per_km <= 0 or math.isnan(sec_per_km):
        return "--"
    mins, secs = divmod(int(round(sec_per_km)), 60)
    return f"{mins}:{secs:02d}"


def _virtual_pr_prediction(
    cedula: str,
    target_distance: str,
    age: "float | None",
    gender: "str | None",
) -> "dict | None":
    """
    Deriva un PR virtual desde los mejores esfuerzos de entrenamiento recientes
    (últimos 180 días) cuando el atleta no tiene marcas personales declaradas.

    Estrategia:
    1. Busca en Supabase carreras (Run/TrailRun) en el rango de distancia esperado
       para cada distancia estándar (5K±40%, 10K±40%, 21K±40%, 42K±30%).
    2. Usa el mejor ritmo observado como PR virtual para esa distancia.
    3. Si no hay esfuerzos cercanos al target, prueba distancias más cortas.
    4. Ajusta la incertidumbre ×1.5 y fija confianza = BAJA.

    Returns:
        dict con predicción enriquecida, o None si no hay datos suficientes.
    """
    from datetime import date, timedelta
    from src.storage.supabase_client import get_client
    from src.ml.predictor import predict_race_time_range
    from src.ml.riegel import PR_KEYS, DISTANCES as DIST_KM

    sb = get_client()
    if not sb:
        return None

    cutoff = (date.today() - timedelta(days=180)).isoformat()

    # Rangos de distancia (km) aceptables para cada distancia estándar
    SEARCH_RANGES: dict[str, tuple[float, float]] = {
        "5K":  (3.0,  7.0),
        "10K": (7.0,  14.0),
        "21K": (14.0, 27.0),
        "42K": (30.0, 50.0),
    }

    # Orden de búsqueda: target primero, luego más cortas (más datos disponibles)
    FALLBACK_ORDER: dict[str, list[str]] = {
        "5K":  ["5K"],
        "10K": ["10K", "5K"],
        "21K": ["21K", "10K", "5K"],
        "42K": ["42K", "21K", "10K", "5K"],
    }

    best_pace: "float | None" = None
    best_dist_key: "str | None" = None

    for dist_key in FALLBACK_ORDER.get(target_distance, [target_distance]):
        lo_km, hi_km = SEARCH_RANGES[dist_key]
        try:
            res = (
                sb.table("activities")
                .select("distance_m,duration_sec,pace_sec_per_km")
                .eq("cedula", cedula)
                .in_("sport_type", ["Run", "TrailRun"])
                .gte("activity_date", cutoff)
                .gte("distance_m", lo_km * 1000)
                .lte("distance_m", hi_km * 1000)
                .execute()
            )
            rows = [
                r for r in (res.data or [])
                if r.get("pace_sec_per_km") and r["pace_sec_per_km"] > 60  # >1 min/km sanity
            ]
        except Exception:
            rows = []

        if rows:
            best_row = min(rows, key=lambda r: r["pace_sec_per_km"])
            best_pace    = best_row["pace_sec_per_km"]
            best_dist_key = dist_key
            break

    if best_pace is None or best_dist_key is None:
        return None

    # Construir perfil virtual con el PR implícito
    virtual_pr_sec = best_pace * DIST_KM[best_dist_key]
    virtual_profile = {PR_KEYS[best_dist_key]: virtual_pr_sec}

    result = predict_race_time_range(
        profile=virtual_profile,
        target_distance=target_distance,
        age=float(age) if age else None,
        gender=gender,
    )
    if result.get("error"):
        return None

    # Inflar incertidumbre ×1.5 (ritmo de entrenamiento ≠ ritmo de competencia)
    uf = result.get("uncertainty_fraction", 0.044)
    result["uncertainty_fraction"] = round(max(uf * 1.5, 0.06), 4)

    # Anular confianza a BAJA y documentar fuente
    result["confidence"]        = "BAJA"
    result["confidence_reason"] = (
        f"PR virtual desde mejor esfuerzo de {best_dist_key} en entrenamiento "
        f"({_fmt_pace_local(best_pace)} min/km, últimos 6 meses)"
    )
    result["virtual_pr"]           = True
    result["virtual_pr_dist"]      = best_dist_key
    result["virtual_pr_pace_fmt"]  = _fmt_pace_local(best_pace)
    result["virtual_pr_sec"]       = round(virtual_pr_sec)
    result["virtual_pr_note"] = (
        "No hay marcas de carrera declaradas. "
        "Predicción basada en mejor ritmo de entrenamiento reciente — "
        "puede diferir del rendimiento en competencia."
    )
    return result


@router.get("/{cedula}/prediction")
def get_prediction(
    cedula: str,
    target: str = Query(default="42K", description="Distancia objetivo: 5K, 10K, 21K o 42K"),
    _: None = Depends(require_api_key),
):
    """
    Predice el rango de ritmo esperado para la distancia objetivo.

    Integra Capas 0 (selección PR) + 1 (Riegel calibrado) + 2 (corrección
    demográfica) del sistema de predicción. Los PRs se leen desde profile.json.

    Cuando el atleta no tiene PRs declarados, intenta derivar un PR virtual
    desde sus mejores esfuerzos de entrenamiento en Strava (últimos 180 días).
    En ese caso: confidence=BAJA y virtual_pr=true en la respuesta.

    Parámetros:
    - target: distancia objetivo (default 42K)

    Campos clave en la respuesta:
    - pace_range_fmt:  rango de ritmo, p.ej. "5:11 – 5:29 min/km"
    - time_range_fmt:  rango de tiempo total, p.ej. "3:38:50 – 3:52:10"
    - confidence:      ALTA / MEDIA / MEDIA-BAJA / BAJA
    - virtual_pr:      true si la predicción usa ritmo de entrenamiento (sin PR declarado)
    - layers_active:   lista de capas aplicadas (Capa0, Capa1, Capa2)
    - source_pr:       distancia del PR usado como fuente
    - empirical_support: nivel de soporte empírico para esta distancia
    - error: 'SIN_PR' | 'SIN_DATOS' si no hay marcas ni actividades disponibles
    """
    if target not in ("5K", "10K", "21K", "42K"):
        raise HTTPException(
            status_code=422,
            detail=f"Distancia '{target}' no válida. Use: 5K, 10K, 21K, 42K.",
        )

    athlete_dir_pred = get_data_dir() / cedula
    profile = read_profile(cedula, athlete_dir_pred if athlete_dir_pred.exists() else None)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Perfil no disponible. Ejecuta primero: POST /athletes/{cedula}/sync",
        )

    from src.ml.predictor import predict_race_time_range

    age    = profile.get("age")
    gender = profile.get("sex")

    result = predict_race_time_range(
        profile=profile,
        target_distance=target,
        age=float(age) if age else None,
        gender=gender,
    )

    # Si no hay PRs declarados, intentar Virtual PR desde actividades Strava
    if result.get("error") == "SIN_PR":
        virtual = _virtual_pr_prediction(
            cedula=cedula,
            target_distance=target,
            age=float(age) if age else None,
            gender=gender,
        )
        if virtual:
            return sanitize_json(virtual)
        # Sin PRs ni actividades → error informativo
        result["error"] = "SIN_DATOS"
        result["message"] = (
            "No hay marcas personales declaradas ni actividades recientes en Strava. "
            "Conecta Strava o registra al menos un tiempo de carrera (5K, 10K, 21K o 42K)."
        )

    return sanitize_json(result)


# ─── Reporte PDF ──────────────────────────────────────────────────────────────

@router.get("/{cedula}/report.pdf")
def get_report_pdf(
    cedula: str,
    athlete_dir: Path = Depends(get_athlete_dir),
    _: None = Depends(require_api_key),
):
    """
    Descarga el último reporte PDF generado para el atleta.
    El PDF se genera en el paso 'pdf' del pipeline (legado/fallback).

    Retorna el archivo más reciente en data/athletes/{cedula}/outputs/*.pdf.
    """
    outputs_dir = athlete_dir / "outputs"
    if not outputs_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Directorio outputs/ no encontrado. Ejecuta el pipeline con step 'pdf'.",
        )

    pdfs = sorted(outputs_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        raise HTTPException(
            status_code=404,
            detail=(
                "No hay reportes PDF disponibles. "
                f"Ejecuta: POST /athletes/{cedula}/pipeline?steps=pdf"
            ),
        )

    latest_pdf = pdfs[0]
    return FileResponse(
        path=str(latest_pdf),
        media_type="application/pdf",
        filename=f"coaching_report_{cedula}_{latest_pdf.stem}.pdf",
    )


# ─── Strava token status (read-only — para validar que Supabase tiene tokens) ──

@router.get("/{cedula}/strava/token-status")
def strava_token_status(cedula: str, _: None = Depends(require_api_key)):
    """
    Lee el estado de los tokens de Strava desde Supabase.
    Solo muestra sufijos (últimos 6 chars) — nunca expone tokens completos.
    Usado para validar que POST /strava/token realmente escribió en Supabase.
    """
    from src.storage.supabase_client import get_client
    client = get_client()
    if not client:
        return {"ok": False, "detail": "Supabase not configured"}
    try:
        resp = (
            client.table("athletes")
            .select("cedula,strava_access_token,strava_refresh_token,strava_token_expires_at,strava_last_sync_at")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return {"ok": False, "detail": f"Athlete {cedula} not found in Supabase"}
        row = resp.data[0]
        at  = row.get("strava_access_token")  or ""
        rt  = row.get("strava_refresh_token") or ""
        return {
            "ok":      True,
            "cedula":  cedula,
            "access_token_suffix":   f"…{at[-6:]}"  if at  else None,
            "refresh_token_suffix":  f"…{rt[-6:]}"  if rt  else None,
            "expires_at":            row.get("strava_token_expires_at"),
            "last_sync_at":          row.get("strava_last_sync_at"),
            "is_test_token":         at.startswith("BRIDGE_TEST_") if at else False,
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# ─── Strava token ingestion (called by ar-athletes-portal after OAuth) ────────

class StravaTokenBody(BaseModel):
    access_token:       str
    refresh_token:      str
    expires_at:         int              # unix timestamp
    strava_athlete_id:  str | None = None  # ID numérico del atleta en Strava
    name:               str | None = None  # nombre del atleta (para push_athlete)


@router.post("/{cedula}/strava/token")
def store_strava_token(
    cedula:     str,
    body:       StravaTokenBody,
    background: BackgroundTasks,
    _:          None = Depends(require_api_key),
):
    """
    Recibe y persiste los tokens de Strava en Supabase para un atleta.
    Llamado por ar-athletes-portal tras completar el flujo OAuth.
    Requiere migration 005_strava_tokens.sql aplicada en Supabase.

    Después de llamar este endpoint, el step strava del sync leerá
    los tokens desde Supabase en lugar de Google Sheets.
    """
    import time
    print(
        f"[/strava/token] Recibido para cedula={cedula}: "
        f"expires_at={body.expires_at} (margen={(body.expires_at - int(time.time()))}s) | "
        f"access_token=…{body.access_token[-6:]} | refresh_token=…{body.refresh_token[-6:]}"
    )
    from src.storage.writer import push_strava_tokens, push_athlete

    # Garantizar fila maestra en athletes ANTES de escribir tokens.
    # push_strava_tokens hace upsert sobre athletes, pero si Supabase tiene
    # RLS activo o la fila no existe aún, el upsert puede fallar.
    # push_athlete es idempotente y resuelve el FK constraint en cascada.
    athlete_row = push_athlete(cedula, name=body.name)  # name=None → no sobreescribe
    print(f"[/strava/token] push_athlete result: {athlete_row}")

    result = push_strava_tokens(
        cedula, body.access_token, body.refresh_token, body.expires_at,
        strava_athlete_id=body.strava_athlete_id,
    )
    print(f"[/strava/token] push_strava_tokens result: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("detail", "Error writing tokens"))

    # Auto-trigger pipeline: strava → features → plan
    # (sin ingest — atletas del portal tienen perfil vía /profile/onboarding)
    from api.routers.sync import _run_pipeline_and_push
    background.add_task(
        _run_pipeline_and_push,
        cedula,
        ["strava", "features", "plan"],
        False,   # skip_strava=False
        True,    # push_to_supabase=True
    )
    print(f"[/strava/token] Pipeline auto-trigger encolado para cedula={cedula}")

    return {"ok": True, "cedula": cedula, "detail": result.get("detail"), "pipeline": "enqueued"}


# ─── Backfill: Strava activities → check-ins ────────────────────────────────

@router.get("/{cedula}/strava-runs")
def get_strava_runs_without_checkin(
    cedula: str,
    days: int = Query(default=90, ge=1, le=365),
    _: None = Depends(require_api_key),
):
    """
    Lista runs de Strava que NO tienen un check-in correspondiente.
    Útil para que el coach vea qué actividades puede importar.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Cutoff: febrero 2026 (inicio del proyecto) — no importar datos antiguos
    project_start = "2026-02-01"
    cutoff = max(
        project_start,
        (date.today() - __import__("datetime").timedelta(days=days)).isoformat(),
    )

    # 1. Traer runs de Strava desde el inicio del proyecto
    activities = (
        sb.table("activities")
        .select("strava_id,activity_date,name,sport_type,distance_m,duration_sec,avg_pace_sec_km,raw")
        .eq("cedula", cedula)
        .in_("sport_type", ["Run", "TrailRun"])
        .gte("activity_date", project_start)
        .order("activity_date", desc=True)
        .execute()
    ).data or []

    # 2. Fechas cubiertas por check-in manual del atleta
    checkin_dates = {
        r["checkin_date"]
        for r in (
            sb.table("checkins")
            .select("checkin_date")
            .eq("cedula", cedula)
            .gte("checkin_date", project_start)
            .execute()
        ).data or []
    }

    # 3. Fechas cubiertas por import del coach (training_snapshots con source=coach_from_strava)
    snapshot_dates = {}  # fecha → strava_id
    for s in (
        sb.table("training_snapshots")
        .select("race_date,data")
        .eq("cedula", cedula)
        .gte("race_date", project_start)
        .execute()
    ).data or []:
        d = s.get("data") or {}
        if d.get("source") == "coach_from_strava":
            snapshot_dates[s["race_date"]] = d.get("strava_id", "")

    # 4. Clasificar cada actividad
    runs = []
    for act in activities:
        act_date = (act.get("activity_date") or "")[:10]
        dist_km  = round((act.get("distance_m") or 0) / 1000, 2)
        dur_sec  = act.get("duration_sec") or 0
        raw      = act.get("raw") or {}
        strava_id = str(act["strava_id"])

        if act_date in checkin_dates:
            status = "manual"        # atleta lo registró
        elif act_date in snapshot_dates:
            status = "imported"      # coach ya importó
        else:
            status = "pending"       # sin cubrir

        runs.append({
            "strava_id":    strava_id,
            "date":         act_date,
            "name":         act.get("name", ""),
            "sport_type":   act.get("sport_type"),
            "distance_km":  dist_km,
            "duration_sec": dur_sec,
            "pace_sec_km":  round(dur_sec / max(dist_km, 0.01), 1) if dist_km > 0.3 else None,
            "avg_heartrate": raw.get("average_heartrate"),
            "max_heartrate": raw.get("max_heartrate"),
            "status":       status,  # "pending" | "manual" | "imported"
        })

    pending  = [r for r in runs if r["status"] == "pending"]
    imported = [r for r in runs if r["status"] == "imported"]
    manual   = [r for r in runs if r["status"] == "manual"]

    return {
        "cedula":       cedula,
        "total_runs":   len(runs),
        "pending":      len(pending),
        "imported":     len(imported),
        "manual":       len(manual),
        # Compatibilidad con UI anterior
        "already_have_checkin": len(imported) + len(manual),
        "missing_checkin":      len(pending),
        # Solo las pendientes van a la tabla de selección
        "runs": pending,
        # Resumen completo para el panel
        "all_runs": runs,
    }


class BackfillRequest(BaseModel):
    """
    Importa actividades seleccionadas de Strava como check-ins.

    El coach selecciona manualmente qué actividades importar desde el panel.
    Cada check-in queda marcado con source='coach_from_strava' para
    transparencia total sobre el origen de los datos.
    """
    strava_ids: list[str] = Field(..., min_length=1, description="IDs de Strava a importar (selección manual del coach)")
    mark_as_official: list[str] = Field(default_factory=list, description="IDs que el coach marca como carrera oficial")
    min_distance_km: float = Field(default=2.0, ge=0, description="Distancia mínima para importar")


@router.post("/{cedula}/backfill-strava")
def backfill_strava_to_checkins(
    cedula: str,
    body: BackfillRequest,
    _: None = Depends(require_api_key),
):
    """
    Importa actividades de Strava seleccionadas por el coach como check-ins.

    Proceso transparente:
      1. El coach ve las actividades en el panel y selecciona cuáles importar
      2. El coach decide cuáles marcar como carrera oficial vs entrenamiento
      3. Cada check-in queda marcado con source='coach_from_strava'
      4. Se genera training_snapshot para el modelo Q2

    NO consulta la API de Strava — usa datos ya almacenados con consentimiento.
    Requiere strava_ids explícitos — no hay importación masiva automática.
    """
    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Normalizar a strings para comparación robusta (Supabase puede devolver int o str)
    id_set = {str(x) for x in body.strava_ids}
    official_set = {str(x) for x in body.mark_as_official}

    activities = (
        sb.table("activities")
        .select("strava_id,activity_date,name,sport_type,distance_m,duration_sec,raw")
        .eq("cedula", cedula)
        .in_("sport_type", ["Run", "TrailRun"])
        .order("activity_date", desc=True)
        .execute()
    ).data or []

    # Filtrar solo los IDs seleccionados manualmente (comparar como strings)
    activities = [a for a in activities if str(a["strava_id"]) in id_set]

    # Filtrar por distancia mínima
    activities = [a for a in activities if (a.get("distance_m") or 0) / 1000 >= body.min_distance_km]

    # Escribir directo a training_snapshots — NO tocar checkins.
    # Checkins es solo para datos subjetivos del atleta.
    #
    # Deduplicación sin schema changes:
    #   - Si ya existe snapshot con source=app_race para esa fecha → el atleta
    #     ya lo registró manualmente, tiene datos subjetivos → NO sobrescribir.
    #   - Si ya existe con source=coach_from_strava → reimport idempotente → OK sobrescribir.
    #   - Si no existe → crear.
    #
    # strava_id se guarda dentro de data{} para trazabilidad.
    imported = []
    skipped = []
    errors = []

    # Cargar snapshots existentes del atleta para dedup
    existing_snapshots = {}
    try:
        snap_res = sb.table("training_snapshots").select("race_date,data").eq("cedula", cedula).execute()
        for s in (snap_res.data or []):
            d = s.get("data") or {}
            existing_snapshots[s["race_date"]] = d.get("source", "unknown")
    except Exception:
        pass

    for act in activities:
        act_date = (act.get("activity_date") or "")[:10]
        strava_id = str(act["strava_id"])

        # Si el atleta ya registró manualmente esta fecha → no tocar
        existing_source = existing_snapshots.get(act_date)
        if existing_source == "app_race":
            skipped.append({"date": act_date, "reason": "atleta ya registró check-in manual"})
            continue

        dist_km = round((act.get("distance_m") or 0) / 1000, 2)
        dur_sec = act.get("duration_sec") or 0
        raw_strava = act.get("raw") or {}
        is_official = str(act["strava_id"]) in official_set

        avg_hr = raw_strava.get("average_heartrate")
        max_hr = raw_strava.get("max_heartrate")
        pace_sec_km = round(dur_sec / max(dist_km, 0.01), 2) if dist_km > 0 else None
        aerobic_eff = round(pace_sec_km / avg_hr, 4) if (avg_hr and avg_hr > 0 and pace_sec_km) else None

        dist_bucket = (
            "5K"  if dist_km <= 6  else
            "10K" if dist_km <= 12 else
            "21K" if dist_km <= 23 else "42K"
        )

        snapshot = {
            "cedula":             cedula,
            "race_date":          act_date,
            "strava_id":          strava_id,   # trazabilidad en data{}
            "source":             "coach_from_strava",
            "recorded_at":        datetime.utcnow().isoformat(),
            "race_distance_km":   dist_km,
            "race_time_sec":      dur_sec,
            "pace_sec_km":        pace_sec_km,
            "dist_bucket":        dist_bucket,
            "is_official":        is_official,
            "sensation_1_5":      None,
            "avg_heartrate":      avg_hr,
            "max_heartrate":      max_hr,
            "aerobic_efficiency": aerobic_eff,
            "strava_name":        act.get("name", ""),
        }

        try:
            sb.table("training_snapshots").upsert(
                {"cedula": cedula, "race_date": act_date, "data": snapshot},
                on_conflict="cedula,race_date",
            ).execute()
            imported.append({
                "date":        act_date,
                "distance_km": dist_km,
                "duration_sec": dur_sec,
                "name":        act.get("name", ""),
                "is_official": is_official,
                "avg_hr":      avg_hr,
            })
        except Exception as exc:
            errors.append({"strava_id": strava_id, "date": act_date, "error": str(exc)})

    return {
        "cedula":   cedula,
        "imported": len(imported),
        "skipped":  len(skipped),
        "errors":   len(errors),
        "error_details": errors[:5] if errors else [],
        "skipped_details": skipped[:5] if skipped else [],
        "runs":     imported,
        "note":     "Datos en training_snapshots (ML). checkins del atleta intactos.",
    }


# ── DELETE athlete ──────────────────────────────────────────────────────────

@router.delete(
    "/{cedula}",
    summary="Eliminar atleta y todos sus datos",
    dependencies=[Depends(require_api_key)],
)
def delete_athlete(
    cedula: str,
    data_dir: Path = Depends(get_data_dir),
):
    """
    Elimina TODOS los datos de un atleta:
    - Supabase: athletes, athlete_profiles, athlete_snapshots,
      weekly_features, activities, checkins, weekly_plans, coach_content
    - Local: data/athletes/{cedula}/ (si existe)

    Requiere API key del coach. Irreversible.
    """
    import shutil
    from src.storage.supabase_client import get_client

    results = {}

    # 1. Supabase cleanup
    client = get_client()
    if client:
        tables = [
            "coach_content",
            "weekly_plans",
            "checkins",
            "activities",
            "weekly_features",
            "athlete_snapshots",
            "athlete_profiles",
            "athletes",  # last — other tables may FK to this
        ]
        for table in tables:
            try:
                client.table(table).delete().eq("cedula", str(cedula)).execute()
                results[table] = "deleted"
            except Exception as exc:
                results[table] = f"error: {exc}"
    else:
        results["supabase"] = "client not available"

    # 2. Local files cleanup
    athlete_dir = data_dir / "athletes" / str(cedula)
    if athlete_dir.exists():
        try:
            shutil.rmtree(athlete_dir)
            results["local_files"] = "deleted"
        except Exception as exc:
            results["local_files"] = f"error: {exc}"
    else:
        results["local_files"] = "not found (ok)"

    # 3. Worker D1 cleanup — propagar delete a Cloudflare D1
    # Sin esto, el atleta queda como orphan en el Panel Admin.
    worker_url = os.getenv("WORKER_URL", "https://app.arathleteslab.com").rstrip("/")
    shared_secret = os.getenv("INTERNAL_SHARED_SECRET", "").strip()
    if shared_secret:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{worker_url}/api/internal/athletes/by-cedula/{cedula}",
                method="DELETE",
                headers={
                    "X-Internal-Secret": shared_secret,
                    # Cloudflare Bot Fight Mode bloquea Python-urllib/3.x.
                    # Usar User-Agent estándar para que pase la WAF.
                    "User-Agent": "running-coaching-backend/1.0 (+https://runningcoaching-production.up.railway.app)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = json.loads(resp.read().decode())
                results["worker_d1"] = (
                    "deleted" if resp_body.get("deleted_from_d1") else "not found in D1 (ok)"
                )
        except Exception as exc:
            results["worker_d1"] = f"error: {exc}"
    else:
        results["worker_d1"] = "skipped (INTERNAL_SHARED_SECRET not configured)"

    return {
        "cedula": cedula,
        "status": "deleted",
        "details": results,
    }


# ─── Detección de orphans en D1 ──────────────────────────────────────────────


def _compute_orphans() -> dict:
    """Lógica pura para diff D1↔Supabase. Sin dependencias FastAPI."""
    from src.storage.supabase_client import get_client

    worker_url = os.getenv("WORKER_URL", "https://app.arathleteslab.com").rstrip("/")
    shared_secret = os.getenv("INTERNAL_SHARED_SECRET", "").strip()
    if not shared_secret:
        raise HTTPException(
            status_code=503,
            detail="INTERNAL_SHARED_SECRET no configurado en Railway. No se puede consultar D1.",
        )

    # 1. Cedulas en D1
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{worker_url}/api/internal/athletes/list-cedulas",
            headers={
                "X-Internal-Secret": shared_secret,
                # Cloudflare Bot Fight Mode bloquea Python-urllib/3.x → User-Agent normal
                "User-Agent": "running-coaching-backend/1.0 (+https://runningcoaching-production.up.railway.app)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            d1_data = json.loads(resp.read().decode())
        d1_athletes = d1_data.get("athletes", [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Worker unreachable: {exc}")

    d1_cedulas = {
        str(a.get("external_athlete_id")): a
        for a in d1_athletes
        if a.get("external_athlete_id")
    }

    # 2. Cedulas en Supabase
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Supabase no disponible")
    res = client.table("athletes").select("cedula,name").execute()
    supabase_cedulas = {str(r["cedula"]): r for r in (res.data or []) if r.get("cedula")}

    # 3. Diff
    orphans_in_d1 = [
        {"cedula": ced, **d1_cedulas[ced]}
        for ced in (set(d1_cedulas.keys()) - set(supabase_cedulas.keys()))
    ]
    missing_in_d1 = [
        supabase_cedulas[ced]
        for ced in (set(supabase_cedulas.keys()) - set(d1_cedulas.keys()))
    ]

    return {
        "d1_total":       len(d1_cedulas),
        "supabase_total": len(supabase_cedulas),
        "orphans_in_d1":  orphans_in_d1,
        "missing_in_d1":  missing_in_d1,
    }


@router.get("/diagnostics/orphans")
def diagnose_orphans(_: None = Depends(require_api_key)):
    """
    Compara el estado entre Cloudflare D1 (Panel Admin) y Supabase (Coach Panel).

    Reporta:
      - orphans_in_d1: atletas que existen en D1 pero NO en Supabase
                       (entradas viejas que el Panel Admin sigue mostrando)
      - missing_in_d1: atletas en Supabase pero no en D1 (raro, pipeline roto)

    Útil para detectar inconsistencias entre los dos sistemas.
    """
    return _compute_orphans()


@router.post("/diagnostics/cleanup-orphans")
def cleanup_orphans(_: None = Depends(require_api_key)):
    """
    Borra de D1 todos los atletas que NO existen en Supabase.
    Idempotente: corre cuantas veces quieras.
    """
    diag = _compute_orphans()
    orphans = diag["orphans_in_d1"]

    worker_url = os.getenv("WORKER_URL", "https://app.arathleteslab.com").rstrip("/")
    shared_secret = os.getenv("INTERNAL_SHARED_SECRET", "").strip()

    deleted = []
    failed = []
    import urllib.request
    common_headers = {
        "X-Internal-Secret": shared_secret,
        "User-Agent": "running-coaching-backend/1.0 (+https://runningcoaching-production.up.railway.app)",
        "Accept": "application/json",
    }
    for orphan in orphans:
        ced = orphan.get("cedula") or orphan.get("external_athlete_id")
        if not ced:
            continue
        try:
            req = urllib.request.Request(
                f"{worker_url}/api/internal/athletes/by-cedula/{ced}",
                method="DELETE",
                headers=common_headers,
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
                if body.get("ok"):
                    deleted.append({"cedula": ced, "name": orphan.get("name")})
                else:
                    failed.append({"cedula": ced, "error": body})
        except Exception as exc:
            failed.append({"cedula": ced, "error": str(exc)})

    return {
        "found":   len(orphans),
        "deleted": len(deleted),
        "failed":  len(failed),
        "details": {"deleted": deleted, "failed": failed},
    }
