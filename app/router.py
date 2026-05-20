import logging
import re

from app.ai import parse_message
from app.bot_reply import BotReply
from app.state.manager import SessionType, get_session

logger = logging.getLogger(__name__)

_FITNESS_INTENTS = {"fitness"}
_FINANCE_INTENTS = {"finance_expense", "finance_income"}
_TASK_INTENTS = {"task", "reminder"}
_NUTRITION_INTENTS = {"nutrition"}
_OPS_INTENTS = {"ops"}


# Сильные fitness-сигналы — если в тексте есть, ВСЕГДА fitness (минуя intent classifier).
_HARD_FITNESS_PATTERNS = [
    r"\bтренировк",
    r"\bупражнен",
    r"\bподход",
    r"\bжим\b", r"\bтяг", r"\bприсед", r"\bстановая",
    r"\bбицепс", r"\bтрицепс", r"\bдельт", r"\bягодиц",
    r"\d+\s*кг\b",
    r"\d+\s*[×x]\s*\d+",
    r"\bштанг", r"\bгантел",
    r"\bвес[ам]", r"\bРПЕ\b", r"\bRPE\b", r"\bAMRAP\b",
    r"\bразминк", r"\bрабочих", r"\bрабочие\s+подход",
    r"\bплан на (неделю|месяц|следующ)",
    r"\bтрениров", r"\bзапланир", r"\bзапиши.*трен",
    # Глаголы записи факта тренировки
    r"\bсделал[аи]?\b", r"\bвыполнил[аи]?\b", r"\bзавершил[аи]?\b",
    r"\bпервый подход", r"\bвторой подход", r"\bтретий подход",
    r"\bпоследний подход", r"\bследующий подход",
    r"\b\d+\s*повтор", r"\b\d+\s*раз\b",
    r"\bпуловер", r"\bбрус", r"\bподтяг", r"\bотжим", r"\bмахи",
    r"\bразводк", r"\bсгибан", r"\bразгибан",
    # Архив и история
    r"\bархивн", r"\bистория\s+(трен|зал)",
    r"\bвсе\s+(мои\s+)?тренировк", r"\bвсе\s+что\s+я\s+(сделал|делал)",
    r"\bсводк[аиу]", r"\bотчет", r"\bстатистик",
    r"\bпрогресс",
    r"\bзакончил\s+(тренир|зал|сесси)",
    # Краткие команды показа плана дня — типичные для фитнес-бота
    r"\bчто\s+(сегодня|завтра|вчера|на\s+(неделю|след|следующ))",
    r"\bчто\s+у\s+меня\s+(сегодня|завтра|вчера|по\s+плану|на\s+(сегод|завтр|неделю))",
    r"\bчто\s+делал\s+(сегодня|вчера)",
    r"\bпокажи\s+(план|трен|неделю|месяц|завтра|сегодня|вчера|посл)",
    r"\bплан\s+на\s+(сегодня|завтра|неделю|месяц|следующ)",
    r"\bтренировк[аиу]\s+(на\s+сегодня|сегодня|завтра|в\s+пят|в\s+пн|в\s+вт|в\s+ср|в\s+чт|в\s+сб|в\s+вс)",
]


# Эти паттерны достаточно специфичны чтобы 1 совпадения хватило.
_STRONG_SOLO_FITNESS = [
    r"\bтренировк",
    r"\bупражнен",
    r"\bподход",
    r"\bпокажи\s+(план|трен)",
    r"\bплан\s+на\s+(сегодня|завтра|неделю|месяц|следующ)",
    r"\bначинаю\s+трен", r"\bстартую\s+трен", r"\bприступаю\s+к\s+трен",
    r"\bзакончил\s+трен", r"\bзавершил\s+трен",
    r"^закончил\.?$", r"^финиш\.?$", r"^всё\.?$", r"^хватит\.?$",
    r"^начинаю\.?$", r"^стартую\.?$",
    r"\bчто\s+(сегодня|завтра|вчера|у\s+меня\s+(сегодня|завтра|по\s+плану|в\s+плане))",
    # дни недели — "что у меня в пятницу/в понедельник/во вторник"
    r"\bчто\s+у\s+меня\s+(в|во)\s+(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)",
    r"\bчто\s+(в|во)\s+(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)",
    r"\bпокажи.*\s+(в|во)\s+(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)",
    # "поставь на пятницу X" - явная команда планирования
    r"\bпостав[ьл]\s+на\s+(пн|вт|ср|чт|пт|сб|вс|сегодня|завтра|послезавтра|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)",
    r"\bархивн", r"\bвсе\s+мои\s+тренировк",
    r"\bсделал.*\d+\s*кг",
    r"\d+\s*кг\s*[×x]\s*\d+", r"\d+\s*[×x]\s*\d+\s*кг",
    # Команды редактирования плана
    r"\b(замени|поменяй|поправь)\s+(жим|тяг|присед|разводк|махи|брус|подтяг|становая|выпад)",
    r"\b(увеличь|сократи|поставь|сделай)\s+.*\s+(в\s+жим|в\s+тяг|в\s+присед)",
    r"\b(поменяй|измени|поставь)\s+вес\s+",
    r"\b(добавь|убери|удали)\s+.*\s+(в\s+план|из\s+план|из\s+тренировк|в\s+тренировк)",
    # Замеры
    r"^вес\s+\d", r"^талия\s+\d", r"^голен[ьи]\s+\d", r"^живот\s+\d",
    r"^бедро\s+\d", r"^бедра\s+\d", r"^грудь\s+\d", r"^рука\s+\d", r"^шея\s+\d",
]


def _has_hard_fitness_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    # Сильный одиночный паттерн — этого достаточно
    for p in _STRONG_SOLO_FITNESS:
        if re.search(p, t):
            return True
    # Иначе нужно 2+ слабых сигнала
    hits = sum(1 for p in _HARD_FITNESS_PATTERNS if re.search(p, t))
    return hits >= 2


# Жёсткие ops-сигналы. БЕЗ них — ops НЕ запускается, даже если intent=ops.
_HARD_OPS_PATTERNS = [
    r"\bзадеплой", r"\bдеплой\b", r"\bdeploy\b",
    r"\bустанови.*(pip|пакет|библиотек|зависимост)",
    r"\bpip install\b", r"\buv add\b", r"\bpoetry add\b",
    r"\bнапиши код", r"\bдобавь модуль", r"\bдобавь файл",
    r"\bсделай (миграцию|migration)", r"\bcommit\b", r"\bgit (commit|push|pull)",
    r"\bpush в (main|master|origin)", r"\bпуш в (main|master|origin)",
    r"\bcreate pr\b", r"\bсоздай pr\b",
    r"\bнапиши функцию", r"\bдобавь функцию.*в код",
    r"\bкласс\s+[A-Z]", r"\bимпорт\b",
    r"\bпропиши\b.*\b(в код|функцию|роут|hand)",
    r"\bв коде\b", r"\bв файле\b.*\.py",
]


def _has_hard_ops_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    return any(re.search(p, t) for p in _HARD_OPS_PATTERNS)


async def route(user_id: str, text: str) -> BotReply | str:
    try:
        # 1. Active workout session — route directly without AI parsing
        workout_session = await get_session(user_id, SessionType.WORKOUT)
        if workout_session is not None:
            from app.modules.fitness.session import handle_session_message
            return await handle_session_message(user_id, text, workout_session)

        # 2. Awaiting user input
        pending = await get_session(user_id, SessionType.AWAITING_INPUT)
        if pending is not None:
            return await _dispatch_pending(user_id, text, pending)

        # 3. Pending confirmation
        confirm = await get_session(user_id, SessionType.PENDING_CONFIRM)
        if confirm is not None:
            return await _dispatch_confirm(user_id, text, confirm)

        # 3a. Fitness pending decisions (disambiguate_log_or_plan и пр.) — короткие
        #     ответы юзера типа "запиши"/"запланируй" должны идти в fitness, не в general AI.
        try:
            from app.db import get_latest_fitness_pending_decision
            _fit_pend = await get_latest_fitness_pending_decision(user_id)
            if _fit_pend and _fit_pend.get("decision_type") in (
                "disambiguate_log_or_plan",
                "active_workout_session",
                "awaiting_custom_workout_details",
                "awaiting_add_exercises_to_workout",
                "cancel_planned_period_confirm",
                "period_copy_confirm",
                "selected_planned_workout_context",
                "exercise_disambiguation",
            ):
                from app.modules.fitness.handler import handle
                return await handle(user_id, text, {"intent": "fitness", "confidence": 1.0})
        except Exception:
            pass

        # 4. HARD fitness signal short-circuit — длинный план или явные веса/упражнения
        #    идут в фитнес сразу, минуя haiku-классификатор (он часто ошибается на длинных текстах)
        if _has_hard_fitness_signal(text):
            logger.debug(f"user={user_id} hard fitness signal — direct route")
            from app.modules.fitness.handler import handle
            return await handle(user_id, text, {"intent": "fitness", "confidence": 1.0})

        # 5. Parse intent with Claude (safe)
        try:
            parsed = await parse_message(text)
        except Exception as e:
            logger.exception("parse_message failed: %s", e)
            parsed = {"intent": "unknown", "confidence": 0.0, "error": str(e)[:200]}
        intent = parsed.get("intent", "unknown")
        logger.debug(f"user={user_id} intent={intent} confidence={parsed.get('confidence')}")

        return await _dispatch_intent(user_id, text, intent, parsed)
    except Exception as e:
        logger.exception("route() crashed: %s", e)
        return (
            f"⚠️ Внутренняя ошибка при обработке сообщения: {type(e).__name__}: {str(e)[:200]}\n\n"
            "Попробуй переформулировать или разбить на меньшие сообщения."
        )


async def _dispatch_intent(user_id: str, text: str, intent: str, parsed: dict) -> str:
    if intent in _FITNESS_INTENTS:
        from app.modules.fitness.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _FINANCE_INTENTS:
        from app.modules.finance.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _TASK_INTENTS:
        from app.modules.tasks.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _NUTRITION_INTENTS:
        from app.modules.nutrition.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _OPS_INTENTS:
        # Двойная защита: classifier мог ошибочно поставить ops на
        # "удали данные / копируй тренировки / почисти историю" — это НЕ ops.
        # ops запускаем только при явных code/deploy маркерах.
        if not _has_hard_ops_signal(text):
            logger.warning(f"intent=ops but no hard ops signal in: {text[:120]!r}")
            if _has_hard_fitness_signal(text):
                from app.modules.fitness.handler import handle
                return await handle(user_id, text, parsed)
            from app.ai import generate_general_answer
            return await generate_general_answer(text)
        from app.modules.ops.handler import handle
        return await handle(user_id, text, parsed)

    if intent == "general_question":
        from app.ai import generate_general_answer
        return await generate_general_answer(text)

    return f"Понял: {parsed.get('summary') or text}"


async def _dispatch_pending(user_id: str, text: str, pending: dict) -> str:
    module = pending.get("module")
    if module == "fitness":
        from app.modules.fitness.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "finance":
        from app.modules.finance.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "tasks":
        from app.modules.tasks.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "ops":
        from app.modules.ops.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    return "Не понял ответ."


async def _dispatch_confirm(user_id: str, text: str, confirm: dict) -> str:
    normalized = text.strip().lower()
    confirmed = any(w in normalized for w in ["да", "подтверждаю", "ок", "yes", "y"])
    cancelled = any(w in normalized for w in ["нет", "отмена", "отменяю", "no", "n"])

    if not confirmed and not cancelled:
        return "Подтверди или отмени действие."

    module = confirm.get("module")
    if module == "ops":
        from app.modules.ops.handler import handle_confirm
        return await handle_confirm(user_id, confirmed, confirm)

    return "Действие отменено." if cancelled else "Действие подтверждено."
