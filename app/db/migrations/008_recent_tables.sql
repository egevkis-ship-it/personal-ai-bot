-- Catch-up migration: таблицы и колонки, которые создавались в init() но
-- не катились на проде (т.к. runner читает только .sql файлы).

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
