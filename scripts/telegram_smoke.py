"""
Telethon-based E2E smoke test.

Шлёт реальные сообщения боту через твой аккаунт Telegram и проверяет ответы.
Использует уже задеплоенный бот.

ОДНОРАЗОВАЯ НАСТРОЙКА:
1. https://my.telegram.org/apps → создай app → получи API_ID и API_HASH
2. Положи их в .env.smoke:
     TELEGRAM_API_ID=12345
     TELEGRAM_API_HASH=abcdef...
     TELEGRAM_BOT_USERNAME=@your_bot_name   # имя бота в Telegram
     STRING_SESSION=                        # оставить пустым в первый раз
3. pip install telethon pyyaml
4. python scripts/telegram_smoke.py
   - В первый раз запросит phone + код из Telegram
   - Сохранит StringSession в .env.smoke
5. После: можешь добавить STRING_SESSION в GitHub Secrets и запускать в Actions

ИСПОЛЬЗОВАНИЕ:
  python scripts/telegram_smoke.py                 # все сценарии
  python scripts/telegram_smoke.py --scenario name # один
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
except ImportError:
    print("ERROR: pip install telethon pyyaml")
    sys.exit(1)


def load_env(path: str = ".env.smoke") -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def save_env(env: dict, path: str = ".env.smoke") -> None:
    with open(path, "w") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


SCENARIOS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "e2e_scenarios.yaml",
)


def load_scenarios() -> list[dict]:
    with open(SCENARIOS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("scenarios", [])


async def run_one(client, bot_username, scenario):
    name = scenario.get("name", "?")
    steps = scenario.get("steps") or []
    step_results = []
    all_ok = True

    for i, step in enumerate(steps, start=1):
        send_text = step.get("send", "")
        # Send + wait for reply
        async with client.conversation(bot_username, timeout=60) as conv:
            await conv.send_message(send_text)
            try:
                reply = await conv.get_response()
                resp_text = reply.text or ""
            except Exception as e:
                step_results.append({"step": i, "passed": False, "reason": f"no reply: {e}"})
                all_ok = False
                continue

        ok = True
        reason = ""
        for n in step.get("must_contain") or []:
            if n.lower() not in resp_text.lower():
                ok = False; reason = f"missing: {n!r}"; break
        if ok:
            for n in step.get("must_not_contain") or []:
                if n.lower() in resp_text.lower():
                    ok = False; reason = f"contains: {n!r}"; break
        if ok:
            for p in step.get("must_contain_regex") or []:
                if not re.search(p, resp_text, re.IGNORECASE):
                    ok = False; reason = f"regex miss: {p!r}"; break
        if ok:
            for p in step.get("must_not_contain_regex") or []:
                if re.search(p, resp_text, re.IGNORECASE):
                    ok = False; reason = f"regex hit: {p!r}"; break

        step_results.append({
            "step": i, "send": send_text[:50], "passed": ok, "reason": reason,
            "response_preview": resp_text[:120],
        })
        if not ok:
            all_ok = False

    return {"name": name, "passed": all_ok, "steps": step_results}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", help="run only this scenario name")
    args = ap.parse_args()

    env = load_env()
    api_id = int(env.get("TELEGRAM_API_ID") or os.getenv("TELEGRAM_API_ID") or 0)
    api_hash = env.get("TELEGRAM_API_HASH") or os.getenv("TELEGRAM_API_HASH")
    bot_username = env.get("TELEGRAM_BOT_USERNAME") or os.getenv("TELEGRAM_BOT_USERNAME")
    string_session = env.get("STRING_SESSION") or os.getenv("TELEGRAM_STRING_SESSION") or ""

    if not (api_id and api_hash and bot_username):
        print("ERROR: настрой TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_USERNAME в .env.smoke")
        sys.exit(1)

    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    await client.start()  # запросит phone+код если string_session пуст

    if not string_session:
        new_session = client.session.save()
        env["STRING_SESSION"] = new_session
        save_env(env)
        print(f"✅ Сохранил StringSession в .env.smoke ({len(new_session)} chars).")
        print(f"   Добавь его в GitHub Secrets как TELEGRAM_STRING_SESSION для CI.")

    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s.get("name") == args.scenario]
        if not scenarios:
            print(f"ERROR: сценарий {args.scenario!r} не найден")
            sys.exit(1)

    print(f"🧪 Запускаю {len(scenarios)} сценариев против {bot_username}...")
    results = []
    start = time.time()
    for s in scenarios:
        print(f"  → {s.get('name')}...")
        try:
            r = await run_one(client, bot_username, s)
        except Exception as e:
            r = {"name": s.get("name"), "passed": False, "steps": [{"step": 0, "passed": False, "reason": str(e)}]}
        results.append(r)
        print(f"    {'✅' if r['passed'] else '❌'}")

    await client.disconnect()

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    elapsed = round(time.time() - start, 1)
    print()
    print(f"{'='*50}")
    print(f"{'✅ ALL PASSED' if passed == total else '❌ SOME FAILED'}: {passed}/{total} ({elapsed}s)")
    print()
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        print(f"{mark} {r['name']}")
        if not r["passed"]:
            for s in r["steps"]:
                if not s.get("passed"):
                    print(f"    ✗ step {s.get('step')}: {s.get('reason')}")
                    print(f"      resp: {s.get('response_preview')}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
