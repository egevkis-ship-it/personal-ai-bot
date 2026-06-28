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
    telegram_file_id         TEXT        NOT NULL,
    telegram_file_unique_id  TEXT,
    ai_description           TEXT,
    notes                    TEXT,
    series_id                TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE INDEX IF NOT EXISTS idx_pw_user_date ON planned_workouts(user_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_w_user_date  ON workouts(user_id, workout_date);
CREATE INDEX IF NOT EXISTS idx_es_workout   ON exercise_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_es_name      ON exercise_sets(workout_id, exercise_name);
CREATE INDEX IF NOT EXISTS idx_bm_user_date ON body_measurements(user_id, taken_on DESC);
"""
