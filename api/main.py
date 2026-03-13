"""
api/main.py — Aplicación FastAPI principal.

Corre con:
    uvicorn api.main:app --reload --port 8000

Desde la raíz del proyecto (donde está run_pipeline.py).
"""

from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=False)

print(f"[api] cwd={Path.cwd()}")
print(f"[api] env_file={ENV_FILE} exists={ENV_FILE.exists()}")
print(f"[api] SUPABASE_URL loaded={bool(os.getenv('SUPABASE_URL', '').strip())}")
print(f"[api] SUPABASE_SERVICE_KEY loaded={bool(os.getenv('SUPABASE_SERVICE_KEY', '').strip())}")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import athletes, health, pipeline, coach, sync

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Running Coaching API",
    description=(
        "Backend API del sistema de running coaching. "
        "Expone los datos del pipeline (ETL + features + plan) como endpoints REST. "
        "Futura base para la app/web de coaching."
    ),
    version="0.1.0",
    docs_url="/docs",      # Swagger UI
    redoc_url="/redoc",    # ReDoc
)

# ─── CORS ────────────────────────────────────────────────────────────────────
# Dev:  ALLOWED_ORIGINS no configurado → permite cualquier origen (["*"])
# Prod: ALLOWED_ORIGINS=https://tudominio.com,https://app.tudominio.com

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
# Acceso: http://localhost:8000/app
# El frontend llama a /athletes/... directamente (mismo origen, sin CORS).

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
