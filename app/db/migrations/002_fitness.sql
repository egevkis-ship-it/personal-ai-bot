CREATE TABLE IF NOT EXISTS exercises (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    canonical_name TEXT,
    muscle_group TEXT,
    equipment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (telegram_user_id, name)
);

CREATE TABLE IF NOT EXISTS workout_plans (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    scheduled_date DATE NOT NULL,
    exercises JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'planned',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workout_plans_user_date
    ON workout_plans (telegram_user_id, scheduled_date);

CREATE TABLE IF NOT EXISTS workout_sessions (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    plan_id INTEGER REFERENCES workout_plans(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    sets JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_workout_sessions_user_status
    ON workout_sessions (telegram_user_id, status);

CREATE TABLE IF NOT EXISTS body_measurements (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    measured_at DATE NOT NULL DEFAULT CURRENT_DATE,
    weight_kg NUMERIC(5, 2),
    body_fat_pct NUMERIC(4, 2),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
