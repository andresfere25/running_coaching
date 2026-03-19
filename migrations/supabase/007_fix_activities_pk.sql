-- Running Coaching — Supabase migration 007
-- Fix PK de activities: garantizar PRIMARY KEY (strava_id, cedula).
--
-- Si la tabla fue creada antes con solo strava_id como PK,
-- el on_conflict de writer.py falla con "no unique constraint matching".
-- Este script recrea el constraint correcto sin perder datos.
--
-- Ejecutar en: Supabase Dashboard → SQL Editor

-- 1. Eliminar PK existente (cualquiera que tenga)
ALTER TABLE activities
    DROP CONSTRAINT IF EXISTS activities_pkey;

-- 2. Crear PK compuesta correcta
ALTER TABLE activities
    ADD CONSTRAINT activities_pkey PRIMARY KEY (strava_id, cedula);
