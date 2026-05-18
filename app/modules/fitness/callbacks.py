"""
Inline callback handlers for fitness module.

Callback data format: "fit:<action>:<args...>"
  fit:confirm_delete_workout:<workout_id>
  fit:cancel:_  — generic cancel
  fit:finish_workout:<workout_id>
  fit:resume_session:<workout_id>
"""
from __future__ import annotations

from app.db import (
    delete_workout,
    get_latest_fitness_pending_decision,
    resolve_fitness_pending_decision,
    update_fitness_pending_decision_context,
)


async def handle_fitness_callback(user_id: str, data: str) -> str:
    parts = data.split(":")
    if len(parts) < 2:
        return "Неизвестное действие."

    action = parts[1]
    args = parts[2:] if len(parts) > 2 else []

    if action == "cancel":
        return "Отменено."

    if action == "confirm_delete_workout" and args:
        try:
            wid = int(args[0])
        except (ValueError, IndexError):
            return "Не понял ID тренировки."
        n = await delete_workout(wid)
        # close pending session if it matches
        pending = await get_latest_fitness_pending_decision(user_id)
        if pending and (pending.get("context_json") or {}).get("workout_id") == wid:
            await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return f"🗑 Тренировка #{wid} удалена." if n else "Не удалось удалить."

    if action == "finish_workout" and args:
        from app.modules.fitness.action_v2 import _finish_workout_with_summary
        try:
            wid = int(args[0])
        except (ValueError, IndexError):
            return "Не понял ID тренировки."
        return await _finish_workout_with_summary(user_id, wid)

    if action == "resume_session" and args:
        try:
            wid = int(args[0])
        except (ValueError, IndexError):
            return "Не понял ID."
        pending = await get_latest_fitness_pending_decision(user_id)
        if pending and (pending.get("context_json") or {}).get("workout_id") == wid:
            ctx = pending.get("context_json") or {}
            ctx["session_status"] = "active"
            from datetime import datetime, timezone
            ctx["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            await update_fitness_pending_decision_context(pending["id"], ctx)
            return f"▶️ Возобновил сессию #{wid}. Продолжай диктовать."
        return "Не нашёл такую сессию."

    return f"Неизвестное действие: {action}"
