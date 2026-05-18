from dataclasses import dataclass

from telegram import InlineKeyboardMarkup


@dataclass
class BotReply:
    text: str
    keyboard: InlineKeyboardMarkup | None = None
    document_bytes: bytes | None = None  # отправить как файл
    document_filename: str | None = None
    document_caption: str | None = None
