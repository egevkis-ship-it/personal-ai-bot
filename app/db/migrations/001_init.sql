CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_messages (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    message_type TEXT,
    original_text TEXT,
    transcript TEXT,
    intent TEXT,
    parsed_json JSONB,
    status TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops_audit_log (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT,
    action TEXT NOT NULL,
    params JSONB,
    result TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
