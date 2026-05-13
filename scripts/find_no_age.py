import os, sys
from dotenv import load_dotenv
from supabase import create_client
import pandas as pd

os.chdir(r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching")
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

print("Cargando activities desde Supabase...", flush=True)
all_acts, offset = [], 0
while True:
    res = (sb.table('activities')
           .select('strava_id,cedula,activity_date,distance_m,duration_sec,raw')
           .in_('sport_type', ['Run', 'TrailRun'])
           .order('strava_id')
           .range(offset, offset+999)
           .execute())
    if not res.data: break
    all_acts.extend(res.data)
    if len(res.data) < 1000: break
    offset += 1000

print(f"Total acts: {len(all_acts)}", flush=True)

df = pd.DataFrame(all_acts)
df['avg_hr'] = df['raw'].apply(lambda r: (r or {}).get('average_heartrate'))
df['avg_hr'] = pd.to_numeric(df['avg_hr'], errors='coerce')
df['distance_km'] = df['distance_m'] / 1000.0
df['pace_sec_per_km'] = df['duration_sec'] / df['distance_km']
df_ok = df[df['avg_hr'].notna() & df['avg_hr'].between(50,220) & (df['distance_km']>1.0) & df['pace_sec_per_km'].between(200,1000)].copy()
df_ok['activity_date'] = pd.to_datetime(df_ok['activity_date'])

stats = df_ok.groupby('cedula').agg(n_runs=('strava_id','count'), first=('activity_date','min'), last=('activity_date','max')).reset_index()
stats['weeks_span'] = (stats['last'] - stats['first']).dt.days / 7
eligible = stats[(stats['n_runs']>=5) & (stats['weeks_span']>=4)]['cedula'].tolist()
print(f"Elegibles N2 (>=5 runs + >=4 sem): {len(eligible)}", flush=True)

profiles = sb.table('athlete_profiles').select('cedula,raw').in_('cedula', eligible).execute()
athletes = sb.table('athletes').select('cedula,name').in_('cedula', eligible).execute()
name_map = {r['cedula']: r.get('name') for r in athletes.data}

df_p = pd.DataFrame(profiles.data)
df_p['age'] = df_p['raw'].apply(lambda r: (r or {}).get('age'))
df_p['name'] = df_p['cedula'].map(name_map)

sin_age = df_p[df_p['age'].isna()][['cedula','name']]
print(f"\nElegibles SIN age en Supabase: {len(sin_age)}")
for _, row in sin_age.iterrows():
    print(f"  {row['cedula']} | {row['name']}")

sin_profile = sorted(set(eligible) - set(df_p['cedula'].tolist()))
print(f"\nElegibles SIN profile en Supabase: {len(sin_profile)}")
for c in sin_profile:
    print(f"  {c} | {name_map.get(c)}")
