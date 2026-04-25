# SESSION_HANDOFF — Running Coaching / RUNA

**Último cierre:** 2026-04-24 · **Próxima reanudación:** en otra máquina vía OneDrive
**Autor del proyecto:** Andrés Felipe Restrepo Melo · Maestría Analítica Aplicada · U. La Sabana

Este archivo es el **punto de entrada único** para retomar el proyecto. Léelo completo antes de cualquier acción. Todos los detalles finos están en `CLAUDE.md` (sección "ESTADO ACTUAL 2026-04-24" al inicio).

---

## 1. Dónde está todo

Ruta raíz del proyecto:
```
C:/Users/andre/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/
```

| Carpeta | Contenido clave |
|---|---|
| `CLAUDE.md` | Contexto operativo completo. Empieza por la sección 🔴 ESTADO ACTUAL. |
| `SESSION_HANDOFF.md` | Este archivo. Índice rápido. |
| `Documentos Maestria/` | Tesis (v3.docx), bitácora de sesión (Sesion_23abril_2026_Resultados.docx), borrador MD, diagramas PNG/MMD |
| `ml/scripts/` | Todos los scripts reproducibles (9 archivos) |
| `ml/notebooks/outputs/nb11/` | Parquets procesados (20 710 sesiones) |
| `ml/notebooks/outputs/nb12/` | Modelos serializados (.pkl), figuras (.png), reportes (.csv/.txt) |
| `Datasets running/` | **MOVIDOS a `C:\Datasets\running_coaching\`** (fuera de OneDrive). Los parquets procesados siguen en `ml/notebooks/outputs/nb11/`. |

---

## 2. Estado del Nivel 1 (CERRADO)

| Métrica | Valor |
|---|---|
| Modelo | **Ridge** (alpha=1.0) sobre 8 features |
| Dataset | FitRec/Endomondo: 20 710 sesiones, 356 usuarios |
| MAE | **40.16 sec/km** (GroupKFold K=10, userId como grupo) |
| R² | +0.188 |
| Conformal | ±63.6 sec/km, cobertura empírica 0.800 (α=0.20) |
| Significancia | Friedman χ²=14.29, p=0.0064; Nemenyi: Ridge > MLP, Ridge > GB |
| Artefacto | `ml/notebooks/outputs/nb12/nivel1_prior_poblacional_FULL_v2.pkl` |

Features v4: `gender_bin, fcmax_obs, hr_mean, pct_fcmax, zona_num, hr_max_rel, log_duration, dens_hr`.

---

## 3. Estado del documento de tesis

**Archivo vivo:** `Documentos Maestria/Avances_Tesis_Running_17abril_v3.docx`

Cambios aplicados el 2026-04-24 (no deshacer sin razón):
- ✅ Sección **8.10 Nivel 1 Prior poblacional** insertada (8 subsecciones, 5 tablas, 4 figuras embebidas)
- ✅ Boston eliminado (secciones 8.2, 8.3, 8.4, 8.7 y tablas asociadas)
- ✅ Reescrituras de 6.1 / 6.4 / 6.5 / 6.7 / 8.1 para coherencia con FitRec
- ✅ 23 citas APA añadidas al glosario (Cap. 5)
- ✅ 22 referencias APA 7 en el Cap. 11
- ⏳ Objetivos específicos OE1/OE2/OE3: pendiente revisión manual del usuario

Backups:
- `Avances_Tesis_Running_17abril_v3.backup.docx` (antes de insertar 8.10)
- `Avances_Tesis_Running_17abril_v3.pre-clean.docx` (antes de limpiar Boston)

**Documento espejo para el director:** `Sesion_23abril_2026_Resultados.docx` — bitácora ejecutiva en primera persona, 10 secciones, 5 figuras, 3 tablas.

---

## 4. Próximos pasos (en orden)

1. **Usuario:** revisar y concisar OE1/OE2/OE3 en Cap. 4 de v3.docx (los siente "cargados"). Hay dos versiones alternativas del objetivo general preparadas para cuando pregunte.
2. **Diferido 8 días** (~2026-05-02): NB13 = Nivel 2 generalizable con cohorte RUNA, protocolo naiveautoml-equivalent + LOAO-CV. No empezar antes — la cohorte no está completa.
3. **Post-Nivel 2:** Nivel 3 bayesiano con race simulations.

---

## 5. Gotchas técnicos aprendidos

- **Python 3.14.3**: sin wheels para `naiveautoml`, `xgboost`, `lightgbm`. Usar sklearn + scikit-posthocs como reemplazo.
- **pandoc**: no instalado. Editar docx vía `python-docx` o el skill `anthropic-skills:docx`.
- **Paths con tildes en Bash**: el heredoc `python -c "..."` rompe con ñ/á. Escribir scripts a archivo y ejecutar con `python archivo.py`.
- **Unidad `speed` en FitRec**: km/h (no m/s — fue comprobado). Ritmo = `60 / speed`.
- **Zonas Z1–Z5**: sobre FCmax observada por usuario (`max(hr)` de sus sesiones), nunca `220−edad`.
- **CV**: siempre `GroupKFold(K=10, groups=userId)`. LOUO puro (N=356 folds) inviable.

---

## 6. Comandos para verificar integridad tras cambio de máquina

```bash
# Desde Git Bash, ajustar usuario si cambia:
PROJ="C:/Users/RentAdvisor/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching"
ls "$PROJ/ml/notebooks/outputs/nb12/"          # debe haber 2 .pkl, 4 .png, 3 csv/txt
ls "$PROJ/Documentos Maestria/"                # v3.docx, Sesion_23abril...docx, backups
ls "$PROJ/ml/scripts/" | grep nb12             # 4 scripts nb12_*
python -c "import pickle, pathlib; p=pickle.load(open(pathlib.Path(r'$PROJ/ml/notebooks/outputs/nb12/nivel1_prior_poblacional_FULL_v2.pkl'),'rb')); print(p['model_name'], p['best_mae_sec_km'])"
# Esperado: Ridge 40.16xxx
```

---

## 7. Prompt para lanzar en la nueva sesión

Copia y pega exactamente esto como primer mensaje en la nueva máquina:

```
Retomo el proyecto RUNA (Tesis Maestría Analítica Aplicada, U. La Sabana).
Acabo de cambiar de máquina; todo está en OneDrive bajo:
C:/Users/andre/OneDrive/Documentos/Maestría Analítica Aplicada/running_coaching/

Por favor:
1. Lee SESSION_HANDOFF.md en la raíz del proyecto.
2. Luego lee la sección "🔴 ESTADO ACTUAL — Sesión 2026-04-24" al inicio de CLAUDE.md.
3. Verifica que los artefactos clave existen (Avances_Tesis_Running_17abril_v3.docx,
   nivel1_prior_poblacional_FULL_v2.pkl, las 4 figuras en ml/notebooks/outputs/nb12/).
4. Resúmeme en 5 líneas: qué está cerrado, qué está pendiente y cuál es el
   próximo paso recomendado (considerando que el Nivel 2 está diferido 8 días
   hasta tener cohorte RUNA).

No ejecutes nada más hasta que yo lo autorice.
```

---
