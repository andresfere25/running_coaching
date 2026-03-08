"""
api/deps.py — Dependencias compartidas entre routers.

Incluye:
- Resolución del directorio de datos del atleta
- Autenticación por API key (opcional en desarrollo)
"""

import math
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader


# ─── API Key (opcional) ──────────────────────────────────────────────────────
# Si API_KEY no está configurada en el entorno, el auth se omite (modo dev).
# En producción: exportar API_KEY=<valor-secreto> antes de levantar el servidor.

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    configured = os.getenv("API_KEY")
    if configured and api_key != configured:
        raise HTTPException(
            status_code=401,
            detail="API key ausente o inválida. Usa el header X-API-Key.",
        )


# ─── Directorio del atleta ───────────────────────────────────────────────────

def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data/athletes"))


def sanitize_json(obj: Any) -> Any:
    """
    Reemplaza float NaN e Inf con None para cumplir con JSON estándar.
    Pandas produce NaN al convertir DataFrames a dict; Python los serializa
    como literales 'NaN' (inválidos en JSON). FastAPI los rechaza al responder.
    """
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json(v) for v in obj]
    return obj


def get_athlete_dir(cedula: str) -> Path:
    """
    Dependency que valida que el atleta existe en data/athletes/{cedula}/.
    FastAPI inyecta `cedula` desde el path parameter automáticamente.
    """
    athlete_dir = get_data_dir() / cedula
    if not athlete_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Atleta '{cedula}' no encontrado en data/athletes/. "
                "Ejecuta primero: POST /athletes/{cedula}/pipeline"
            ),
        )
    return athlete_dir
