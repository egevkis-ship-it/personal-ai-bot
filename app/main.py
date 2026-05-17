import asyncio
import logging

from app.db.migrations.runner import run_migrations
from app.telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def _startup() -> None:
    await run_migrations()


def main() -> None:
    asyncio.run(_startup())
    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
