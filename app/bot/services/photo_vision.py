"""
Fitness vision skill — TWO outputs per call:

  SHORT (≤2 предложения): для чата. Сухо, по делу. Без сравнений в самом
        тексте (т.к. модель не видит предыдущих фото).

  DETAILED: для отчёта / контекста сравнения. Структурный анализ с
        конкретными признаками: пропорции, толщина в разрезах, рельеф,
        тонус, поза. Это то, что увидит следующая AI при сравнении.

API:
  describe_photo_series(images) → {"short": str, "detailed": str} | None
"""
from __future__ import annotations

import base64
import logging
import re

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


_FITNESS_VISION_SYSTEM = """\
Ты — фитнес-аналитик с опытом телесного скоринга и физиотерапии. На вход — \
1..5 фото тела одной сессии. На выход — РОВНО ДВЕ части в строгом формате:

[SHORT]
1-2 коротких предложения для чата пользователя. Только самое важное: общее \
впечатление, заметная зона. Без эмодзи, без перечислений по группам. Без \
оценочных слов "молодец"/"хорошо". Сухо, нейтрально, информативно.

[DETAILED]
Структурный отчёт для архива и сравнения с другими сессиями. Каждый блок \
с маркером, как ниже:

Ракурсы: <фронт / спина / лево / право; качество кадра, освещение>
Композиция: <визуальная оценка %жира диапазоном; распределение — где жироотложение \
больше всего; водо-/жиро-удержание>
Грудь: <объём, симметрия L/R, опущена/подтянута, видна ли разделение мышцы и подгрудная складка>
Плечи: <ширина, заполненность 3-х пучков, передние/средние/задние дельты>
Руки: <бицепс — пик/длина, трицепс — латеральная головка, предплечья>
Спина: <широчайшие — расширяют ли силуэт; трапеции верх; ромбовидные; поясница>
Живот: <округлый/плоский; видны ли мышцы пресса; косые; разделение на уровне пупка>
Талия и бёдра: <V-силуэт; разница ширины плечи vs талия; форма таза/ягодиц если видно>
Ноги: <квадрицепс, бицепс бедра, икры — если видны>
Постура: <положение плеч, головы, поясничный лордоз, наклон таза>
Симметрия: <L/R асимметрии, наклон корпуса>
Кожа и тонус: <тонус, наличие растяжек/складок видимого характера, гидратация на вид>

ПРАВИЛА:
- ТОЛЬКО факты с фото. Никаких советов и мотивации.
- Если ракурс не позволяет оценить блок — пиши "не видно".
- Каждый блок DETAILED — 1-3 предложения, конкретно, без воды.
- НЕ упоминай предполагаемый вес в килограммах.
- HTML-теги не используй.
- Структура SHORT/DETAILED обязательна — без неё парсер сломается.
"""


_SHORT_RE = re.compile(r"\[SHORT\]\s*(.*?)\s*\[DETAILED\]", re.DOTALL | re.IGNORECASE)
_DETAILED_RE = re.compile(r"\[DETAILED\]\s*(.*)$", re.DOTALL | re.IGNORECASE)


def _split_short_detailed(raw: str) -> dict:
    """Split the model's reply into {short, detailed}. Fallback to whole text
    as detailed if the markers are missing.
    """
    raw = (raw or "").strip()
    m_short = _SHORT_RE.search(raw)
    m_det = _DETAILED_RE.search(raw)
    short = m_short.group(1).strip() if m_short else ""
    detailed = m_det.group(1).strip() if m_det else (raw if not short else "")
    if not short and detailed:
        # Use first sentence as a fallback short
        first = re.split(r"(?<=[.!?])\s+", detailed, maxsplit=1)
        if first:
            short = first[0].strip()
    if not short:
        short = "Фото сохранено."
    if not detailed:
        detailed = raw
    return {"short": short, "detailed": detailed}


async def describe_photo_series(images: list[tuple[bytes, str]]) -> dict | None:
    """
    images: [(image_bytes, media_type), ...]
    Returns {"short": str, "detailed": str} or None on failure.
    """
    if not images:
        return None
    try:
        content: list[dict] = []
        for img, mt in images[:5]:  # cap at 5 photos for token budget
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mt,
                           "data": base64.b64encode(img).decode("ascii")},
            })
        content.append({
            "type": "text",
            "text": f"Это серия из {len(images)} фото одной сессии. "
                    "Дай SHORT и DETAILED ровно в указанном формате."
        })
        resp = await _anthropic.messages.create(
            model="claude-opus-4-8",
            max_tokens=1500,
            system=_FITNESS_VISION_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        text_out = resp.content[0].text.strip()
        return _split_short_detailed(text_out)
    except Exception as exc:
        log.error("describe_photo_series error: %s", exc, exc_info=True)
        return None
