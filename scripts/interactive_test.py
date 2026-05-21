"""
Интерактивный тест: отправляем по одному сообщению, читаем ПОЛНЫЙ ответ,
проверяем точные значения (exercise, weight, reps) и выводим всё для анализа.

Запуск: python scripts/interactive_test.py
"""
import asyncio
import re
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


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


import os
env = load_env()


async def send(conv, message: str, timeout: int = 60) -> str:
    print(f"\n>>> SEND: {message!r}")
    await conv.send_message(message)
    reply = await conv.get_response(timeout=timeout)
    text = reply.text or "<пусто>"
    print(f"<<< BOT:\n{text}\n")
    return text


def extract_numbers(text: str) -> set:
    """Все числа из текста."""
    return set(re.findall(r'\d+(?:[.,]\d+)?', text))


def check_exact(label: str, response: str, must_contain: list[str], must_not_contain: list[str] = None) -> bool:
    """Строгая проверка: all of must_contain present, none of must_not_contain."""
    ok = True
    for kw in must_contain:
        if kw.lower() not in response.lower():
            print(f"  {FAIL} Нет {kw!r}")
            ok = False
    for kw in (must_not_contain or []):
        if kw.lower() in response.lower():
            print(f"  {FAIL} Нежданный {kw!r}")
            ok = False
    status = PASS if ok else FAIL
    print(f"  {status} {label}")
    return ok


# ── СЕССИЯ 1: Критичные форматы ──────────────────────────────────────────────

SESSION1_EXERCISES = [
    # (сообщение, ожидаемое_имя_фрагмент, ожидаемый_вес, ожидаемые_повторения)
    ("Велосипед 10 минут",                  "велосипед",   None,  None),   # time-based
    ("Планка 60 секунд",                    "планка",      None,  None),   # time-based
    ("Жим штанги лёжа 80 кг 3x10",         "жим",         "80",  "10"),   # classic W NxM
    ("Hip trust 4x12 10 кг",               "hip trust",   "10",  "12"),   # NxM W кг (CRITICAL)
    ("Сгибание ног 50кг 4х12",             "сгибание",    "50",  "12"),   # Wкг NxM (CRITICAL)
    ("Разгибание ног 3х15 40кг",           "разгибание",  "40",  "15"),   # NxM Wкг
    ("Жим ногами 4x12 80 кг",              "жим ногами",  "80",  "12"),   # NxM W кг
    ("Икры в тренажере 50кг 4x20",         "икры",        "50",  "20"),   # Wкг NxM
    ("Подтягивания 10 8 7",                "подтяг",      None,  "10"),   # bodyweight multi
    ("Разводка гантелей 17.5 кг 3×12",    "разводка",    "17",  "12"),   # decimal
    ("Жим гантелей лёжа тридцать пять килограмм на двенадцать",
                                           "жим",         "35",  "12"),   # SPOKEN NUMBERS (CRITICAL)
    ("Ягодичный мостик со штангой 60 кг 4x15", "ягодич",  "60",  "15"),
]


async def run_session1(conv, results: list):
    print("\n" + "═"*60)
    print("СЕССИЯ 1: Критичные форматы")
    print("═"*60)

    # Сброс
    r = await send(conv, "/wipe yes all")
    check_exact("wipe", r, ["truncate", "обнул"])
    await asyncio.sleep(1)

    # Старт
    r = await send(conv, "Начинаю тренировку")
    check_exact("start", r, [], ["ошибк"])

    # Упражнения
    for msg, name_frag, exp_weight, exp_reps in SESSION1_EXERCISES:
        r = await send(conv, msg)
        must = []
        must_not = ["ошибк", "не смог", "непонятн", "error"]
        if exp_weight:
            must.append(exp_weight)
        if exp_reps:
            must.append(exp_reps)
        if name_frag:
            must.append(name_frag)
        ok = check_exact(f"  {msg[:50]}", r, must, must_not)
        results.append((msg[:40], ok))

    # Закончили
    r = await send(conv, "Закончил тренировку", timeout=90)
    ok = check_exact("finish", r, [], ["ошибк"])
    results.append(("finish", ok))

    # ДАМП АРХИВА — проверяем что реально записалось
    print("\n" + "─"*40)
    print("ДАМП: Что реально записалось в архив")
    print("─"*40)
    r = await send(conv, "Что я делал сегодня", timeout=60)
    print("FULL ARCHIVE RESPONSE:")
    print(r)

    # Теперь проверяем конкретные значения в архиве
    archive_checks = [
        ("Hip trust 10кг×12 в архиве",    r, ["10", "12"],  ["4 кг"]),
        ("Сгибание ног 50кг×12 в архиве", r, ["50", "12"],  ["4 кг"]),
        ("Жим 35кг×12 (spoken) в архиве", r, ["35", "12"],  ["5 кг"]),
    ]
    for label, response, must, must_not in archive_checks:
        ok = check_exact(label, response, must, must_not)
        results.append((label, ok))


# ── СЕССИЯ 2: Исправления ────────────────────────────────────────────────────

async def run_corrections(conv, results: list):
    print("\n" + "═"*60)
    print("СЕССИЯ 2: Исправления (свежая сессия)")
    print("═"*60)

    r = await send(conv, "Начинаю тренировку — тест исправлений")
    check_exact("start", r, [], ["ошибк"])

    # Записываем ДВА упражнения с разными весами
    r = await send(conv, "Жим штанги лёжа 80 кг 3x10")
    check_exact("record Жим 80x10", r, ["80", "10"], ["ошибк"])

    r = await send(conv, "Приседания 100 кг 4x8")
    check_exact("record Приседания 100x8", r, ["100", "8"], ["ошибк"])

    # "Не 80, а 82.5" — должно исправить ЖИМ (80кг), не ПРИСЕДАНИЯ (100кг = последнее)
    r = await send(conv, "Не 80, а 82.5")
    ok = check_exact("correct weight 80→82.5 (жим, не приседания)", r,
                     ["82", "жим"], ["100", "приседан", "ошибк"])
    results.append(("correct_weight_by_value", ok))

    # Показать сессию — должно вернуть список подходов, не ошибку плана
    r = await send(conv, "Покажи текущую сессию")
    ok = check_exact("session display (fix 3)", r,
                     ["82", "100"],
                     ["ошибк", "плановой тренировки не найдена", "не найдено"])
    results.append(("session_show_during_active", ok))
    print(f"  Сессия сейчас:\n  {r[:400]}")

    # Исправление количества повторений по номеру подхода
    r = await send(conv, "Во 2-м подходе приседаний было 6 повторений, а не 8")
    ok = check_exact("correct reps by number", r, ["6"], ["ошибк"])
    results.append(("correct_reps_by_num", ok))

    # Удалить последний подход
    r = await send(conv, "Удали последний подход")
    ok = check_exact("delete last set", r, [], ["ошибк"])
    results.append(("delete_last_set", ok))

    r = await send(conv, "Закончил", timeout=90)
    check_exact("finish", r, [], ["ошибк"])


# ── СЕССИЯ 3: Замеры ─────────────────────────────────────────────────────────

async def run_measurements(conv, results: list):
    print("\n" + "═"*60)
    print("ЗАМЕРЫ")
    print("═"*60)

    r = await send(conv, "Вес 97.3")
    ok = check_exact("weight only", r, ["97", "вес"], ["ошибк"])
    results.append(("measure_weight", ok))

    multiline = "Вес 102.3\nГолень 46\nБедро 68\nБедра 105\nЖивот 98\nТалия 92\nГрудь 108\nРука 40\nШея 40"
    r = await send(conv, multiline)
    ok = check_exact("multiline measure", r,
                     ["102", "замер"],
                     ["ошибк", "error"])
    results.append(("measure_multiline", ok))
    print(f"  Ответ:\n  {r[:400]}")


# ── СЕССИЯ 4: Планирование ───────────────────────────────────────────────────

async def run_planning(conv, results: list):
    print("\n" + "═"*60)
    print("ПЛАНИРОВАНИЕ")
    print("═"*60)

    # Добавить на конкретный день — ДОЛЖНО создать план (не edit_plan с ошибкой)
    r = await send(conv, "Поставь на сегодня грудь: Жим штанги 4×10 80кг, Жим гантелей наклон 3×12 25кг")
    ok = check_exact("set plan today (B2 fix)", r,
                     [],
                     ["ошибк", "не смог", "для редактирования не найдена"])
    results.append(("plan_set_today_B2", ok))
    print(f"  Ответ на Поставь: {r[:300]}")

    # Посмотреть план — должен содержать жим и 80
    r = await send(conv, "Что у меня сегодня")
    ok = check_exact("show plan today", r, ["жим", "80"], ["ошибк", "не найдено"])
    results.append(("plan_show_today", ok))
    print(f"  План сегодня:\n  {r[:400]}")

    # Добавить в план
    r = await send(conv, "Добавь в план сегодня Разводка гантелей 3×15 20кг")
    ok = check_exact("edit plan add", r, [], ["ошибк", "не нашёл"])
    results.append(("plan_edit_add", ok))

    # Компактный недельный план
    r = await send(conv,
        "Запланируй неделю: пн грудь жим 4×10 80кг; ср спина тяга 4×8 70кг; пт ноги присед 4×5 100кг",
        timeout=90)
    ok = check_exact("weekly compact plan (B4)", r, [], ["ошибк", "не смог разобрать"])
    results.append(("weekly_plan_compact", ok))

    # Сдвиг плана
    r = await send(conv, "Сдвинь план на 1 день вперёд")
    ok = check_exact("shift plan", r, [], ["ошибк"])
    results.append(("plan_shift", ok))
    print(f"  Shift ответ: {r[:300]}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    api_id = int(env["TELEGRAM_API_ID"])
    api_hash = env["TELEGRAM_API_HASH"]
    bot = env["TELEGRAM_BOT_USERNAME"]
    session_str = env.get("STRING_SESSION", "")

    print("🔬 INTERACTIVE FITNESS-BOT TEST")
    print("Режим: полный вывод каждого ответа + проверка точных значений")
    print("=" * 60)

    results: list[tuple[str, bool]] = []

    async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
        await client.start()

        async with client.conversation(bot, timeout=180) as conv:
            await run_session1(conv, results)
            await run_corrections(conv, results)
            await run_measurements(conv, results)
            await run_planning(conv, results)

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
        print("\n🎉 Все проверки прошли!")
    return len(failed) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
