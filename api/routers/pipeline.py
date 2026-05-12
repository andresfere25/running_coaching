"""
api/routers/pipeline.py — Endpoint para disparar el pipeline por cédula.

El pipeline corre en background (BackgroundTasks de FastAPI).
El endpoint responde inmediatamente con status "queued".
Los resultados estarán disponibles en GET /athletes/{cedula}/snapshot
una vez que el pipeline termine (~30-90 segundos según pasos).

Endpoints:
  POST /athletes/{cedula}/pipeline   → dispara pipeline en background (1 atleta)
  POST /pipeline/bulk                → actualiza múltiples atletas en SECUENCIA
                                       sin saturar el servidor (usar en lugar de
                                       disparar N requests independientes)
"""

import os
import sys
import subprocess
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from api.deps import require_api_key

router = APIRouter()

# Pasos válidos del pipeline (mismo orden que run_pipeline.py)
VALID_STEPS = ["ingest", "strava", "features", "plan", "pdf"]

# Raíz del proyecto (api/ está un nivel abajo del root)
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ── Semáforo global: máx 1 pipeline a la vez ────────────────────────────────
# CRÍTICO: Railway free tier (0.5 vCPU compartido) no soporta múltiples
# pipelines paralelos. Con 3 simultáneos, los HTTP requests (/stats, /snapshot,
# dashboard) hacen timeout porque no queda CPU para servirlos.
# Bajado de 3 → 1 hasta migrar a Railway Pro o reducir build_features cost.
_PIPELINE_SEMAPHORE = threading.Semaphore(1)


def _run_pipeline_subprocess(cedula: str, steps: list[str], skip_strava: bool) -> None:
    """
    Corre run_pipeline.py como subproceso desde la raíz del proyecto.
    Diseñado para ser llamado desde BackgroundTasks.
    Los logs van a stdout/stderr del proceso del servidor.
    Usa semáforo global para limitar la concurrencia a 3 pipelines simultáneos.
    """
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--cedula", cedula,
        "--steps", *steps,
    ]
    if skip_strava:
        cmd.append("--skip-strava")

    # Forzar UTF-8 en el subproceso (necesario en Windows donde la consola
    # puede usar cp1252 y no soporta los emojis de los logs del pipeline)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    with _PIPELINE_SEMAPHORE:
        print(f"[pipeline] Iniciando para cedula={cedula} steps={steps}")
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=False,  # logs van al stdout del servidor
            text=True,
            env=env,
        )
    if result.returncode != 0:
        print(f"[pipeline] ERROR para cedula={cedula} (exit code {result.returncode})")
    else:
        print(f"[pipeline] OK para cedula={cedula}")


# ─── Endpoint ────────────────────────────────────────────────────────────────

@router.post("/{cedula}/pipeline")
def trigger_pipeline(
    cedula: str,
    background_tasks: BackgroundTasks,
    steps: Annotated[
        list[str],
        Query(description="Pasos a ejecutar. Default: todos.")
    ] = VALID_STEPS,
    skip_strava: bool = Query(default=False, description="Omitir sync de Strava"),
    _: None = Depends(require_api_key),
):
    """
    Dispara el pipeline para un atleta en background.

    El endpoint responde inmediatamente. El pipeline corre de forma asíncrona.
    Para ver los resultados, consulta GET /athletes/{cedula}/snapshot después
    de ~30-90 segundos (según los pasos elegidos).

    Pasos disponibles: ingest, strava, features, plan, pdf

    Ejemplos:
    - Pipeline completo: POST /athletes/1070982737/pipeline
    - Solo features+plan: POST /athletes/1070982737/pipeline?steps=features&steps=plan
    - Sin Strava: POST /athletes/1070982737/pipeline?skip_strava=true
    """
    invalid = [s for s in steps if s not in VALID_STEPS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Steps inválidos: {invalid}. Válidos: {VALID_STEPS}",
        )

    background_tasks.add_task(_run_pipeline_subprocess, cedula, steps, skip_strava)

    return {
        "status": "queued",
        "cedula": cedula,
        "steps": steps,
        "skip_strava": skip_strava,
        "message": (
            "Pipeline iniciado en background. "
            f"Consulta GET /athletes/{cedula}/snapshot en ~30-90s para ver resultados."
        ),
    }


# ─── Endpoint: resync histórico completo ────────────────────────────────────

@router.post("/{cedula}/resync")
def resync_full_history(
    cedula: str,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_key),
):
    """
    Fuerza un re-sync completo del historial de Strava para el atleta.

    1. Resetea strava_last_sync_at = NULL en Supabase → el sync pide TODO
       el historial desde 2009 (sin límite de 365 días)
    2. Dispara pipeline strava+features+plan en background (~90s)

    Usar cuando:
    - Atleta activó HR en Garmin/Apple Watch → actividades viejas ahora tienen HR
    - Atleta cambió privacidad en Strava → más actividades visibles
    - Dashboard tiene menos datos de los esperados
    - Se quiere recalcular todo desde cero con datos frescos
    """
    from src.storage.supabase_client import get_client
    sb = get_client()
    if sb:
        try:
            sb.table("athletes").update({"strava_last_sync_at": None}).eq("cedula", cedula).execute()
            print(f"[resync] strava_last_sync_at reseteado para cedula={cedula}")
        except Exception as e:
            print(f"[resync] advertencia: no se pudo resetear last_sync_at: {e}")

    background_tasks.add_task(_run_pipeline_subprocess, cedula, ["strava", "features", "plan"], False)

    return {
        "status":  "queued",
        "cedula":  cedula,
        "message": "strava_last_sync_at reseteado. Pipeline strava+features+plan iniciado con historial completo (~90s).",
    }


# ─── Endpoint: bootstrap parquet desde Supabase + recompute features ────────

@router.post("/{cedula}/rehydrate", tags=["pipeline"])
def rehydrate_athlete(
    cedula: str,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_key),
):
    """
    Reconstruye el parquet local del atleta desde Supabase y recomputa
    features+plan. Usar cuando:
    - El parquet local quedó desincronizado tras un restart de Railway
    - Las actividades en Supabase son más completas que el parquet local
    - El dashboard muestra menos datos de los esperados (1 sem vs N)

    No llama a Strava API — solo lee de Supabase. Rápido (~5s).
    """
    from src.storage.bootstrap import bootstrap_parquet_from_supabase

    n = bootstrap_parquet_from_supabase(cedula)
    if n == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No hay actividades en Supabase para cedula={cedula}. Corre primero el step 'strava'.",
        )

    # Recompute features+plan en background
    background_tasks.add_task(_run_pipeline_subprocess, cedula, ["features", "plan"], True)

    return {
        "status": "rehydrated",
        "cedula": cedula,
        "activities_restored": n,
        "message": "Parquet reconstruido. features+plan corriendo en background (~30s).",
    }


# ─── Endpoint: bulk resync historial completo ────────────────────────────────

@router.post("/bulk-resync", tags=["pipeline"])
def bulk_resync_history(
    background_tasks: BackgroundTasks,
    cedulas: list[str] | None = None,
    _: None = Depends(require_api_key),
):
    """
    Re-sync historial completo para múltiples atletas en SECUENCIA ESTRICTA.

    1. Resetea strava_last_sync_at = NULL para cada atleta en Supabase.
       Esto fuerza al sync a pedir historial desde 2009 en lugar de incremental.
    2. Procesa UN atleta a la vez (semáforo=1 para este endpoint) con 5s de
       pausa entre cada uno para respetar el rate limit de Strava
       (100 req / 15 min, 1000 req / día por app).

    Body: lista de cédulas ["1070982737", ...]
    Si no se envía body, usa todos los atletas con strava_refresh_token en Supabase.

    Estimado de tiempo:
      - 44 atletas × ~90s por sync = ~66 minutos total en background
      - Railway NO se satura porque es 1 subproceso a la vez
      - Strava NO se satura porque hay pausa entre atletas

    Responde inmediatamente (pipeline corre en background).
    """
    import time

    from src.storage.supabase_client import get_client

    sb = get_client()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase no disponible")

    # Si no se envían cédulas, obtener todos con token Strava
    if not cedulas:
        res = (
            sb.table("athletes")
            .select("cedula")
            .not_.is_("strava_refresh_token", "null")
            .execute()
        )
        cedulas = [r["cedula"] for r in (res.data or []) if r.get("cedula")]

    if not cedulas:
        raise HTTPException(status_code=404, detail="No hay atletas con token Strava en Supabase.")

    if len(cedulas) > 100:
        raise HTTPException(status_code=422, detail="Máximo 100 cédulas por llamada.")

    # Resetear last_sync_at para todos antes de iniciar
    reset_ok, reset_err = [], []
    for ced in cedulas:
        try:
            sb.table("athletes").update({"strava_last_sync_at": None}).eq("cedula", ced).execute()
            reset_ok.append(ced)
        except Exception as e:
            reset_err.append(ced)
            print(f"[bulk-resync] WARNING: no se pudo resetear last_sync_at para {ced}: {e}")

    print(f"[bulk-resync] strava_last_sync_at reseteado: {len(reset_ok)} OK, {len(reset_err)} errores")

    def _run_all_with_delay():
        ok, err = [], []
        for i, ced in enumerate(cedulas, 1):
            print(f"[bulk-resync] ── Atleta {i}/{len(cedulas)}: {ced} ──")
            try:
                _run_pipeline_subprocess(ced, ["strava", "features", "plan"], False)
                ok.append(ced)
                print(f"[bulk-resync] ✅ {ced} completado ({i}/{len(cedulas)})")
            except Exception as e:
                err.append(ced)
                print(f"[bulk-resync] ❌ {ced} error: {e}")
            # Pausa entre atletas para respetar rate limits de Strava
            if i < len(cedulas):
                print(f"[bulk-resync] ⏸  Pausa 5s antes del siguiente atleta…")
                time.sleep(5)

        print(f"[bulk-resync] ══ COMPLETADO: {len(ok)} OK · {len(err)} errores ══")
        if err:
            print(f"[bulk-resync] Atletas con error: {err}")

    background_tasks.add_task(_run_all_with_delay)

    return {
        "status":          "bulk_resync_queued",
        "total":           len(cedulas),
        "cedulas":         cedulas,
        "reset_ok":        len(reset_ok),
        "reset_err":       len(reset_err),
        "estimated_min":   round(len(cedulas) * 1.5),   # ~90s por atleta
        "message": (
            f"{len(cedulas)} atletas encolados para re-sync histórico completo. "
            f"Procesando 1 a la vez con pausa de 5s entre cada uno. "
            f"Estimado: ~{round(len(cedulas) * 1.5)} minutos en total."
        ),
    }


# ─── Endpoint bulk (secuencial) ──────────────────────────────────────────────

@router.post("/bulk", tags=["pipeline"])
def trigger_bulk_pipeline(
    background_tasks: BackgroundTasks,
    cedulas: list[str],
    steps: Annotated[
        list[str],
        Query(description="Pasos a ejecutar. Default: strava+features+plan.")
    ] = ["strava", "features", "plan"],
    skip_strava: bool = Query(default=False, description="Omitir sync de Strava"),
    _: None = Depends(require_api_key),
):
    """
    Actualiza múltiples atletas en SECUENCIA (uno tras otro) en un único
    BackgroundTask. Usar en lugar de disparar N requests de /pipeline
    independientes para evitar saturar el servidor.

    Body: lista de cédulas  → ["1070982737", "1003567622", ...]
    Responde inmediatamente con la lista encolada.
    Los pipelines corren uno a uno respetando el semáforo global.
    """
    invalid_steps = [s for s in steps if s not in VALID_STEPS]
    if invalid_steps:
        raise HTTPException(
            status_code=422,
            detail=f"Steps inválidos: {invalid_steps}. Válidos: {VALID_STEPS}",
        )
    if not cedulas:
        raise HTTPException(status_code=422, detail="Se requiere al menos una cédula.")
    if len(cedulas) > 100:
        raise HTTPException(status_code=422, detail="Máximo 100 cédulas por llamada.")

    def _run_all_sequentially():
        ok, err = [], []
        for ced in cedulas:
            try:
                _run_pipeline_subprocess(ced, steps, skip_strava)
                ok.append(ced)
            except Exception as e:
                print(f"[pipeline/bulk] ERROR cedula={ced}: {e}")
                err.append(ced)
        print(f"[pipeline/bulk] Completado: {len(ok)} OK, {len(err)} errores")

    background_tasks.add_task(_run_all_sequentially)

    return {
        "status":      "bulk_queued",
        "total":       len(cedulas),
        "cedulas":     cedulas,
        "steps":       steps,
        "skip_strava": skip_strava,
        "message": (
            f"{len(cedulas)} atletas encolados para actualización secuencial. "
            "Los pipelines corren uno a uno. Consulta /snapshot por atleta para ver resultados."
        ),
    }
