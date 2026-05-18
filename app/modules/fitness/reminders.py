"""
Reminder loop для фитнес-напоминаний.
Poll каждую минуту, отправляет due reminders через bot instance.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.db import (
    get_due_reminders, mark_reminder_sent, reschedule_recurring,
    get_today_planned_workout, schedule_reminder,
)
from app.modules.fitness.formatter import format_planned_workout, format_human_date

logger = logging.getLogger(__name__)


async def _compose_message(reminder: dict) -> str:
    kind = reminder.get("kind") or "custom"
    payload = reminder.get("payload_json") or {}
    if isinstance(payload, str):
        import json as _j
        try:
            payload = _j.loads(payload)
        except Exception:
            payload = {}

    if kind == "workout_today":
        uid = reminder.get("telegram_user_id")
        today = datetime.now(timezone.utc).date().isoformat()
        data = await get_today_planned_workout(uid, today)
        if data:
            return f"🏋️ Напоминание про тренировку на сегодня:\n\n{format_planned_workout(data)}"
        return "🏋️ На сегодня плановой тренировки не нашёл."

    if kind == "custom":
        text = payload.get("text") or "Напоминание."
        return f"⏰ {text}"

    if kind == "recurring_daily":
        return f"🔁 Напоминание: {payload.get('text', 'ежедневное')}"

    return f"📢 Напоминание (kind={kind})"


def _next_fire_for_recurring(current: datetime, recurrence: str) -> datetime | None:
    """Compute next fire time based on recurrence rule."""
    if recurrence == "daily":
        return current + timedelta(days=1)
    if recurrence and recurrence.startswith("weekly:"):
        # weekly:mon,wed,fri  → next matching weekday >= tomorrow
        days_str = recurrence.split(":", 1)[1]
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        wanted = sorted({day_map[d.strip()] for d in days_str.split(",") if d.strip() in day_map})
        if not wanted:
            return None
        for offset in range(1, 8):
            nxt = current + timedelta(days=offset)
            if nxt.weekday() in wanted:
                return nxt
        return None
    return None


async def reminder_loop(bot=None) -> None:
    """Poll due reminders every minute, send via bot."""
    while True:
        try:
            due = await get_due_reminders()
            for r in due:
                try:
                    uid = r.get("telegram_user_id")
                    if not uid:
                        await mark_reminder_sent(r["id"])
                        continue
                    msg = await _compose_message(r)
                    if bot is not None:
                        try:
                            await bot.send_message(chat_id=int(uid), text=msg)
                        except Exception as send_err:
                            logger.exception("reminder send failed for %s: %s", uid, send_err)
                    else:
                        logger.warning("reminder %s ready but no bot instance", r["id"])

                    rec = r.get("recurrence")
                    if rec:
                        fire_at = r.get("fire_at")
                        if isinstance(fire_at, str):
                            try:
                                fire_at = datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
                            except Exception:
                                fire_at = datetime.now(timezone.utc)
                        if not fire_at.tzinfo:
                            fire_at = fire_at.replace(tzinfo=timezone.utc)
                        nxt = _next_fire_for_recurring(fire_at, rec)
                        if nxt:
                            await reschedule_recurring(r["id"], nxt.isoformat())
                        else:
                            await mark_reminder_sent(r["id"])
                    else:
                        await mark_reminder_sent(r["id"])
                except Exception as e:
                    logger.exception("reminder %s processing failed: %s", r.get("id"), e)
        except Exception as e:
            logger.exception("reminder_loop iteration failed: %s", e)

        await asyncio.sleep(60)
