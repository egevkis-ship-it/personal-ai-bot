from app.modules.fitness.action_v2 import handle_fitness_action_v2


async def handle(user_id: str, text: str, parsed: dict) -> str:
    result = await handle_fitness_action_v2(user_id, text)
    return result or "Не понял, уточни запрос."


async def handle_pending(user_id: str, text: str, pending: dict) -> str:
    result = await handle_fitness_action_v2(user_id, text)
    return result or "Не понял ответ."
