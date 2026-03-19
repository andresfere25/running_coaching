-- Running Coaching — Supabase migration 006
-- Tabla activities: persistencia de actividades Strava (silver).
--
-- Propósito: sobrevivir redeployments en Railway (filesystem efímero).
-- El backend restaura el parquet local desde esta tabla al inicio del pipeline
-- si el archivo no existe en disco.
--
-- Schema alineado con src/storage/reader.py (_ACTIVITY_COLS y _normalize_activity).
--
-- Ejecutar en: Supabase Dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS activities (
    strava_id        BIGINT        NOT NULL,
    cedula           TEXT          NOT NULL,
    name             TEXT,
    sport_type       TEXT,
    activity_date    TEXT,                    -- start_date_local del parquet silver
    distance_m       FLOAT,
    duration_sec     INT,                     -- moving_time_s
    elevation_m      FLOAT,                   -- total_elevation_gain_m
    avg_pace_sec_km  FLOAT,                   -- pace_sec_per_km
    raw              JSONB,                   -- campos extra (heartrate, cadence, etc.)
    synced_at        TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (strava_id, cedula)
);

CREATE INDEX IF NOT EXISTS activities_cedula_date_idx
    ON activities (cedula, activity_date DESC);
