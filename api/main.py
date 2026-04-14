"""
api/main.py — Aplicación FastAPI principal.

Corre con:
    uvicorn api.main:app --reload --port 8000

Desde la raíz del proyecto (donde está run_pipeline.py).
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("api.startup")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=False)

# ─── Logs de arranque (útiles en Railway/Fly, sin ensuciar) ──────────────────
logger.info("cwd=%s", Path.cwd())
logger.info("env_file=%s exists=%s", ENV_FILE, ENV_FILE.exists())
logger.info("SUPABASE_URL loaded=%s", bool(os.getenv("SUPABASE_URL", "").strip()))
logger.info("SUPABASE_KEY loaded=%s", bool(os.getenv("SUPABASE_SERVICE_KEY", "").strip()))
logger.info("STRAVA_CLIENT_ID loaded=%s", bool(os.getenv("STRAVA_CLIENT_ID", "").strip()))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import athletes, health, pipeline, coach, sync, webhooks

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Running Coaching API",
    description=(
        "Backend API del sistema de running coaching. "
        "Expone los datos del pipeline (ETL + features + plan) como endpoints REST."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
# Dev:  ALLOWED_ORIGINS vacío → permite cualquier origen (["*"])
# Prod: ALLOWED_ORIGINS=https://app.arathleteslab.com,https://otra-url.com

_raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
_allowed_origins: list[str] = _raw_origins.split(",") if _raw_origins else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(athletes.router, prefix="/athletes", tags=["athletes"])
app.include_router(pipeline.router, prefix="/athletes", tags=["pipeline"])
app.include_router(sync.router,     prefix="/athletes", tags=["sync"])
app.include_router(coach.router,    prefix="/athletes", tags=["coach"])
app.include_router(webhooks.router,                     tags=["webhooks"])


# ─── Startup: re-hidratar datos desde Supabase después de cada deploy ─────────

@app.on_event("startup")
async def _startup_rehydrate():
    """
    Railway tiene disco efímero: cada deploy borra archivos locales.
    Los datos viven en Supabase, pero el pipeline genera archivos locales
    que algunos pasos necesitan. Al arrancar, disparamos pipelines para
    todos los atletas registrados en background.
    """
    import asyncio
    import threading

    def _run_all_pipelines():
        import time
        time.sleep(5)  # esperar a que FastAPI termine de arrancar
        try:
            from src.storage.supabase_client import get_client
            client = get_client()
            if not client:
                logger.warning("[startup] No Supabase client — skipping rehydration")
                return
            res = client.table("athletes").select("cedula").execute()
            cedulas = [r["cedula"] for r in (res.data or []) if r.get("cedula")]
            logger.info("[startup] Rehydrating %d athletes...", len(cedulas))

            for ced in cedulas:
                try:
                    from api.routers.pipeline import _run_pipeline_subprocess
                    _run_pipeline_subprocess(ced, ["ingest", "features", "plan"], skip_strava=True)
                    logger.info("[startup] Pipeline OK: %s", ced)
                except Exception as exc:
                    logger.warning("[startup] Pipeline failed for %s: %s", ced, exc)

            logger.info("[startup] Rehydration complete for %d athletes", len(cedulas))
        except Exception as exc:
            logger.error("[startup] Rehydration error: %s", exc)

    # Correr en thread separado para no bloquear el servidor
    threading.Thread(target=_run_all_pipelines, daemon=True).start()


# ─── Root ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["root"])
def root():
    return {
        "name": "Running Coaching API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "app": "/app",
    }


# ─── Frontend estático ────────────────────────────────────────────────────────
# Montado AL FINAL para que los routers de la API tengan prioridad.
# Acceso: https://<dominio>/app  (o http://localhost:8000/app en dev)

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
