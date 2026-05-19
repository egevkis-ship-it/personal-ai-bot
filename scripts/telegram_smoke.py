"""
E2E тесты в РЕАЛЬНОМ Telegram.

Шлёт сообщения боту от твоего аккаунта, читает ответы, проверяет.

ОДНОРАЗОВАЯ НАСТРОЙКА:

1. Получи API_ID и API_HASH:
   - https://my.telegram.org/apps
   - Создай "Telegram App" (любое название)
   - Сохрани api_id и api_hash

2. Создай файл .env.smoke в корне проекта:

     TELEGRAM_API_ID=12345
     TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
     TELEGRAM_BOT_USERNAME=@your_bot_username
     STRING_SESSION=

3. Установи зависимости:
     .venv/bin/python -m pip install telethon pyyaml

4. Первый запуск (интерактивный логин):
     .venv/bin/python scripts/telegram_smoke.py

   - Запросит твой номер телефона
   - Пришлёт код в Telegram (от Telegram, не от твоего бота)
   - Ты его введёшь
   - Сохранит STRING_SESSION в .env.smoke

5. Все следующие запуски — БЕЗ логина:
     .venv/bin/python scripts/telegram_smoke.py

CI/Cron:
   Добавь STRING_SESSION в env-переменные деплоя/CI и
   копируй .env.smoke оттуда.
"""
import argparse
import asyncio
import os
import re
import sys
import time

try:
    import yaml
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import FloodWaitError
except ImportError:
    print("❌ Не установлены зависимости.")
    print("   Запусти: .venv/bin/python -m pip install telethon pyyaml")
    sys.exit(1)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env.smoke")
SCENARIOS_PATH = os.path.join(ROOT, "tests", "e2e_scenarios.yaml")


def load_env() -> dict:
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def save_env(env: dict) -> None:
    with open(ENV_PATH, "w") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


def load_scenarios() -> list[dict]:
    with open(SCENARIOS_PATH) as f:
        return (yaml.safe_load(f) or {}).get("scenarios", [])


def check_assertions(response_text: str, step: dict) -> tuple[bool, str]:
    """Returns (passed, reason)."""
    must = step.get("must_contain") or []
    must_not = step.get("must_not_contain") or []
    must_re = step.get("must_contain_regex") or []
    must_not_re = step.get("must_not_contain_regex") or []

    rt = (response_text or "").lower()
    for n in must:
        if n.lower() not in rt:
            return False, f"missing required: {n!r}"
    for n in must_not:
        if n.lower() in rt:
            return False, f"contains forbidden: {n!r}"
    for p in must_re:
        if not re.search(p, response_text, re.IGNORECASE):
            return False, f"regex not matched: {p!r}"
    for p in must_not_re:
        if re.search(p, response_text, re.IGNORECASE):
            return False, f"forbidden regex matched: {p!r}"
    return True, ""


async def send_and_wait(client, bot, text: str, timeout: int = 30) -> str:
    """Send message, wait for the first reply."""
    async with client.conversation(bot, timeout=timeout) as conv:
        await conv.send_message(text)
        try:
            reply = await conv.get_response()
            return reply.text or ""
        except asyncio.TimeoutError:
            return ""


async def run_scenarios(client, bot_username: str, only: str | None = None) -> list[dict]:
    scenarios = load_scenarios()
    if only:
        scenarios = [s for s in scenarios if s.get("name") == only or only in s.get("name", "")]
        if not scenarios:
            print(f"❌ Сценарий {only!r} не найден")
            sys.exit(1)

    results = []
    for scenario in scenarios:
        name = scenario.get("name", "?")
        steps = scenario.get("steps") or []
        print(f"  ▶  {name}")

        steps_results = []
        all_ok = True

        for i, step in enumerate(steps, start=1):
            send_text = step.get("send", "")
            timeout = int(step.get("timeout_s", 30))
            preview_send = send_text if len(send_text) < 50 else send_text[:50] + "…"
            print(f"     step {i}: send {preview_send!r} ... ", end="", flush=True)

            t0 = time.time()
            try:
                response = await send_and_wait(client, bot_username, send_text, timeout)
            except FloodWaitError as e:
                print(f"⚠️  FLOOD WAIT {e.seconds}s")
                steps_results.append({"step": i, "passed": False, "reason": f"flood {e.seconds}s"})
                all_ok = False
                continue
            except Exception as e:
                print(f"❌ {type(e).__name__}: {e}")
                steps_results.append({"step": i, "passed": False, "reason": f"{type(e).__name__}: {e}"})
                all_ok = False
                continue

            elapsed = time.time() - t0

            if not response:
                print(f"❌ no reply in {timeout}s")
                steps_results.append({"step": i, "passed": False, "reason": "no reply (timeout)"})
                all_ok = False
                continue

            ok, reason = check_assertions(response, step)
            mark = "✅" if ok else "❌"
            print(f"{mark} {elapsed:.1f}s")
            if not ok:
                print(f"          reason: {reason}")
                print(f"          reply: {response[:200]!r}")
                all_ok = False
            steps_results.append({
                "step": i, "passed": ok, "reason": reason,
                "response_preview": response[:200],
                "elapsed_s": round(elapsed, 2),
            })

        results.append({"name": name, "passed": all_ok, "steps": steps_results})

    return results


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", help="прогнать только этот сценарий (по имени или подстроке)")
    args = ap.parse_args()

    env = load_env()
    api_id_raw = env.get("TELEGRAM_API_ID") or os.getenv("TELEGRAM_API_ID")
    api_hash = env.get("TELEGRAM_API_HASH") or os.getenv("TELEGRAM_API_HASH")
    bot_username = env.get("TELEGRAM_BOT_USERNAME") or os.getenv("TELEGRAM_BOT_USERNAME")
    string_session = env.get("STRING_SESSION") or os.getenv("TELEGRAM_STRING_SESSION") or ""

    if not (api_id_raw and api_hash and bot_username):
        print("❌ Нет конфигурации.")
        print(f"   Создай файл {ENV_PATH} со строками:")
        print("   TELEGRAM_API_ID=12345")
        print("   TELEGRAM_API_HASH=abcdef...")
        print("   TELEGRAM_BOT_USERNAME=@your_bot_username")
        print("   STRING_SESSION=")
        print()
        print("   API_ID/API_HASH получи на https://my.telegram.org/apps")
        sys.exit(1)

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print(f"❌ TELEGRAM_API_ID должен быть числом, а не {api_id_raw!r}")
        sys.exit(1)

    print(f"🔌 Подключаюсь к Telegram как пользователь...")
    client = TelegramClient(StringSession(string_session), api_id, api_hash)

    try:
        await client.start()
    except Exception as e:
        print(f"❌ Не получилось залогиниться: {e}")
        sys.exit(1)

    if not string_session:
        new_session = client.session.save()
        env["STRING_SESSION"] = new_session
        save_env(env)
        print(f"✅ Сохранил STRING_SESSION в {ENV_PATH}")
        print(f"   (длина {len(new_session)} символов — больше логиниться не будет)")

    me = await client.get_me()
    print(f"   логин: @{me.username or me.id}")
    print(f"   бот:   {bot_username}")
    print()

    print(f"🧪 Прогоняю сценарии:")
    start = time.time()
    try:
        results = await run_scenarios(client, bot_username, args.scenario)
    finally:
        await client.disconnect()

    elapsed = round(time.time() - start, 1)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    print()
    print("=" * 60)
    if passed == total:
        print(f"✅ ALL PASSED — {passed}/{total} сценариев за {elapsed}s")
    else:
        print(f"❌ FAILED — {passed}/{total} сценариев прошло за {elapsed}s")
        print()
        for r in results:
            if not r["passed"]:
                print(f"  ❌ {r['name']}")
                for s in r["steps"]:
                    if not s.get("passed"):
                        print(f"     step {s['step']}: {s.get('reason')}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
