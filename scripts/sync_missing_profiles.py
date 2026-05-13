"""Sincroniza profiles de D1 → Supabase para los 2 atletas sin age.
Lee onboarding_data desde dump D1 y hace upsert en athlete_profiles + athletes.
"""
import os, json
from dotenv import load_dotenv
from supabase import create_client

os.chdir(r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching")
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

DUMP = r"C:\Users\andre\AppData\Local\Temp\d1_dump.json"
with open(DUMP) as f:
    data = json.load(f)

for row in data[0]['results']:
    ced = row['ced']
    od  = json.loads(row['onboarding_data']) if row.get('onboarding_data') else {}
    print(f"\n=== {ced} ===")
    
    age = od.get('age')
    sex = od.get('sex')
    # Normalizar sex → sex_bin (Masculino=1, Femenino=0)
    if isinstance(sex, str):
        sex_bin = 1 if sex.lower().startswith('m') else 0
    else:
        sex_bin = None
    
    print(f"  age={age}, sex={sex} (bin={sex_bin})")
    
    # 1) Asegurar fila en athletes
    name = od.get('name') or row.get('name')
    sb.table('athletes').upsert({
        'cedula': ced,
        'name':   name,
    }, on_conflict='cedula').execute()
    print(f"  athletes upsert OK")
    
    # 2) Construir payload athlete_profiles
    profile_payload = {
        'cedula':        ced,
        'age':           age,
        'sex':           sex,
        'weight_kg':     od.get('weight_kg'),
        'height_cm':     od.get('height_cm'),
        'pr_5k_sec':     od.get('pr_5k_sec'),
        'pr_10k_sec':    od.get('pr_10k_sec'),
        'pr_21k_sec':    od.get('pr_21k_sec'),
        'pr_42k_sec':    od.get('pr_42k_sec'),
        'race_distance': od.get('race_distance'),
        'race_date_raw': od.get('race_date_raw'),
        'time_goal_sec': od.get('time_goal_sec'),
    }
    # raw con todos los datos del form
    raw_payload = dict(od)
    raw_payload['cedula']  = ced
    raw_payload['source']  = 'd1-sync-recover'
    raw_payload['sex_bin'] = sex_bin
    profile_payload['raw'] = raw_payload
    
    sb.table('athlete_profiles').upsert(profile_payload, on_conflict='cedula').execute()
    print(f"  athlete_profiles upsert OK")

# Verificación
print("\n=== Verificación ===")
res = sb.table('athlete_profiles').select('cedula,age,sex').in_('cedula', ['1128276790','4576469']).execute()
for r in res.data:
    print(f"  {r['cedula']}: age={r['age']}, sex={r['sex']}")
