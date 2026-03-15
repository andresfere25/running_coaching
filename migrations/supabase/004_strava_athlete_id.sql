-- 004_strava_athlete_id.sql
-- Adds strava_athlete_id column to athletes table.
--
-- Purpose: map Strava's owner_id (sent in webhook events) → cedula,
-- so the deauthorize webhook can find and delete the correct athlete's data.
--
-- Run in Supabase SQL Editor: https://app.supabase.com → SQL Editor → New query

ALTER TABLE athletes ADD COLUMN IF NOT EXISTS strava_athlete_id TEXT;

-- Unique index: one Strava account can only be connected to one cedula.
-- Partial index (WHERE NOT NULL) avoids conflicts on rows without Strava connected.
CREATE UNIQUE INDEX IF NOT EXISTS athletes_strava_athlete_id_idx
    ON athletes (strava_athlete_id)
    WHERE strava_athlete_id IS NOT NULL;
