"""
Idempotent schema for the local prototype API.

Same tables the bot uses (CREATE IF NOT EXISTS only — no DROP, so it is safe to
run against a database that already holds bot data).
"""

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS planned_workouts (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    planned_date DATE       NOT NULL,
    focus_label TEXT,
    exercises   JSONB       NOT NULL DEFAULT '[]',
    notes       TEXT,
    status      TEXT        NOT NULL DEFAULT 'planned'
                            CHECK (status IN ('planned','completed','skipped')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workouts (
    id                  SERIAL PRIMARY KEY,
    user_id             TEXT        NOT NULL,
    workout_date        DATE        NOT NULL,
    planned_workout_id  INTEGER     REFERENCES planned_workouts(id) ON DELETE SET NULL,
    focus_label         TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS exercise_sets (
    id                  SERIAL PRIMARY KEY,
    workout_id          INTEGER     NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_name       TEXT        NOT NULL,
    set_number          INTEGER,
    weight_kg           NUMERIC,
    reps                INTEGER,
    reps_text           TEXT,
    duration_seconds    INTEGER,
    superset_group      TEXT,
    is_warmup           BOOLEAN     NOT NULL DEFAULT FALSE,
    is_failure          BOOLEAN     NOT NULL DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS body_measurements (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    taken_on     DATE        NOT NULL,
    weight_kg    NUMERIC,
    calf_cm      NUMERIC,
    thigh_cm     NUMERIC,
    hips_cm      NUMERIC,
    belly_cm     NUMERIC,
    waist_cm     NUMERIC,
    chest_cm     NUMERIC,
    arm_cm       NUMERIC,
    neck_cm      NUMERIC,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tables the bot also owns (created by app/main.py at bot startup). Declared here
-- with IF NOT EXISTS so the web API also works standalone in local dev. In prod
-- the bot already created them, so these statements are no-ops.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id     TEXT        PRIMARY KEY,
    tz_name     TEXT        NOT NULL DEFAULT 'UTC',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Goals + unit preference (phase G). Idempotent — no-ops if already present.
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS target_weight NUMERIC;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS weekly_goal   INTEGER;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS unit          TEXT NOT NULL DEFAULT 'kg';
-- Rest-timer preference (phase 2).
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rest_timer_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rest_timer_seconds INTEGER NOT NULL DEFAULT 90;
-- Per-user recovery context (flagship coach). 'natural' (default) | 'enhanced'.
-- NEVER hard-code per a specific user — it is the user's own declared setting.
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS recovery_mode TEXT NOT NULL DEFAULT 'natural';

-- Last coach-survey answers (sleep / energy / stress / injuries / notes), owner-scoped.
CREATE TABLE IF NOT EXISTS coach_context (
    uid        TEXT        PRIMARY KEY,
    data       JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exercise_aliases (
    id              SERIAL PRIMARY KEY,
    alias_text      TEXT        NOT NULL,
    alias_clean     TEXT        NOT NULL UNIQUE,
    canonical       TEXT        NOT NULL,
    muscle_group    TEXT,
    source          TEXT        NOT NULL DEFAULT 'ai',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progress_photos (
    id                       SERIAL PRIMARY KEY,
    user_id                  TEXT        NOT NULL,
    taken_on                 DATE        NOT NULL,
    telegram_file_id         TEXT,
    telegram_file_unique_id  TEXT,
    ai_description           TEXT,
    ai_description_short     TEXT,
    notes                    TEXT,
    series_id                TEXT,
    storage_key              TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The prod table is created by the bot with telegram_file_id NOT NULL and no
-- web-storage columns. Relax it and add storage columns so web-uploaded photos
-- (stored on disk, no telegram_file_id) fit. All idempotent / no-ops in prod.
ALTER TABLE progress_photos ALTER COLUMN telegram_file_id DROP NOT NULL;
ALTER TABLE progress_photos ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE progress_photos ADD COLUMN IF NOT EXISTS ai_description_short TEXT;

-- Idempotency ledger for offline-queued client operations (e.g. set logging
-- replayed after reconnect). Owned by the web API only.
CREATE TABLE IF NOT EXISTS processed_ops (
    uid        TEXT        NOT NULL,
    op_id      TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (uid, op_id)
);

-- Per-user daily AI call counter (rate limit). Owned by the web API only.
CREATE TABLE IF NOT EXISTS ai_usage (
    uid    TEXT    NOT NULL,
    day    DATE    NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, day)
);

-- Web-app access control (gate + roles). Owned by the web API only.
CREATE TABLE IF NOT EXISTS app_access (
    uid          TEXT        PRIMARY KEY,
    status       TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','approved','blocked')),
    role         TEXT        NOT NULL DEFAULT 'user'
                             CHECK (role IN ('user','admin')),
    display_name TEXT,
    username     TEXT,
    invited_by   TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ
);

-- Reusable weekly templates ("routines"): days = [{weekday 0-6, focus_label,
-- exercises:[...]}] in the same exercise shape as planned_workouts. Owned by the
-- web API only (phase F).
CREATE TABLE IF NOT EXISTS routines (
    id         SERIAL      PRIMARY KEY,
    user_id    TEXT        NOT NULL,
    name       TEXT        NOT NULL,
    days       JSONB       NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_routines_user ON routines(user_id);

CREATE INDEX IF NOT EXISTS idx_pw_user_date ON planned_workouts(user_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_w_user_date  ON workouts(user_id, workout_date);
CREATE INDEX IF NOT EXISTS idx_es_workout   ON exercise_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_es_name      ON exercise_sets(workout_id, exercise_name);
CREATE INDEX IF NOT EXISTS idx_bm_user_date ON body_measurements(user_id, taken_on DESC);
-- Phase 5: support the cross-workout name lookups (stats/history/PR/dashboard
-- group by lower(exercise_name)), the plan status filter, and the FK lookup.
CREATE INDEX IF NOT EXISTS idx_es_lower_name ON exercise_sets (lower(exercise_name));
CREATE INDEX IF NOT EXISTS idx_pw_user_date_status ON planned_workouts(user_id, planned_date, status);
CREATE INDEX IF NOT EXISTS idx_w_planned ON workouts(planned_workout_id);
"""
