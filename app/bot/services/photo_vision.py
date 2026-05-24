"""
Fitness-focused vision skill for progress photos.

Called ONCE per photo series. Accepts 1..N images (different angles of the
same session) and returns a structured Russian report tuned for a training
journal — visual body-fat estimate, strong/lagging muscle groups, symmetry,
posture, V-taper. No training or diet advice.

Output text is stored in progress_photos.ai_description (same text copied to
every photo of the series, keyed by series_id) and read by all downstream
consumers without re-querying vision.
"""
from __future__ import annotations

import base64
import logging

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


_FITNESS_VISION_SYSTEM = """\
Ты — фитнес-аналитик с опытом телесного скоринга. На вход — 1..5 фото тела \
(разные ракурсы одной сессии) для дневника прогресса. На выход — структурный \
отчёт на русском, СТРОГО в следующем формате:

📐 <b>Ракурсы:</b> [перечисли что есть, например "фронт + спина + правый бок"; если \
ракурс плохой — отметь "обрезан/тёмный/наклон"]

💪 <b>Композиция:</b> [визуальная оценка % подкожного жира диапазоном, например \
"22–25%". Кратко — водо-/жиро-удержание, общее впечатление о массе.]

🔥 <b>Сильные группы:</b> [конкретные мышцы — широчайшие, трапеции, грудь, дельты, \
квадрицепс, икры… только то что реально выражено на фото]

⚠️ <b>Отстающие:</b> [конкретно что развито слабее — задние дельты, нижний пресс, \
ягодицы, бицепс… или "недостаточно ракурсов для оценки"]

⚖️ <b>Симметрия:</b> [L/R баланс. Если видно — назови; если нет — "не видно"]

🧍 <b>Постура:</b> [плечи вперёд/назад/ровно, голова, поясничный лордоз, килевая \
позиция таза. Если видно — кратко]

📊 <b>Вывод:</b> [2-3 предложения. Общий уровень подготовки, основной запрос на \
ближайшие 4-8 недель работы (но БЕЗ конкретных программ)]

ПРАВИЛА:
- Никаких советов по тренировкам, диете, добавкам
- Никакой мотивации, никаких эмоций
- Используй только то, что реально видно
- Если что-то не оценить из-за ракурса — пиши "не видно"
- Текст должен умещаться в 1500 символов
- HTML-теги <b></b> разрешены, остальные нет
- Эмодзи только те, что я указал
"""


async def describe_photo_series(images: list[tuple[bytes, str]]) -> str | None:
    """
    images: list of (image_bytes, media_type) — e.g. [(b"...", "image/jpeg"), ...]
    Returns one structured fitness report covering all photos in the series.
    """
    if not images:
        return None
    try:
        content: list[dict] = []
        for img, mt in images[:5]:  # cap at 5 photos to stay within token budget
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mt,
                           "data": base64.b64encode(img).decode("ascii")},
            })
        content.append({
            "type": "text",
            "text": f"Это серия из {len(images)} фото одной сессии. "
                    "Дай единый структурный фитнес-отчёт."
        })
        resp = await _anthropic.messages.create(
            model="claude-opus-4-7",
            max_tokens=900,
            system=_FITNESS_VISION_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.error("describe_photo_series error: %s", exc)
        return None
