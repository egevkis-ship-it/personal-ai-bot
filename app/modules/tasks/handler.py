from app.router import _has_hard_fitness_signal


async def handle(user_id: str, text: str, parsed: dict) -> str:
    if _has_hard_fitness_signal(text):
        # Misrouted by intent classifier — delegate to fitness
        from app.modules.fitness.handler import handle as fh
        return await fh(user_id, text, parsed)
    return "Модуль задач пока в разработке. Если речь про тренировку — скажи явно «запиши тренировку...» или «план на неделю...»."


async def handle_pending(user_id: str, text: str, pending: dict) -> str:
    return "Модуль задач пока в разработке."
