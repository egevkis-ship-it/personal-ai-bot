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

from app.bot.handlers import history, menu, plans, workout
from app.config import settings
from app.db.engine import init_db, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


_MIGRATE_SQL = """
CREATE TABLE IF NOT EXISTS planned_workouts (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    planned_date DATE       NOT NULL,
    focus_label TEXT,
    exercises   JSONB       NOT NULL DEFAULT '[]',
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

CREATE INDEX IF NOT EXISTS idx_pw_user_date ON planned_workouts(user_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_w_user_date  ON workouts(user_id, workout_date);
CREATE INDEX IF NOT EXISTS idx_es_workout   ON exercise_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_es_name      ON exercise_sets(workout_id, exercise_name);
"""


async def run_migrations() -> None:
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text(_MIGRATE_SQL))
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

    # Register routers (order matters — more specific first)
    dp.include_router(workout.router)
    dp.include_router(plans.router)
    dp.include_router(history.router)
    dp.include_router(menu.router)   # catch-all last

    log.info("Bot polling started.")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
