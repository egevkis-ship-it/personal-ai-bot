CREATE TABLE IF NOT EXISTS food_items (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    calories_per_100g NUMERIC(6, 2),
    protein_per_100g NUMERIC(5, 2),
    fat_per_100g NUMERIC(5, 2),
    carbs_per_100g NUMERIC(5, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (telegram_user_id, name)
);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    food_item_id INTEGER REFERENCES food_items(id) ON DELETE SET NULL,
    food_name TEXT NOT NULL,
    amount_grams NUMERIC(7, 2),
    calories NUMERIC(7, 2),
    protein NUMERIC(6, 2),
    fat NUMERIC(6, 2),
    carbs NUMERIC(6, 2),
    meal_type TEXT CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')),
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nutrition_log_user_date
    ON nutrition_log (telegram_user_id, logged_at);

CREATE TABLE IF NOT EXISTS nutrition_goals (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL UNIQUE,
    calories NUMERIC(7, 2),
    protein NUMERIC(6, 2),
    fat NUMERIC(6, 2),
    carbs NUMERIC(6, 2),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
