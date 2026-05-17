import asyncio
import logging

logger = logging.getLogger(__name__)

async def reminder_loop() -> None:
    while True:
        await asyncio.sleep(60)
