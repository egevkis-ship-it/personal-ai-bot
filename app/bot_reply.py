from dataclasses import dataclass, field

from telegram import InlineKeyboardMarkup


@dataclass
class BotReply:
    text: str
    keyboard: InlineKeyboardMarkup | None = None
