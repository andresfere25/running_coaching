"""Crea NB13b v2 con eligibility relajada (>=3 runs + >=2 semanas)."""
import json, shutil, os

SRC = r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching\ml\notebooks\13_nivel2_naiveautoml_runa.ipynb"
DST = r"C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching\ml\notebooks\13b_nivel2_runa_v2_relajado.ipynb"

shutil.copy(SRC, DST)

with open(DST, 'r', encoding='utf-8') as f:
    nb = json.load(f)

# Reemplazos sistemáticos en celdas
REPLACEMENTS = [
    # Eligibility thresholds
    (">= 5",         ">= 3"),   # cualquier ">= 5" en código
    (">=5",          ">=3"),
    (">= 4",         ">= 2"),
    (">=4",          ">=2"),
    # En texto markdown y print statements
    ("≥5 runs",      "≥3 runs"),
    ("≥4 semanas",   "≥2 semanas"),
    ("≥ 5 runs",     "≥ 3 runs"),
    ("≥ 4 semanas",  "≥ 2 semanas"),
    ("≥5 HR runs",   "≥3 HR runs"),
    ("≥ 5 HR runs",  "≥ 3 HR runs"),
    # Versión
    ("NB13 — Nivel 2: Prior de Cohorte RUNA (LOAO-CV)",
     "NB13b — Nivel 2: Prior de Cohorte RUNA v2 (eligibility relajada)"),
]

changes = 0
for cell in nb['cells']:
    src_lines = cell.get('source', [])
    new_lines = []
    cell_changed = False
    for line in src_lines:
        original = line
        for old, new in REPLACEMENTS:
            line = line.replace(old, new)
        if line != original:
            cell_changed = True
        new_lines.append(line)
    if cell_changed:
        cell['source'] = new_lines
        cell['outputs'] = []
        cell['execution_count'] = None
        changes += 1

# Limpia TODOS los outputs para forzar re-ejecución
for cell in nb['cells']:
    if cell.get('cell_type') == 'code':
        cell['outputs'] = []
        cell['execution_count'] = None

with open(DST, 'w', encoding='utf-8') as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print(f"NB13b creado: {DST}")
print(f"Celdas modificadas con replacements: {changes}")
print(f"Total celdas con outputs limpios: {sum(1 for c in nb['cells'] if c.get('cell_type')=='code')}")
