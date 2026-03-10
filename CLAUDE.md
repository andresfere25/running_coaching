# CLAUDE.md — Running Coaching Project

Archivo de contexto operativo para Claude Code. Léelo al inicio de cada sesión.

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
6. **Fix bug `pr_21k_sec: 85.0`**: parser HH:MM falla en `ingest_forms.py` — el predictor da resultados incorrectos con el atleta real
7. **NB06 — Capa 3**: corrección por carga con Injury Prediction dataset (74 atletas)
8. **Migrar storage a Supabase**: desbloquea CI real y deploy
9. **Migrar frontend a React/Vite**: cuando Node.js esté disponible

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
