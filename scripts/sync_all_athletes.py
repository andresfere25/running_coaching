"""
sync_all_athletes.py — Sincroniza todos los atletas activos via Railway API.

Uso:
    python scripts/sync_all_athletes.py                    # todos los atletas
    python scripts/sync_all_athletes.py --cedula 1070982737 # una cédula

Variables de entorno (opcionales):
    RAILWAY_URL   Default: https://runningcoaching-production.up.railway.app
    API_KEY       Si está configurado en Railway
"""

import os
import sys
import time
import argparse
import urllib.request
import urllib.error
import json

BASE_URL = (
    os.getenv("RAILWAY_URL") or "https://runningcoaching-production.up.railway.app"
).rstrip("/")

API_KEY = os.getenv("API_KEY", "")


def _headers():
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _request(method: str, path: str) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method, headers=_headers())
    if method == "POST":
        req.data = b"{}"
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode())
        except Exception:
            pass
        return e.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


def get_athletes() -> list[str]:
    print(f"Descubriendo atletas desde {BASE_URL}/athletes ...")
    status, data = _request("GET", "/athletes")
    if status != 200:
        print(f"  ERROR al obtener atletas: HTTP {status} — {data}")
        sys.exit(1)
    athletes = data.get("athletes", [])
    print(f"  Total: {len(athletes)} atletas")
    return athletes


def sync_athlete(cedula: str) -> bool:
    path = (
        f"/athletes/{cedula}/sync"
        "?steps=ingest&steps=strava&steps=features&steps=plan&push_to_supabase=true"
    )
    status, data = _request("POST", path)
    if status == 200 and data.get("ok"):
        stages = data.get("stages", {})
        sb = (data.get("supabase") or {})
        print(f"  ✅ OK  stages={stages}  supabase_ok={sb.get('ok')}")
        return True
    else:
        print(f"  ⚠️  HTTP {status}  detail={data.get('detail') or data.get('error', '')}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cedula", default="", help="Cédula específica (omitir = todos)")
    args = parser.parse_args()

    if args.cedula:
        athletes = [args.cedula]
        print(f"Modo manual: cedula={args.cedula}")
    else:
        athletes = get_athletes()

    if not athletes:
        print("Sin atletas. Nada que hacer.")
        sys.exit(0)

    success = 0
    failed  = 0
    total   = len(athletes)

    for i, cedula in enumerate(athletes, 1):
        print(f"\n[{i}/{total}] cedula={cedula}")
        ok = sync_athlete(cedula)
        if ok:
            success += 1
        else:
            failed += 1
        if i < total:
            time.sleep(3)   # pausa entre atletas — no saturar API de Strava

    print(f"\n{'='*40}")
    print(f"Sync completo")
    print(f"  ✅ Exitosos  : {success}/{total}")
    print(f"  ⚠️  Con error : {failed}/{total}")
    print(f"{'='*40}")

    # Falla solo si ninguno se pudo sincronizar
    if success == 0 and total > 0:
        print("ERROR: ningún atleta sincronizado correctamente")
        sys.exit(1)


if __name__ == "__main__":
    main()
