"""
api/routers/sync.py — Endpoint de sincronización a Supabase.

Fase A: Corre el pipeline localmente y luego hace dual-write a Supabase.
Fase B (futura): El pipeline escribe directamente a Supabase; data/ local queda como caché.

Endpoints:
  POST /athletes/{cedula}/sync       → pipeline + push a Supabase
  POST /athletes/{cedula}/sync/push  → solo push (asume pipeline ya corrió)
  GET  /athletes/{cedula}/sync/status → estado del último sync
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from api.deps import get_data_dir, require_api_key
from src.storage.supabase_client import is_configured

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
VALID_STEPS  = ["ingest", "strava", "features", "plan"]

# Estado en memoria del último sync por cédula (simple, sin persistencia).
# En Fase B esto vendrá de Supabase.
_sync_status: dict[str, dict] = {}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _athlete_dir(cedula: str) -> Path:
    return get_data_dir() / cedula


def _run_pipeline_and_push(
    cedula: str,
    steps: list[str],
    skip_strava: bool,
    push_to_supabase: bool,
) -> None:
    """
    Tarea de background:
    1. Corre el pipeline como subproceso.
    2. Si push_to_supabase=True y Supabase está configurado → push_all().
    Actualiza _sync_status[cedula] con el resultado.
    """
    global _sync_status
    started_at = datetime.now().isoformat(timespec="seconds")
    _sync_status[cedula] = {"status": "running", "started_at": started_at}

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
        _sync_status[cedula] = {
            "status":     "error",
            "stage":      "pipeline",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        return

    print(f"[sync] Pipeline OK para cedula={cedula}")

    # ── Paso 2: push a Supabase ────────────────────────────────────────────────
    supabase_result: dict = {"detail": "skipped (not requested)"}

    if push_to_supabase:
        if not is_configured():
            supabase_result = {"ok": True, "detail": "skipped (SUPABASE_URL not set)"}
        else:
            try:
                from src.storage.writer import push_all
                athlete_dir = _athlete_dir(cedula)
                supabase_result = push_all(cedula, athlete_dir)
                if supabase_result.get("ok"):
                    print(f"[sync] Supabase push OK para cedula={cedula}")
                else:
                    print(f"[sync] Supabase push parcial para cedula={cedula}: {supabase_result.get('errors')}")
            except Exception as exc:
                supabase_result = {"ok": False, "detail": str(exc)}
                print(f"[sync] Supabase push ERROR para cedula={cedula}: {exc}")

    _sync_status[cedula] = {
        "status":         "ok" if supabase_result.get("ok", True) else "partial",
        "started_at":     started_at,
        "finished_at":    datetime.now().isoformat(timespec="seconds"),
        "pipeline_steps": steps,
        "supabase":       supabase_result,
    }


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/{cedula}/sync")
def sync_athlete(
    cedula: str,
    background_tasks: BackgroundTasks,
    steps: Annotated[
        list[str],
        Query(description="Pasos del pipeline. Default: ingest, features, plan.")
    ] = ["ingest", "features", "plan"],
    skip_strava: bool = Query(default=True,  description="Omitir sync de Strava (default: True en sync)"),
    push:        bool = Query(default=True,  description="Hacer push a Supabase después del pipeline"),
    _: None = Depends(require_api_key),
):
    """
    Dispara el pipeline para un atleta y (opcionalmente) hace push a Supabase.

    Fase A: pipeline local → push a Supabase (dual write).
    Fase B: pipeline escribirá directo a Supabase.

    - Responde inmediatamente (pipeline corre en background).
    - Para ver resultados: GET /athletes/{cedula}/sync/status  (después de ~30-90s).
    - Luego: GET /athletes/{cedula}/snapshot para los datos actualizados.

    Diferencia vs /pipeline:
    - /pipeline: solo corre el ETL local, sin Supabase.
    - /sync: corre el ETL + hace push a Supabase (si está configurado).
    """
    invalid = [s for s in steps if s not in VALID_STEPS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Steps inválidos: {invalid}. Válidos: {VALID_STEPS}",
        )

    background_tasks.add_task(
        _run_pipeline_and_push,
        cedula, list(steps), skip_strava, push,
    )

    return {
        "status":       "queued",
        "cedula":       cedula,
        "steps":        steps,
        "skip_strava":  skip_strava,
        "push_supabase": push and is_configured(),
        "supabase_configured": is_configured(),
        "message": (
            f"Pipeline + sync iniciado en background para {cedula}. "
            f"Consulta GET /athletes/{cedula}/sync/status en ~30-90s."
        ),
    }


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

    athlete_dir = _athlete_dir(cedula)
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
    Estados: queued / running / ok / partial / error
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
