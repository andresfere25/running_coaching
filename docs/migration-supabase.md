# Migración a Supabase — Guía operativa

## Resumen de fases

| Fase | Descripción | Estado |
|------|-------------|--------|
| **A — Espejo** | Pipeline local → dual write a Supabase. `data/` sigue siendo la fuente de verdad. | **Implementado** |
| **B — Fuente primaria** | Supabase reemplaza `data/`. API lee de Supabase. | Pendiente |
| **C — Multi-atleta** | Múltiples cédulas, CI real, deploy en Railway/Fly.io | Pendiente |

---

## Fase A — Setup mínimo (puedes hacer esto hoy)

### 1. Crear proyecto en Supabase

1. Ve a [supabase.com](https://supabase.com) → New project
2. Nombre sugerido: `running-coaching`
3. Región: `South America (São Paulo)` → reduce latencia
4. Guarda la contraseña de la base de datos

### 2. Crear el schema

En **Supabase Dashboard → SQL Editor**, ejecuta:

```sql
-- Pega el contenido completo de:
migrations/supabase/001_initial_schema.sql
```

Esto crea 6 tablas: `athletes`, `athlete_profiles`, `activities`, `checkins`, `weekly_features`, `weekly_plans`, `athlete_snapshots`.

### 3. Obtener credenciales

**Supabase Dashboard → Settings → API:**

| Variable | Dónde está | Para qué |
|----------|-----------|---------|
| `SUPABASE_URL` | "Project URL" | Base URL del proyecto |
| `SUPABASE_SERVICE_KEY` | "service_role" key | Escritura desde el backend (**NO usar la anon key**) |

> ⚠️ La `service_role` key tiene acceso total. Nunca la expongas en el frontend ni en el repo.

### 4. Configurar en `.env`

```bash
# Copia .env.example → .env (si no lo has hecho)
cp .env.example .env

# Agrega las variables:
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### 5. Instalar el SDK de Supabase

```bash
pip install "supabase>=2.0.0"
```

O descomenta la línea en `requirements.txt` y ejecuta `pip install -r requirements.txt`.

### 6. Verificar que funciona

```bash
# Levantar la API
uvicorn api.main:app --reload --port 8000

# Probar endpoint de sync (asume que el pipeline ya corrió)
curl -X POST "http://localhost:8000/athletes/1070982737/sync/push"
```

Respuesta esperada:
```json
{
  "ok": true,
  "cedula": "1070982737",
  "results": {
    "athletes": "athletes upserted",
    "profile": "athlete_profiles upserted",
    "snapshot": "athlete_snapshots upserted",
    "weekly_features": "weekly_features: 24 rows upserted",
    "plan": "weekly_plans upserted",
    "checkin": "checkins upserted"
  },
  "errors": {}
}
```

---

## Endpoints nuevos (Fase A)

### `POST /athletes/{cedula}/sync`
Pipeline completo + push a Supabase (background).

```bash
# Flujo completo: ingest + features + plan + push a Supabase
curl -X POST "http://localhost:8000/athletes/1070982737/sync"

# Sin Strava, solo features + plan
curl -X POST "http://localhost:8000/athletes/1070982737/sync?steps=features&steps=plan"

# Sin push a Supabase (solo pipeline local)
curl -X POST "http://localhost:8000/athletes/1070982737/sync?push=false"
```

### `POST /athletes/{cedula}/sync/push`
Push inmediato de datos locales existentes a Supabase (síncrono, sin pipeline).

```bash
curl -X POST "http://localhost:8000/athletes/1070982737/sync/push"
```

### `GET /athletes/{cedula}/sync/status`
Estado del último sync.

```bash
curl "http://localhost:8000/athletes/1070982737/sync/status"
# → { "status": "ok", "started_at": "...", "finished_at": "...", "supabase": {...} }
```

### `GET /athletes/{cedula}/report.pdf`
Descarga el último PDF generado.

```bash
curl -o report.pdf "http://localhost:8000/athletes/1070982737/report.pdf"
```

---

## Endpoints existentes (sin cambios)

| Endpoint | Descripción |
|----------|-------------|
| `GET /health` | Healthcheck |
| `GET /athletes` | Listar atletas con data local |
| `GET /athletes/{cedula}/profile` | Perfil del atleta |
| `GET /athletes/{cedula}/snapshot` | Estado actual (semáforo, readiness, ACWR) |
| `GET /athletes/{cedula}/plan` | Plan semanal |
| `GET /athletes/{cedula}/features?weeks=N` | Historial de features |
| `GET /athletes/{cedula}/checkin` | Último check-in |
| `GET /athletes/{cedula}/prediction?target=21K` | Predicción de tiempo/ritmo |
| `POST /athletes/{cedula}/pipeline` | Pipeline local (background, sin Supabase) |

---

## Workflow recomendado (Fase A)

```
1. python run_pipeline.py --cedula 1070982737     # pipeline completo
2. POST /athletes/1070982737/sync/push             # push a Supabase

# O en un solo paso (pipeline + push en background):
3. POST /athletes/1070982737/sync
4. GET  /athletes/1070982737/sync/status           # verificar resultado
```

El lunes (GitHub Actions) todavía corre `run_pipeline.py --cedula {CEDULA_DEFAULT}`.
Para agregar el push automático, añade una llamada HTTP al workflow o ejecuta
`push_all()` desde `run_pipeline.py` al final (Fase B).

---

## Schema de tablas

| Tabla | Filas esperadas | Poblada por |
|-------|----------------|-------------|
| `athletes` | 1 por atleta | `push_athlete()` |
| `athlete_profiles` | 1 por atleta | `push_profile()` |
| `weekly_features` | N semanas por atleta | `push_weekly_features()` |
| `checkins` | 1+ por atleta | `push_checkin()` (solo el último por ahora) |
| `weekly_plans` | 1 por atleta por semana | `push_plan()` |
| `athlete_snapshots` | 1 por atleta | `push_snapshot()` |
| `activities` | 0 en Fase A | Pendiente Fase B (webhook Strava) |

---

## Qué queda fuera en Fase A

| Tema | Por qué | Cuándo |
|------|---------|--------|
| `activities` sync masivo | Hasta 1000+ filas por atleta — implementar con paginación en Fase B | Fase B |
| Leer desde Supabase en los GET endpoints | Endpoints leen de `data/` local; migrar query por query | Fase B |
| Auth (JWT/RLS) en Supabase | No hay usuarios todavía | Fase C |
| Race_snapshots | No se exportan en Fase A (Layer 4 pendiente) | Futuro |
| Google Sheets → Supabase migration | Las hojas de Forms son la fuente de ingesta hasta Fase B | Fase B |

---

## Fase B — Plan (referencia futura)

1. **Leer desde Supabase en los GET endpoints**: modificar `api/deps.py` para que `get_athlete_dir` sea opcional y los routers acepten Supabase como fuente.
2. **Migrar `get_snapshot()`**: leer de `athlete_snapshots` en vez de `athlete_snapshot.json`.
3. **Migrar `get_features()`**: leer de `weekly_features` en vez de `.parquet`.
4. **Pipeline escribe directo a Supabase**: modificar `build_features.py` para llamar `push_all()` al final de `main()`.
5. **Activities sync**: añadir `push_activities()` en `writer.py`, llamada desde `sync_strava.py`.

---

## Debugging

```bash
# Ver logs de Supabase (útil si el push falla)
# Supabase Dashboard → Logs → API

# Verificar que las tablas se crearon
# Supabase Dashboard → Table Editor

# Test manual del cliente
python -c "
from src.storage.supabase_client import get_client
c = get_client()
print('OK' if c else 'No configurado')
"

# Ver estado del sync
curl "http://localhost:8000/athletes/1070982737/sync/status"
```
