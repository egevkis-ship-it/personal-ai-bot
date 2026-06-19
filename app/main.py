"""
Fitness bot entry point — aiogram 3.x + FSM + Redis storage.

Run:  python -m app.main
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import (
    history, measurements, menu, photos, plans, reports, service, workout,
)
from app.config import settings
from app.db.engine import init_db, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


_MIGRATE_SQL = """
DROP TABLE IF EXISTS fitness_exercise_sets CASCADE;
DROP TABLE IF EXISTS fitness_workouts CASCADE;
DROP TABLE IF EXISTS planned_exercises CASCADE;
DROP TABLE IF EXISTS training_plans CASCADE;
DROP TABLE IF EXISTS body_measurements CASCADE;
DROP TABLE IF EXISTS fitness_pending_decisions CASCADE;
DROP TABLE IF EXISTS fitness_goals CASCADE;
DROP TABLE IF EXISTS workout_templates CASCADE;
DROP TABLE IF EXISTS last_interaction CASCADE;
DROP TABLE IF EXISTS learning_corrections CASCADE;
DROP TABLE IF EXISTS user_preferences CASCADE;
DROP TABLE IF EXISTS exercise_normalization_cache CASCADE;

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

CREATE TABLE IF NOT EXISTS exercise_aliases (
    id              SERIAL PRIMARY KEY,
    alias_text      TEXT        NOT NULL,
    alias_clean     TEXT        NOT NULL UNIQUE,
    canonical       TEXT        NOT NULL,
    muscle_group    TEXT,
    source          TEXT        NOT NULL DEFAULT 'ai',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id     TEXT        PRIMARY KEY,
    tz_name     TEXT        NOT NULL DEFAULT 'UTC',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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

ALTER TABLE progress_photos ADD COLUMN IF NOT EXISTS series_id TEXT;
ALTER TABLE progress_photos ADD COLUMN IF NOT EXISTS ai_description_short TEXT;

ALTER TABLE planned_workouts ADD COLUMN IF NOT EXISTS notes TEXT;

CREATE INDEX IF NOT EXISTS idx_pw_user_date ON planned_workouts(user_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_w_user_date  ON workouts(user_id, workout_date);
CREATE INDEX IF NOT EXISTS idx_es_workout   ON exercise_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_es_name      ON exercise_sets(workout_id, exercise_name);
CREATE INDEX IF NOT EXISTS idx_ea_clean     ON exercise_aliases(alias_clean);
CREATE INDEX IF NOT EXISTS idx_bm_user_date ON body_measurements(user_id, taken_on DESC);
CREATE INDEX IF NOT EXISTS idx_pp_user_date ON progress_photos(user_id, taken_on DESC);
"""


async def run_migrations() -> None:
    """Run idempotent DDL on startup.

    asyncpg can't execute multiple statements in one call, so we split on ';'.
    To avoid a stray ';' inside a SQL comment splitting a statement in half,
    strip all '--' line comments first.
    """
    import re
    from sqlalchemy import text
    sql = re.sub(r"--[^\n]*", "", _MIGRATE_SQL)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
    log.info("Migrations OK")


async def main() -> None:
    log.info("Starting fitness bot...")

    # DB connection pool
    await init_db()
    await run_migrations()

    # Redis FSM storage
    storage = RedisStorage.from_url(settings.redis_url)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)

    # Register routers. menu.router goes FIRST so that reply-keyboard
    # taps (💪 Тренировка / 📅 Планы / 📖 История / 📏 Замеры / 📸 Фото /
    # ⚙️ Сервис) and /start always preempt whatever FSM state the user
    # was in. Reply-button texts are exact-match so they never steal
    # set-input / note-input / measurement-input messages.
    dp.include_router(menu.router)
    dp.include_router(workout.router)
    dp.include_router(plans.router)
    dp.include_router(history.router)
    dp.include_router(measurements.router)
    dp.include_router(photos.router)
    dp.include_router(reports.router)
    dp.include_router(service.router)

    log.info("Bot polling started.")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
