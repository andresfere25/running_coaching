# ml/ — Componente de Machine Learning

Este módulo contiene el trabajo de modelado predictivo del proyecto de Maestría.
Es independiente del pipeline operacional (`run_pipeline.py`).

## Propósito

- Entrenar y evaluar modelos de predicción de rendimiento en running
- Comparar contra baselines (Riegel, McMillan)
- Producir el componente metodológico del proyecto académico

## Datasets disponibles

Ver `Datasets running/` en la raíz del repo (no rastreado por git):

| Dataset | Ruta | Uso |
|---|---|---|
| Running mundial 2019-2020 | `16620238/*.parquet` | Feature engineering CTL/ATL/ACWR |
| Resultados maratón 2023 | `archive (3)/Results.csv` | Variable objetivo: tiempo de carrera |
| Fitbit + PMSys | `pmdata/p01/` | Zonas con HR real, fatiga percibida |
| Archivos TCX | `tcx-test-files-main/` | Validación de pipeline (no entrenar) |

## Features clave a calcular

- **CTL** (Carga Crónica): EWMA de distancia, ventana 28 días
- **ATL** (Carga Aguda): EWMA de distancia, ventana 7 días
- **ACWR** = ATL / CTL (zona segura: 0.8–1.3)
- **EF** (Factor de Eficiencia) = ritmo (m/s) / FC media
- **Monotonía** = media carga semanal / std carga semanal

## Baseline de comparación

**Fórmula de Riegel**: `T2 = T1 × (D2 / D1)^1.06`
- T1 = tiempo conocido en distancia D1
- T2 = predicción para distancia D2

Todo modelo debe superar este baseline para justificar su complejidad.

## Estructura esperada

```
ml/
├── notebooks/
│   ├── 01_eda_marathon_results.ipynb
│   ├── 02_feature_engineering_acwr.ipynb
│   ├── 03_model_race_time_prediction.ipynb
│   └── 04_validation_vs_riegel.ipynb
├── models/
│   └── (artefactos serializados: .joblib, .onnx)
├── train.py          (script reproducible de entrenamiento)
└── README.md
```

## Consideraciones metodológicas

- Usar **GroupKFold** agrupando por `athlete` para evitar data leakage
- El modelo de producción debe integrarse como endpoint del backend (no como parte del pipeline semanal)
- Documentar métricas: MAE, RMSE, R² comparados contra Riegel
