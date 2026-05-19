"""Отправь одно сообщение боту и получи ответ. Для интерактивной отладки.

Usage:
  python scripts/chat.py "текст сообщения"
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


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


async def main(message: str):
    env = load_env()
    api_id = int(env["TELEGRAM_API_ID"])
    api_hash = env["TELEGRAM_API_HASH"]
    bot = env["TELEGRAM_BOT_USERNAME"]
    session = env.get("STRING_SESSION", "")

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()
    try:
        async with client.conversation(bot, timeout=60) as conv:
            await conv.send_message(message)
            reply = await conv.get_response()
            print(reply.text or "<пусто>")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: chat.py 'text'")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
