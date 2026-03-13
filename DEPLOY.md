# Deploy — Running Coaching Backend

**Plataforma:** Railway (docker-based)
**URL prevista:** `https://<proyecto>.up.railway.app`
**Comando de arranque:** inyectado por Dockerfile CMD (uvicorn + PORT dinámico)

---

## Variables de entorno en Railway

### Obligatorias para arrancar el backend (sin estas el servicio no levanta útil)

| Variable | Valor | Cómo obtenerlo |
|---|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Supabase → Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | `eyJ...` | Supabase → Settings → API → service_role key |
| `ALLOWED_ORIGINS` | `https://app.arathleteslab.com` | Dominio del portal (sin barra final) |

### Obligatorias para sync completo (pipeline + Strava + Google Sheets)

| Variable | Valor | Cómo obtenerlo |
|---|---|---|
| `GOOGLE_SA_JSON_B64` | base64 del JSON del service account | Ver instrucción abajo |
| `GOOGLE_SA_JSON` | `secrets/google_service_account.json` | Path fijo (el Dockerfile lo reconstruye) |
| `SHEET_ID` | ID del Google Sheet | URL del sheet: `/d/{SHEET_ID}/edit` |
| `STRAVA_CLIENT_ID` | `180293` | Strava → Settings → API |
| `STRAVA_CLIENT_SECRET` | `xxx` | Strava → Settings → API |

### Opcionales (demo mínima funciona sin ellas)

| Variable | Default | Efecto si falta |
|---|---|---|
| `API_KEY` | vacío | Endpoints públicos sin auth |
| `DATA_DIR` | `data/athletes` | Directorio de datos local (efímero en Railway) |
| `TIMEZONE` | `America/Bogota` | Zona horaria para cálculos |

---

## Cómo preparar GOOGLE_SA_JSON_B64

En tu máquina local, con el archivo `secrets/google_service_account.json`:

```bash
# macOS/Linux:
base64 -w 0 secrets/google_service_account.json

# Windows (PowerShell):
[Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets\google_service_account.json"))
```

El output (una línea larga, sin saltos de línea) va como valor de `GOOGLE_SA_JSON_B64` en Railway.

El Dockerfile lo decodifica y escribe en disco antes de iniciar uvicorn.

---

## Pasos de deploy en Railway

1. **Crear proyecto en Railway**: New Project → Deploy from GitHub Repo → seleccionar `andresfere25/running_coaching`
2. **Agregar las variables** (Settings → Variables) — todas las de la tabla "Obligatorias"
3. **Primer deploy**: Railway detecta el `Dockerfile` y el `railway.json` automáticamente
4. **Verificar healthcheck**: Railway muestra el status en el dashboard. El endpoint `/health` debe devolver `{"status": "ok"}`
5. **Obtener la URL pública**: Settings → Networking → Generate Domain

---

## Qué verificar después del deploy

```bash
# 1. Health
curl https://<url>.up.railway.app/health

# 2. Atleta real
curl https://<url>.up.railway.app/athletes/1070982737/snapshot

# 3. Dashboard (abrir en browser)
https://<url>.up.railway.app/app

# 4. Predicción
curl https://<url>.up.railway.app/athletes/1070982737/prediction?target=21K

# 5. Coach content
curl https://<url>.up.railway.app/athletes/1070982737/coach-content
```

---

## Nota sobre data/ en Railway

El contenedor Railway tiene filesystem efímero. Los datos de `data/athletes/` NO persisten entre deploys.

**Para demo**: los datos del atleta ya están en Supabase (push validado localmente).
Los endpoints `/snapshot`, `/plan`, `/features`, `/coach-content`, `/prediction` leen desde Supabase cuando está configurado.

**Para sync nuevo**: `POST /athletes/{cedula}/sync` corre el pipeline y escribe a Supabase.
Requiere que Google Sheets esté configurado (`GOOGLE_SA_JSON_B64` + `SHEET_ID`).

---

## Pendiente (no bloquea demo)

- [ ] `ALLOWED_ORIGINS` ajustado al dominio final de Railway
- [ ] `API_KEY` configurado para proteger endpoints en producción
- [ ] RLS en Supabase activado por atleta
- [ ] Sync periódico (ver sección "Operación productiva" en CLAUDE.md)
