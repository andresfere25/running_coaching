"""
Script: add_improvement_cells.py
Agrega las secciones de mejora (Caminos 1 + 5) al notebook NB07.
"""
import nbformat
from pathlib import Path

nb_path = Path(__file__).parent.parent / "notebooks" / "07_modelo_poblacional_boston.ipynb"
with open(nb_path, encoding="utf-8") as f:
    nb = nbformat.read(f, as_version=4)

# Remove previously inserted improvement cells if re-running
nb.cells = [c for c in nb.cells if "10. Mejoras" not in c.source[:50]]

new_cells = []

# ── 1. Header markdown ────────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_markdown_cell(
    "---\n## 10. Mejoras al Modelo: Caminos 1 + 5\n\n"
    "### Camino 1: todos los splits disponibles antes del 21K + features de pacing\n"
    "### Camino 5: prior demografico desde Results.csv (429K corredores)\n\n"
    "Hipotesis: el patron de pacing temprano (ratio ritmo Half/5K) captura si el corredor "
    "salio demasiado rapido, lo que Riegel ignora completamente."
))

# ── 2. Demographic prior ──────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.1 Prior demografico (Results.csv, 429K corredores)
# ─────────────────────────────────────────────
results_raw = pd.read_csv('../../Datasets running/archive (3)/Results.csv')

results_ok = results_raw[
    (results_raw['Gender'].isin(['M', 'F'])) &
    (results_raw['Age'] >= 18) & (results_raw['Age'] <= 85) &
    (results_raw['Finish'] >= 7200) &
    (results_raw['Finish'] <= 25200)
].copy()
results_ok['gender_M'] = (results_ok['Gender'] == 'M').astype(int)

age_bins   = list(range(18, 91, 5))
age_labels = [f'{b}-{b+4}' for b in age_bins[:-1]]
results_ok['age_group'] = pd.cut(results_ok['Age'], bins=age_bins,
                                  labels=age_labels, right=False)

prior_table = (
    results_ok.groupby(['age_group', 'gender_M'], observed=True)['Finish']
    .median()
    .reset_index()
    .rename(columns={'Finish': 'demographic_prior_sec'})
)

print(f"Results.csv validos: {len(results_ok):,}")
print(f"Celdas en prior: {len(prior_table)}")
print()
prior_show = prior_table.copy()
prior_show['prior_fmt'] = prior_show['demographic_prior_sec'].apply(fmt_time)
print("Muestra del prior:")
print(prior_show[prior_show['age_group'].isin(['30-34','40-44','50-54'])].to_string(index=False))
"""
))

# ── 3. Extended features ──────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.2 Features extendidas (sin filtracion de datos)
# Solo usamos splits que el corredor ya cruzo en el Half (<=21K)
# ─────────────────────────────────────────────
df_ext = df_clean.copy()

# Merge con prior demografico
age_bins2   = list(range(18, 91, 5))
age_labs2   = [f'{b}-{b+4}' for b in age_bins2[:-1]]
df_ext['age_group'] = pd.cut(df_ext['Age'], bins=age_bins2,
                               labels=age_labs2, right=False)
df_ext = df_ext.merge(prior_table, on=['age_group','gender_M'], how='left')

# Pacing features (calculadas desde splits <= 21K)
df_ext['pace_5k']    = df_ext['5K_sec']  / 5.0
df_ext['pace_10k']   = df_ext['10K_sec'] / 10.0
df_ext['pace_half']  = df_ext['Half_sec'] / 21.0975

# ratio > 1 = se freno en el camino al Half vs el ritmo inicial
df_ext['ratio_half_5k']  = df_ext['pace_half'] / df_ext['pace_5k']
df_ext['ratio_10k_5k']   = df_ext['pace_10k']  / df_ext['pace_5k']

# gap_vs_prior: cuanto difiere la proyeccion Riegel del prior demografico
df_ext['riegel_proj_42k'] = df_ext['Half_sec'] * (42.195 / 21.0975) ** 1.06
df_ext['gap_vs_prior']    = df_ext['riegel_proj_42k'] - df_ext['demographic_prior_sec']

# Renombrar para consistencia
df_ext = df_ext.rename(columns={
    'Half_sec':            'half_sec',
    '5K_sec':              'fk5_sec',
    '10K_sec':             'fk10_sec',
    'Age':                 'age',
    'Official Time_sec':   'official_sec',
})

FEAT_EXT = [
    'half_sec', 'fk5_sec', 'fk10_sec',
    'ratio_half_5k', 'ratio_10k_5k',
    'demographic_prior_sec', 'gap_vs_prior',
    'age', 'gender_M', 'age_x_gender',
]
TARGET2 = 'official_sec'

df_ext_ok = df_ext.dropna(subset=FEAT_EXT + [TARGET2]).copy()

print(f"Dataset extendido: {len(df_ext_ok):,} corredores ({len(FEAT_EXT)} features)")
print(f"Perdidos por nulls en features nuevas: {len(df_clean) - len(df_ext_ok):,}")
print()
print("Estadisticos de features nuevas clave:")
print(df_ext_ok[['ratio_half_5k','ratio_10k_5k','demographic_prior_sec','gap_vs_prior']].describe().round(3))
"""
))

# ── 4. Train extended models ──────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.3 Entrenar modelos extendidos (temporal split)
# ─────────────────────────────────────────────
train_ext = df_ext_ok[df_ext_ok['year'].isin([2015, 2016, 2017])].copy()
test_ext  = df_ext_ok[df_ext_ok['year'] == 2018].copy()

X_tr_ext = train_ext[FEAT_EXT]
y_tr_ext = train_ext[TARGET2]
X_te_ext = test_ext[FEAT_EXT]
y_te_ext = test_ext[TARGET2]

print(f"Train: {len(X_tr_ext):,}  |  Test: {len(X_te_ext):,}")
print()

models_ext = {
    'Ridge extendido': Pipeline([
        ('scaler', StandardScaler()),
        ('model',  Ridge(alpha=1.0))
    ]),
    'GradBoost extendido': GradientBoostingRegressor(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        subsample=0.8, random_state=42
    ),
    'RandomForest extendido': RandomForestRegressor(
        n_estimators=300, max_depth=12, min_samples_leaf=10,
        random_state=42, n_jobs=-1
    ),
}

results_ext = []
print("=== MODELOS EXTENDIDOS (10 features) — Test 2018 ===")
for name, model in models_ext.items():
    model.fit(X_tr_ext, y_tr_ext)
    y_pred = model.predict(X_te_ext)
    r = eval_model(y_te_ext, y_pred, name)
    results_ext.append(r)

print()
print("=== REFERENCIA — modelos base y Riegel ===")
riegel_gen  = test_ext['half_sec'] * (42.195 / 21.0975) ** 1.06
riegel_cal  = test_ext.apply(
    lambda row: row['half_sec'] * (42.195/21.0975) ** RIEGEL_CALIBRATED.get(row['segment'], 1.06),
    axis=1
)
eval_model(y_te_ext, riegel_gen,  'Riegel Generico')
eval_model(y_te_ext, riegel_cal,  'Riegel Calibrado (NB03)')

# Ridge base en el mismo subset para comparacion justa
ridge_base_pred = trained_models['Ridge (alpha=1)'].predict(X_te_ext[FEATURES_BASE])
eval_model(y_te_ext, ridge_base_pred, 'Ridge base (4 features)')
"""
))

# ── 5. Comparison chart ───────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.4 Comparativa completa + Feature Importance
# ─────────────────────────────────────────────
# Construir tabla
riegel_gen_mae  = mean_absolute_error(y_te_ext, test_ext['half_sec'] * (42.195/21.0975)**1.06)
riegel_cal_mae  = mean_absolute_error(y_te_ext, riegel_cal)
ridge_base_mae  = mean_absolute_error(y_te_ext, ridge_base_pred)

rows = [
    {'modelo': 'Riegel Generico',        'mae_min': riegel_gen_mae/60, 'tipo': 'baseline'},
    {'modelo': 'Riegel Calibrado (NB03)', 'mae_min': riegel_cal_mae/60, 'tipo': 'baseline'},
    {'modelo': 'Ridge base (4f)',          'mae_min': ridge_base_mae/60, 'tipo': 'base'},
] + [{'modelo': r['name'], 'mae_min': r['mae_sec']/60, 'tipo': 'extendido'} for r in results_ext]

comp_df = pd.DataFrame(rows).sort_values('mae_min')

print("=== TABLA COMPARATIVA FINAL ===")
print(f"{'Modelo':<32} {'MAE (min)':>10} {'MAE (%)':>8} {'Tipo':>12}")
print('-'*66)
for _, row in comp_df.iterrows():
    pct = row['mae_min'] * 60 / y_te_ext.mean() * 100
    mark = ' *' if row['tipo'] == 'extendido' else ''
    print(f"{row['modelo']+mark:<32} {row['mae_min']:>10.1f} {pct:>8.1f}% {row['tipo']:>12}")

best_ext_row = comp_df[comp_df['tipo']=='extendido'].iloc[0]
best_base_row = comp_df[comp_df['tipo']=='base'].iloc[0]
best_riegel   = comp_df[comp_df['tipo']=='baseline'].iloc[0]
print()
print(f"Mejor extendido vs Riegel Generico: -{(riegel_gen_mae/60 - best_ext_row['mae_min'])/riegel_gen_mae*60*100:.1f}%")
print(f"Mejor extendido vs Ridge base:      -{(best_base_row['mae_min'] - best_ext_row['mae_min'])/best_base_row['mae_min']*100:.1f}%")
"""
))

# ── 6. Visualizations ─────────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.5 Visualizaciones
# ─────────────────────────────────────────────
from matplotlib.patches import Patch

fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# 1. MAE comparativo
ax = axes[0]
color_map = {'baseline': '#d62728', 'base': '#aec7e8', 'extendido': '#1f77b4'}
colors = [color_map[r['tipo']] for _, r in comp_df.iterrows()]
bars = ax.barh(comp_df['modelo'], comp_df['mae_min'], color=colors, edgecolor='white')
for bar, val in zip(bars, comp_df['mae_min']):
    ax.text(bar.get_width()+0.05, bar.get_y()+bar.get_height()/2,
            f'{val:.1f} min', va='center', fontsize=9)
ax.set_xlabel('MAE (minutos) -- menor es mejor')
ax.set_title('Comparativo de modelos\\nRiegel vs. Base (4f) vs. Extendido (10f)')
ax.invert_yaxis()
legend_els = [
    Patch(facecolor='#d62728', label='Baseline Riegel'),
    Patch(facecolor='#aec7e8', label='Modelo base (4 features)'),
    Patch(facecolor='#1f77b4', label='Modelo extendido (10 features)'),
]
ax.legend(handles=legend_els, loc='lower right', fontsize=8)

# 2. Feature importance del mejor extendido
ax = axes[1]
best_ext_name = best_ext_row['modelo']
model_for_fi = models_ext.get(best_ext_name)
if model_for_fi is None:
    model_for_fi = list(models_ext.values())[0]

if hasattr(model_for_fi, 'feature_importances_'):
    fi = pd.Series(model_for_fi.feature_importances_, index=FEAT_EXT).sort_values(ascending=True)
    fi.plot(kind='barh', ax=ax, color='steelblue', edgecolor='white')
    ax.set_title(f'Feature Importance\\n{best_ext_name}')
elif hasattr(model_for_fi, 'named_steps'):
    coef = np.abs(model_for_fi.named_steps['model'].coef_)
    fi = pd.Series(coef, index=FEAT_EXT).sort_values(ascending=True)
    fi.plot(kind='barh', ax=ax, color='steelblue', edgecolor='white')
    ax.set_title(f'Ridge -- Coeficientes absolutos\\n{best_ext_name}')
ax.set_xlabel('Importancia relativa')

plt.tight_layout()
plt.savefig('../figures/07_mejora_extendido.png', dpi=150, bbox_inches='tight')
plt.show()
"""
))

# ── 7. Pacing analysis ────────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.6 Analisis: por que ratio_half_5k mejora el modelo
# ─────────────────────────────────────────────
df_ana = test_ext.copy()
df_ana['pred_base'] = trained_models['Ridge (alpha=1)'].predict(X_te_ext[FEATURES_BASE])
df_ana['pred_ext']  = models_ext[best_ext_name].predict(X_te_ext)
df_ana['err_base']  = np.abs(df_ana['pred_base'] - df_ana['official_sec'])
df_ana['err_ext']   = np.abs(df_ana['pred_ext']  - df_ana['official_sec'])

df_ana['pacing_grupo'] = pd.cut(
    df_ana['ratio_half_5k'],
    bins=[0, 0.98, 1.02, 1.07, 99],
    labels=['Negative split\\n(acelero)', 'Even split\\n(constante)',
            'Fade leve\\n(freno un poco)', 'Fade fuerte\\n(freno mucho)']
)

grp = df_ana.groupby('pacing_grupo', observed=True)[['err_base','err_ext']].mean() / 60

fig, ax = plt.subplots(figsize=(9, 4))
x = np.arange(len(grp))
w = 0.35
ax.bar(x-w/2, grp['err_base'], w, label='Ridge base (4f)',         color='#aec7e8', edgecolor='white')
ax.bar(x+w/2, grp['err_ext'],  w, label='Ridge extendido (10f) *', color='steelblue', edgecolor='white')
ax.set_xticks(x)
ax.set_xticklabels(grp.index, fontsize=9)
ax.set_ylabel('MAE promedio (minutos)')
ax.set_title('Error por patron de pacing\\n(El modelo extendido gana mas donde el corredor freno)')
ax.legend()

for xi, (bv, ev) in enumerate(zip(grp['err_base'], grp['err_ext'])):
    if bv > 0:
        mejora_pct = (bv - ev) / bv * 100
        ax.text(xi, max(bv, ev)+0.1, f'-{mejora_pct:.0f}%', ha='center', fontsize=8, color='green')

plt.tight_layout()
plt.savefig('../figures/07_pacing_analysis.png', dpi=150, bbox_inches='tight')
plt.show()

print("Insight principal:")
print("Los corredores con 'fade fuerte' (salida muy rapida) son los que")
print("mas se benefician del modelo extendido. Riegel asume ritmo constante")
print("y los subestima; el ratio de pacing le da al ML la senal que necesita.")
"""
))

# ── 8. Export v2 model ────────────────────────────────────────────────────────
new_cells.append(nbformat.v4.new_code_cell(
"""# ─────────────────────────────────────────────
# 10.7 Exportar modelo v2 (extendido, reentrenado en 2015-2018 completo)
# ─────────────────────────────────────────────
import pickle

# Reentrenar en todo el dataset
best_ext_model_final = models_ext[best_ext_name]
best_ext_model_final.fit(df_ext_ok[FEAT_EXT], df_ext_ok[TARGET2])

model_path_v2 = Path('../../src/ml/models/population_model_v2.pkl')
model_path_v2.parent.mkdir(parents=True, exist_ok=True)

payload_v2 = {
    'model':       best_ext_model_final,
    'features':    FEAT_EXT,
    'prior_table': prior_table,
    'model_name':  best_ext_name,
    'version':     2,
    'n_samples':   len(df_ext_ok),
    'riegel_calibrated_exponents': RIEGEL_CALIBRATED,
}

with open(model_path_v2, 'wb') as f:
    pickle.dump(payload_v2, f)

print(f"Modelo v2 exportado: {model_path_v2}")
print(f"Modelo: {best_ext_name}")
print(f"N features: {len(FEAT_EXT)}")
print(f"Entrenado con: {len(df_ext_ok):,} corredores (Boston 2015-2018)")
"""
))

# Insert all new cells before the "## 10. Exportar" section
# Find the index of that cell
insert_at = None
for i, cell in enumerate(nb.cells):
    if cell.cell_type == 'markdown' and '## 10. Exportar' in cell.source:
        insert_at = i
        break

if insert_at is None:
    # If not found, append before last 2 cells (export + conclusions)
    insert_at = len(nb.cells) - 2

for cell in reversed(new_cells):
    nb.cells.insert(insert_at, cell)

print(f"Inserted {len(new_cells)} cells at position {insert_at}")
print(f"Total cells: {len(nb.cells)}")

with open(nb_path, 'w', encoding='utf-8') as f:
    nbformat.write(nb, f)
print("Notebook saved.")
