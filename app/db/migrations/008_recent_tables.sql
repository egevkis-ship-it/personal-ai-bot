-- Catch-up migration: таблицы и колонки, которые создавались в init() но
-- не катились на проде (т.к. runner читает только .sql файлы).
-- Включает СТАРЫЕ таблицы фитнес-модуля которые на проде существуют по легаси.

CREATE TABLE IF NOT EXISTS training_plans (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT,
    plan_name TEXT,
    period_type TEXT,
    start_date DATE,
    end_date DATE,
    source_text TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    plan_id BIGINT REFERENCES training_plans(id) ON DELETE CASCADE,
    telegram_user_id TEXT,
    planned_date DATE,
    weekday TEXT,
    sequence_number INTEGER,
    is_floating BOOLEAN DEFAULT false,
    title TEXT,
    focus TEXT,
    focus_label TEXT,
    workout_type TEXT DEFAULT 'planned',
    status TEXT DEFAULT 'planned',
    replaced_by_id BIGINT,
    source_text TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS planned_exercises (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE CASCADE,
    exercise_order INTEGER,
    exercise_name TEXT,
    target_sets INTEGER,
    target_reps_min INTEGER,
    target_reps_max INTEGER,
    target_reps_text TEXT,
    target_weight_kg NUMERIC,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS planned_workout_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
    event_type TEXT,
    old_value_json JSONB,
    new_value_json JSONB,
    source_text TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fitness_workouts (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT,
    planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
    workout_date DATE,
    workout_type TEXT,
    focus TEXT,
    focus_label TEXT,
    bodyweight_kg NUMERIC,
    completion_type TEXT DEFAULT 'custom',
    source_text TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fitness_exercise_sets (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    workout_id BIGINT REFERENCES fitness_workouts(id) ON DELETE CASCADE,
    exercise_name TEXT,
    set_number INTEGER,
    weight_kg NUMERIC,
    reps INTEGER,
    rpe NUMERIC,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fitness_workout_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT NOT NULL,
    planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
    workout_date DATE,
    title TEXT,
    status TEXT DEFAULT 'active',
    started_at TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    source_text TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fitness_workout_log_sets (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    workout_log_id BIGINT REFERENCES fitness_workout_logs(id) ON DELETE CASCADE,
    exercise_name TEXT,
    exercise_key TEXT,
    set_number INTEGER,
    weight_kg NUMERIC,
    reps INTEGER,
    rpe NUMERIC,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS training_constraints (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT,
    constraint_date DATE,
    body_part TEXT,
    severity TEXT,
    note TEXT,
    source_text TEXT,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS fitness_pending_decisions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT,
    decision_type TEXT,
    status TEXT DEFAULT 'pending',
    context_json JSONB,
    source_text TEXT,
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_planned_workouts_user_date_status
ON planned_workouts (telegram_user_id, planned_date, status);

CREATE INDEX IF NOT EXISTS idx_fitness_workouts_user_date
ON fitness_workouts (telegram_user_id, workout_date);

CREATE INDEX IF NOT EXISTS idx_body_measurements_user_date
ON body_measurements (telegram_user_id, measurement_date);

CREATE TABLE IF NOT EXISTS last_interaction (
    telegram_user_id TEXT PRIMARY KEY,
    updated_at TIMESTAMPTZ DEFAULT now(),
    input_text TEXT,
    bot_response TEXT,
    action TEXT,
    parsed_json JSONB,
    current_workout_date DATE,
    current_planned_workout_id BIGINT,
    current_focus TEXT
);

ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_workout_date DATE;
ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_planned_workout_id BIGINT;
ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_focus TEXT;

CREATE TABLE IF NOT EXISTS learning_corrections (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    telegram_user_id TEXT,
    original_input TEXT,
    bot_response TEXT,
    user_feedback TEXT,
    correction_type TEXT,
    rule_pattern TEXT,
    rule_action TEXT,
    scope TEXT DEFAULT 'fitness',
    status TEXT DEFAULT 'active',
    applied_count INTEGER DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_corrections_user_status
ON learning_corrections (telegram_user_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS user_preferences (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    key TEXT,
    value TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (telegram_user_id, key)
);

CREATE TABLE IF NOT EXISTS workout_templates (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    name TEXT,
    focus TEXT,
    focus_label TEXT,
    exercises_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_templates_user_name
ON workout_templates (telegram_user_id, name);

CREATE TABLE IF NOT EXISTS fitness_goals (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    goal_type TEXT,
    target_exercise TEXT,
    target_value NUMERIC,
    target_unit TEXT,
    target_deadline DATE,
    status TEXT DEFAULT 'active',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    achieved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pain_journal (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    log_date DATE DEFAULT CURRENT_DATE,
    body_part TEXT,
    severity NUMERIC,
    note TEXT,
    source_text TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pain_user_date
ON pain_journal (telegram_user_id, log_date DESC);

CREATE TABLE IF NOT EXISTS scheduled_reminders (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    fire_at TIMESTAMPTZ NOT NULL,
    kind TEXT,
    payload_json JSONB,
    status TEXT DEFAULT 'pending',
    fired_at TIMESTAMPTZ,
    recurrence TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reminders_fire
ON scheduled_reminders (status, fire_at) WHERE status = 'pending';

-- Forward-compat columns
ALTER TABLE body_measurements ADD COLUMN IF NOT EXISTS bodyfat_pct NUMERIC;
ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS distance_m NUMERIC;
ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS is_warmup BOOLEAN DEFAULT false;
ALTER TABLE planned_exercises ADD COLUMN IF NOT EXISTS superset_group TEXT;
ALTER TABLE planned_exercises ADD COLUMN IF NOT EXISTS tempo TEXT;
