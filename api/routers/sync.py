"""
api/routers/sync.py — Endpoint de sincronización a Supabase.

Fase B: Pipeline corre directamente (sin subprocess) con push a Supabase
        inmediatamente después de cada paso.
        DATA_DIR local actúa como caché intermedio;
        Supabase es el store durable.

Beneficios vs Fase A (subprocess + push_all al final):
  - Push incremental: datos en Supabase aunque un paso posterior falle.
  - Sin subprocess: stack traces claros, sin problemas de cwd/env.
  - Aislamiento: saber exactamente qué paso falló y qué ya se guardó.

Endpoints:
  POST /athletes/{cedula}/sync        → pipeline + push a Supabase (síncrono)
  POST /athletes/{cedula}/sync/push   → solo push (asume pipeline ya corrió)
  GET  /athletes/{cedula}/sync/status → estado del último sync
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_data_dir, require_api_key
from src.storage.supabase_client import is_configured

router = APIRouter()

VALID_STEPS = ["ingest", "strava", "features", "plan"]

# Estado en memoria del último sync por cédula (simple, sin persistencia).
_sync_status: dict[str, dict] = {}


# ─── Helpers internos ────────────────────────────────────────────────────────

def _try_push(log: dict, name: str, push_fn, *args) -> None:
    """Ejecuta un push individual y registra el resultado en log."""
    try:
        r = push_fn(*args)
        log[name] = r.get("detail", "ok")
    except Exception as exc:
        log[name] = f"error: {exc}"


def _build_result(
    ok: bool,
    stage_failed: "str | None",
    cedula: str,
    started_at: str,
    steps: list[str],
    stages: dict,
    pushes: dict,
    push_requested: bool,
) -> dict:
    """Construye el dict de respuesta consolidado."""
    if not push_requested:
        sb: dict = {"ok": True, "detail": "skipped (not requested)"}
    elif not is_configured():
        sb = {"ok": True, "detail": "skipped (SUPABASE_URL not set)"}
    else:
        errors = {k: v for k, v in pushes.items() if str(v).startswith("error")}
        sb = {
            "ok":     not errors,
            "pushes": pushes,
            **({"errors": errors} if errors else {}),
        }

    return {
        "ok":             ok and sb.get("ok", True),
        "cedula":         cedula,
        "status":         "ok" if ok else "error",
        "stage_failed":   stage_failed,
        "pipeline_ok":    ok,
        "started_at":     started_at,
        "finished_at":    datetime.now().isoformat(timespec="seconds"),
        "pipeline_steps": steps,
        "stages":         stages,
        "supabase":       sb,
    }


# ─── Helper central ──────────────────────────────────────────────────────────

def _run_pipeline_and_push(
    cedula: str,
    steps: list[str],
    skip_strava: bool,
    push_to_supabase: bool,
) -> dict:
    """
    Fase B: ejecuta cada paso del pipeline directamente (sin subprocess)
    y hace push a Supabase inmediatamente después de cada uno.

    Flujo por paso:
      ingest   → push_profile + push_checkin
      strava   → push_activities
      features → push_snapshot + push_weekly_features
      plan     → push_athlete + push_plan
    """
    started_at = datetime.now().isoformat(timespec="seconds")
    athlete_dir = get_data_dir() / cedula
    athlete_dir.mkdir(parents=True, exist_ok=True)

    stages: dict[str, str] = {}
    pushes: dict[str, str] = {}
    do_push = push_to_supabase and is_configured()

    print(f"[sync] Iniciando pipeline cedula={cedula} steps={steps} push={do_push}")

    # ── 1. INGEST — Forms + Check-ins (global) ───────────────────────────────
    if "ingest" in steps:
        try:
            from src.ingest.ingest_forms import main as ingest_forms
            from src.ingest.ingest_checkins import main as ingest_checkins
            ingest_forms(cedula)
            ingest_checkins(cedula)
            stages["ingest"] = "ok"
            print("[sync] ✅ ingest OK")
        except Exception as exc:
            stages["ingest"] = f"error: {exc}"
            print(f"[sync] ❌ ingest FALLÓ: {exc}")
            return _build_result(False, "ingest", cedula, started_at, steps, stages, pushes, push_to_supabase)

        if do_push:
            from src.storage.writer import push_profile, push_checkin
            _try_push(pushes, "profile", push_profile, cedula, athlete_dir)
            _try_push(pushes, "checkin", push_checkin, cedula, athlete_dir)

    # ── 2. STRAVA ─────────────────────────────────────────────────────────────
    # Si el paso "strava" es el único solicitado, usar path Supabase-first
    # (lee tokens desde athletes.strava_refresh_token, sin depender de Sheets).
    # Si se llama desde el pipeline global (ingest+features+plan), comportamiento previo.
    if "strava" in steps and not skip_strava:
        try:
            from src.strava.sync_strava import main as sync_strava
            # Pasar cedula para activar el path Supabase cuando es un sync específico
            sync_strava(cedula=cedula)
            stages["strava"] = "ok"
            print("[sync] ✅ strava OK")
        except Exception as exc:
            stages["strava"] = f"error: {exc}"
            # No abortar — features/plan pueden correr con datos previos de Supabase
            print(f"[sync] ⚠️ strava FALLÓ: {exc} — continuando con features/plan")

        # push_activities omitted: raw Strava activity data is served from local parquet
        # to minimize third-party storage of Strava Data (§2.9/§7 Strava API Agreement).

    # ── 3. FEATURES ──────────────────────────────────────────────────────────
    # Si el parquet de actividades no existe (redeploy borró el disco),
    # intentar restaurarlo desde Supabase antes de calcular features.
    if "features" in steps:
        silver_path = athlete_dir / "silver" / "activities.parquet"
        if not silver_path.exists() and is_configured():
            from src.storage.writer import restore_activities_to_parquet
            restored = restore_activities_to_parquet(cedula, athlete_dir)
            if restored:
                print(f"[sync] ♻️  Actividades restauradas desde Supabase para features")
            else:
                print(f"[sync] ⚠️  No se encontraron actividades en Supabase para restaurar")

    if "features" in steps:
        try:
            from src.features.build_features import main as build_features
            build_features(cedula=cedula)
            stages["features"] = "ok"
            print("[sync] ✅ features OK")
        except Exception as exc:
            stages["features"] = f"error: {exc}"
            print(f"[sync] ❌ features FALLÓ: {exc}")
            return _build_result(False, "features", cedula, started_at, steps, stages, pushes, push_to_supabase)

        if do_push:
            from src.storage.writer import push_snapshot, push_weekly_features
            _try_push(pushes, "snapshot",        push_snapshot,        cedula, athlete_dir)
            _try_push(pushes, "weekly_features", push_weekly_features, cedula, athlete_dir)

    # ── 4. PLAN ───────────────────────────────────────────────────────────────
    if "plan" in steps:
        try:
            from src.plan.plan_builder import main as build_plan
            build_plan(cedula=cedula)
            stages["plan"] = "ok"
            print("[sync] ✅ plan OK")
        except Exception as exc:
            stages["plan"] = f"error: {exc}"
            print(f"[sync] ❌ plan FALLÓ: {exc}")
            return _build_result(False, "plan", cedula, started_at, steps, stages, pushes, push_to_supabase)

        if do_push:
            from src.storage.writer import push_athlete, push_plan
            try:
                p = json.loads((athlete_dir / "meta" / "profile.json").read_text("utf-8"))
                athlete_name = p.get("name")
            except Exception:
                athlete_name = None
            _try_push(pushes, "athlete", push_athlete, cedula, athlete_name)
            _try_push(pushes, "plan",    push_plan,    cedula, athlete_dir)

    print(f"[sync] Pipeline completo cedula={cedula}")
    return _build_result(True, None, cedula, started_at, steps, stages, pushes, push_to_supabase)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/{cedula}/sync")
def sync_athlete(
    cedula: str,
    steps: Annotated[
        list[str],
        Query(description="Pasos del pipeline. Default: ingest, features, plan.")
    ] = ["ingest", "features", "plan"],
    skip_strava: bool = Query(default=False, description="Omitir sync de Strava (default: False)"),
    push:        bool = Query(default=True,  description="Hacer push a Supabase después de cada paso"),
    _: None = Depends(require_api_key),
):
    """
    Ejecuta el pipeline para un atleta y hace push a Supabase. Respuesta síncrona.

    Fase B: push incremental por paso — datos en Supabase aunque un paso posterior falle.
    El campo `stages` en la respuesta indica el estado de cada paso individual.
    """
    invalid = [s for s in steps if s not in VALID_STEPS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Steps inválidos: {invalid}. Válidos: {VALID_STEPS}",
        )

    result = _run_pipeline_and_push(cedula, list(steps), skip_strava, push)
    _sync_status[cedula] = result
    return result


@router.post("/{cedula}/sync/push")
def push_to_supabase(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Push inmediato de los datos locales existentes a Supabase.
    No re-ejecuta el pipeline. Útil para re-sincronizar sin re-procesar.
    """
    if not is_configured():
        return {
            "ok":     False,
            "detail": "Supabase no configurado. Agrega SUPABASE_URL y SUPABASE_SERVICE_KEY al .env.",
        }

    athlete_dir = get_data_dir() / cedula
    if not athlete_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Atleta '{cedula}' no encontrado. Ejecuta primero POST /athletes/{cedula}/sync",
        )

    try:
        from src.storage.writer import push_all
        result = push_all(cedula, athlete_dir)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{cedula}/sync/status")
def get_sync_status(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """Estado del último sync para un atleta."""
    status = _sync_status.get(cedula)
    if not status:
        return {
            "cedula":              cedula,
            "status":              "unknown",
            "detail":              "No se ha iniciado ningún sync en esta sesión.",
            "supabase_configured": is_configured(),
        }
    return {"cedula": cedula, **status}
