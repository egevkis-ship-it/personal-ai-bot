import asyncio
import logging

from app.telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
