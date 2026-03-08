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

## Estado actual vs. arquitectura objetivo

**SIEMPRE distinguir entre:**

| Dimensión | Estado actual | Objetivo futuro |
|---|---|---|
| Entregable | PDF por atleta | App/web con dashboard interactivo |
| Storage | `data/` local + Google Sheets | Base de datos externa (Supabase/PostgreSQL) |
| Backend | Script CLI (`run_pipeline.py`) | API REST (FastAPI) |
| Frontend | Ninguno | Next.js o SvelteKit |
| ML | Heurísticas (reglas if/else) | Modelos entrenados con datos reales |
| CI/CD | GitHub Actions (parcialmente roto) | Pipeline estable + deploy automático |

**El PDF es legado/fallback.** No es el objetivo. No construir nuevas features sobre el PDF.

---

## Prioridad actual (en orden)

1. **Estabilizar GitHub**: repo limpio, Actions funcional, estrategia de automatización clara
2. **Estructura ML**: módulo `ml/` separado del pipeline operacional
3. **Backend API**: FastAPI sobre el pipeline existente (no reescribir, envolver)
4. **Frontend mínimo**: dashboard básico por atleta
5. **Modelo ML**: predicción de tiempo de carrera con datos reales

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
| `pr_21k_sec: 85.0` | Pendiente | `ingest_forms.py` | Parser de tiempo HH:MM → segundos falla para "1:25" |

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
