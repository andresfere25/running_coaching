"""
Script: run_nb08.py
Ejecuta la logica de NB08 (prediccion multi-punto) directamente en Python.
Guarda resultados como JSON y figuras como PNG.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path
import json, pickle, warnings
warnings.filterwarnings('ignore')

from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

BASE        = Path('Datasets running/Nuevo dataset Project_2-Marathon-Predictor')
RESULTS_CSV = Path('Datasets running/archive (3)/Results.csv')
OUT_FIGS    = Path('ml/figures')
OUT_MODELS  = Path('src/ml/models')
OUT_FIGS.mkdir(parents=True, exist_ok=True)
OUT_MODELS.mkdir(parents=True, exist_ok=True)

# ─── 1. Cargar dataset Boston ─────────────────────────────────────────────────
def time_to_seconds(t):
    if pd.isna(t) or str(t).strip() in ['-', '', 'nan']:
        return np.nan
    parts = str(t).strip().split(':')
    try:
        if len(parts) == 3:
            return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0])*60 + int(parts[1])
    except Exception:
        return np.nan

print("Cargando dataset Boston...")
years = [2015, 2016, 2017, 2018]
dfs = []
for y in years:
    path = BASE / f'marathon_results_{y}.csv'
    tmp = pd.read_csv(path, encoding='latin-1')
    tmp['year'] = y
    dfs.append(tmp)
raw = pd.concat(dfs, ignore_index=True)

TIME_COLS = ['5K','10K','15K','20K','Half','25K','30K','35K','40K','Official Time']
df = raw.copy()
for col in TIME_COLS:
    if col in df.columns:
        df[col + '_sec'] = df[col].apply(time_to_seconds)
    else:
        df[col + '_sec'] = np.nan

df['gender_M'] = (df['M/F'] == 'M').astype(int)

df_clean = df.dropna(subset=['Half_sec', 'Official Time_sec', 'Age']).copy()
df_clean = df_clean[
    (df_clean['Official Time_sec'] >= 7200) &
    (df_clean['Official Time_sec'] <= 25200) &
    (df_clean['Age'] >= 18) &
    (df_clean['Age'] <= 85)
].copy()
print(f"Dataset limpio: {len(df_clean):,}  |  Train: {(df_clean['year']<2018).sum():,}  |  Test: {(df_clean['year']==2018).sum():,}")

# ─── 2. Prior demografico ─────────────────────────────────────────────────────
print("Construyendo prior demografico...")
results_raw = pd.read_csv(RESULTS_CSV)
results_ok = results_raw[
    (results_raw['Gender'].isin(['M','F'])) &
    (results_raw['Age'] >= 18) & (results_raw['Age'] <= 85) &
    (results_raw['Finish'] >= 7200) & (results_raw['Finish'] <= 25200)
].copy()
results_ok['gender_M'] = (results_ok['Gender'] == 'M').astype(int)

age_bins   = list(range(18, 91, 5))
age_labels = [f'{b}-{b+4}' for b in age_bins[:-1]]
results_ok['age_group'] = pd.cut(results_ok['Age'], bins=age_bins, labels=age_labels, right=False)
prior_table = (
    results_ok.groupby(['age_group', 'gender_M'])['Finish']
    .median().reset_index()
    .rename(columns={'Finish': 'demographic_prior_sec'})
)

df_clean['age_group'] = pd.cut(df_clean['Age'], bins=age_bins, labels=age_labels, right=False)
df_clean = df_clean.merge(prior_table, on=['age_group','gender_M'], how='left')

# ─── 3. Features derivadas ────────────────────────────────────────────────────
df_feat = df_clean.copy()
df_feat['pace_5k']        = df_feat['5K_sec']   / 5.0
df_feat['pace_10k']       = df_feat['10K_sec']  / 10.0
df_feat['pace_15k']       = df_feat['15K_sec']  / 15.0
df_feat['pace_20k']       = df_feat['20K_sec']  / 20.0
df_feat['pace_half']      = df_feat['Half_sec'] / 21.0975
df_feat['ratio_10k_5k']   = df_feat['10K_sec'] / df_feat['5K_sec']
df_feat['ratio_half_5k']  = df_feat['Half_sec'] / (df_feat['5K_sec'] * 4.2195)
half_pace = df_feat['Half_sec'] / 21.0975
df_feat['fade_25k'] = (df_feat['25K_sec'] - df_feat['Half_sec']) / (3.9025 * half_pace)
df_feat['fade_30k'] = (df_feat['30K_sec'] - df_feat['Half_sec']) / (8.9025 * half_pace)
df_feat['fade_35k'] = (df_feat['35K_sec'] - df_feat['Half_sec']) / (13.9025 * half_pace)

TARGET = 'Official Time_sec'

# ─── 4. Checkpoints ───────────────────────────────────────────────────────────
CHECKPOINTS_DEF = [
    {'name': 'C0 — Sin splits', 'km': 0,    'new_features': ['Age','gender_M','demographic_prior_sec']},
    {'name': 'C1 — 5K',         'km': 5,    'new_features': ['5K_sec','pace_5k']},
    {'name': 'C2 — 10K',        'km': 10,   'new_features': ['10K_sec','pace_10k','ratio_10k_5k']},
    {'name': 'C3 — 15K',        'km': 15,   'new_features': ['15K_sec','pace_15k']},
    {'name': 'C4 — 20K',        'km': 20,   'new_features': ['20K_sec','pace_20k']},
    {'name': 'C5 — Half (21K)', 'km': 21.1, 'new_features': ['Half_sec','pace_half','ratio_half_5k']},
    {'name': 'C6 — 25K',        'km': 25,   'new_features': ['25K_sec','fade_25k']},
    {'name': 'C7 — 30K',        'km': 30,   'new_features': ['30K_sec','fade_30k']},
    {'name': 'C8 — 35K',        'km': 35,   'new_features': ['35K_sec','fade_35k']},
]
all_feat_sets = []
accumulated = []
for cp in CHECKPOINTS_DEF:
    accumulated = accumulated + cp['new_features']
    all_feat_sets.append({'name': cp['name'], 'km': cp['km'], 'features': list(accumulated)})

# ─── 5. Riegel helpers ────────────────────────────────────────────────────────
RIEGEL_CALIBRATED = {'elite':1.0366,'sub3h':1.0332,'3to4h':1.0613,'4hplus':1.1100}

def classify_segment(official_sec):
    h = official_sec / 3600
    if h < 2.5: return 'elite'
    if h < 3.0: return 'sub3h'
    if h < 4.0: return '3to4h'
    return '4hplus'

def riegel_calibrated_from_split(split_sec, split_km, target_km=42.195):
    ratio     = target_km / split_km
    base_pred = split_sec * ratio ** 1.06
    exp       = RIEGEL_CALIBRATED[classify_segment(base_pred)]
    return split_sec * ratio ** exp

def fmt_min(seconds):
    m = int(seconds // 60); s = int(seconds % 60)
    return f"{m}:{s:02d}"

# GradBoost mas rapido (100 arboles, depth=4) — suficiente para curva MAE
GB_PARAMS = dict(n_estimators=100, max_depth=4, learning_rate=0.1, subsample=0.8, random_state=42)

SPLIT_KM_MAP = {
    '5K_sec':5,'10K_sec':10,'15K_sec':15,'20K_sec':20,
    'Half_sec':21.0975,'25K_sec':25,'30K_sec':30,'35K_sec':35
}

# ─── 6. Entrenar un modelo por checkpoint ─────────────────────────────────────
print()
print(f"{'Checkpoint':<24} {'N_train':>8} {'N_test':>8} {'MAE_ML':>10} {'MAE_Riegel':>12} {'Mejora%':>8}")
print("-" * 74)

results = []
for cp_info in all_feat_sets:
    name  = cp_info['name']
    km    = cp_info['km']
    feats = cp_info['features']

    mask  = df_feat[feats + [TARGET]].notna().all(axis=1)
    df_cp = df_feat[mask].copy()

    train = df_cp[df_cp['year'] < 2018]
    test  = df_cp[df_cp['year'] == 2018]

    X_tr, y_tr = train[feats], train[TARGET]
    X_te, y_te = test[feats],  test[TARGET]

    model = GradientBoostingRegressor(**GB_PARAMS)
    model.fit(X_tr, y_tr)
    pred_ml = model.predict(X_te)
    mae_ml  = mean_absolute_error(y_te, pred_ml)

    if km == 0:
        riegel_pred = test['demographic_prior_sec'].fillna(y_te.mean())
        mae_riegel  = mean_absolute_error(y_te, riegel_pred)
    else:
        split_col   = [f for f in feats if f.endswith('_sec') and f != 'demographic_prior_sec'][-1]
        split_km_v  = SPLIT_KM_MAP.get(split_col, km)
        riegel_pred = test[split_col].apply(lambda s: riegel_calibrated_from_split(s, split_km_v))
        mae_riegel  = mean_absolute_error(y_te, riegel_pred)

    mejora = (mae_riegel - mae_ml) / mae_riegel * 100

    results.append({
        'name': name, 'km': km, 'n_train': len(train), 'n_test': len(test),
        'mae_ml': mae_ml, 'mae_riegel': mae_riegel, 'mejora_pct': mejora,
        'model': model, 'features': feats
    })
    print(f"{name:<24} {len(train):>8,} {len(test):>8,} "
          f"{fmt_min(mae_ml):>10} {fmt_min(mae_riegel):>12} {mejora:>+7.1f}%")

# ─── 7. Tabla detallada ───────────────────────────────────────────────────────
print()
print("=" * 76)
print("TABLA COMPARATIVA — Test 2018")
print(f"{'Checkpoint':<22} {'km':>5} {'N test':>8} {'MAE Modelo':>12} {'MAE Riegel':>12} {'Mejora':>8}")
print("-" * 76)
for r in results:
    cp = r['name'].split(' — ')[1] if ' — ' in r['name'] else r['name']
    print(f"{cp:<22} {r['km']:>5.1f} {r['n_test']:>8,} "
          f"{fmt_min(r['mae_ml']):>12} {fmt_min(r['mae_riegel']):>12} {r['mejora_pct']:>+7.1f}%")

print()
print("REDUCCION INCREMENTAL (valor de cada checkpoint adicional):")
print(f"{'Checkpoint':<22} {'MAE':>8} {'vs anterior':>12} {'vs C0':>10}")
print("-" * 56)
mae_c0   = results[0]['mae_ml']
mae_prev = mae_c0
for i, r in enumerate(results):
    cp = r['name'].split(' — ')[1] if ' — ' in r['name'] else r['name']
    delta_prev = (mae_prev - r['mae_ml']) / mae_prev * 100 if i > 0 else 0
    delta_c0   = (mae_c0   - r['mae_ml']) / mae_c0   * 100
    print(f"{cp:<22} {fmt_min(r['mae_ml']):>8} "
          f"{'--' if i==0 else f'-{delta_prev:.1f}%':>12} {delta_c0:>+9.1f}%")
    mae_prev = r['mae_ml']

# ─── 8. Analisis por segmento (checkpoint Half) ───────────────────────────────
def segment(sec):
    h = sec/3600
    if h<2.5: return 'Elite (<2:30)'
    if h<3.0: return 'Sub-3h'
    if h<4.0: return '3h-4h'
    return '+4h (amateur)'

cp_half   = results[5]
feats_half = cp_half['features']
mask_half  = df_feat[feats_half + [TARGET]].notna().all(axis=1)
test_2018  = df_feat[mask_half & (df_feat['year'] == 2018)].copy()
test_2018['pred_ml']     = cp_half['model'].predict(test_2018[feats_half])
test_2018['pred_riegel'] = test_2018['Half_sec'].apply(
    lambda s: riegel_calibrated_from_split(s, 21.0975))
test_2018['err_ml']     = (test_2018['pred_ml']     - test_2018[TARGET]).abs()
test_2018['err_riegel'] = (test_2018['pred_riegel']  - test_2018[TARGET]).abs()
test_2018['segment']    = test_2018[TARGET].apply(segment)

SEG_ORDER = ['Elite (<2:30)','Sub-3h','3h-4h','+4h (amateur)']
seg_stats = (
    test_2018.groupby('segment')
    .agg(N=('err_ml','count'), MAE_ML=('err_ml','mean'), MAE_Riegel=('err_riegel','mean'))
    .reindex(SEG_ORDER)
)
seg_stats['Mejora%'] = (seg_stats['MAE_Riegel'] - seg_stats['MAE_ML']) / seg_stats['MAE_Riegel'] * 100

print()
print("ANALISIS POR SEGMENTO — Checkpoint Half (21K)")
print(f"{'Segmento':<20} {'N':>7} {'MAE ML':>10} {'MAE Riegel':>12} {'Mejora':>8}")
print("-" * 62)
for seg, row in seg_stats.iterrows():
    print(f"{seg:<20} {int(row['N']):>7,} {fmt_min(row['MAE_ML']):>10} "
          f"{fmt_min(row['MAE_Riegel']):>12} {row['Mejora%']:>+7.1f}%")

# ─── 9. Figuras ───────────────────────────────────────────────────────────────
print()
print("Generando figuras...")

kms    = [r['km']            for r in results]
mae_ml = [r['mae_ml']/60     for r in results]
mae_ri = [r['mae_riegel']/60  for r in results]

# Figura 1: Curva principal
fig, ax = plt.subplots(figsize=(11, 6))
ax.plot(kms, mae_ml, 'o-', color='#1f77b4', lw=2.5, ms=8, label='GradBoost (ML)', zorder=3)
ax.plot(kms, mae_ri, 's--', color='#d62728', lw=2, ms=7, label='Riegel Calibrado', zorder=3)
ax.fill_between(kms, mae_ml, mae_ri,
                where=[ml < ri for ml, ri in zip(mae_ml, mae_ri)],
                alpha=0.12, color='#1f77b4', label='Ventaja del ML')

for km, mae, r in zip(kms, mae_ml, results):
    label = fmt_min(r['mae_ml'])
    ax.annotate(label, xy=(km, mae), xytext=(km, mae + 0.4),
                ha='center', va='bottom', fontsize=8.5, color='#1f77b4', fontweight='bold')

xtick_labels = ['Sin splits\n(C0)','5K','10K','15K','20K','Half\n(21K)','25K','30K','35K']
ax.set_xticks(kms)
ax.set_xticklabels(xtick_labels, fontsize=9)
ax.set_xlabel('Checkpoint (km recorridos)', fontsize=12)
ax.set_ylabel('MAE (minutos)  —  menor es mejor', fontsize=12)
ax.set_title('Curva de exactitud progresiva: Boston Marathon 2015-2018\n'
             'GradBoost vs. Riegel Calibrado por checkpoint', fontsize=13)
ax.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f min'))
ax.grid(axis='y', alpha=0.3)
ax.legend(fontsize=10, loc='upper right')
plt.tight_layout()
fig.savefig(OUT_FIGS / 'nb08_curva_exactitud_progresiva.png', dpi=150, bbox_inches='tight')
print(f"  Figura 1 guardada: {OUT_FIGS / 'nb08_curva_exactitud_progresiva.png'}")
plt.close()

# Figura 2: Por segmento
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
ax = axes[0]
x = np.arange(len(SEG_ORDER)); w = 0.35
bars_ml = ax.bar(x-w/2, seg_stats['MAE_ML']/60,    w, label='GradBoost ML',     color='#1f77b4')
bars_ri = ax.bar(x+w/2, seg_stats['MAE_Riegel']/60, w, label='Riegel Calibrado', color='#d62728', alpha=0.7)
for bar in list(bars_ml) + list(bars_ri):
    ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.1,
            f'{bar.get_height():.1f}', ha='center', va='bottom', fontsize=8)
ax.set_xticks(x); ax.set_xticklabels(SEG_ORDER, fontsize=8.5)
ax.set_ylabel('MAE (minutos)'); ax.legend(fontsize=9); ax.grid(axis='y', alpha=0.3)
ax.set_title('MAE por segmento — Checkpoint Half (21K)', fontsize=11)

ax2 = axes[1]
colores = ['#2ca02c' if v>0 else '#d62728' for v in seg_stats['Mejora%']]
bars2 = ax2.bar(SEG_ORDER, seg_stats['Mejora%'], color=colores, edgecolor='white')
for bar, val in zip(bars2, seg_stats['Mejora%']):
    ax2.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.2,
             f'{val:+.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')
ax2.axhline(0, color='black', lw=1)
ax2.set_ylabel('Mejora del ML sobre Riegel (%)'); ax2.grid(axis='y', alpha=0.3)
ax2.set_title('Donde aporta mas el ML?', fontsize=11)
ax2.set_xticklabels(SEG_ORDER, fontsize=8.5)
plt.suptitle('Analisis por segmento — Checkpoint Half (21K)\nBoston Marathon Test 2018',
             fontsize=12, fontweight='bold')
plt.tight_layout()
fig.savefig(OUT_FIGS / 'nb08_analisis_por_segmento.png', dpi=150, bbox_inches='tight')
print(f"  Figura 2 guardada: {OUT_FIGS / 'nb08_analisis_por_segmento.png'}")
plt.close()

# ─── 10. Exportar resultados JSON + modelo Half ────────────────────────────────
results_export = [{
    'name': r['name'], 'km': r['km'], 'n_test': r['n_test'],
    'mae_ml_sec': round(r['mae_ml'],1), 'mae_ml_min': round(r['mae_ml']/60,2),
    'mae_riegel_sec': round(r['mae_riegel'],1), 'mejora_pct': round(r['mejora_pct'],2),
    'features': r['features'],
} for r in results]

with open(OUT_FIGS / 'nb08_resultados.json', 'w', encoding='utf-8') as f:
    json.dump(results_export, f, indent=2, ensure_ascii=False)
print(f"  Resultados JSON: {OUT_FIGS / 'nb08_resultados.json'}")

model_half_export = {
    'model': results[5]['model'], 'features': results[5]['features'],
    'checkpoint': 'Half (21K)', 'km': 21.0975,
    'mae_sec': results[5]['mae_ml'], 'mae_min': results[5]['mae_ml']/60,
    'prior_table': prior_table, 'notebook': 'NB08',
}
with open(OUT_MODELS / 'multi_point_model_half.pkl', 'wb') as f:
    pickle.dump(model_half_export, f)
print(f"  Modelo Half: {OUT_MODELS / 'multi_point_model_half.pkl'}")

print()
print("LISTO.")
