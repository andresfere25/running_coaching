import os
from dotenv import load_dotenv
from supabase import create_client

os.chdir(r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching")
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

CEDULAS = ['1128276790', '4576469']
for ced in CEDULAS:
    p = sb.table('athlete_profiles').select('raw').eq('cedula', ced).execute().data
    if p:
        raw = p[0].get('raw') or {}
        print(f"\n{ced}: source={raw.get('source')!r}")
        print(f"  name={raw.get('name')!r}")
        print(f"  race_history keys: {list((raw.get('race_history') or {}).keys()) if isinstance(raw.get('race_history'), dict) else type(raw.get('race_history'))}")

# También verifica athletes table
print("\n--- athletes table ---")
for ced in CEDULAS:
    a = sb.table('athletes').select('*').eq('cedula', ced).execute().data
    if a:
        print(f"\n{ced}:")
        for k, v in a[0].items():
            if v is not None and k not in ['strava_refresh_token', 'strava_access_token']:
                print(f"  {k}: {v!r}")
