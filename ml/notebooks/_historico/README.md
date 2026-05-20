# `_historico/` — Notebooks legados

> ⚠️ **NO USAR ESTOS NOTEBOOKS PARA LA TESIS VIGENTE.**
> Pertenecen a iteraciones previas de la metodología que fueron **pivoteadas**. Se conservan únicamente como registro histórico y para trazabilidad académica.

---

## ¿Por qué están aquí?

La metodología vigente de la tesis (`Avances_Tesis_Running_30abril_v4.docx`) usa:

- **Dataset principal:** FitRec / Endomondo (NB11, NB12) — 20.710 sesiones / 356 usuarios
- **Modelo:** Arquitectura jerárquica de 3 niveles
- **Pipeline ganador:** QuantileTransformer + PCA + ARDRegression vía naïveAutoML

Los notebooks de esta carpeta corresponden a una **iteración previa** que fue pivoteada. Esa iteración usaba:
- Dataset principal: Boston Marathon (103K finishers) y Results.csv (429K)
- Modelo: Arquitectura de 4 capas (Riegel calibrado + corrección demográfica Ridge)

Esa metodología quedó como **módulo auxiliar** para extrapolación de tiempos entre distancias (5K↔10K↔21K↔42K) en el módulo `src/ml/predictor.py`, pero **NO** es el modelo principal de la tesis.

---

## Inventario

### Notebooks movidos aquí

| Notebook | Razón |
|---|---|
| `01_eda_baseline_riegel.ipynb` | EDA inicial sobre Boston con baseline Riegel — superseded por NB10/NB11 |
| `02_features_longitudinales_carga.ipynb` | Features longitudinales tempranas — superseded por la metodología jerárquica |
| `04_arquitectura_capas_y_correccion_demografica.ipynb` | Arquitectura 4 capas (Riegel + Ridge demográfico) — pivoteada hacia jerárquica 3 niveles |
| `05b_validacion_step_multipliers_boston.ipynb` | Validación de step multipliers con splits de Boston — pacing artifact documentado, ya no necesario como notebook activo |
| `06_capa3_carga_y_riesgo_lesion.ipynb` | Capa 3 ACWR → injury (resultado negativo, r=0.011, AUC=0.607±0.157). Documentado en Sección 8.6 del docx v4 |
| `08_prediccion_multipunto.ipynb` | Predicción multipunto temprana — superseded por NB12 |
| `intelligent-marathon-race-predictions.ipynb` | Notebook externo de Kaggle, referencia exploratoria, no propio |
| `notebook638672aed0.ipynb` | Notebook autogenerado, sin propósito identificable |

### Notebooks que **NO** están aquí (siguen en `notebooks/` y son AUXILIARES o CURRENT)

| Notebook | Estado |
|---|---|
| `03_calibracion_riegel_boston.ipynb` | AUXILIAR — calibra exponentes Riegel usados en `predictor.py` |
| `05_sistema_prediccion_integrado_y_rango_ritmo.ipynb` | AUXILIAR — produjo `predictor.py` (en producción) |
| `07_modelo_poblacional_boston.ipynb` | AUXILIAR — Boston referenciado en estado del arte (Sección 2.1) |
| `09_power_analysis_tesis.ipynb` | CURRENT — soporta Tabla 1 del docx v4 |
| `10_eda_robusto_tesis.ipynb` | CURRENT — EDA citado en Sección 8.1 |
| `11_eda_endomondo_nivel1.ipynb` (+ `_executed`) | **CURRENT** — EDA del dataset poblacional Nivel 1 |
| `12_nivel1_naiveautoml_endomondo.ipynb` (+ `_executed`) | **CURRENT** — entrenamiento del Nivel 1 |

---

## Si necesitas algo de aquí

- **Para citar Boston como referencia metodológica:** Sección 2.1 del docx v4 ya tiene la cita.
- **Para reproducir Capa 3 / ACWR (resultado negativo):** Notebook 06 está disponible aquí.
- **Para reproducir calibración exponente Riegel:** Notebook 03 sigue en `notebooks/` (no aquí).

**No re-introducir esta metodología al pipeline principal sin discutir con el tutor.** El pivoteo fue una decisión metodológica deliberada documentada en `THESIS_CONTEXT.md`.

---

**Última actualización:** 2026-05-09
**Movido por:** Reorganización de claridad documental.
**Decisión registrada en:** `Documentos Maestria/THESIS_CONTEXT.md` — Sección 12 (Errores corregidos) y Sección 14 (Inventario).
