import asyncio

from app.db import init_db
from app.telegram_bot import build_application


async def bootstrap() -> None:
    await init_db()


def main() -> None:
    asyncio.run(bootstrap())

    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
