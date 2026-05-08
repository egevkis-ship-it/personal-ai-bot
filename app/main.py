import asyncio

from app.db import init_db
from app.telegram_bot import build_application


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(init_db())

    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
