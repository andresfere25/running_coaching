"""
src/storage/supabase_client.py — Cliente Supabase singleton.

Uso:
    from src.storage.supabase_client import get_client
    client = get_client()   # None si SUPABASE_URL / SUPABASE_SERVICE_KEY no están en .env
    if client:
        client.table("athletes").upsert({...}).execute()

Requiere en .env:
    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_SERVICE_KEY=<service_role_key>   # no la anon key

Instalar SDK:
    pip install supabase>=2.0.0
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from supabase import Client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

_client: "Client | None" = None
_env_loaded = False


def _ensure_env_loaded() -> None:
    global _env_loaded
    if not _env_loaded:
        load_dotenv(dotenv_path=ENV_FILE, override=False)
        _env_loaded = True


def get_client() -> "Client | None":
    """
    Retorna el cliente Supabase o None si no está configurado / SDK no instalado.
    Singleton: la conexión se crea una sola vez por proceso.
    No memoriza un "miss" — si las vars no estaban listas en el primer intento,
    el siguiente intento volverá a intentar crear el cliente.
    """
    global _client

    if _client is not None:
        return _client

    _ensure_env_loaded()

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        print(
            "[storage] Supabase no configurado: "
            f"SUPABASE_URL={bool(url)} SUPABASE_SERVICE_KEY={bool(key)}"
        )
        return None

    try:
        from supabase import create_client  # type: ignore[import]
        _client = create_client(url, key)
        print("[storage] Cliente Supabase inicializado correctamente.")
        return _client
    except ImportError:
        print(
            "[storage] supabase package no instalado. "
            'Para activar dual-write: pip install "supabase>=2.0.0"'
        )
        return None
    except Exception as exc:
        print(f"[storage] Error inicializando cliente Supabase: {exc}")
        return None


def is_configured() -> bool:
    """True si SUPABASE_URL y SUPABASE_SERVICE_KEY están en el entorno."""
    _ensure_env_loaded()
    return bool(
        os.getenv("SUPABASE_URL", "").strip()
        and os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )
