# Running Coaching System

Sistema de coaching personalizado para corredores. Integra datos de **Strava**, **Google Forms** y **Google Sheets** para generar un plan de entrenamiento semanal adaptado a cada atleta.

Proyecto de grado — Maestría en Analítica Aplicada.

---

## Objetivo del proyecto

El proyecto tiene dos dimensiones:

**a) Componente académico / ML**
Diseñar y evaluar una metodología de predicción de rendimiento en running, entrenar modelos con datasets reales y comparar contra baselines como Riegel.

**b) Componente aplicado / producto**
Construir una app/web de coaching con interfaz para que los atletas vean su progreso, métricas, plan y evolución.

> El pipeline actual genera PDFs como salida legado. El objetivo futuro es una app/web.
> Ver `CLAUDE.md` para el contexto técnico completo del proyecto.

---

## Cómo funciona hoy

```
Google Forms (perfil + check-in semanal)
        ↓
Google Sheets (fuente central de datos)
        ↓
Strava API (actividades reales del atleta)
        ↓
Pipeline Python: ETL → features → plan → PDF (legado)
        ↓
Reporte PDF por atleta  [fallback mientras no hay app/web]
```

El pipeline corre **automáticamente cada lunes a las 6am** vía GitHub Actions.

---

## Estructura del proyecto

```
running_coaching/
├── src/
│   ├── ingest/          # Lee Forms y Sheets (ETL)
│   ├── strava/          # Conecta con Strava API (OAuth)
│   ├── features/        # Calcula ACWR, zonas, semáforo
│   ├── plan/            # Construye el plan semanal
│   └── reports/         # Genera el PDF (legado/fallback)
├── tests/               # Tests de sanidad (pytest)
├── ml/                  # Modelos ML y notebooks (en construcción)
├── .github/workflows/   # GitHub Actions (automatización semanal)
├── CLAUDE.md            # Contexto operativo para Claude Code
├── run_pipeline.py      # Orquestador principal
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## Configuración inicial

### 1. Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/running-coaching.git
cd running-coaching
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales reales
```

### 4. Agregar credenciales de Google

Coloca `google_service_account.json` en la carpeta `secrets/`.
Esta carpeta está en `.gitignore` y nunca se sube al repo.

---

## Correr el pipeline localmente

```bash
# Un atleta específico
python run_pipeline.py --cedula 1070982737

# Todos los atletas en data/athletes/ (solo local, no en CI)
python run_pipeline.py --all

# Solo features + PDF (sin re-ingestar desde Sheets)
python run_pipeline.py --cedula 1070982737 --steps features plan pdf

# Sin Strava (solo Forms + check-ins)
python run_pipeline.py --cedula 1070982737 --skip-strava
```

---

## Variables de entorno

Ver `.env.example` para la lista completa con descripciones.

| Variable | Descripción |
|---|---|
| `SHEET_ID` | ID del Google Sheet master |
| `GOOGLE_SA_JSON` | Ruta al service account JSON |
| `STRAVA_CLIENT_ID` | Client ID de la app Strava |
| `STRAVA_CLIENT_SECRET` | Client Secret de la app Strava |
| `DATA_DIR` | Carpeta local de datos (default: `data/athletes`) |
| `TIMEZONE` | Zona horaria (default: `America/Bogota`) |
| `ANTHROPIC_API_KEY` | (Opcional) Para integración con Claude API |

---

## Automatización con GitHub Actions

El workflow `.github/workflows/weekly_pipeline.yml` se activa:
- **Automáticamente** cada lunes a las 6:00am (hora Bogotá)
- **Manualmente** desde GitHub → Actions → Run workflow

Los PDFs generados quedan disponibles como artefactos por 30 días.

### Secrets requeridos en GitHub

Ir a: Repo → Settings → Secrets and variables → Actions

| Secret | Descripción |
|---|---|
| `SHEET_ID` | ID del Google Sheet |
| `GOOGLE_SA_JSON` | Contenido completo del service_account.json |
| `STRAVA_CLIENT_ID` | Client ID Strava |
| `STRAVA_CLIENT_SECRET` | Client Secret Strava |
| `CEDULA_DEFAULT` | Cédula del atleta a procesar en CI (ej: `1070982737`) |
| `ANTHROPIC_API_KEY` | (Opcional) API key de Anthropic |

> **Nota**: el flag `--all` solo funciona localmente donde existe `data/athletes/`.
> En CI siempre se usa `--cedula` con el valor de `CEDULA_DEFAULT`.

---

## Datos por atleta

Cada atleta tiene su carpeta en `data/athletes/{cedula}/` (gitignoreada, solo local):

```
data/athletes/1070982737/
├── raw/          # Datos originales sin tocar
├── silver/       # Datos normalizados (Parquet)
├── meta/         # profile.json + latest_checkin.json
├── features/     # weekly_features.parquet + athlete_snapshot.json + weekly_plan.json
└── outputs/      # PDFs generados (legado)
```

---

## Tests

```bash
pip install pytest
pytest tests/
```

Los tests de sanidad verifican importaciones y lógica central sin requerir credenciales externas.

---

## Stack técnico actual

- **Python 3.12** — pipeline principal
- **DuckDB + Pandas** — procesamiento de datos (Parquet sin PyArrow)
- **gspread** — Google Sheets API
- **Strava API v3** — actividades de running
- **ReportLab + Matplotlib** — generación de PDFs (legado)
- **GitHub Actions** — automatización semanal

## Stack objetivo futuro

- **FastAPI** — backend API REST
- **Supabase / PostgreSQL** — base de datos central (reemplaza Google Sheets + `data/` local)
- **Next.js o SvelteKit** — frontend del dashboard
- **Anthropic Claude API** — observaciones personalizadas
