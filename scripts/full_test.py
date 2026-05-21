"""
Полный тест-сьют фитнес-бота через Telethon.
Запуск: python scripts/full_test.py
"""
import asyncio
import os
import sys
import time

from telethon import TelegramClient
from telethon.sessions import StringSession

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

# ── helpers ──────────────────────────────────────────────────────────────────

def load_env(path: str = ".env.smoke") -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


async def send(conv, message: str, timeout: int = 60) -> str:
    await conv.send_message(message)
    reply = await conv.get_response(timeout=timeout)
    return reply.text or "<пусто>"


def check(label: str, response: str, expect_contains=(), expect_not_contains=()):
    ok = True
    for kw in expect_contains:
        if kw.lower() not in response.lower():
            print(f"  {FAIL} Ожидали {kw!r} в ответе")
            ok = False
    for kw in expect_not_contains:
        if kw.lower() in response.lower():
            print(f"  {FAIL} НЕ ожидали {kw!r} в ответе")
            ok = False
    status = PASS if ok else FAIL
    print(f"{status} {label}")
    if not ok:
        print(f"  Ответ: {response[:300]!r}")
    return ok


# ── test suites ──────────────────────────────────────────────────────────────

async def suite_reset(conv, results):
    """Clean slate"""
    print("\n═══ RESET ═══")
    r = await send(conv, "/wipe yes all")
    ok = check("Wipe all", r, expect_contains=["truncate", "обнул"])
    results.append(("reset/wipe", ok))
    await asyncio.sleep(1)


async def suite_plan_setup(conv, results):
    """Set up a simple plan for today and tomorrow"""
    print("\n═══ ПЛАН: НАСТРОЙКА ═══")
    today = "сегодня"
    tomorrow = "завтра"

    r = await send(conv, "Поставь на сегодня грудь: Жим штанги лёжа 4×10 80кг, Жим гантелей наклонный 3×12 25кг, Кабельный кроссовер 3×15 15кг")
    ok = check("Поставь на сегодня [план]", r, expect_not_contains=["ошибк", "error", "не смог", "fail"])
    results.append(("plan/set_today", ok))

    r = await send(conv, "Поставь на завтра спина: Тяга штанги в наклоне 4×8 70кг, Подтягивания 3×10, Тяга гантели 3×12 30кг")
    ok = check("Поставь на завтра [план]", r, expect_not_contains=["ошибк", "error", "не смог"])
    results.append(("plan/set_tomorrow", ok))

    r = await send(conv, "Что у меня сегодня")
    ok = check("Что у меня сегодня", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/show_today", ok))

    r = await send(conv, "Что у меня завтра")
    ok = check("Что у меня завтра", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/show_tomorrow", ok))


async def suite_active_session(conv, results):
    """Full active session: start → exercises → corrections → finish"""
    print("\n═══ АКТИВНАЯ СЕССИЯ ═══")

    # Start
    r = await send(conv, "Начинаю тренировку")
    ok = check("Начинаю тренировку", r, expect_not_contains=["ошибк", "error"])
    results.append(("session/start", ok))

    # Cardio / time-based (was broken: Велосипед 10 минут)
    r = await send(conv, "Велосипед 10 минут")
    ok = check("Велосипед 10 минут [CRITICAL FIX]", r, expect_contains=["велосипед", "10", "мин"], expect_not_contains=["не смог", "ошибк", "непонятно"])
    results.append(("session/cardio_time", ok))

    # Планка 60 секунд
    r = await send(conv, "Планка 60 секунд")
    ok = check("Планка 60 секунд [CRITICAL FIX]", r, expect_contains=["планка", "60", "сек"], expect_not_contains=["не смог", "ошибк"])
    results.append(("session/plank_time", ok))

    # Classic: weight×reps
    r = await send(conv, "Жим штанги лёжа 80x10")
    ok = check("Жим 80x10 [classic]", r, expect_contains=["жим", "80", "10"])
    results.append(("session/classic_wx_reps", ok))

    # CRITICAL FIX: NxM W кг format
    r = await send(conv, "Hip trust 4x12 10 кг")
    ok = check("Hip trust 4x12 10 кг [CRITICAL FIX]", r, expect_contains=["10", "12"], expect_not_contains=["4 кг", "не смог"])
    results.append(("session/nxm_weight_after", ok))

    # CRITICAL FIX: W кг NxM format
    r = await send(conv, "Сгибание ног 50кг 4х12")
    ok = check("Сгибание ног 50кг 4х12 [CRITICAL FIX]", r, expect_contains=["50", "12"], expect_not_contains=["4 кг", "не смог"])
    results.append(("session/weight_before_nxm", ok))

    # Bodyweight
    r = await send(conv, "Подтягивания 10 8 7")
    ok = check("Подтягивания 10 8 7 [bodyweight multi-set]", r, expect_contains=["подтяг", "10"], expect_not_contains=["ошибк"])
    results.append(("session/bodyweight_multi", ok))

    # Decimal weight
    r = await send(conv, "Разводка гантелей 17.5 кг 3×12")
    ok = check("Разводка 17.5кг [decimal]", r, expect_contains=["17", "12"])
    results.append(("session/decimal_weight", ok))

    # Sets with W N по M format
    r = await send(conv, "Присед 100 4 по 8")
    ok = check("Присед 100 4 по 8 [count-by-reps]", r, expect_contains=["присед", "100", "8"])
    results.append(("session/count_by_reps", ok))

    # Spoken number
    r = await send(conv, "Жим гантелей лёжа тридцать пять килограмм на двенадцать")
    ok = check("Жим гантелей [spoken numbers]", r, expect_contains=["35", "12"], expect_not_contains=["не смог", "ошибк"])
    results.append(("session/spoken_numbers", ok))


async def suite_corrections(conv, results):
    """Correction scenarios inside active session"""
    print("\n═══ ИСПРАВЛЕНИЯ ═══")

    r = await send(conv, "Не 35, а 32.5")
    ok = check("Не 35 а 32.5 [correct weight]", r, expect_not_contains=["ошибк", "error"])
    results.append(("correction/weight", ok))

    r = await send(conv, "Удали последний подход")
    ok = check("Удали последний подход", r, expect_not_contains=["ошибк", "error"])
    results.append(("correction/delete_last", ok))

    r = await send(conv, "Во втором подходе жима было 8 повторений, не 10")
    ok = check("Исправь 2й подход жима: reps", r, expect_not_contains=["ошибк", "error"])
    results.append(("correction/reps_by_num", ok))


async def suite_finish_measurements(conv, results):
    """Finish session and add measurements"""
    print("\n═══ ФИНИШ + ЗАМЕРЫ ═══")

    r = await send(conv, "Закончил тренировку", timeout=90)
    ok = check("Закончил тренировку", r, expect_not_contains=["ошибк", "error"])
    results.append(("session/finish", ok))

    r = await send(conv, "Вес 95.5")
    ok = check("Замер: вес", r, expect_contains=["95", "вес"])
    results.append(("measurement/weight_only", ok))

    r = await send(conv, "Вес 95.5\nГолень 44\nБедро 62\nБедра 100\nЖивот 88\nТалия 85\nГрудь 105\nРука 38\nШея 40")
    ok = check("Мульти-замеры", r, expect_contains=["замер", "95"], expect_not_contains=["ошибк"])
    results.append(("measurement/multiline", ok))


async def suite_archive(conv, results):
    """Archive queries: today, yesterday, week"""
    print("\n═══ АРХИВ ═══")

    r = await send(conv, "Что я делал сегодня")
    ok = check("Что я делал сегодня [archive]", r, expect_not_contains=["ошибк", "plan", "ничего нет", "не нашёл"])
    results.append(("archive/today", ok))

    r = await send(conv, "Покажи последнее что я записал")
    ok = check("Покажи последнее", r, expect_not_contains=["ошибк", "error"])
    results.append(("archive/show_last", ok))

    r = await send(conv, "Что я делал на этой неделе")
    ok = check("Архив этой недели", r, expect_not_contains=["ошибк", "error"])
    results.append(("archive/this_week", ok))


async def suite_plan_queries(conv, results):
    """Plan display queries"""
    print("\n═══ ПРОСМОТР ПЛАНА ═══")

    r = await send(conv, "Что у меня сегодня")
    ok = check("Что у меня сегодня [plan]", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/today", ok))

    r = await send(conv, "Что у меня завтра")
    ok = check("Что завтра", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/tomorrow", ok))

    r = await send(conv, "Что у меня в пятницу")
    ok = check("Что в пятницу", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/weekday_friday", ok))

    r = await send(conv, "Что у меня в понедельник")
    ok = check("Что в понедельник", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/weekday_monday", ok))

    r = await send(conv, "План на неделю")
    ok = check("План на неделю", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan/week", ok))


async def suite_plan_edit(conv, results):
    """Plan editing: add, remove, replace"""
    print("\n═══ РЕДАКТИРОВАНИЕ ПЛАНА ═══")

    r = await send(conv, "Добавь в план сегодня Жим гантелей 3×15 20кг")
    ok = check("Добавь в план [B7 fix]", r, expect_not_contains=["ошибк", "не нашёл", "не смог"])
    results.append(("plan_edit/add_exercise", ok))

    r = await send(conv, "Убери из сегодняшнего плана кабельный кроссовер")
    ok = check("Убери из плана", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan_edit/remove_exercise", ok))

    r = await send(conv, "Замени жим штанги лёжа на жим гантелей лёжа в сегодняшнем плане")
    ok = check("Замени упражнение в плане", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan_edit/replace_exercise", ok))

    # Shift plan
    r = await send(conv, "Пропустил вчера, сдвинь план на 1 день вперёд")
    ok = check("Сдвинь план на день", r, expect_not_contains=["ошибк", "error"])
    results.append(("plan_edit/shift_plan", ok))


async def suite_weekly_plan(conv, results):
    """Weekly plan creation"""
    print("\n═══ НЕДЕЛЬНЫЙ ПЛАН ═══")

    r = await send(conv, (
        "Запланируй следующую неделю:\n"
        "Пн — грудь: жим штанги 4×8 85кг, жим гантелей наклон 3×10 27.5кг\n"
        "Ср — спина: тяга штанги 4×8 75кг, подтягивания 4×8\n"
        "Пт — ноги: присед 4×5 100кг, жим ногами 3×12 120кг\n"
        "Сб — плечи: жим гантелей стоя 4×10 22кг, тяга к подбородку 3×12 40кг"
    ), timeout=90)
    ok = check("Недельный план [4 дня]", r, expect_not_contains=["ошибк", "error", "не смог разобрать"])
    results.append(("weekly_plan/multi_day", ok))

    r = await send(conv, "Запланируй неделю: пн грудь жим 4×10 80кг; ср спина тяга 4×8 70кг; пт ноги присед 4×5 100кг")
    ok = check("Компактный план через ';' [B4 fix]", r, expect_not_contains=["ошибк", "error", "не смог разобрать"])
    results.append(("weekly_plan/compact_semicolons", ok))


async def suite_stats_misc(conv, results):
    """Stats and misc commands"""
    print("\n═══ СТАТИСТИКА И ПРОЧЕЕ ═══")

    r = await send(conv, "Сводка")
    ok = check("Сводка / quick_stats", r, expect_not_contains=["ошибк", "error"])
    results.append(("stats/quick_stats", ok))

    r = await send(conv, "Сколько тренировок на этой неделе")
    ok = check("Стрик / count", r, expect_not_contains=["ошибк", "error"])
    results.append(("stats/streak", ok))

    r = await send(conv, "/help")
    ok = check("/help", r, expect_not_contains=["ошибк", "error"])
    results.append(("misc/help", ok))


async def suite_second_session(conv, results):
    """Second session with the problematic formats from manual testing"""
    print("\n═══ ВТОРАЯ СЕССИЯ (проблемные форматы) ═══")

    r = await send(conv, "Начинаю тренировку ягодицы и ноги")
    ok = check("Старт 2й сессии", r, expect_not_contains=["ошибк", "error"])
    results.append(("session2/start", ok))

    tests = [
        ("Велосипед 10 минут", ["велосипед", "10", "мин"], ["не смог", "ошибк"]),
        ("Hip trust 4x12 10 кг", ["10", "12"], ["4 кг", "не смог"]),
        ("Сгибание ног 50кг 4х12", ["50", "12"], ["4 кг", "не смог"]),
        ("Разгибание ног 3х15 40кг", ["40", "15"], ["3 кг", "не смог"]),
        ("Жим ногами 4x12 80 кг", ["80", "12"], ["4 кг", "не смог"]),
        ("Икры в тренажере 50кг 4x20", ["50", "20"], ["4 кг", "не смог"]),
        ("Ягодичный мостик со штангой 60 кг 4x15", ["60", "15"], ["не смог"]),
        ("Планка 60 секунд 3 подхода", ["планка", "60", "сек"], ["не смог", "ошибк"]),
    ]

    for msg, expect, not_expect in tests:
        r = await send(conv, msg)
        ok = check(f"  {msg}", r, expect_contains=expect, expect_not_contains=not_expect)
        results.append((f"session2/{msg[:20]}", ok))

    r = await send(conv, "Всё, завершаю", timeout=90)
    ok = check("Финиш 2й сессии", r, expect_not_contains=["ошибк", "error"])
    results.append(("session2/finish", ok))


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    env = load_env()
    api_id = int(env["TELEGRAM_API_ID"])
    api_hash = env["TELEGRAM_API_HASH"]
    bot = env["TELEGRAM_BOT_USERNAME"]
    session = env.get("STRING_SESSION", "")

    print("🤖 Comprehensive fitness-bot test suite")
    print("=" * 60)

    results: list[tuple[str, bool]] = []

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        await client.start()

        # Run all suites sequentially within a single conversation
        async with client.conversation(bot, timeout=180) as conv:
            await suite_reset(conv, results)
            await suite_plan_setup(conv, results)
            await suite_active_session(conv, results)
            await suite_corrections(conv, results)
            await suite_finish_measurements(conv, results)
            await suite_archive(conv, results)
            await suite_plan_queries(conv, results)
            await suite_plan_edit(conv, results)
            await suite_weekly_plan(conv, results)
            await suite_stats_misc(conv, results)
            await suite_second_session(conv, results)

    # Summary
    print("\n" + "=" * 60)
    print("ИТОГИ:")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    failed = [(label, ok) for label, ok in results if not ok]

    print(f"\n{PASS} Прошло: {passed}/{total}")
    if failed:
        print(f"\n{FAIL} Упало ({len(failed)}):")
        for label, _ in failed:
            print(f"  • {label}")
    else:
        print("\n🎉 Все тесты прошли!")

    print(f"\nРезультат: {'PASS' if passed == total else f'FAIL ({len(failed)} failures)'}")
    return len(failed) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
