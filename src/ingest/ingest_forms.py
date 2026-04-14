"""
src/ingest/ingest_forms.py — Ingesta de perfiles de atletas desde Supabase.

Migrado de Google Sheets a Supabase (2026-04-14).
Los atletas se registran via el portal (app.arathleteslab.com → D1 → Supabase).
El pipeline lee perfiles de Supabase y escribe profile.json local.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv


def ensure_dirs(base_dir: Path, cedula: str) -> dict:
    athlete_dir = base_dir / str(cedula)
    paths = {
        "athlete_dir": athlete_dir,
        "raw": athlete_dir / "raw",
        "silver": athlete_dir / "silver",
        "meta": athlete_dir / "meta",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def _create_minimal_profile_from_supabase(cedula: str, data_dir: Path) -> bool:
    """
    Fallback para atletas sin perfil completo en athlete_profiles.
    Lee datos minimos desde Supabase tabla `athletes` y crea profile.json.
    Retorna True si se creo el perfil, False si no hay datos.
    """
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if not client:
            return False

        res = (
            client.table("athletes")
            .select("cedula, name, strava_athlete_id")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        )
        if not res.data:
            return False

        row = res.data[0]
        profile = {
            "cedula": cedula,
            "name": row.get("name") or f"Atleta {cedula}",
            "source": "portal",
            "race_history": [],
        }

        # Escribir profile.json
        paths = ensure_dirs(data_dir, cedula)
        profile_json_path = paths["meta"] / "profile.json"
        profile_json_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Push a Supabase athlete_profiles para que read_profile lo encuentre
        try:
            from src.storage.writer import push_profile
            push_profile(cedula, paths["athlete_dir"])
        except Exception:
            pass  # No critico, el archivo local ya existe

        print(f"[ingest] Perfil minimo creado para {cedula} (fuente: portal)")
        return True

    except Exception as exc:
        print(f"[ingest] Fallback Supabase fallo para {cedula}: {exc}")
        return False


def _ingest_single_profile(cedula: str, data_dir: Path, client) -> None:
    """Ingesta de un solo perfil desde Supabase."""
    # 1. Intentar leer perfil completo de athlete_profiles
    try:
        res = (
            client.table("athlete_profiles")
            .select("raw")
            .eq("cedula", cedula)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("raw"):
            profile = res.data[0]["raw"]
            paths = ensure_dirs(data_dir, cedula)
            profile_json_path = paths["meta"] / "profile.json"
            profile_json_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[ingest] Perfil OK para {cedula} (fuente: athlete_profiles)")
            return
    except Exception as exc:
        print(f"[ingest] Error leyendo athlete_profiles para {cedula}: {exc}")

    # 2. Fallback: crear perfil minimo desde tabla athletes
    created = _create_minimal_profile_from_supabase(cedula, data_dir)
    if created:
        print(f"[ingest] Perfil minimo creado para {cedula} (fuente: athletes)")
    else:
        print(f"[ingest] No se pudo crear perfil para {cedula}")


def main(cedula: str | None = None):
    """
    Ingesta de perfiles de atletas desde Supabase.

    Lee athlete_profiles desde Supabase y escribe profile.json local.
    Si el atleta no tiene perfil en athlete_profiles, crea uno minimo
    desde la tabla athletes (datos del portal).

    cedula (opcional):
      - Si se provee: procesa solo ese atleta.
      - Si es None:   procesa todos los atletas registrados.
    """
    load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))

    try:
        from src.storage.supabase_client import get_client
        client = get_client()
    except Exception:
        client = None

    if not client:
        print("[ingest] Supabase no configurado. No se puede hacer ingesta de perfiles.")
        return

    if cedula:
        _ingest_single_profile(cedula, data_dir, client)
    else:
        try:
            res = client.table("athletes").select("cedula").order("cedula").execute()
            cedulas = [r["cedula"] for r in res.data if r.get("cedula")]
        except Exception as exc:
            print(f"[ingest] Error listando atletas: {exc}")
            return

        for ced in cedulas:
            try:
                _ingest_single_profile(ced, data_dir, client)
            except Exception as exc:
                print(f"[ingest] Error procesando {ced}: {exc}")

        print(f"[ingest] Perfiles procesados: {len(cedulas)} atletas.")


if __name__ == "__main__":
    main()
