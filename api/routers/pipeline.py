"""
api/routers/pipeline.py — Endpoint para disparar el pipeline por cédula.

El pipeline corre en background (BackgroundTasks de FastAPI).
El endpoint responde inmediatamente con status "queued".
Los resultados estarán disponibles en GET /athletes/{cedula}/snapshot
una vez que el pipeline termine (~30-90 segundos según pasos).

Endpoints:
  POST /athletes/{cedula}/pipeline   → dispara pipeline en background
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from api.deps import require_api_key

router = APIRouter()

# Pasos válidos del pipeline (mismo orden que run_pipeline.py)
VALID_STEPS = ["ingest", "strava", "features", "plan", "pdf"]

# Raíz del proyecto (api/ está un nivel abajo del root)
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _run_pipeline_subprocess(cedula: str, steps: list[str], skip_strava: bool) -> None:
    """
    Corre run_pipeline.py como subproceso desde la raíz del proyecto.
    Diseñado para ser llamado desde BackgroundTasks.
    Los logs van a stdout/stderr del proceso del servidor.
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
