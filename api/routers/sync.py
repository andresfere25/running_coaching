"""
api/routers/sync.py — Endpoint de sincronización a Supabase.

Fase A: Corre el pipeline localmente y luego hace dual-write a Supabase.
Fase B (futura): El pipeline escribe directamente a Supabase; data/ local queda como caché.

Endpoints:
  POST /athletes/{cedula}/sync       → pipeline + push a Supabase (síncrono, devuelve resultado)
  POST /athletes/{cedula}/sync/push  → solo push (asume pipeline ya corrió)
  GET  /athletes/{cedula}/sync/status → estado del último sync
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_data_dir, require_api_key
from src.storage.supabase_client import is_configured

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
VALID_STEPS  = ["ingest", "strava", "features", "plan"]

# Estado en memoria del último sync por cédula (simple, sin persistencia).
# En Fase B esto vendrá de Supabase.
_sync_status: dict[str, dict] = {}


# ─── Helper central ──────────────────────────────────────────────────────────

def _run_pipeline_and_push(
    cedula: str,
    steps: list[str],
    skip_strava: bool,
    push_to_supabase: bool,
) -> dict:
    """
    Ejecuta pipeline + push Supabase de forma síncrona.
    Retorna dict con ok, status, pipeline_ok, supabase.
    """
    started_at = datetime.now().isoformat(timespec="seconds")

    # ── Paso 1: pipeline ───────────────────────────────────────────────────────
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--cedula", cedula,
        "--steps", *steps,
    ]
    if skip_strava:
        cmd.append("--skip-strava")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"[sync] Pipeline iniciando para cedula={cedula} steps={steps}")
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=False,
        text=True,
        env=env,
    )

    pipeline_ok = proc.returncode == 0
    if not pipeline_ok:
        print(f"[sync] Pipeline FALLÓ para cedula={cedula} (exit={proc.returncode})")
        return {
            "ok":           False,
            "cedula":       cedula,
            "status":       "error",
            "stage_failed": "pipeline",
            "pipeline_ok":  False,
            "started_at":   started_at,
            "finished_at":  datetime.now().isoformat(timespec="seconds"),
            "pipeline_steps": steps,
            "supabase":     None,
        }

    print(f"[sync] Pipeline OK para cedula={cedula}")

    # ── Paso 2: push a Supabase ────────────────────────────────────────────────
    supabase_result: dict = {"ok": True, "detail": "skipped (not requested)"}

    if push_to_supabase:
        if not is_configured():
            supabase_result = {"ok": True, "detail": "skipped (SUPABASE_URL not set)"}
        else:
            try:
                from src.storage.writer import push_all
                athlete_dir = get_data_dir() / cedula
                supabase_result = push_all(cedula, athlete_dir)
                if supabase_result.get("ok"):
                    print(f"[sync] Supabase push OK para cedula={cedula}")
                else:
                    print(f"[sync] Supabase push parcial para cedula={cedula}: {supabase_result.get('errors')}")
            except Exception as exc:
                supabase_result = {"ok": False, "detail": str(exc)}
                print(f"[sync] Supabase push ERROR para cedula={cedula}: {exc}")

    push_ok = supabase_result.get("ok", True)
    result = {
        "ok":             push_ok,
        "cedula":         cedula,
        "status":         "ok" if push_ok else "partial",
        "stage_failed":   None if push_ok else "push",
        "pipeline_ok":    True,
        "started_at":     started_at,
        "finished_at":    datetime.now().isoformat(timespec="seconds"),
        "pipeline_steps": steps,
        "supabase":       supabase_result,
    }
    return result


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/{cedula}/sync")
def sync_athlete(
    cedula: str,
    steps: Annotated[
        list[str],
        Query(description="Pasos del pipeline. Default: ingest, features, plan.")
    ] = ["ingest", "features", "plan"],
    skip_strava: bool = Query(default=True,  description="Omitir sync de Strava (default: True en sync)"),
    push:        bool = Query(default=True,  description="Hacer push a Supabase después del pipeline"),
    _: None = Depends(require_api_key),
):
    """
    Ejecuta el pipeline para un atleta y hace push a Supabase. Respuesta síncrona.

    Espera ~30-90s mientras corre el pipeline y el push.
    Retorna el resultado consolidado directamente (no hace polling).

    - Si el pipeline falla → ok=false, stage_failed="pipeline", sin push.
    - Si el pipeline OK pero push parcial → ok=false, stage_failed="push", detalle por tabla.
    - Si todo OK → ok=true, status="ok".

    Diferencia vs /pipeline:
    - /pipeline: solo corre el ETL local, sin Supabase, responde inmediatamente.
    - /sync: corre el ETL + hace push a Supabase, espera y devuelve resultado.
    """
    invalid = [s for s in steps if s not in VALID_STEPS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Steps inválidos: {invalid}. Válidos: {VALID_STEPS}",
        )

    result = _run_pipeline_and_push(cedula, list(steps), skip_strava, push)

    # Guardar en _sync_status para que GET /sync/status también lo refleje
    _sync_status[cedula] = result

    return result


@router.post("/{cedula}/sync/push")
def push_to_supabase(
    cedula: str,
    _: None = Depends(require_api_key),
):
    """
    Push inmediato (síncrono) de los datos locales existentes a Supabase.
    Asume que el pipeline ya corrió. No re-ejecuta el pipeline.

    Útil para: sincronizar datos ya generados localmente sin re-correr el ETL.
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
            detail=f"Atleta '{cedula}' no encontrado en data/athletes/. Ejecuta primero POST /athletes/{cedula}/sync",
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
    """
    Estado del último sync para un atleta.
    Estados: ok / partial / error
    """
    status = _sync_status.get(cedula)
    if not status:
        return {
            "cedula":  cedula,
            "status":  "unknown",
            "detail":  "No se ha iniciado ningún sync para este atleta en esta sesión.",
            "supabase_configured": is_configured(),
        }
    return {"cedula": cedula, **status}
