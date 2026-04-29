# api/models/ — Modelos ML serializados

Modelos listos para producción que el API puede cargar sin sklearn ni archivos .pkl.
Formato JSON: legible, versionable en git, sin dependencias de runtime.

---

## Modelos disponibles

### `nivel1_ridge_v4.json` — Prior poblacional FC↔ritmo (Nivel 1)

| Campo | Valor |
|---|---|
| Algoritmo | Ridge Regression (alpha=1.0) |
| Feature set | v4 — 8 features (`gender_bin`, `fcmax_obs`, `hr_mean`, `pct_fcmax`, `zona_num`, `hr_max_rel`, `log_duration`, `dens_hr`) |
| Target | `pace_min_km` |
| Dataset | Endomondo/FitRec — 20 710 sesiones, 356 usuarios |
| MAE CV | 40.16 sec/km (GroupKFold K=10) |
| R² | 0.188 |
| Conformal | ±1.06 min/km a 80% de cobertura |
| Notebook fuente | `ml/notebooks/12_nivel1_naiveautoml_endomondo.ipynb` |
| PKL original | `ml/notebooks/outputs/nb12/nivel1_prior_poblacional_FULL_v2.pkl` (gitignoreado) |

---

## Convenciones del dataset Endomondo/FitRec

> ⚠️ Leer antes de reentrenar o modificar features.

| Convención | Detalle |
|---|---|
| **Unidad de velocidad** | Campo `H2` = km/h. Campo `H1` = m/s. **Siempre usar H2**; H1 produce ritmos de ~1.5 min/km imposibles |
| **FCmax por usuario** | Empírica: percentil 99 de `max_heartrate` por usuario, mínimo ≥10 sesiones. No usar `220−edad` para entrenamiento |
| **Zonas Z1–Z5** | Porcentaje de FCmax_obs: <60 / 60–70 / 70–80 / 80–90 / ≥90 % |
| **Validación cruzada** | Siempre `GroupKFold(n_splits=10)` agrupado por `userId`. Nunca `KFold` plano (data leakage entre sesiones del mismo atleta) |
| **Conformal** | Split 60/20/20 estratificado por atleta, alpha=0.20 |
| **Datasets crudos** | `endomondoHR.json` (6.6 GB) y `endomondoMeta.json` (10 GB) en `C:\Datasets\running_coaching\`. Nunca cargar completos en memoria — usar streaming (ver `ml/scripts/nb11_full_scale.py`) |

---

## Cómo actualizar el modelo (cuando se reentréne)

1. Reentrenar en `ml/notebooks/12_nivel1_naiveautoml_endomondo.ipynb`
2. Exportar los parámetros a JSON:
   ```python
   import json
   from pathlib import Path

   model_data = {
       "version": "v5",  # incrementar versión
       "description": "...",
       "trained_on": "...",
       "features": list(pipeline.feature_names_in_),
       "coefs": ridge.coef_.tolist(),
       "intercept": float(ridge.intercept_),
       "scaler_mean": scaler.mean_.tolist(),
       "scaler_scale": scaler.scale_.tolist(),
       "conformal_q": float(q_conformal),
       "mae_sec_km": float(mae_cv),
       "metrics": { "cv_mae_sec_km": ..., "r2": ..., "conformal_coverage": 0.800 }
   }
   Path("api/models/nivel1_ridge_v5.json").write_text(json.dumps(model_data, indent=2))
   ```
3. Actualizar `api/routers/athletes.py` → cambiar `nivel1_ridge_v4.json` por `nivel1_ridge_v5.json`
4. Commitear y pushear → Railway lo tiene automáticamente

---

## Niveles pendientes

| Nivel | Estado | Archivo futuro |
|---|---|---|
| N1 Prior poblacional | ✅ `nivel1_ridge_v4.json` | — |
| N2 Núcleo generalizable (RUNA) | ⏳ Pendiente cohorte ≥30 atletas | `nivel2_runa_v1.json` |
| N3 Personalización bayesiana | 🔮 Futuro post-N2 | `nivel3_bayesian_{cedula}.json` |
