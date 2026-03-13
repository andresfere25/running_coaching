-- ============================================================
-- Running Coaching — Migración 003: campos effective_week en coach_content
--
-- Propósito: almacenar el plan efectivo calculado server-side al publicar.
-- Evita que el frontend re-derive la distribución desde fuentes mezcladas.
--
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ============================================================

ALTER TABLE coach_content
    ADD COLUMN IF NOT EXISTS week_type_override     TEXT,
    ADD COLUMN IF NOT EXISTS week_type_system       TEXT,
    ADD COLUMN IF NOT EXISTS effective_week_type    TEXT,
    ADD COLUMN IF NOT EXISTS effective_distribution JSONB,
    ADD COLUMN IF NOT EXISTS effective_totals       JSONB;

COMMENT ON COLUMN coach_content.week_type_override     IS 'Override del coach sobre el tipo de semana del sistema (PROGRESO, CONSERVADORA, DESCARGA). Null = usar week_type_system.';
COMMENT ON COLUMN coach_content.week_type_system       IS 'Tipo de semana calculado automáticamente por el sistema (del plan base).';
COMMENT ON COLUMN coach_content.effective_week_type    IS 'week_type_override ?? week_type_system. Fuente única para el badge visible al atleta.';
COMMENT ON COLUMN coach_content.effective_distribution IS 'Distribución de km recalculada al publicar: {easy_km, fondo_km, quality_km}. Usa category explícita de overrides; fallback heurística de título para sesiones base.';
COMMENT ON COLUMN coach_content.effective_totals       IS 'Totals recalculados al publicar: {km, days_running, days_strength, days_rest}.';
