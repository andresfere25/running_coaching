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

## Arquitectura actual (estado real a 2026-05-09)

### Stack completo en producción

| Capa | Tecnología | URL / Ubicación | Estado |
|------|-----------|----------------|--------|
| Portal atletas + Coach Panel | React + Vite + Cloudflare Pages | `app.arathleteslab.com` | ✅ producción |
| API proxy + D1 | Cloudflare Worker (`worker.ts`) | mismo dominio | ✅ producción |
| Backend / pipeline | FastAPI en Railway | `runningcoaching-production.up.railway.app` | ✅ producción |
| Dashboard atleta (legado) | Alpine.js servido por FastAPI | `.../app?cedula=X` | ✅ funcional |
| Base de datos | Supabase (PostgreSQL) | proyecto Supabase privado | ✅ producción |
| Storage local | `data/athletes/{cedula}/` | local/Railway ephemeral | solo Railway |

**Google Sheets y Google Forms ya NO son fuentes activas.** Fueron reemplazados por ar-athletes-portal + Supabase.

---

## Repositorios del proyecto

### 1. `running_coaching/` — Backend FastAPI + pipeline ML
- **GitHub**: `andresfere25/running_coaching`
- **Deploy**: Railway (auto-deploy en push a `main`)
- **Path local**: `C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\running_coaching`

### 2. `ar-athletes-portal/` — Portal web (atletas + coach)
- **GitHub**: `andresfere25/ar-athletes-portal`
- **Deploy**: Cloudflare Pages (auto-deploy en push a `main`) + Cloudflare Worker
- **Path local**: `C:\Users\andre\OneDrive\Documentos\Maestría Analítica Aplicada\ar-athletes-portal`

---

## ar-athletes-portal — Arquitectura completa

### Estructura de carpetas clave

```
ar-athletes-portal/
├── worker.ts                          # Cloudflare Worker — todas las rutas API
├── wrangler.toml                      # Config Cloudflare (DB binding, vars)
├── functions/
│   └── api/
│       └── auth/
│           └── strava/
│               ├── callback.ts        # OAuth callback — guarda tokens, dispara pipeline
│               ├── initiate.ts        # Inicia OAuth Strava
│               └── deauthorize.ts     # Desconexión Strava
│       └── webhooks/
│           └── strava.ts              # Webhook Strava (actividades en tiempo real)
├── src/
│   ├── pages/
│   │   ├── invite/
│   │   │   ├── Onboarding.tsx         # Formulario 7 pasos
│   │   │   ├── Dashboard.tsx          # Dashboard mini del atleta
│   │   │   └── ConnectStrava.tsx      # Pantalla conexión Strava
│   │   └── Coach.tsx                  # Panel coach (admin)
│   └── components/
│       └── onboarding/
│           ├── StepWizard.tsx         # Controlador del form (buildPayload, submit)
│           ├── StepPersonal.tsx       # Paso 1: datos personales
│           ├── StepRunning.tsx        # Paso 2: historial running
│           ├── StepGoals.tsx          # Paso 3: objetivo y carrera
│           ├── StepAvailability.tsx   # Paso 4: horario y disponibilidad
│           ├── StepRecords.tsx        # Paso 5: PRs y ritmos
│           ├── StepHealth.tsx         # Paso 6: salud
│           └── StepConsent.tsx        # Paso 7: consentimiento
└── migrations/
    ├── 0001_initial.sql               # Tabla athletes en D1
    ├── 0004_add_external_athlete_id.sql  # cedula column en D1
    └── 0005_add_onboarding_data.sql   # onboarding_data JSON en D1
```

### Cloudflare D1 — tabla `athletes`

```sql
CREATE TABLE athletes (
  id                      TEXT PRIMARY KEY,
  invite_token            TEXT UNIQUE NOT NULL,
  name                    TEXT,
  email                   TEXT,
  strava_athlete_id       TEXT UNIQUE,
  strava_access_token     TEXT,
  strava_refresh_token    TEXT,
  strava_token_expires_at INTEGER,
  strava_scopes           TEXT,
  created_at              TEXT,
  onboarded_at            TEXT,
  external_athlete_id     TEXT,     -- cédula del atleta
  onboarding_data         TEXT      -- JSON completo del formulario
);
```

### Rutas del Cloudflare Worker (`worker.ts`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/auth/strava/initiate` | Inicia OAuth Strava |
| GET | `/api/auth/strava/callback` | Completa OAuth, guarda tokens D1, push Railway, **dispara pipeline** |
| POST | `/api/auth/strava/deauthorize` | Desconecta Strava |
| GET/POST | `/api/webhooks/strava` | Webhook actividades Strava |
| POST | `/api/onboarding/{token}` | Guarda form en D1 + proxy a Railway `/athletes/{cedula}/profile/onboarding` |
| POST | `/api/admin/pipeline/{cedula}` | Dispara pipeline para 1 atleta |
| POST | `/api/admin/pipeline/bulk` | Dispara pipeline para N atletas **en secuencia** (evita 503) |
| POST | `/api/admin/sync-onboarding` | Re-empuja datos D1 → Railway (safety net si Railway estuvo caído) |
| GET | `/api/admin/athletes` | Lista atletas de D1 con stats de Railway |
| DELETE | `/api/internal/athletes/by-cedula/{cedula}` | Elimina atleta |
| GET | `/api/internal/athletes/list-cedulas` | Lista cédulas en D1 |
| POST | `/api/internal/sync` | Sincronización D1 ↔ Supabase |

### Variables de entorno del Worker (Cloudflare)

```
DB                  D1 binding (base de datos SQLite de Cloudflare)
STRAVA_CLIENT_ID    App Strava
STRAVA_CLIENT_SECRET
APP_URL             https://app.arathleteslab.com
BACKEND_URL         https://runningcoaching-production.up.railway.app
BACKEND_API_KEY     debe coincidir con API_KEY en Railway
ADMIN_SECRET        llave para rutas /api/admin/*
```

### Flujo completo de onboarding de un atleta nuevo

```
1. Admin crea invitación en Coach Panel
   → inserta fila en D1 con invite_token y nombre

2. Atleta abre enlace: app.arathleteslab.com/invite/{token}/onboarding
   → llena formulario 7 pasos (57 campos)
   → POST /api/onboarding/{token}
      ├── Guarda en D1: onboarding_data (JSON completo) + onboarded_at + cedula
      └── Proxy a Railway: POST /athletes/{cedula}/profile/onboarding
          ├── push_athlete(cedula, name)  ← CRÍTICO: crea fila FK en athletes de Supabase
          └── push_profile(cedula, ...)   ← inserta athlete_profiles en Supabase

3. Atleta conecta Strava: /invite/{token}/connect-strava
   → callback.ts ejecuta:
      ├── Intercambia código OAuth por tokens Strava
      ├── Guarda tokens en D1
      ├── POST /athletes/{cedula}/strava/token → push_strava_tokens() en Supabase
      └── ctx.waitUntil(_triggerPipeline()) → dispara pipeline AUTOMÁTICAMENTE
          POST /athletes/{cedula}/pipeline?steps=ingest&steps=strava&steps=features&steps=plan

4. Pipeline corre en Railway (~60-90s):
   ├── strava: sincroniza actividades desde Strava API
   ├── features: calcula weekly_features, CTL/ATL/TSB, readiness
   └── plan: genera plan semanal

5. Dashboard disponible:
   ├── runningcoaching-production.up.railway.app/app?cedula={cedula}  (Alpine.js)
   └── app.arathleteslab.com/invite/{token}/dashboard  (React mini-dashboard)
```

### Safety net para datos perdidos

Si Railway estuvo caído cuando el atleta llenó el form, los datos quedan en D1 pero NO en Supabase. Recuperar con:
```
POST /api/admin/sync-onboarding  (con X-Admin-Key header)
```
Esto re-empuja todos los `onboarding_data` de D1 → Railway → Supabase.

---

## Backend FastAPI (running_coaching/api/)

### Routers activos

| Router | Prefijo | Descripción |
|--------|---------|-------------|
| `athletes.py` | `/athletes` | Perfil, snapshot, plan, features, actividades, predicción |
| `pipeline.py` | `/athletes` | POST pipeline individual + POST bulk secuencial |
| `sync.py` | `/athletes` | Sincronización Strava, push Supabase |
| `coach.py` | `/coach` | Endpoints del coach |
| `webhooks.py` | `/webhooks` | Webhook Strava |
| `health.py` | `/` | Health check |

### Endpoints clave

```
GET  /athletes                              → lista todos los atletas
GET  /athletes/{cedula}/profile             → perfil (form data)
GET  /athletes/{cedula}/snapshot            → estado actual (dashboard principal)
GET  /athletes/{cedula}/plan                → plan semanal
GET  /athletes/{cedula}/features            → historial semanal
GET  /athletes/{cedula}/activities          → actividades Strava
GET  /athletes/{cedula}/checkin             → último check-in
GET  /athletes/{cedula}/prediction          → predicción de carrera
POST /athletes/{cedula}/profile/onboarding  → recibe form del portal ← CRÍTICO
POST /athletes/{cedula}/strava/token        → recibe tokens OAuth del portal
POST /athletes/{cedula}/pipeline            → dispara pipeline (background)
POST /athletes/bulk                         → dispara pipelines en secuencia
```

### Pipeline — pasos válidos y semáforo

```python
VALID_STEPS = ["ingest", "strava", "features", "plan", "pdf"]
_PIPELINE_SEMAPHORE = threading.Semaphore(3)  # máx 3 pipelines simultáneos
```

- `ingest`: lee Google Sheets (LEGADO — solo para atletas muy antiguos sin portal)
- `strava`: sincroniza actividades desde Strava API
- `features`: calcula weekly_features.parquet, CTL/ATL/TSB, readiness_score, snapshot
- `plan`: genera weekly_plan.json con Claude/heurística
- `pdf`: genera PDF (LEGADO — no usar)

**Para atletas del portal**: usar `steps=strava&steps=features&steps=plan` (sin ingest ni pdf)

---

## Supabase — Schema de tablas

| Tabla | PK | FK | Descripción |
|-------|----|----|-------------|
| `athletes` | `cedula` | — | Registro maestro. Incluye strava_tokens |
| `athlete_profiles` | `cedula` | → `athletes.cedula` | Datos del form de onboarding |
| `activities` | `strava_id` | → `athletes.cedula` | Actividades Strava |
| `weekly_features` | UUID | → `athletes.cedula` | Features semanales (CTL/ATL/TSB/ACWR) |
| `weekly_plans` | UUID | → `athletes.cedula` | Planes semanales generados |
| `athlete_snapshots` | `cedula` | → `athletes.cedula` | Último snapshot computado |
| `checkins` | UUID | → `athletes.cedula` | Check-ins semanales |
| `coach_content` | UUID | → `athletes.cedula` | Contenido publicado por el coach |

**CRÍTICO — FK constraint**: `athlete_profiles`, `activities`, etc. referencian `athletes.cedula`.
Siempre hacer `push_athlete(cedula)` ANTES de cualquier insert en tablas hijas.
Esto ya está corregido en `create_onboarding_profile` (commit `a9db134`).

---

## Sesión 2026-05-09 — Lo que se construyó y corrigió

### 1. Fix FC (frecuencia cardíaca) vacía para atletas multi-deporte
- **Problema**: atleta con 279 actividades (solo 31 runs), `limit=30` no capturaba ningún run
- **Fix**: `activities?limit=200` en `frontend/app.js`
- **Fix adicional**: null guard en Alpine.js — `hrZoneDistribution && hrZoneDistribution.total > 0`
  (sin el `&&`, Alpine crashea silenciosamente si `hrZoneDistribution` es null)
- **Fix adicional**: `max_heartrate` agregado al schema canónico en `src/storage/reader.py`

### 2. Fix columna FC "—" en Historial Semanal
- **Problema**: `weeklyAvgHR` alineaba a martes (`(dayOfWeek + 5) % 7`) pero `build_features.py`
  usa `W-SUN` (semanas ISO que empiezan el lunes). Las claves nunca coincidían.
- **Fix**: `(dayOfWeek + 6) % 7` → alineación correcta a lunes

### 3. Fix 503 "Actualizar todos" en Coach Panel
- **Causa**: `Promise.all` con 27 atletas = 27 subprocesos Python simultáneos en Railway
- **Fix 1 (backend)**: `threading.Semaphore(3)` en `pipeline.py` — máx 3 subprocesos simultáneos
- **Fix 2 (backend)**: nuevo endpoint `POST /athletes/bulk` — procesa atletas secuencialmente
- **Fix 3 (worker)**: nueva ruta `POST /api/admin/pipeline/bulk` en `worker.ts`
- **Fix 4 (frontend)**: `Coach.tsx` — `triggerAll()` reemplaza `Promise.all` con 1 sola request
- **Commits**: `8fc6df6`, `5f5d3bc` (running_coaching) + `cc9b73c` (ar-athletes-portal)

### 4. Auto-trigger pipeline al conectar Strava
- **Dónde**: `functions/api/auth/strava/callback.ts` — nuevo Step 7
- **Cómo**: `ctx.waitUntil(_triggerPipeline(env, cedula))` — fire-and-forget, no bloquea el redirect
- **Pasos**: `ingest,strava,features,plan` — dashboard listo ~60-90s después de conectar
- **Commit**: `830db00` (ar-athletes-portal)

### 5. Fix FK constraint en onboarding (bug crítico)
- **Problema**: `POST /athletes/{cedula}/profile/onboarding` llamaba `push_profile` sin crear
  primero la fila en `athletes`. FK constraint violada → perfil nunca llegaba a Supabase.
  Todos los atletas del portal (sin Google Sheets) fallaban silenciosamente.
- **Fix**: agregar `push_athlete(cedula, name)` como paso 0 en `create_onboarding_profile`
- **Commit**: `a9db134` (running_coaching)
- **Diagnóstico**: Juan Diego Ramirez (1070985887) — form en D1, perfil ausente en Supabase

### 6. Recovery manual de Juan Diego Ramirez (1070985887)
- Form data recuperado de D1 vía wrangler CLI
- Perfil empujado manualmente a Railway después del fix FK
- Pipeline `strava+features+plan` disparado — en curso

---

## Reglas de trabajo para Claude Code

- No sobre-ingenierizar. El mínimo que funciona es suficiente en esta etapa.
- No introducir herramientas nuevas sin justificación pragmática explícita.
- Siempre diferenciar: estado actual / quick win / mejora de mediano plazo / arquitectura futura.
- No construir nuevas features sobre el generador de PDF.
- Si hay que elegir entre dos enfoques, elegir el más simple que resuelva el problema real.
- Hacer commits atómicos por tema (no mezclar fixes de bugs con features nuevas).
- **Google Sheets / Google Forms ya no son fuentes activas.** No referenciarlos como solución.
- El paso `ingest` del pipeline lee Google Sheets — solo útil para atletas muy antiguos.
  Para atletas del portal usar siempre `steps=strava,features,plan`.

---

## Prioridades actuales

1. ~~Estabilizar GitHub~~ ✅
2. ~~Backend API~~ ✅ FastAPI con endpoints completos
3. ~~Frontend mínimo~~ ✅ Alpine.js dashboard
4. ~~EDA + baseline ML~~ ✅ Riegel calibrado, arquitectura 4 capas
5. ~~Predicción en el dashboard~~ ✅ endpoint + panel visual
6. ~~Fix bugs predictor~~ ✅ MAE relativo, step multipliers
7. ~~NB06 Capa 3~~ ✅ diseñada, apagada (evidencia débil)
8. ~~Dashboard CTL/ATL/TSB~~ ✅ readiness, race_snapshots
9. ~~Migrar a Supabase~~ ✅ todas las tablas activas
10. ~~Deploy Railway~~ ✅ backend en producción
11. ~~Portal ar-athletes-portal~~ ✅ onboarding + Strava + Coach Panel
12. ~~Fix 503 Actualizar todos~~ ✅ bulk endpoint + semáforo
13. ~~Auto-trigger pipeline en registro~~ ✅ callback.ts Step 7
14. ~~Fix FK constraint onboarding~~ ✅ push_athlete antes de push_profile
15. **Fix FC zones para atletas multi-deporte** ✅ (sesión 2026-05-09)
16. **Verificar end-to-end nuevos atletas**: flujo completo sin intervención manual
17. **NB13 — Nivel 2 del modelo**: cuando N≥40 atletas con datos suficientes
18. **Chapters 9-10 de tesis**: Discusión y Conclusiones

---

## Bugs conocidos (pendientes)

| Bug | Archivo | Descripción |
|-----|---------|-------------|
| `ingest` lee Google Sheets | `ingest_forms.py` | Legado — solo funciona para atletas muy antiguos. Portal reemplaza esta fuente |
| Atletas con form en D1 sin sync a Railway | D1 / Railway | Usar `POST /api/admin/sync-onboarding` para recuperar |

---

## Arquitectura de datos local (pipeline / Railway)

```
data/athletes/{cedula}/     ← en Railway es ephemeral; fuente real = Supabase
├── raw/                    # Datos crudos de Strava
├── silver/                 # Actividades normalizadas (Parquet)
├── meta/                   # profile.json, latest_checkin.json
├── features/               # weekly_features.parquet, athlete_snapshot.json, weekly_plan.json
└── outputs/                # PDFs (LEGADO — ignorar)
```

**Medallion pattern**: RAW → SILVER → features → Supabase

---

## src/storage/reader.py — Schema canónico de actividad

```python
# 13 campos siempre presentes (Supabase o local):
activity_id, cedula, name, sport_type, activity_date,
distance_m, distance_km, duration_sec, elevation_m,
pace_sec_per_km, average_heartrate, max_heartrate, average_cadence
```

`average_heartrate` y `max_heartrate` viven en columna `raw` JSONB de Supabase.
El reader los extrae con `raw.get("average_heartrate")`.

---

## Datasets externos disponibles (ML — no en pipeline de producción)

| Dataset | Tipo | Uso |
|---------|------|-----|
| `16620238/` | Parquet diario/semanal | Feature engineering CTL/ATL/ACWR |
| `archive (3)/Results.csv` | CSV 429K maratonistas | Target variable predicción maratón |
| `pmdata/p01/` | JSON + CSV Fitbit/PMSys | Zonas HR, ACWR con EWMA |

---

## GitHub Actions

**Archivo**: `.github/workflows/weekly_pipeline.yml`
**Trigger**: Lunes 11:00 UTC + manual dispatch
**Nota**: El workflow corre el pipeline para la cédula en `CEDULA_DEFAULT`. `--all` no funciona en CI (sin `data/` local en el runner).

---

## Variables de entorno del backend (Railway)

Ver `.env.example`. Clave: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `API_KEY` (= `BACKEND_API_KEY` del Worker).

---

## Historial ML (componente académico)

- **NB03**: Calibración Riegel con Boston 2015-2018 (103K runners) → exponentes por segmento
- **NB04**: Corrección demográfica Ridge por edad×género — MAE ~35-45 min
- **NB05**: Sistema integrado de predicción multi-distancia (`src/ml/predictor.py`)
- **NB05b**: Validación step multipliers — paso 2 validado, paso 3 heurístico conservador
- **NB06**: Capa 3 lesiones — AUC 0.607, inestable → **apagada en producción**
- **NB09-12**: EDA robusto + Endomondo dataset + NaiveAutoML Nivel 1
- **Nivel 1 actual**: Ridge (`nivel1_ridge_v4.json`) — modelo en producción en Railway
- **Nivel 2**: pendiente N≥40 atletas con ≥3 carreras registradas
