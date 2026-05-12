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
    Railway tiene disco efímero: cada deploy borra archivos locales (parquets).
    Los datos viven en Supabase. Al arrancar, reconstruimos el parquet local
    de cada atleta directamente desde la tabla activities de Supabase
    (sin llamar a Strava — ahorra rate limit) y luego corremos features+plan
    para regenerar weekly_features y el snapshot.
    """
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
            logger.info("[startup] Bootstrap-only rehydration for %d athletes...", len(cedulas))

            from src.storage.bootstrap import bootstrap_parquet_from_supabase

            # Solo reconstruimos el parquet local desde Supabase (lectura pura, sin subprocesos).
            # NO corremos features/plan aquí: esos pasos spawnan subprocesos Python que consumen
            # ~300 MB cada uno. Con 90+ atletas el servidor se queda sin memoria (OOM en Railway).
            # Los datos de snapshot/plan/features SE LEEN DESDE SUPABASE directamente en los
            # endpoints GET, así que el dashboard funciona aunque no haya parquet local.
            # El parquet local solo es necesario para los pipeline steps que lo requieren
            # (features, plan) — esos se disparan on-demand vía POST /athletes/{cedula}/pipeline.
            ok = err = 0
            for ced in cedulas:
                try:
                    n = bootstrap_parquet_from_supabase(ced)
                    if n > 0:
                        logger.info("[startup] Bootstrap %s: %d activities", ced, n)
                    ok += 1
                except Exception as exc:
                    logger.warning("[startup] Bootstrap failed for %s: %s", ced, exc)
                    err += 1

            logger.info("[startup] Bootstrap complete: %d OK / %d errores", ok, err)
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


# ─── Cache-Control para assets estáticos ─────────────────────────────────────
# Evita que el navegador sirva HTML/JS/CSS viejo después de un deploy.
# Estrategia: el navegador SÍ cachea, pero SIEMPRE revalida con el servidor
# (304 Not Modified si no cambió, 200 con archivo nuevo si cambió).
# Resultado: cargas instantáneas si no cambió, sin cache hell tras un deploy.

@app.middleware("http")
async def no_stale_cache_for_frontend(request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Aplicar a todo lo servido bajo /app (HTML, JS, CSS, JSON, etc.)
    if path.startswith("/app"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ─── Frontend estático ────────────────────────────────────────────────────────
# Montado AL FINAL para que los routers de la API tengan prioridad.
# Acceso: https://<dominio>/app  (o http://localhost:8000/app en dev)

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
