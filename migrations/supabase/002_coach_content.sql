-- ============================================================
-- Running Coaching — Migración 002: tabla coach_content
--
-- Propósito: almacenar el contenido editorial publicado por el
-- coach en Supabase, reemplazando la dependencia de archivos
-- weekly_published.json en local.
--
-- Patrón: dual-write (publish.py escribe local + Supabase).
--         El endpoint /coach-content lee Supabase first, fallback local.
--
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ============================================================


-- ──────────────────────────────────────────────────────────────────────────
-- coach_content — Publicaciones semanales del coach por atleta
-- Clave: (cedula, week_start) — permite una publicación activa por semana
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS coach_content (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Claves de negocio
    cedula           TEXT        NOT NULL REFERENCES athletes(cedula) ON DELETE CASCADE,
    week_start       DATE        NOT NULL,

    -- Metadatos de publicación
    published_at     TIMESTAMPTZ NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'published',

    -- Contenido editorial (editable por el coach)
    coach_message    TEXT,
    weekly_focus     TEXT,
    weekly_objective TEXT,
    alerts           JSONB       NOT NULL DEFAULT '[]'::JSONB,
    restrictions     JSONB       NOT NULL DEFAULT '[]'::JSONB,
    session_notes    JSONB       NOT NULL DEFAULT '{}'::JSONB,
    plan_overrides   JSONB       NOT NULL DEFAULT '{}'::JSONB,

    -- Snapshot completo del publicado para auditoría
    raw              JSONB,

    -- Timestamps de fila
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),

    -- Una publicación por atleta por semana (permite re-publicar la misma semana)
    CONSTRAINT coach_content_cedula_week_start_key UNIQUE (cedula, week_start)
);

COMMENT ON TABLE  coach_content IS 'Contenido editorial semanal publicado por el coach. Una fila por atleta por semana.';
COMMENT ON COLUMN coach_content.week_start     IS 'Lunes de la semana (YYYY-MM-DD). Clave de negocio junto con cedula.';
-- Nota: is_stale es un campo CALCULADO en la API (week_start != lunes actual), NO se almacena.
COMMENT ON COLUMN coach_content.raw            IS 'weekly_published.json completo para auditoría y rollback.';
COMMENT ON COLUMN coach_content.plan_overrides IS 'Ajustes sobre el plan automático por día. Ej: {"SAB": {"km": 14, "pace": "6:00–6:30"}}.';
COMMENT ON COLUMN coach_content.session_notes  IS 'Notas por sesión por día. Ej: {"MIE": "Corre suave...", "SAB": "Fondo largo..."}.';


-- ── Índice para la lectura del endpoint (más reciente por atleta) ──────────
CREATE INDEX IF NOT EXISTS coach_content_cedula_week_idx
    ON coach_content (cedula, week_start DESC);


-- ── Trigger updated_at ────────────────────────────────────────────────────
-- (Reutilizar si ya existe la función moddatetime del schema inicial)
-- Si no existe, crear la función:
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER coach_content_updated_at
    BEFORE UPDATE ON coach_content
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── RLS (Row Level Security) — desactivado por ahora (usa service key) ─────
-- Cuando se implemente autenticación de atletas activar:
-- ALTER TABLE coach_content ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "coach puede ver todo" ON coach_content FOR ALL USING (true);
-- CREATE POLICY "atleta ve su propio contenido" ON coach_content FOR SELECT
--     USING (cedula = auth.uid()::text);
