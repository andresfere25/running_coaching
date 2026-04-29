# CLAUDE.md — Running Coaching Project

> Archivo de contexto operativo. **Leer entero al inicio de cada sesión** antes de tocar código.
> Actualizado: 2026-04-28

---

## 🔴 ESTADO ACTUAL (snapshot al cierre de sesión 2026-04-28)

### Entorno de desarrollo

| Qué | Valor |
|---|---|
| SO | Windows · bash disponible vía Git Bash |
| Python | **3.11** — conda env `running_coaching` (`conda activate running_coaching`) |
| Proyecto | `C:/Users/andre/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/` |
| Datasets grandes | **`C:\Datasets\running_coaching\`** — FUERA de OneDrive (no mover) |
| pandoc | NO instalado — usar `python-docx` para editar .docx |

### Infraestructura en producción (Railway)

| Componente | Estado |
|---|---|
| **FastAPI en Railway** | ✅ `https://runningcoaching-production.up.railway.app` |
| **Supabase** | ✅ almacena atletas y snapshots |
| **Strava webhook** | ✅ subscription ID 335250 (desde 2026-03-15) → `POST /webhooks/strava` |
| **GitHub Actions** | ✅ Weekly Pipeline Sync — lunes 11:00 UTC (6am Bogotá), run #11 passing |

### Automatización completa (objetivo logrado esta sesión)

- **Tiempo real**: Strava webhook → Railway dispara pipeline `strava→features→plan` en background (<2s respuesta)
- **Semanal**: GitHub Actions → `scripts/sync_all_athletes.py` → llama `GET /athletes` (auto-descubre desde Supabase) → `POST /athletes/{cedula}/sync` por cada uno
- **Nuevo atleta**: cuando completa OAuth de Strava, queda en Supabase → incluido automáticamente en el próximo weekly sync. **Zero config.**

### Secrets activos en GitHub Actions

```
RAILWAY_URL   # URL de Railway (si vacío, usa hardcoded default)
API_KEY       # Opcional — si configurado en Railway
```

> **Nota**: `CEDULA_DEFAULT` ya NO se usa. El workflow auto-descubre atletas desde Supabase vía `GET /athletes`.

### Dashboard — tabs activos

| Tab | Contenido |
|---|---|
| Hoy | Semáforo, KPIs, plan semanal, readiness score, CTL/ATL/TSB/ACWR |
| Plan | Plan semanal detallado |
| Rendimiento | Historial 9 columnas, gráfico km/ACWR |
| Analítica | Gráfico CTL/ATL, evolución carga |
| **ML** ✅ nuevo | FCmax, zonas Z1-Z5, predicción ritmo Ridge, intervalos conformales, 4 distancias |
| Glosario | Definiciones |

### Modelo ML en producción (Tab ML)

- **Nivel 1 — Prior poblacional** (Ridge v4, 8 features, Endomondo 20K sesiones)
- Coeficientes hardcodeados en `api/routers/athletes.py` — **NO requiere pkl** (Railway no tiene acceso a `ml/notebooks/outputs/`)
- MAE CV = 40.16 sec/km · R²=0.188 · Conformal ±63.6 sec/km (80% cobertura)
- FCmax: observado desde Strava (`max_heartrate`) o fallback `220−edad`
- Endpoint: `GET /athletes/{cedula}/ml-hierarchy`

### Archivos clave del sistema

```
api/routers/athletes.py      # Todos los endpoints de atletas + ml-hierarchy
api/routers/health.py        # /health con config status
frontend/index.html          # Dashboard completo (Alpine.js + Tailwind + Chart.js)
frontend/app.js              # Lógica reactiva del dashboard
scripts/sync_all_athletes.py # Script de sync (urllib puro, sin deps externas)
.github/workflows/weekly_pipeline.yml  # CI — llama al script Python
src/features/build_features.py         # Pipeline features (CTL/ATL/TSB/ACWR/readiness)
src/ml/predictor.py                    # Predictor Riegel + corrección demográfica
```

### Bugs resueltos (histórico relevante)

| Bug | Solución |
|---|---|
| PKL no encontrado en Railway | Coeficientes Ridge hardcodeados en el endpoint |
| Dashboard mostraba 11km / datos viejos | Sync manual + pipeline ahora automatizado |
| GitHub Actions runs #7–#10 fallaban | Bash+python3 inline → `sync_all_athletes.py` |
| `RAILWAY_URL=""` → URL inválida | `os.getenv("VAR") or "default"` en lugar de `getenv("VAR", "default")` |
| `pr_21k_sec: 85.0` | Parser HH:MM corregido. `pr_21k_sec=5100.0` ✓ |

---

## Objetivo dual del proyecto

### a) Componente académico — Machine Learning
Arquitectura jerárquica de 3 niveles para predecir **ritmo sostenible (min/km) por zona FC (Z1–Z5)**:

| Nivel | Datos | Estado |
|---|---|---|
| **N1 — Prior poblacional** | Endomondo (40K sesiones HR+velocidad) | ✅ Entrenado · Ridge v4 · MAE=40s/km |
| **N2 — Núcleo generalizable** | RUNA/Strava features · LOAO-CV + naiveautoml | ⏳ Pendiente cohorte RUNA (≥30 atletas) |
| **N3 — Personalización bayesiana** | Historial atleta + race simulations | 🔮 Futuro post-N2 |

**Decisiones metodológicas fijas:**
- AutoML: `naiveautoml` (recomendación del profesor)
- Validación: LOAO-CV (Leave-One-Athlete-Out)
- Intervalos: split-conformal (n_cal≥100)
- Comparación: Friedman-Nemenyi
- Baselines: Karvonen, VDOT (Jack Daniels), Riegel calibrado

### b) Componente aplicado — Producto
App de running coaching con dashboard por atleta, integración Strava, predicciones de carrera, plan semanal, análisis de carga (CTL/ATL/TSB).

---

## Pendientes inmediatos

1. **Nivel 2 (NB13)** — DIFERIDO ~2026-05-06 · la cohorte RUNA aún no está completa ("no tengo los atletas aun, dejémoslo para dentro de 8 días" — dicho el 2026-04-24)
2. **Revisión Cap. 4** — OE1/OE2/OE3 "muy cargados" — el usuario los revisa él mismo
3. **Nivel 3** — pendiente post-N2

---

## Convenciones técnicas críticas

- **Unidad speed Endomondo/FitRec**: `H2` = km/h (no H1=m/s, produce 1.5 min/km imposible)
- **Zonas Z1–Z5**: empíricas sobre FCmax_obs por usuario. Umbrales: <60/60-70/70-80/80-90/≥90 %
- **Filtro mínimo**: ≥10 sesiones/usuario para FCmax_obs estable
- **Target N1**: `pace_min_km` (no tiempo absoluto)
- **Grupo CV**: siempre `userId` (GroupKFold K=10 — nunca KFold plano)
- **Path datasets crudos**: `C:\Datasets\running_coaching\endomondoHR.json` (6.6 GB) y `endomondoMeta.json` (10 GB) — **nunca cargar completos**, usar streaming como `ml/scripts/nb11_full_scale.py`
- **Docx con rutas Python**: usar `Path(r'...')` con raw string para rutas con ñ/tildes

---

## Arquitectura de datos

```
data/athletes/{cedula}/     ← GITIGNOREADO
├── raw/                    # Strava + Forms crudos
├── silver/                 # Parquet normalizados
├── meta/                   # profile.json, latest_checkin.json
├── features/               # weekly_features.parquet, athlete_snapshot.json, weekly_plan.json
└── outputs/                # PDFs (legado — no construir features nuevas aquí)
```

**Medallion**: RAW → SILVER → features → outputs

---

## Modelo Ridge N1 — coeficientes hardcodeados (no cambiar sin reentrenar)

```python
RIDGE_COEFS    = [-0.31159683, -0.80448786,  0.65093851, -1.13399642,
                   0.02877941, -0.00124473, -0.02539242, -0.10388543]
RIDGE_INTERCEPT = 5.432260096762721
SCALER_MEAN    = [8.97827137e-01, 1.98815934e+02, 1.48784194e+02, 7.51608968e+01,
                  3.02071463e+00, 8.42795057e-01, 7.64848316e+00, 1.91625406e-01]
SCALER_SCALE   = [0.3028755, 14.86179749, 14.86155342, 8.501989,
                  0.88749327, 0.08838219, 0.91433443, 0.12751841]
CONFORMAL_Q    = 1.0605492948701531   # ±1.06 min/km, cobertura 80%
MAE_SEC_KM     = 40.157287499449254
# Features (orden): gender_bin, fcmax_obs, hr_mean, pct_fcmax, zona_num, hr_max_rel, log_duration, dens_hr
```

---

## Estado de documentos de tesis

Carpeta: `Documentos Maestria/`

| Archivo | Estado |
|---|---|
| `Avances_Tesis_Running_17abril_v3.docx` | **ACTUAL — trabajar aquí** |
| `Sesion_23abril_2026_Resultados.docx` | Bitácora ejecutiva para el director |
| `RUNA_Flujos_Editables.md` | Fuente Mermaid de diagramas |

**Cambios aplicados a v3.docx** (no deshacer):
- Sección 8.10 (Nivel 1, 8 subsecciones, 5 tablas, 4 figuras) insertada
- Boston eliminado: secciones 8.2, 8.3, 8.4
- Cap. 5 Glosario con citas APA · Cap. 11 con 22 referencias APA 7
- 6.1, 6.4, 6.5, 6.7, 8.1 reescritos para coherencia con FitRec como fuente primaria

---

## Reglas de trabajo

- No sobre-ingenierizar. El mínimo que funciona es suficiente.
- No introducir herramientas nuevas sin justificación pragmática.
- Commits atómicos por tema (no mezclar bugs con features).
- No construir features nuevas sobre el generador de PDF.
- Siempre distinguir: estado actual / quick win / arquitectura futura.
- Los datasets grandes (endomondoHR, endomondoMeta) nunca van a OneDrive.

---

## Variables de entorno

Ver `.env.example` para la lista completa. Las reales nunca van al repo.
`secrets/google_service_account.json` tampoco se commitea.
