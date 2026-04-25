# CLAUDE.md — Running Coaching Project

Archivo de contexto operativo para Claude Code. Léelo al inicio de cada sesión.

---

## 🔴 ESTADO ACTUAL — Sesión 2026-04-24 (LEER PRIMERO)

Esta sección es el snapshot al final de la jornada del 24 de abril. Si abres una sesión nueva (incluso en otro computador vía OneDrive), **empieza por aquí**.

### Entorno

- **SO**: Windows · shell bash disponible vía Git Bash
- **Python**: 3.14.3 — ⚠️ **no hay wheels para muchos paquetes ML** (naiveautoml, xgboost, lightgbm fallaron). Usar sólo `scikit-learn`, `scikit-posthocs`, `pandas`, `numpy`, `matplotlib`, `python-docx`, `pyarrow`.
- **pandoc**: NO instalado. Para editar .docx usar `python-docx` directamente o el skill `anthropic-skills:docx` (unpack/pack XML).
- **OneDrive root**: `C:/Users/RentAdvisor/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/`

### Qué está hecho (Nivel 1 cerrado)

El **Nivel 1 del modelo jerárquico está entrenado, validado y serializado**. Resultados:

| Métrica | Valor |
|---|---|
| Modelo ganador | **Ridge** (alpha=1.0) |
| Feature set | **v4 (+dens_hr)** — 8 features |
| N sesiones / usuarios | 20 710 / 356 |
| MAE CV (GroupKFold K=10) | **40.16 sec/km** |
| R² | **+0.188** |
| Conformal (α=0.20) | ±63.6 sec/km, cobertura empírica 0.800 |
| vs Baseline Karvonen | −3.75 sec/km (−8.5 %), Friedman p=0.0064 |

**Features ganadoras (v4):** `gender_bin`, `fcmax_obs`, `hr_mean`, `pct_fcmax`, `zona_num`, `hr_max_rel`, `log_duration`, `dens_hr`.

**Conclusión metodológica:** el techo del prior poblacional está en ~40 sec/km con variables agregadas por sesión. Feature engineering adicional no cierra la brecha — requiere datos individuales del atleta (Niveles 2 y 3).

### Artefactos producidos (rutas absolutas)

**Scripts reproducibles** (en `ml/scripts/`):
- `nb11_full_scale.py` — EDA streaming de endomondoHR.json (6.6 GB) → parquet
- `nb12_full_run.py` — entrenamiento + Friedman-Nemenyi + conformal
- `nb12_v2_feature_engineering.py` — grid 5 feature sets × 5 modelos
- `nb12_full_figures.py` — genera figuras 1, 2 y 3
- `insert_nivel1_into_v3.py` — inserta sección 8.10 en el docx
- `fix_v3_coherence.py` — embebió imágenes + reescribió 6.1/6.4/8.1/8.4
- `clean_boston_v3.py` — eliminó 8.2/8.3/8.4 Boston y reescrituras
- `add_citations_and_refs.py` — citas APA al glosario + 22 referencias
- `build_session_doc.py` — generador de la bitácora de sesión

**Datos procesados** (en `ml/notebooks/outputs/nb11/`):
- `endomondo_runs_clean_FULL.parquet` — 20 710 sesiones limpias
- `user_summary_FULL.parquet` — 356 usuarios con FCmax_obs estable

**Modelos serializados** (en `ml/notebooks/outputs/nb12/`):
- `nivel1_prior_poblacional_FULL.pkl` — Ridge v1 (5 features, histórico)
- `nivel1_prior_poblacional_FULL_v2.pkl` — **Ridge v4 (8 features, RECOMENDADO)**

**Figuras** (en `ml/notebooks/outputs/nb12/`):
- `nb12_full_fig1_ranking_modelos.png` — ranking 9 modelos
- `nb12_full_fig2_cobertura_conformal.png` — 0.804 vs 0.80
- `nb12_full_fig3_ritmo_por_zona.png` — boxplot Z1–Z5
- `nb12_v2_fig_iteracion_features.png` — curva FE por # features

**CSVs de soporte**:
- `nb12_v2_feature_grid.csv` — 25 combinaciones
- `nb12_v2_pivot_mae.csv` — pivot modelo × feature set
- `nb12_full_report.txt` — bitácora run principal

**Diagramas** (en `Documentos Maestria/diagramas_png/`):
- `03_flujo_general.png` + `.mmd` — arquitectura macro
- `02_flujo_ml.png` + `.mmd` — detalle componente ML (landscape, flowchart LR)
- `01_flujo_datos.png` + `.mmd` — pipeline de ingesta

### Documentos de tesis al cierre de la sesión

Carpeta: `Documentos Maestria/`

| Archivo | Estado |
|---|---|
| `Avances_Tesis_Running_17abril_v1.docx` | Original (previo al reencuadre 23-abril) |
| `Avances_Tesis_Running_17abril_v2.docx` | Intermedio |
| `Avances_Tesis_Running_17abril_v3.docx` | **ACTUAL — trabajar aquí** |
| `Avances_Tesis_Running_17abril_v3.backup.docx` | Snapshot previo a inserción de 8.10 |
| `Avances_Tesis_Running_17abril_v3.pre-clean.docx` | Snapshot previo a limpieza Boston |
| `Sesion_23abril_2026_Resultados.docx` | **Bitácora ejecutiva para el director** (10 secciones, 5 figuras, 3 tablas) |
| `Resultados_Nivel1_borrador.md` | Markdown fuente con 6.X.1–6.X.8 (incluye 8.X.8 FE v2) |
| `RUNA_Flujos_Editables.md` | Fuente Mermaid de los 3 diagramas |

**Cambios aplicados a v3.docx hoy** (no deshacer sin razón):
1. Sección **8.10 · Nivel 1 · Prior poblacional FC ↔ ritmo (Endomondo)** insertada antes del Cap. 9 (8 subsecciones 8.10.1–8.10.8, 5 tablas nuevas, 4 figuras embebidas).
2. **Boston eliminado**: secciones 8.2, 8.3, 8.4 y stub 8.7 borradas (tablas incluidas).
3. **Reescrituras**: 6.1, 6.4, 6.5, 6.7, 8.1 para coherencia con FitRec como fuente primaria.
4. **Glosario (Cap. 5)** con citas APA en 23/24 definiciones; título limpio.
5. **Cap. 11 Referencias** poblado con 22 entradas APA 7 consolidadas.

### Pendientes inmediatos cuando retomes

1. **Revisión manual del usuario** de los objetivos específicos OE1/OE2/OE3 en Cap. 4 — quedaron "muy cargados". El usuario lo hace él mismo; cuando pregunte, tenemos preparadas dos redacciones alternativas del **objetivo general** (ver historial de chat: versión A neutra vs. B explícita con LOAO-CV).
2. **Nivel 2 (NB13)** — DIFERIDO 8 días (hasta ~2026-05-02) porque la cohorte RUNA aún no está completa. El usuario dijo textualmente: "No tengo los atletas aun, entonces eso dejemoslo para dentro de 8 dias".
3. **Nivel 3** — personalización bayesiana, pendiente post-Nivel 2.

### Si el usuario te muestra un docx con Boston residual

Ya se limpió la versión v3 actual. Si aparecen menciones, pueden ser:
- Legítimas: "Riegel" / "VDOT" / "Karvonen" como baselines en glosario (Cap. 5) o comparación (Cap. 6.8) — OK
- "42K" como distancia objetivo en OE1/OE2 — OK
- "Gradient Boosting" como uno de los 7 modelos del AutoML-equivalente en 8.10 — OK
- Cualquier otra mención a Boston, splits, pacing artifact activo → borrarla

### Convenciones aprendidas esta sesión

- **Unidad speed en FitRec**: H2 (km/h). H1 (m/s) produce 1.5 min/km imposible.
- **Zonas Z1–Z5**: empíricas sobre FCmax_obs por usuario (no `220−edad`). Umbrales: <60 / 60-70 / 70-80 / 80-90 / ≥90 %.
- **Filtro mínimo**: ≥10 sesiones/usuario para FCmax_obs estable.
- **Target Nivel 1**: `pace_min_km` (no tiempo absoluto).
- **Grupo CV**: siempre `userId` (GroupKFold K=10, nunca KFold plano).
- **Conformal**: split 60/20/20 estratificado por atleta.
- **Path de dataset crudo**: `Datasets running/endomondoHR.json` (6.6 GB) y `endomondoMeta.json` (10 GB). Nunca cargar completos; procesar en streaming como hace `nb11_full_scale.py`.

### Patrón de trabajo con docx desde Python 3.14

```python
# NO usar string con ñ/á/í en path sin codificación explícita — rompe.
# Usar Path literal o variable:
from pathlib import Path
DOCX = Path(r'C:/Users/RentAdvisor/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/Documentos Maestria/Avances_Tesis_Running_17abril_v3.docx')
# En script a través de Bash, siempre sys.stdout.reconfigure(encoding='utf-8')
```

Para insertar párrafos antes de un ancla: `p.insert_paragraph_before(text)`. Para tablas: crearlas con `doc.add_table`, luego mover `tbl_elem.getparent().remove(tbl_elem); anchor._element.addprevious(tbl_elem)`.

### Comandos rápidos para reanudar

```bash
# Verificar que todo siga en su sitio tras cambio de máquina:
ls "C:/Users/RentAdvisor/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/ml/notebooks/outputs/nb12/"
ls "C:/Users/RentAdvisor/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/Documentos Maestria/"

# Regenerar figuras si hacen falta:
python "C:/Users/.../ml/scripts/nb12_full_figures.py"

# Regenerar bitácora de sesión:
python "C:/Users/.../ml/scripts/build_session_doc.py"
```

---

## Objetivo dual del proyecto

Este proyecto tiene **dos dimensiones que deben coexistir**, no competir:

### a) Componente académico — Machine Learning
- Diseñar y evaluar una metodología de predicción/estimación de rendimiento en running
- Entrenar y validar modelos usando los datasets en `Datasets running/`
- Comparar resultados contra baselines conocidos (Riegel, McMillan)
- Producir un componente metodológico sólido para tesis de Maestría en Analítica Aplicada

### b) Componente aplicado — Producto
- Construir una app/web de running coaching con interfaz útil para atletas
- Mostrar progreso, métricas, plan semanal, evolución y recomendaciones
- Permitir check-ins, seguimiento y visualización de carga de entrenamiento

---

## Reencuadre metodológico 2026-04-23 (DECISIÓN ACTUAL, prevalece sobre notas previas)

Tras feedback del profesor (Teams + recursos compartidos: Asana SMART goals, github.com/fmohr/naiveautoml) y análisis de los datasets `endomondoHR.json` (6 GB, ~40K corridas con HR+velocidad) y `endomondoMeta.json` (10 GB) disponibles desde 2026-04-23, se reformula el proyecto así:

### Target de predicción (unificado)
**Ritmo sostenible (min/km) por zona de FC (Z1–Z5)** para un atleta dado.
De ahí se derivan:
- Tiempo estimado en 5K / 10K / 21K (prioridad) y 42K (reporte honesto).
- Recomendación de ritmo de entrenamiento por zona (uso coaching).

Un solo modelo, dos usos. No son modelos separados.

### Arquitectura jerárquica de 3 niveles (reformulada)

| Nivel | Fuente de datos | Rol | N validado (NB09) |
|---|---|---|---|
| **Nivel 1 — Prior poblacional** | **Endomondo** (~40K corridas con HR+velocidad, público, anónimo) | Aprende relación universal FC↔ritmo por zona. Resuelve cold start. | (dataset público) |
| **Nivel 2 — Núcleo generalizable** | **RUNA: features derivados de Strava + autorreporte** | Modelo generalizable con LOAO-CV + AutoML (naiveautoml). ESTE es el modelo generalizable que pidió el profesor. | **N≥30–40 atletas, n_cal≥100 obs** |
| **Nivel 3 — Personalización bayesiana** | Historial del atleta + carreras reales + **entrenamientos duros como "race simulations"** (tempo runs/sesiones cercanas a esfuerzo de carrera) | Refina por atleta. Multiplica por 5–10× el N efectivo de "carreras". | **≥3 race simulations** |

### Legal / ético (crítico)
- **NO se entrena con datos crudos de Strava** (GPS trace). Solo features derivados por sesión: FC media, FC máx, ritmo medio, distancia, duración, fecha.
- Anonimización en ingesta: hash SHA-256 del user_id, sin nombre/email en tablas de entrenamiento.
- Consentimiento explícito Ley 1581 Colombia + términos Strava API.
- Sección "Consideraciones éticas" obligatoria en el documento de tesis.

### Datasets: qué se mantiene y qué se descarta

| Dataset | Decisión | Rol |
|---|---|---|
| **Endomondo (HR+Meta)** | ✅ INCORPORAR | Nivel 1 — prior poblacional FC↔ritmo |
| **RUNA/Strava features** | ✅ NÚCLEO | Nivel 2 — modelo generalizable |
| **Results.csv** (399K maratonistas) | ✅ MANTENER (mínimo) | Solo NB09 Análisis 4: baseline honesto IC 80% para 42K poblacional |
| **Boston 2015–2018** | ❌ DESCARTAR de la tesis | NB03/NB07/NB08 pasan a "trabajo exploratorio previo" mencionado en 1 frase / anexo. No aporta valor dado el tiempo restante. Élite-sesgado. |
| **Injury Prediction** (NB06) | ❌ FUERA DE ALCANCE | Ya decidido en 2026-03-10: evidencia débil, capa apagada. |

### Metodología ML
- **AutoML**: `naiveautoml` (github.com/fmohr/naiveautoml) — recomendación explícita del profesor.
- **Validación**: LOAO-CV (Leave-One-Athlete-Out).
- **Intervalos**: split-conformal prediction (n_cal≥100).
- **Comparación de modelos**: Friedman–Nemenyi.
- **Baselines de comparación**: Karvonen (zonas FC), VDOT (Jack Daniels), Riegel calibrado.

### Objetivos SMART (3 grupos, pendiente escritura en v2.docx)
El profesor pidió agrupar los 6 objetivos específicos previos en 3 grupos SMART:
- **OE1 (analítico)**: Nivel 1 + Nivel 2 — modelo generalizable con AutoML
- **OE2 (producto)**: Plataforma RUNA integrando Strava + motor de ritmos
- **OE3 (validación de campo)**: Cohorte RUNA + Nivel 3 bayesiano + Results.csv baseline 42K

### Cronograma forzado por tiempo restante de maestría
1. NB11 — EDA Endomondo + validar unidades de velocidad + calcular ritmo por zona FC
2. Actualizar objetivos SMART en `Avances_Tesis_Running_17abril_v2.docx`
3. Implementar Nivel 1 con naiveautoml sobre Endomondo
4. Integrar Nivel 2 con cohorte real de RUNA
5. Race simulations + refinamiento bayesiano (Nivel 3)

### Notas previas obsoletas
- La nota de 2026-03-11 "Strava: producto/visualización/coaching analytics — NO para entrenamiento del modelo base" **QUEDA SUPERADA**. Strava (vía features derivados) es ahora el núcleo generalizable (Nivel 2).
- Toda la infraestructura NB03/NB07/NB08 de Boston se conserva en el repo como trabajo exploratorio, pero **no entra al documento de tesis** salvo mención breve.

---

## Lo que se construyó en la sesión 2026-03-11

### Dashboard Strava enriquecido + pipeline de race_snapshots

#### `src/features/build_features.py` — refactor completo
- **ACWR** reemplazado por versión EWMA (via `add_load_metrics()`). Columna `acwr` conserva nombre; columnas `ctl`, `atl`, `tsb` añadidas.
- Nuevas funciones: `_compute_racha_semanas()`, `_compute_km_trend()`, `compute_readiness_score()`
- Nuevas columnas en `weekly_features.parquet`: `fondo_largo_4s`, `racha_semanas`, `km_trend`, `pace_delta_4s_sec`, `semana_spike`, `pico_semana_ratio`
- Nueva función `build_race_snapshots(profile, weekly_df)` → escribe `race_snapshots.json` (pipeline silencioso, no expuesto en API)
- `main()` añade `readiness_score` y `acwr_zone_latest` al snapshot JSON

#### `src/ingest/ingest_forms.py` — campo `race_history`
- Agrega parsing de hasta 3 carreras pasadas (Distancia / Tiempo / Fecha) al perfil normalizado
- Requiere 3 nuevos grupos de preguntas en Google Form (acción externa pendiente)

#### Frontend — panel de forma y analítica de carga
- `app.js`: `ACWR_ZONE_CONFIG`, getter `formaPanel`, chart CTL/ATL, KPI "Racha activa"
- `index.html`: Sección 1b (readiness score, CTL/ATL/TSB/ACWR grid, km_trend, pace_delta, spike alert, disclaimer heurístico), tabla historial 9 columnas (Semana/Km/Ses/Fondo/Ritmo/CTL/TSB/ACWR/Racha), gráfico CTL/ATL
- `needsMoreData` warning actualizado: menciona CTL (≥8 semanas) además de ACWR

#### Tests
- 13 nuevos tests: `compute_readiness_score`, `_compute_racha_semanas`, `_compute_km_trend`, `build_race_snapshots`
- Total: 61/61 pasando

#### Roles de datos clarificados (decisión arquitectural)
- Strava: producto/visualización/coaching analytics — **NO para entrenamiento del modelo base**
- Datasets externos (Boston, Results.csv): modelo base exclusivamente
- Personalización individual futura: solo datos del atleta (≥3 carreras con carga, Tier 3)

---

## Lo que se construyó en sesiones anteriores (2026-03-10 en adelante)

### NB06 — Capa 3: señal de carga y diseño heurístico

- `ml/notebooks/06_capa3_carga_y_riesgo_lesion.ipynb` — 23 celdas, LOAO-CV con 74 atletas
- Dataset: Injury Prediction for Competitive Runners, `week_approach_maskedID_timeseries.csv` (42K filas)
- **Hallazgos clave**:
  - ACWR ratio simple → injury: r=+0.011, injury rate PLANA entre zonas (1.40%-1.48%). Sin evidencia.
  - Señal más fuerte: `avg_exertion` (r=+0.048, +26% en medias). Débil pero presente.
  - LOAO-CV: Mediana AUC=0.607, Std=0.157, P25-P75=[0.501-0.695]. INESTABLE.
- **Decisión**: Capa 3 diseñada pero **apagada** en producción. Sin evidencia suficiente.
- Umbrales de `acwr_zone()` son heurísticos (Hulin 2016) — no validados en este dataset.

### Refactor predictor.py — rango de ritmo relativo (sesión misma)

- Remplazado MAE absoluto por `CALIBRATED_RELATIVE_MAE` (Boston NB03): elite=1.8%, sub3h=2.2%, 3to4h=3.0%, 4hplus=4.4%
- `EXTRAPOLATION_STEP_MULTIPLIER = {0:0.85, 1:1.00, 2:1.60, 3:2.30}` — heurística metodológica
- `DISTANCE_RANK` + `_extrapolation_steps()` para calcular paso de extrapolación
- Clasificación de segmento ahora desde `ref_42k = riegel(source_sec, source_km, 42.195, 1.06)` (evita "always elite")
- Fix `_fmt_time` para tiempos < 1h (MM:SS en lugar de 0:MM:SS)
- Fix confidence: no degrada si `extrapolation_steps == 0`
- Tests: 47/47 pasando

### NB05b — Validación step multipliers con splits de Boston

- `ml/notebooks/05b_validacion_step_multipliers_boston.ipynb`
- Paso 2 (10K→Full, mult=1.60): VALIDADO para 3to4h (empírico=1.66, +4%)
- Paso 3 (5K→Full, mult=2.30): heurístico conservador — pacing artifact infla el empírico a 2.85
- Capas 0–2 consideradas cerradas metodológicamente

---

## Lo que se construyó en la sesión 2026-03-10

### Módulo ML de predicción multi-distancia (commits `…` → `f5c86b4`)

#### Calibración Riegel + tests (NB03 + `src/ml/riegel.py`)
- `ml/notebooks/03_calibracion_riegel_boston.ipynb` — calibración con Boston 2015-2018 (103K corredores)
- Exponentes calibrados por segmento: elite=1.0366, sub3h=1.0332, 3to4h=1.0613, 4hplus=1.1100
- `riegel_calibrated()` y `predict_from_profile_calibrated()` en `src/ml/riegel.py`
- Tests ampliados a 39 (todos pasan); corrección de bugs en tests de segmento

#### Arquitectura 4 capas + corrección demográfica (NB04)
- `ml/notebooks/04_arquitectura_capas_y_correccion_demografica.ipynb`
- Capa 2a: prior demográfico desde Results.csv (429K maratonistas) — MAE ~35-45 min
- Capa 2b: corrección residual Ridge sobre Boston por edad×género — mejora ~1-2 min
- Confirmado: Results.csv es solo 42K; 16620238 no tiene tiempos de carrera (solo volumen)

#### Sistema integrado de predicción (NB05 + `src/ml/predictor.py`)
- `ml/notebooks/05_sistema_prediccion_integrado_y_rango_ritmo.ipynb`
- `src/ml/predictor.py` — Capas 0+1+2 operativas; Capas 3+4 reservadas
- Salida: `pace_range_fmt` (ritmo ± MAE), `time_range_fmt`, `confidence`, `layers_active`
- `check_pr_consistency()` — detecta PRs contradictorios
- Run Club dataset descartado (confirmado sintético: 97.5% tiempos enteros, 221 valores únicos en 80K filas)

#### Endpoint de predicción + panel en el dashboard (commit `f5c86b4`)
- `GET /athletes/{cedula}/prediction?target=5K|10K|21K|42K` en `api/routers/athletes.py`
  Lee PRs desde `profile.json`, llama `predict_race_time_range()`, retorna JSON completo
- `frontend/app.js`: estado `prediction`/`predTarget`, `fetchPrediction()`, auto-set desde `race_distance`
- `frontend/index.html`: sección 2.5 con selector de distancia, rango de ritmo, badge de confianza,
  capas activas, corrección demográfica, soporte empírico, nota de extrapolación descendente

---

## Lo que se construyó en la sesión 2026-03-07

### Estabilización del repo (commit `12909a5`)
- `CLAUDE.md` creado (este archivo)
- `.env.example` creado con todas las variables documentadas
- `tests/test_sanity.py` creado — 12 smoke tests sin dependencias externas
- `ml/README.md` creado — estructura base del módulo de ML
- Fix GitHub Actions: eliminado `--all` en CI, reemplazado por lógica de 3 niveles (input → secret `CEDULA_DEFAULT` → fallback `1070982737`)
- Fix `compute_semaforo`: ahora respeta `is_recent` — check-in antiguo retorna `SIN_CHECKIN`
- `requirements.txt`: eliminados `anthropic` y `openpyxl` (sin uso)
- `pyproject.toml`: corregido entry point, `anthropic` movido a dep opcional `[ai]`, pytest configurado

### Backend FastAPI (commit `a8d6941`)
- `api/` creado con 8 endpoints funcionales sobre el pipeline existente
- `GET /health`, `GET /athletes`, `GET /athletes/{cedula}/profile|snapshot|plan|features|checkin`
- `POST /athletes/{cedula}/pipeline` — dispara pipeline en background (BackgroundTask + subprocess)
- `api/deps.py`: `sanitize_json()` resuelve NaN de pandas antes de serializar
- Frontend y backend en el mismo origen — sin CORS ni proxy
- Documentación Swagger auto-generada en `/docs`

### Frontend Alpine.js (commit `c662906`)
- `frontend/index.html` + `frontend/app.js` — dashboard completo sin Node.js
- Stack: Alpine.js 3 + Tailwind CSS Play CDN + Chart.js 4 (todo vía CDN)
- Servido por FastAPI como static files en `/app`
- Secciones: semáforo banner, 6 KPIs, plan semanal (7 col), 2 gráficos (km/ACWR), tabla de historial
- Lógica separada en `app.js` para facilitar futura migración a React

---

## Siguiente paso recomendado

**Prioridad inmediata — una acción antes de la próxima sesión de código:**
> Agregar el secret `CEDULA_DEFAULT = 1070982737` en GitHub → Settings → Secrets → Actions.
> Esto activa el workflow automático de los lunes sin intervención manual.

**Siguiente bloque de trabajo (elegir uno):**

| Opción | Qué desbloquea | Esfuerzo |
|---|---|---|
| **A — NB06: Capa 3 con Injury Prediction dataset** | Corrección por carga CTL/ATL — cierra el loop académico de la arquitectura de 4 capas | 1 sesión |
| **B — Migrar `data/` a Supabase** | CI real, deploy del backend, múltiples atletas sin depender del disco local | 2-3 sesiones |
| **C — Fix `pr_21k_sec: 85.0`** | Bug pendiente en `ingest_forms.py`: parser HH:MM → segundos falla para "1:25" | < 1 sesión |

**Recomendación**: ir por **C** primero (bug de 30 min que corrompe el predictor para el atleta real), luego **A** para completar el componente académico, luego **B** para desbloquear deploy.

---

## Estado actual vs. arquitectura objetivo

**SIEMPRE distinguir entre:**

| Dimensión | Estado actual | Objetivo futuro |
|---|---|---|
| Entregable | PDF (legado) + dashboard Alpine.js en `/app` | App/web React con auth y deploy |
| Storage | `data/` local + Google Sheets | Supabase (PostgreSQL + Storage) |
| Backend | FastAPI local (`api/`) — 9 endpoints funcionales | FastAPI deployado en Fly.io/Railway |
| Frontend | Alpine.js servido por FastAPI (sin Node) | React + Vite + Next.js |
| ML | Riegel calibrado + corrección demográfica (Capas 0-2 operativas) | Capas 3-4 + modelo supervisado con datos reales |
| CI/CD | GitHub Actions estabilizado (cédula por secret) | Pipeline + deploy automático |

**El PDF es legado/fallback.** No es el objetivo. No construir nuevas features sobre el PDF.

---

## Prioridad actual (en orden)

1. ~~Estabilizar GitHub~~ ✅ resuelto
2. ~~Backend API~~ ✅ FastAPI con 9 endpoints funcionales
3. ~~Frontend mínimo~~ ✅ Alpine.js dashboard en `/app`
4. ~~EDA + baseline ML~~ ✅ NB03–NB05: Riegel calibrado, arquitectura 4 capas, predictor multi-distancia
5. ~~Predicción en el dashboard~~ ✅ endpoint + panel visual con rango de ritmo
6. ~~Fix bug `pr_21k_sec: 85.0`~~ ✅ resuelto (parser correcto, profile.json verified)
7. ~~Refactor predictor.py — rango relativo~~ ✅ CALIBRATED_RELATIVE_MAE + step multipliers
8. ~~NB05b — validación step multipliers~~ ✅ paso 2 validado, paso 3 heurístico justificado
9. ~~NB06 — Capa 3~~ ✅ diseñada, evidencia débil → **apagada en producción**
10. ~~Dashboard Strava enriquecido~~ ✅ CTL/ATL/TSB, readiness, race_snapshots pipeline
11. ~~Agregar preguntas al Google Form~~ **FUERA DE ALCANCE por ahora** — formulario base intacto, race_history queda vacío (inofensivo), Capa 4 pospuesta
12. **Verificar end-to-end con atleta real**: correr pipeline completo y confirmar que readiness_score/acwr_zone_latest/CTL aparecen en el dashboard
13. **Migrar storage a Supabase**: desbloquea CI real y deploy
14. **Migrar frontend a React/Vite**: cuando Node.js esté disponible

---

## Reglas de trabajo para Claude Code

- No sobre-ingenierizar. El mínimo que funciona es suficiente en esta etapa.
- No introducir herramientas nuevas sin justificación pragmática explícita.
- Siempre diferenciar: estado actual / quick win / mejora de mediano plazo / arquitectura futura.
- No construir features nuevas sobre el generador de PDF.
- Si hay que elegir entre dos enfoques, elegir el más simple que resuelva el problema real.
- Hacer commits atómicos por tema (no mezclar fixes de bugs con features nuevas).

---

## Arquitectura de datos actual

```
data/athletes/{cedula}/     ← GITIGNOREADO, solo existe localmente
├── raw/                    # Datos crudos de Strava + Forms
├── silver/                 # Datos normalizados (Parquet)
├── meta/                   # profile.json, latest_checkin.json
├── features/               # weekly_features.parquet, athlete_snapshot.json, weekly_plan.json
└── outputs/                # PDFs generados (legado)
```

**Medallion pattern**: RAW → SILVER → features → outputs

---

## Fuentes de datos activas

| Fuente | Rol actual | Futura |
|---|---|---|
| Google Sheets | Fuente de verdad (perfil, check-ins, strava_tokens) | Reemplazar por DB propia |
| Google Forms | Ingesta de datos de atletas | Reemplazar por formulario web propio |
| Strava API v3 | Actividades reales por atleta (OAuth) | Mantener |
| `data/` local | Persistencia Parquet por atleta | Migrar a storage externo |

**Strava sync es siempre global**: `sync_strava.main()` no acepta `cedula`.
Opera sobre todos los atletas con `status=CONNECTED` en la hoja `strava_tokens`.

---

## Datasets externos disponibles (no integrados al pipeline aún)

Carpeta: `Datasets running/` (no rastreada por git)

| Dataset | Tipo | Uso previsto |
|---|---|---|
| `16620238/` | Parquet (diario/semanal) | Feature engineering CTL/ATL/ACWR, evolución de rendimiento |
| `archive (3)/` | CSV (Results.csv) | Target variable: predicción de tiempo de maratón por edad/género |
| `pmdata/p01/` | JSON + CSV (Fitbit/PMSys) | Zonas con HR real, ACWR con EWMA, fatiga percibida vs. FC |
| `tcx-test-files-main/` | XML (TCX) | Solo para validar pipeline de ingesta, NO para entrenamiento |

Features clave a calcular: CTL (EWMA 28d), ATL (EWMA 7d), ACWR, Factor de Eficiencia, Monotonía.
Baseline a comparar: Riegel (T2 = T1 × (D2/D1)^1.06).

---

## GitHub Actions

**Archivo**: `.github/workflows/weekly_pipeline.yml`
**Trigger**: Lunes 11:00 UTC (6am Bogotá) + manual dispatch

**Comportamiento esperado**:
- El workflow usa `--cedula` con una cédula conocida (no `--all`).
- `--all` solo funciona localmente donde `data/` existe.
- Para agregar atletas al CI, actualizar el secret `CEDULA_DEFAULT` o hacer dispatch manual.

**Secrets requeridos en GitHub**:
```
SHEET_ID
GOOGLE_SA_JSON         (contenido JSON del service account)
STRAVA_CLIENT_ID
STRAVA_CLIENT_SECRET
CEDULA_DEFAULT         (cédula por defecto, ej: "1070982737")
ANTHROPIC_API_KEY      (opcional, para cuando se implemente la IA)
```

---

## Bugs conocidos (pendientes o ya resueltos)

| Bug | Estado | Archivo | Descripción |
|---|---|---|---|
| `--all` en CI falla | Resuelto en workflow | `weekly_pipeline.yml` | `data/` gitignoreada, runner no tiene atletas |
| `is_recent` ignorado | Resuelto | `build_features.py` | Check-in viejo coloreaba semáforo como reciente |
| `pr_21k_sec: 85.0` | Resuelto | `parsers.py` / `profile.json` | Parser corregido (H:MM heurística). JSON local verificado: `pr_21k_sec=5100.0` ✓ |

---

## Evolución futura esperada (no implementar todavía)

- **FastAPI**: envolver el pipeline como API REST (sin reescribir la lógica)
- **Supabase**: reemplazar `data/` local + Google Sheets como DB central
- **Next.js / SvelteKit**: frontend del dashboard por atleta
- **Claude API**: observaciones personalizadas en reportes (ya en `.env`, pendiente código)
- **Ollama**: para tareas simples offline si se necesita inferencia sin costo de API
- **MLflow / DVC**: tracking de experimentos ML cuando el módulo `ml/` esté activo
- **Autenticación**: Supabase Auth o similar cuando haya frontend

---

## Variables de entorno

Ver `.env.example` para la lista completa. Las variables reales nunca van al repo.
El archivo `secrets/google_service_account.json` tampoco se commitea.
