"""Inspecciona el profile completo de los 2 atletas sin age."""
import os, json
from dotenv import load_dotenv
from supabase import create_client

os.chdir(r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching")
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

CEDULAS = ['1128276790', '4576469']

for ced in CEDULAS:
    print(f"\n{'='*60}\nCEDULA: {ced}")
    print('='*60)
    p = sb.table('athlete_profiles').select('*').eq('cedula', ced).execute().data
    if not p:
        print("  Sin profile en Supabase")
        continue
    row = p[0]
    raw = row.get('raw') or {}
    print(f"  age: {raw.get('age')!r}")
    print(f"  sex: {raw.get('sex')!r} | sex_bin: {raw.get('sex_bin')!r} | gender: {raw.get('gender')!r}")
    print(f"  birth_year: {raw.get('birth_year')!r} | birth_date: {raw.get('birth_date')!r}")
    print(f"  pr_5k_sec: {raw.get('pr_5k_sec')!r} | pr_10k: {raw.get('pr_10k_sec')!r}")
    print(f"  fcmax: {raw.get('fcmax')!r}")
    print(f"\n  Top-level columns in athlete_profiles:")
    for k, v in row.items():
        if k != 'raw':
            print(f"    {k}: {v!r}")
    print(f"\n  Claves disponibles en raw ({len(raw)}):")
    print(f"    {sorted(raw.keys())}")
