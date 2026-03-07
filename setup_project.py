#!/usr/bin/env python3
"""
setup_project.py — Configura el proyecto la primera vez.

Uso:
    python setup_project.py

Hace:
  1. Verifica que .env existe y tiene las variables necesarias
  2. Verifica que secrets/google_service_account.json existe
  3. Verifica conexión con Google Sheets
  4. Verifica conexión con Strava (si hay tokens)
  5. Crea carpetas necesarias
  6. Muestra un resumen del estado
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OK = "✅"
WARN = "⚠️ "
ERR = "❌"


def check(label, condition, fix_msg=""):
    icon = OK if condition else ERR
    print(f"  {icon}  {label}")
    if not condition and fix_msg:
        print(f"       → {fix_msg}")
    return condition


def main():
    print("\n🏃  Running Coaching — Setup Check\n" + "─" * 45)
    all_ok = True

    # ── .env ──
    print("\n📋  Variables de entorno:")
    required_vars = ["SHEET_ID", "GOOGLE_SA_JSON", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"]
    for var in required_vars:
        val = os.getenv(var)
        ok = bool(val and val != f"TU_{var}_AQUI")
        all_ok &= check(var, ok, f"Configura {var} en tu archivo .env")

    # ── Service Account ──
    print("\n🔑  Google Credentials:")
    sa_path = Path(os.getenv("GOOGLE_SA_JSON", "secrets/google_service_account.json"))
    sa_exists = sa_path.exists()
    all_ok &= check(f"{sa_path}", sa_exists, f"Coloca el service_account.json en {sa_path.parent}/")

    if sa_exists:
        try:
            sa_data = json.loads(sa_path.read_text())
            check("service_account.json válido", sa_data.get("type") == "service_account")
            check("tiene private_key", bool(sa_data.get("private_key")))
            print(f"       project: {sa_data.get('project_id','?')}")
            print(f"       email:   {sa_data.get('client_email','?')}")
        except Exception as e:
            print(f"  {ERR}  Error leyendo JSON: {e}")
            all_ok = False

    # ── Google Sheets ──
    print("\n📊  Google Sheets:")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        if sa_exists:
            creds = Credentials.from_service_account_file(
                str(sa_path),
                scopes=["https://www.googleapis.com/auth/spreadsheets",
                        "https://www.googleapis.com/auth/drive"]
            )
            gc = gspread.authorize(creds)
            sheet = gc.open_by_key(os.getenv("SHEET_ID"))
            ws_names = [ws.title for ws in sheet.worksheets()]
            check("Conexión OK", True)
            print(f"       Hojas encontradas: {', '.join(ws_names)}")
        else:
            check("Conexión Sheets", False, "Primero configura el service account")
            all_ok = False
    except Exception as e:
        check("Conexión Sheets", False, str(e))
        all_ok = False

    # ── Carpetas ──
    print("\n📁  Carpetas:")
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))
    for folder in [data_dir, Path("secrets"), Path("data")]:
        folder.mkdir(parents=True, exist_ok=True)
        check(str(folder), True)

    # ── Strava ──
    print("\n🚴  Strava:")
    try:
        import requests
        cid = os.getenv("STRAVA_CLIENT_ID")
        check("STRAVA_CLIENT_ID configurado", bool(cid))
    except Exception as e:
        check("Strava config", False, str(e))

    # ── Resumen ──
    print("\n" + "─" * 45)
    if all_ok:
        print(f"{OK}  Todo listo. Puedes correr:\n")
        print("    python run_pipeline.py --all\n")
    else:
        print(f"{ERR}  Hay configuraciones pendientes (ver arriba).\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
