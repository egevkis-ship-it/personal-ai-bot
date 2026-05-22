-- ────────────────────────────────────────────────────────────────────────────
-- Migration 010: новая чистая схема фитнес-бота
-- Сносим все старые таблицы, создаём 3 чистых.
-- ────────────────────────────────────────────────────────────────────────────

-- Снести старое (безопасно — IF EXISTS)
DROP TABLE IF EXISTS fitness_exercise_sets CASCADE;
DROP TABLE IF EXISTS fitness_workouts CASCADE;
DROP TABLE IF EXISTS planned_exercises CASCADE;
DROP TABLE IF EXISTS planned_workouts CASCADE;
DROP TABLE IF EXISTS training_plans CASCADE;
DROP TABLE IF EXISTS body_measurements CASCADE;
DROP TABLE IF EXISTS fitness_pending_decisions CASCADE;
DROP TABLE IF EXISTS fitness_goals CASCADE;
DROP TABLE IF EXISTS workout_templates CASCADE;
DROP TABLE IF EXISTS last_interaction CASCADE;
DROP TABLE IF EXISTS learning_corrections CASCADE;
DROP TABLE IF EXISTS user_preferences CASCADE;
DROP TABLE IF EXISTS exercise_normalization_cache CASCADE;

-- ── 1. Запланированные тренировки ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS planned_workouts (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    planned_date DATE        NOT NULL,
    focus_label TEXT,
    exercises   JSONB       NOT NULL DEFAULT '[]',
    -- exercises: [{name, target_sets, target_reps_min, target_reps_max,
    --              target_weight, reps_text, notes, superset_group,
    --              individual_sets:[{weight, reps_min, reps_max}]}]
    status      TEXT        NOT NULL DEFAULT 'planned'
                            CHECK (status IN ('planned','completed','skipped')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pw_user_date
    ON planned_workouts(user_id, planned_date);

-- ── 2. Выполненные тренировки ───────────────────────────────────────────────
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

CREATE INDEX IF NOT EXISTS idx_w_user_date
    ON workouts(user_id, workout_date);

-- ── 3. Подходы ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exercise_sets (
    id                  SERIAL PRIMARY KEY,
    workout_id          INTEGER     NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_name       TEXT        NOT NULL,
    set_number          INTEGER,
    weight_kg           NUMERIC,
    reps                INTEGER,
    reps_text           TEXT,           -- "AMRAP", "до отказа", "10-12"
    duration_seconds    INTEGER,        -- for planks, cardio
    superset_group      TEXT,           -- "A", "B", "СС1"
    is_warmup           BOOLEAN         NOT NULL DEFAULT FALSE,
    is_failure          BOOLEAN         NOT NULL DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_es_workout
    ON exercise_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_es_name
    ON exercise_sets(workout_id, exercise_name);
