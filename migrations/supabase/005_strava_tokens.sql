-- Running Coaching — Supabase migration 005
-- Agrega columnas de tokens Strava a la tabla athletes.
-- Permite que el backend lea tokens desde Supabase en lugar de Google Sheets,
-- cerrando el gap entre ar-athletes-portal (D1) y running_coaching (backend).
--
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- O vía CLI:   supabase db push

ALTER TABLE athletes ADD COLUMN IF NOT EXISTS strava_access_token     TEXT;
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS strava_refresh_token    TEXT;
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS strava_token_expires_at BIGINT;   -- unix timestamp
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS strava_last_sync_at     TIMESTAMPTZ;
