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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

_client: "Client | None" = None
_checked = False   # evita reintentar si ya sabemos que no está configurado


def get_client() -> "Client | None":
    """
    Retorna el cliente Supabase o None si no está configurado / SDK no instalado.
    Singleton: la conexión se crea una sola vez por proceso.
    """
    global _client, _checked

    if _checked:
        return _client

    _checked = True

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        return None

    try:
        from supabase import create_client  # type: ignore[import]
        _client = create_client(url, key)
        return _client
    except ImportError:
        print(
            "[storage] supabase package no instalado. "
            "Para activar dual-write: pip install supabase>=2.0.0"
        )
        return None
    except Exception as exc:
        print(f"[storage] Error inicializando cliente Supabase: {exc}")
        return None


def is_configured() -> bool:
    """True si SUPABASE_URL y SUPABASE_SERVICE_KEY están en el entorno."""
    return bool(
        os.getenv("SUPABASE_URL", "").strip()
        and os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )
