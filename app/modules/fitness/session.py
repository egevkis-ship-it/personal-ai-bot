from app.modules.fitness.action_v2 import try_handle_active_workout_message


async def handle_session_message(user_id: str, text: str, session: dict) -> str:
    result = await try_handle_active_workout_message(user_id, text)
    return result or "Не понял, уточни."
