async def handle(user_id: str, text: str, parsed: dict) -> str:
    return "Модуль в разработке."

async def handle_pending(user_id: str, text: str, pending: dict) -> str:
    return "Модуль в разработке."

async def handle_confirm(user_id: str, confirmed: bool, confirm: dict) -> str:
    return "Ок." if confirmed else "Отменено."
