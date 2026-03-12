"""
src/storage/reader.py — Lectura híbrida: Supabase primero, fallback local.

Para cada tipo de dato:
1. Intenta leer desde Supabase (si está configurado y hay datos).
2. Si Supabase no tiene la fila (o no está configurado), lee desde archivo local.
3. Si tampoco hay datos locales, retorna None.

Funciones públicas:
  read_snapshot(cedula, athlete_dir)        → dict | None
  read_plan(cedula, athlete_dir)            → dict | None
  read_features(cedula, athlete_dir, weeks) → list[dict] | None
  read_checkin(cedula, athlete_dir)         → dict | None
"""

import json
from pathlib import Path

from src.storage.supabase_client import get_client


# ─── snapshot ────────────────────────────────────────────────────────────────

def read_snapshot(cedula: str, athlete_dir: Path | None) -> dict | None:
    """
    Lee athlete_snapshot.json.
    Fuente 1: Supabase athlete_snapshots.raw
    Fuente 2: features/athlete_snapshot.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("athlete_snapshots")
                .select("raw")
                .eq("cedula", cedula)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["raw"]
        except Exception as exc:
            print(f"[reader] Supabase snapshot error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "athlete_snapshot.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None


# ─── plan ─────────────────────────────────────────────────────────────────────

def read_plan(cedula: str, athlete_dir: Path | None) -> dict | None:
    """
    Lee weekly_plan.json.
    Fuente 1: Supabase weekly_plans.plan_json (más reciente)
    Fuente 2: features/weekly_plan.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("weekly_plans")
                .select("plan_json")
                .eq("cedula", cedula)
                .order("week_start", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["plan_json"]
        except Exception as exc:
            print(f"[reader] Supabase plan error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "weekly_plan.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None


# ─── features ────────────────────────────────────────────────────────────────

def read_features(cedula: str, athlete_dir: Path | None, weeks: int = 12) -> list[dict] | None:
    """
    Lee historial de weekly_features.
    Fuente 1: Supabase weekly_features.raw (últimas N semanas, orden ascendente)
    Fuente 2: features/weekly_features.parquet
    Retorna lista de dicts compatible con df.to_dict(orient='records').
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("weekly_features")
                .select("raw, week_start")
                .eq("cedula", cedula)
                .order("week_start", desc=True)
                .limit(weeks)
                .execute()
            )
            if res.data:
                # Invertir para orden ascendente (más antiguo primero)
                return [r["raw"] for r in reversed(res.data)]
        except Exception as exc:
            print(f"[reader] Supabase features error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "features" / "weekly_features.parquet"
        if path.exists():
            import duckdb
            df = duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()
            df = df.sort_values("week_start").tail(weeks).reset_index(drop=True)
            return df.to_dict(orient="records")

    return None


# ─── checkin ─────────────────────────────────────────────────────────────────

def read_checkin(cedula: str, athlete_dir: Path | None) -> dict | None:
    """
    Lee latest_checkin.json.
    Fuente 1: Supabase checkins.raw (más reciente por checkin_date)
    Fuente 2: meta/latest_checkin.json
    """
    client = get_client()
    if client:
        try:
            res = (
                client.table("checkins")
                .select("raw")
                .eq("cedula", cedula)
                .order("checkin_date", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["raw"]
        except Exception as exc:
            print(f"[reader] Supabase checkin error para {cedula}: {exc}")

    if athlete_dir:
        path = athlete_dir / "meta" / "latest_checkin.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None
