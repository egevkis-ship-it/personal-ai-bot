CREATE TABLE IF NOT EXISTS finance_accounts (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (telegram_user_id, name)
);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    account_id INTEGER REFERENCES finance_accounts(id) ON DELETE SET NULL,
    type TEXT NOT NULL CHECK (type IN ('expense', 'income', 'transfer')),
    amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    category TEXT,
    description TEXT,
    transaction_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_user_date
    ON finance_transactions (telegram_user_id, transaction_date);

CREATE TABLE IF NOT EXISTS finance_budgets (
    id SERIAL PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    period TEXT NOT NULL DEFAULT 'monthly',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (telegram_user_id, category, period)
);
