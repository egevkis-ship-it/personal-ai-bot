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
from app.db.engine import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Starting fitness bot...")

    # DB connection pool
    await init_db()

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
