"""
One-shot Sonnet Vision description for a progress photo.

Called ONCE at upload time. The text description is stored in
progress_photos.ai_description and used by all downstream consumers
(history view, exports, coach context) without re-sending the image.

Output: short factual Russian description focused on body composition.
No motivation, no advice — just what's visible.
"""
from __future__ import annotations

import base64
import logging

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


_VISION_SYSTEM = """\
Ты помощник, который описывает фото прогресса для тренировочного дневника. \
На вход — одно фото тела. Опиши кратко (3-5 предложений на русском):

  - ракурс фото (фронт / спина / сбоку / другое)
  - какие группы мышц видны (грудь, пресс, плечи, бицепс, спина, ноги)
  - общее впечатление по их выраженности (плоский/округлый живот, выражены ли дельты, \
    плотность груди и т.п.)
  - заметные детали (свет/тень, поза, одежда если мешает)

Правила:
  - ТОЛЬКО факты которые видны на фото
  - НЕ давай советов, НЕ оценивай прогресс ("молодец", "хорошо растёшь") — это не нужно
  - НЕ упоминай возможный вес или жир в процентах
  - Если фото не подходит (не тело, селфи лица, темно) — напиши одну строку об этом
  - Без markdown, без эмодзи
"""


async def describe_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> str | None:
    """Returns short Russian description or None on failure."""
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = await _anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_VISION_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": media_type, "data": b64}},
                    {"type": "text", "text": "Опиши это фото для дневника."},
                ],
            }],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.error("describe_photo error: %s", exc)
        return None
