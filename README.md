# 🏃 Running Coaching System

Sistema automatizado de coaching personalizado para corredores. Integra datos de **Strava**, **Google Forms** y **Google Sheets** para generar semanalmente un reporte PDF con el plan de entrenamiento adaptado a cada atleta.

Proyecto de grado — Maestría en Analítica Aplicada.

---

## ¿Cómo funciona?

```
Google Forms (perfil + check-in semanal)
        ↓
Google Sheets (base central)
        ↓
Strava API (actividades reales)
        ↓
Pipeline Python (ETL → features → plan → PDF)
        ↓
📄 Reporte PDF personalizado por atleta
```

El pipeline corre **automáticamente cada lunes a las 6am** vía GitHub Actions.

---

## Estructura del proyecto

```
running_coaching/
├── src/
│   ├── ingest/          # Lee Forms y Sheets
│   ├── strava/          # Conecta con Strava API
│   ├── features/        # Calcula ACWR, zonas, semáforo
│   ├── plan/            # Construye el plan semanal
│   └── reports/         # Genera el PDF
├── tests/               # Tests unitarios
├── .github/workflows/   # GitHub Actions (automatización)
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
# o editable para desarrollo:
pip install -e .
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales reales
```

### 4. Agregar credenciales de Google

Coloca el archivo `google_service_account.json` en la carpeta `secrets/`.  
Esta carpeta está en `.gitignore` — nunca se sube al repo.

---

## Correr el pipeline localmente

```bash
# Un atleta
python run_pipeline.py --cedula 1070982737

# Todos los atletas
python run_pipeline.py --all

# Solo features + PDF (sin re-ingestar)
python run_pipeline.py --cedula 1070982737 --steps features plan pdf

# Sin Strava
python run_pipeline.py --all --skip-strava
```

---

## Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `SHEET_ID` | ID del Google Sheet master |
| `GOOGLE_SA_JSON` | Ruta al service account JSON |
| `STRAVA_CLIENT_ID` | Client ID de la app Strava |
| `STRAVA_CLIENT_SECRET` | Client Secret de la app Strava |
| `DATA_DIR` | Carpeta local de datos (default: `data/athletes`) |
| `TIMEZONE` | Zona horaria (default: `America/Bogota`) |
| `ANTHROPIC_API_KEY` | (Opcional) Para observaciones con IA |

---

## Automatización con GitHub Actions

El workflow `.github/workflows/weekly_pipeline.yml` se activa:
- **Automáticamente** cada lunes a las 6:00am (hora Bogotá)
- **Manualmente** desde GitHub → Actions → Run workflow

Los PDFs generados quedan disponibles como artefactos descargables por 30 días.

### Secrets requeridos en GitHub

Ir a: Repo → Settings → Secrets and variables → Actions

| Secret | Valor |
|---|---|
| `SHEET_ID` | ID del Google Sheet |
| `GOOGLE_SA_JSON` | Contenido completo del service_account.json |
| `STRAVA_CLIENT_ID` | Client ID Strava |
| `STRAVA_CLIENT_SECRET` | Client Secret Strava |
| `ANTHROPIC_API_KEY` | (Opcional) API key de Anthropic |

---

## Datos por atleta

Cada atleta tiene su carpeta en `data/athletes/{cedula}/`:

```
data/athletes/1070982737/
├── raw/          # Datos originales sin tocar
├── silver/       # Datos normalizados
├── meta/         # profile.json + latest_checkin.json
├── features/     # weekly_features.parquet + athlete_snapshot.json
└── outputs/      # PDFs generados
```

---

## Stack técnico

- **Python 3.12** — pipeline principal
- **DuckDB + Pandas** — procesamiento de datos
- **gspread** — Google Sheets API
- **Strava API v3** — actividades
- **ReportLab + Matplotlib** — generación de PDFs
- **GitHub Actions** — automatización semanal
- **Anthropic Claude API** — observaciones personalizadas (fase 2)
