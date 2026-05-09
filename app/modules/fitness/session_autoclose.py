import asyncio
import logging

from app.db import auto_close_stale_active_workout_sessions

logger = logging.getLogger(__name__)

AUTO_CLOSE_TIMEOUT_MINUTES = 60
AUTO_CLOSE_CHECK_INTERVAL_SECONDS = 300


async def fitness_session_autoclose_loop() -> None:
    """
    Background cleanup loop.

    Does not send Telegram messages.
    Only closes stale active workout sessions in DB.
    """
    logger.info(
        "Fitness session auto-close loop started: timeout=%s min, interval=%s sec",
        AUTO_CLOSE_TIMEOUT_MINUTES,
        AUTO_CLOSE_CHECK_INTERVAL_SECONDS,
    )

    while True:
        try:
            closed_count = await auto_close_stale_active_workout_sessions(
                timeout_minutes=AUTO_CLOSE_TIMEOUT_MINUTES,
            )

            if closed_count:
                logger.info("Auto-closed stale workout sessions: %s", closed_count)

        except asyncio.CancelledError:
            logger.info("Fitness session auto-close loop cancelled")
            raise
        except Exception:
            logger.exception("Fitness session auto-close loop failed")

        await asyncio.sleep(AUTO_CLOSE_CHECK_INTERVAL_SECONDS)
