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
logger.info("GOOGLE_SA_JSON path=%s", os.getenv("GOOGLE_SA_JSON", "secrets/google_service_account.json"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import athletes, health, pipeline, coach, sync

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
