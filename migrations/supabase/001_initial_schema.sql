-- ============================================================
-- Running Coaching — Supabase schema inicial
-- Fase A: Supabase como espejo de data/ local
-- Fase B (futura): Supabase como fuente primaria
--
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- O vía CLI: supabase db push
-- ============================================================


-- ──────────────────────────────────────────────────────────────
-- athletes — Registro maestro por atleta
-- Clave: cedula (ID documento, ya usada como PK en data/ local)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS athletes (
    cedula          TEXT        PRIMARY KEY,
    name            TEXT,
    invite_token    TEXT        UNIQUE,   -- futura conexión con ar-athletes-portal
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  athletes IS 'Registro maestro de atletas. Clave = cédula.';
COMMENT ON COLUMN athletes.invite_token IS 'Token del portal ar-athletes-portal (futuro).';


-- ──────────────────────────────────────────────────────────────
-- athlete_profiles — Perfil del Form de ingreso (profile.json)
-- 1:1 con athletes
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS athlete_profiles (
    cedula          TEXT        PRIMARY KEY REFERENCES athletes(cedula) ON DELETE CASCADE,
    age             NUMERIC,
    sex             TEXT,
    weight_kg       NUMERIC,
    height_cm       NUMERIC,
    pr_5k_sec       NUMERIC,
    pr_10k_sec      NUMERIC,
    pr_21k_sec      NUMERIC,
    pr_42k_sec      NUMERIC,
    race_distance   TEXT,
    race_date_raw   TEXT,
    time_goal_sec   NUMERIC,
    raw             JSONB,      -- perfil completo para campos no tipificados
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  athlete_profiles IS 'Perfil del atleta (Form 1). Sincronizado desde profile.json.';
COMMENT ON COLUMN athlete_profiles.raw IS 'JSON completo de profile.json para campos adicionales.';


-- ──────────────────────────────────────────────────────────────
-- activities — Actividades Strava por atleta
-- Poblada por sync_strava (Fase B); en Fase A queda vacía
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activities (
    strava_id       BIGINT      PRIMARY KEY,
    cedula          TEXT        NOT NULL REFERENCES athletes(cedula) ON DELETE CASCADE,
    name            TEXT,
    activity_date   TIMESTAMPTZ NOT NULL,
    distance_m      NUMERIC,
    duration_sec    NUMERIC,
    elevation_m     NUMERIC,
    avg_pace_sec_km NUMERIC,
    sport_type      TEXT        DEFAULT 'Run',
    raw             JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activities_cedula_date ON activities(cedula, activity_date DESC);
COMMENT ON TABLE activities IS 'Actividades de Strava. Fase A: vacía. Fase B: poblada por webhook o sync.';


-- ──────────────────────────────────────────────────────────────
-- checkins — Check-ins semanales (Form 2)
-- One row per athlete per date
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkins (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cedula          TEXT        NOT NULL REFERENCES athletes(cedula) ON DELETE CASCADE,
    checkin_date    DATE        NOT NULL,
    pain            INT,        -- 1-5
    fatigue         INT,        -- 1-5
    feeling         INT,        -- 1-5
    sleep           NUMERIC,    -- horas
    stress          INT,        -- 1-5
    semaforo        TEXT,       -- VERDE / AMARILLO / ROJO / SIN_CHECKIN
    is_recent       BOOLEAN     DEFAULT FALSE,
    raw             JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cedula, checkin_date)
);

CREATE INDEX IF NOT EXISTS idx_checkins_cedula_date ON checkins(cedula, checkin_date DESC);
COMMENT ON TABLE checkins IS 'Check-ins semanales. Semáforo calculado por build_features.py.';


-- ──────────────────────────────────────────────────────────────
-- weekly_features — Historial de features semanales
-- Espejo de weekly_features.parquet por atleta
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weekly_features (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cedula          TEXT        NOT NULL REFERENCES athletes(cedula) ON DELETE CASCADE,
    week_start      DATE        NOT NULL,
    km_total        NUMERIC,
    sessions        INT,
    longest_run_km  NUMERIC,
    avg_pace_sec_km NUMERIC,
    ctl             NUMERIC,    -- Chronic Training Load (EWMA 28d)
    atl             NUMERIC,    -- Acute Training Load (EWMA 7d)
    tsb             NUMERIC,    -- Training Stress Balance = CTL - ATL
    acwr            NUMERIC,    -- Acute:Chronic Workload Ratio
    racha_semanas   INT,
    km_trend        NUMERIC,
    pace_delta_4s_sec NUMERIC,
    semana_spike    BOOLEAN,
    raw             JSONB,      -- fila completa del parquet
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cedula, week_start)
);

CREATE INDEX IF NOT EXISTS idx_weekly_features_cedula_week ON weekly_features(cedula, week_start DESC);
COMMENT ON TABLE weekly_features IS 'Features semanales. Espejo de weekly_features.parquet.';


-- ──────────────────────────────────────────────────────────────
-- weekly_plans — Planes semanales por atleta
-- Espejo de weekly_plan.json
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weekly_plans (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cedula          TEXT        NOT NULL REFERENCES athletes(cedula) ON DELETE CASCADE,
    week_start      DATE        NOT NULL,
    week_type       TEXT,       -- DESCARGA / CONSERVADORA / PROGRESO
    semaforo        TEXT,
    km_target       NUMERIC,
    running_days    INT,
    strength_days   INT,
    plan_json       JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cedula, week_start)
);

COMMENT ON TABLE weekly_plans IS 'Planes semanales. Espejo de weekly_plan.json.';


-- ──────────────────────────────────────────────────────────────
-- athlete_snapshots — Último snapshot computado
-- Espejo de athlete_snapshot.json (un registro por atleta)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS athlete_snapshots (
    cedula              TEXT        PRIMARY KEY REFERENCES athletes(cedula) ON DELETE CASCADE,
    generated_at        TIMESTAMPTZ,
    semaforo            TEXT,
    readiness_score     NUMERIC,
    acwr_zone           TEXT,
    race_countdown_days INT,
    data_weeks_available INT,
    raw                 JSONB       NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE athlete_snapshots IS 'Último snapshot del atleta. Un registro por cedula.';


-- ──────────────────────────────────────────────────────────────
-- updated_at triggers (auto-actualizar timestamps)
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_athletes_updated_at
    BEFORE UPDATE ON athletes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_profiles_updated_at
    BEFORE UPDATE ON athlete_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_features_updated_at
    BEFORE UPDATE ON weekly_features
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_plans_updated_at
    BEFORE UPDATE ON weekly_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_snapshots_updated_at
    BEFORE UPDATE ON athlete_snapshots
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
