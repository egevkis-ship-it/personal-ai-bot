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

    if action in ("disamb_log", "disamb_plan", "disamb_cancel"):
        return await _handle_disambiguation_callback(user_id, action)

    return f"Неизвестное действие: {action}"


async def _handle_disambiguation_callback(user_id: str, action: str) -> str:
    """User chose 'записать факт' or 'запланировать' for ambiguous workout input."""
    pending = await get_latest_fitness_pending_decision(user_id)
    if not pending or pending.get("decision_type") != "disambiguate_log_or_plan":
        return "Уточнение уже не актуально."

    ctx = pending.get("context_json") or {}
    parsed = ctx.get("parsed") or {}
    source_text = ctx.get("source_text") or ""

    if action == "disamb_cancel":
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Отменил. Скажи фразу заново, если хочешь."

    # Конвертим в синтетический parsed под нужный экшен
    wk = parsed.get("workout") or {}
    exes = wk.get("exercises") or []
    logged = parsed.get("logged_exercises") or []

    if action == "disamb_log":
        if not logged and exes:
            logged = []
            for ex in exes:
                sets_count = ex.get("target_sets") or 1
                reps = ex.get("target_reps_min") or ex.get("target_reps_max") or None
                weight = ex.get("target_weight_kg")
                synth_sets = [
                    {"set_number": i + 1, "weight_kg": weight, "reps": reps}
                    for i in range(sets_count)
                ]
                logged.append({
                    "exercise_name": ex.get("exercise_name"),
                    "sets": synth_sets,
                })
        synth = {
            "action": "log_workout_sets",
            "date": parsed.get("date"),
            "workout": wk,
            "logged_exercises": logged,
        }
        await resolve_fitness_pending_decision(pending["id"], status="resolved")
        from app.modules.fitness.action_v2 import _log_workout_sets
        result = await _log_workout_sets(user_id, source_text, synth, active_session=None)
        if hasattr(result, "text"):
            return result.text
        return result

    if action == "disamb_plan":
        if not exes and logged:
            wk["exercises"] = []
            for ex in logged:
                sets_list = ex.get("sets") or []
                wk["exercises"].append({
                    "exercise_name": ex.get("exercise_name"),
                    "target_sets": len(sets_list) or 1,
                    "target_reps_min": sets_list[0].get("reps") if sets_list else None,
                    "target_weight_kg": sets_list[0].get("weight_kg") if sets_list else None,
                })
        synth = {
            "action": "add_custom_workout",
            "date": parsed.get("date"),
            "workout": wk,
        }
        await resolve_fitness_pending_decision(pending["id"], status="resolved")
        from app.modules.fitness.action_v2 import _add_custom_workout
        result = await _add_custom_workout(user_id, source_text, synth)
        if hasattr(result, "text"):
            return result.text
        return result

    return "Неизвестное действие."
