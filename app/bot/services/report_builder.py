"""
Period report → PDF (reportlab).

build_period_report(bot, user_id, from_date, to_date) → bytes (PDF)

Sections:
  - Title + period summary stats
  - Workouts: per-day, exercises grouped, with comments
  - Measurements: table with deltas + period delta
  - Photos: each series with its fitness analysis + thumbnails

All data fetched from the local DB. Photos are downloaded once via Bot.get_file
and resized via Pillow before being embedded — keeps the PDF under ~10 MB for a
typical month with 4-5 photo sessions.

Bot DejaVuSans font is embedded so Cyrillic renders correctly without relying
on system fonts that may not exist in the container.
"""
from __future__ import annotations

import io
import logging
import os
from datetime import date

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import text

from app.db.engine import get_session

log = logging.getLogger(__name__)


# ─────────────────────────── font setup ───────────────────────────────────

_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"


# W3-7: DejaVuSans (full Cyrillic) is bundled in the repo so the PDF renders
# Cyrillic even in a minimal container that has no system fonts. This path is
# tried FIRST; the system paths remain as fallbacks.
_BUNDLED_FONT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "fonts"))


def _register_fonts():
    """Try to register a Cyrillic-capable TTF; fall back to Helvetica."""
    global _FONT_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONT_REGISTERED:
        return
    candidates = [
        (os.path.join(_BUNDLED_FONT_DIR, "DejaVuSans.ttf"),
         os.path.join(_BUNDLED_FONT_DIR, "DejaVuSans-Bold.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/Library/Fonts/Arial Unicode.ttf",
         "/Library/Fonts/Arial Unicode.ttf"),
    ]
    for reg, bold in candidates:
        try:
            pdfmetrics.registerFont(TTFont("Body", reg))
            pdfmetrics.registerFont(TTFont("BodyBold", bold))
            _FONT_NAME = "Body"
            _FONT_BOLD = "BodyBold"
            _FONT_REGISTERED = True
            log.info("PDF font: %s", reg)
            return
        except Exception:
            continue
    log.warning("PDF: no Cyrillic font found, falling back to Helvetica (Cyrillic may be empty)")
    _FONT_REGISTERED = True


# ─────────────────────────── DB fetchers ───────────────────────────────────

async def _fetch_workouts(uid: str, fd: date, td: date) -> list[tuple[dict, list[dict]]]:
    async with get_session() as s:
        wr = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid
                  AND workout_date BETWEEN :fd AND :td
                  AND finished_at IS NOT NULL
                ORDER BY workout_date ASC, started_at ASC
            """),
            {"uid": uid, "fd": fd, "td": td},
        )
        workouts = [dict(r) for r in wr.mappings().all()]
        result = []
        for w in workouts:
            sr = await s.execute(
                text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id ASC"),
                {"wid": w["id"]},
            )
            sets = [dict(r) for r in sr.mappings().all()]
            result.append((w, sets))
        return result


async def _fetch_measurements(uid: str, fd: date, td: date) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM body_measurements
                WHERE user_id = :uid AND taken_on BETWEEN :fd AND :td
                ORDER BY taken_on ASC, id ASC
            """),
            {"uid": uid, "fd": fd, "td": td},
        )
        return [dict(x) for x in r.mappings().all()]


async def _fetch_photos(uid: str, fd: date, td: date) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM progress_photos
                WHERE user_id = :uid AND taken_on BETWEEN :fd AND :td
                ORDER BY taken_on ASC, id ASC
            """),
            {"uid": uid, "fd": fd, "td": td},
        )
        return [dict(x) for x in r.mappings().all()]


# ─────────────────────────── helpers ───────────────────────────────────────

_DAYS_RU = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
_M_FIELDS = (
    ("weight_kg", "Вес, кг"),
    ("calf_cm",   "Голень"),
    ("thigh_cm",  "Бедро"),
    ("hips_cm",   "Бёдра"),
    ("belly_cm",  "Живот"),
    ("waist_cm",  "Талия"),
    ("chest_cm",  "Грудь"),
    ("arm_cm",    "Рука"),
    ("neck_cm",   "Шея"),
)


def _fmt_date(d) -> str:
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d
    return f"{d.day:02d}.{d.month:02d} {_DAYS_RU.get(d.weekday(), '')}"


def _workout_tonnage(sets: list[dict]) -> float:
    total = 0.0
    for s in sets:
        w = s.get("weight_kg")
        r = s.get("reps")
        if w is not None and r is not None:
            total += float(w) * float(r)
    return total


def _resize_photo(img_bytes: bytes, max_dim: int = 1280, quality: int = 82) -> bytes | None:
    """Shrink to max_dim on the long side, JPEG, return bytes. None on failure.

    1280px @ q82 keeps photos large enough that a downstream AI can re-analyze
    them from the PDF (rough estimate: 200-400 KB per shot).
    """
    try:
        img = PILImage.open(io.BytesIO(img_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        scale = max_dim / max(w, h)
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception as exc:
        log.warning("resize_photo failed: %s", exc)
        return None


def _fmt_duration(secs) -> str:
    if secs is None:
        return "—"
    s = int(secs)
    m, r = divmod(s, 60)
    if m == 0:
        return f"{r}с"
    if r == 0:
        return f"{m} мин"
    return f"{m}:{r:02d}"


# ─────────────────────────── main builder ───────────────────────────────────

async def build_period_report(bot, user_id: str, from_date: date, to_date: date) -> bytes:
    """Generate PDF report. Returns raw bytes."""
    _register_fonts()
    workouts = await _fetch_workouts(user_id, from_date, to_date)
    measurements = await _fetch_measurements(user_id, from_date, to_date)
    photos = await _fetch_photos(user_id, from_date, to_date)

    # Pre-download photos (resized)
    photo_blobs: dict[int, bytes] = {}
    for p in photos:
        try:
            file = await bot.get_file(p["telegram_file_id"])
            file_io = await bot.download_file(file.file_path)
            raw = file_io.read() if hasattr(file_io, "read") else bytes(file_io)
            resized = _resize_photo(raw, max_dim=600, quality=70)
            if resized:
                photo_blobs[p["id"]] = resized
        except Exception as exc:
            log.warning("download photo %s failed: %s", p["id"], exc)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm,
        topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        title=f"Workout report {from_date} - {to_date}",
        author="StonedBot",
    )

    INK = colors.HexColor("#111827")        # primary text
    INK_SOFT = colors.HexColor("#374151")   # secondary text
    INK_MUTE = colors.HexColor("#6b7280")   # tertiary
    BG_HEAD  = colors.HexColor("#f3f4f6")
    LINE     = colors.HexColor("#d1d5db")
    ACCENT   = colors.HexColor("#2563eb")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontName=_FONT_BOLD,
        fontSize=22, leading=26, spaceAfter=2, textColor=INK,
    )
    subtitle = ParagraphStyle(
        "Sub", parent=styles["BodyText"], fontName=_FONT_NAME,
        fontSize=10, leading=12, textColor=INK_MUTE, spaceAfter=14,
    )
    section = ParagraphStyle(
        "Section", parent=styles["Heading2"], fontName=_FONT_BOLD,
        fontSize=13, leading=16, spaceBefore=16, spaceAfter=8,
        textColor=ACCENT,
    )
    h3 = ParagraphStyle(
        "H3", parent=styles["Heading3"], fontName=_FONT_BOLD,
        fontSize=11, leading=14, spaceBefore=10, spaceAfter=3,
        textColor=INK,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName=_FONT_NAME,
        fontSize=10, leading=13, textColor=INK_SOFT,
    )
    small = ParagraphStyle(
        "Small", parent=styles["BodyText"], fontName=_FONT_NAME,
        fontSize=8, leading=10, textColor=INK_MUTE,
    )
    note_style = ParagraphStyle(
        "Note", parent=body, fontName=_FONT_NAME, fontSize=9,
        leading=12, textColor=INK_MUTE, leftIndent=14,
    )
    delta_pos = ParagraphStyle(
        "DPos", parent=body, fontName=_FONT_NAME, fontSize=9,
        leading=12, textColor=colors.HexColor("#047857"),
    )
    delta_neg = ParagraphStyle(
        "DNeg", parent=body, fontName=_FONT_NAME, fontSize=9,
        leading=12, textColor=colors.HexColor("#b91c1c"),
    )

    story = []
    # Header block
    story.append(Paragraph(f"Тренировочный отчёт", title_style))
    story.append(Paragraph(
        f"Период: <b>{_fmt_date(from_date)}</b> — <b>{_fmt_date(to_date)}</b>  "
        f"·  всего {(to_date - from_date).days + 1} дн.",
        subtitle,
    ))

    # ─── Summary ───────────────────────────────────────────────────────────
    total_tonnage = sum(_workout_tonnage(s) for _, s in workouts)
    total_sets = sum(len(s) for _, s in workouts)
    n_unique_dates = len({w["workout_date"] for w, _ in workouts})

    # Two columns, label-value pairs
    summary_pairs = [
        ("Тренировок", str(len(workouts))),
        ("Тренировочных дней", str(n_unique_dates)),
        ("Подходов всего", str(total_sets)),
        ("Тоннаж, кг", f"{total_tonnage:,.0f}".replace(",", " ")),
        ("Замеров", str(len(measurements))),
        ("Серий фото", str(len({(p.get('series_id') or p['id']) for p in photos}))),
    ]
    rows_2col = []
    for i in range(0, len(summary_pairs), 2):
        left = summary_pairs[i]
        right = summary_pairs[i + 1] if i + 1 < len(summary_pairs) else ("", "")
        rows_2col.append([left[0], left[1], right[0], right[1]])
    summary_table = Table(rows_2col, colWidths=[4.5 * cm, 3 * cm, 4.5 * cm, 3 * cm])
    summary_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), _FONT_NAME, 10),
        ("FONT", (0, 0), (0, -1), _FONT_BOLD, 10),
        ("FONT", (2, 0), (2, -1), _FONT_BOLD, 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK_SOFT),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("TEXTCOLOR", (3, 0), (3, -1), INK),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(Paragraph("Сводка", section))
    story.append(summary_table)

    # ─── Workouts ──────────────────────────────────────────────────────────
    story.append(Paragraph("Тренировки", section))
    if not workouts:
        story.append(Paragraph("<i>Нет завершённых тренировок за период.</i>", body))
    else:
        for w, sets in workouts:
            focus = w.get("focus_label") or "тренировка"
            tonn = _workout_tonnage(sets)
            header = (
                f"<font color='#2563eb'>{_fmt_date(w['workout_date'])}</font>"
                f"  ·  <b>{_escape(focus)}</b>"
                f"  ·  <font color='#6b7280'>{len(sets)} подх · {tonn:.0f} кг</font>"
            )
            story.append(Paragraph(header, h3))
            if w.get("notes"):
                story.append(Paragraph(f"<i>{_escape(w['notes'])}</i>", note_style))

            # Group sets by exercise
            seen: list[str] = []
            by_ex: dict[str, list[dict]] = {}
            for s in sets:
                ex = s.get("exercise_name") or "?"
                if ex not in by_ex:
                    by_ex[ex] = []
                    seen.append(ex)
                by_ex[ex].append(s)
            for ex in seen:
                ex_sets = by_ex[ex]
                pieces = []
                notes_collected = []
                for s in ex_sets:
                    wkg = s.get("weight_kg")
                    rps = s.get("reps")
                    dur = s.get("duration_seconds")
                    if dur:
                        pieces.append(_fmt_duration(dur))
                    elif wkg is not None and rps is not None:
                        suffix = "*" if s.get("is_failure") else ""
                        pref = "(р) " if s.get("is_warmup") else ""
                        pieces.append(f"{pref}{float(wkg):g}×{int(rps)}{suffix}")
                    elif rps is not None:
                        pieces.append(str(int(rps)))
                    n = s.get("notes")
                    if n:
                        notes_collected.append(n)
                line = f"<b>{_escape(ex)}</b>: <font color='#374151'>{', '.join(pieces)}</font>"
                story.append(Paragraph(line, body))
                for n in notes_collected:
                    story.append(Paragraph(f"— {_escape(n)}", note_style))
            story.append(Spacer(1, 4))
        story.append(Paragraph("<i>* — до отказа; (р) — разминка</i>", small))

    # ─── Measurements ──────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Замеры тела", section))
    if not measurements:
        story.append(Paragraph("<i>Нет замеров за период.</i>", body))
    else:
        header = ["Дата"] + [label for _, label in _M_FIELDS]
        rows = [header]
        for m in measurements:
            row = [_fmt_date(m["taken_on"])]
            for f, _ in _M_FIELDS:
                v = m.get(f)
                row.append(f"{float(v):g}" if v is not None else "—")
            rows.append(row)
        col_widths = [2.2 * cm] + [1.6 * cm] * len(_M_FIELDS)
        tab = Table(rows, colWidths=col_widths, repeatRows=1)
        tab.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), _FONT_NAME, 8),
            ("FONT", (0, 0), (-1, 0), _FONT_BOLD, 8),
            ("BACKGROUND", (0, 0), (-1, 0), BG_HEAD),
            ("TEXTCOLOR", (0, 0), (-1, 0), INK),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK_SOFT),
            ("GRID", (0, 0), (-1, -1), 0.2, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ]))
        story.append(tab)

        # Period delta — coloured per direction
        if len(measurements) >= 2:
            first = measurements[0]; last = measurements[-1]
            story.append(Spacer(1, 8))
            story.append(Paragraph("<b>Изменение за период</b>", body))
            delta_rows = []
            for f, label in _M_FIELDS:
                a, b = first.get(f), last.get(f)
                if a is None or b is None:
                    continue
                diff = float(b) - float(a)
                if abs(diff) < 0.05:
                    continue
                sign = "+" if diff > 0 else ""
                # weight: '-' is good (depends on goal). For body parts: + is muscle, - is fat.
                # We don't know goal — just paint by direction.
                delta_rows.append([label, f"{float(a):g}", f"{float(b):g}",
                                   f"{sign}{diff:g}"])
            if delta_rows:
                dt = Table([["Параметр", "Было", "Стало", "Δ"]] + delta_rows,
                           colWidths=[3.5 * cm, 2.2 * cm, 2.2 * cm, 2.5 * cm])
                dt.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, -1), _FONT_NAME, 9),
                    ("FONT", (0, 0), (-1, 0), _FONT_BOLD, 9),
                    ("BACKGROUND", (0, 0), (-1, 0), BG_HEAD),
                    ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                    ("GRID", (0, 0), (-1, -1), 0.2, LINE),
                    ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(dt)

    # ─── Photos ────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Фотографии и AI-анализ", section))
    if not photos:
        story.append(Paragraph("<i>Нет фото за период.</i>", body))
    else:
        # Group by series
        series: dict[str, list[dict]] = {}
        order: list[str] = []
        for p in photos:
            sid = p.get("series_id") or f"_solo_{p['id']}"
            if sid not in series:
                series[sid] = []
                order.append(sid)
            series[sid].append(p)

        for sid in order:
            grp = series[sid]
            first = grp[0]
            story.append(Paragraph(
                f"<font color='#2563eb'>{_fmt_date(first['taken_on'])}</font>"
                f"  ·  серия из {len(grp)} фото",
                h3,
            ))
            ai_desc = first.get("ai_description") or ""
            if ai_desc:
                clean = _safe_html_for_pdf(_strip_emoji_section_headers(ai_desc))
                story.append(Paragraph(clean, body))
            notes = [g.get("notes") for g in grp if g.get("notes")]
            for n in notes:
                story.append(Paragraph(f"— {_escape(n)}", note_style))

            # Thumbnails: 2 per row, larger (8 cm wide)
            thumbs = []
            for p in grp:
                if p["id"] in photo_blobs:
                    try:
                        thumbs.append(Image(io.BytesIO(photo_blobs[p["id"]]),
                                            width=8 * cm, height=8 * cm,
                                            kind="proportional"))
                    except Exception as exc:
                        log.warning("embed image %s failed: %s", p["id"], exc)
            if thumbs:
                rows = []
                for i in range(0, len(thumbs), 2):
                    row = thumbs[i:i + 2]
                    while len(row) < 2:
                        row.append("")
                    rows.append(row)
                t = Table(rows, colWidths=[8.5 * cm] * 2)
                t.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(t)
            story.append(Spacer(1, 10))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"Сгенерировано {date.today().isoformat()} · StonedBot",
        small,
    ))

    doc.build(story)
    return buf.getvalue()


# Map Telegram emoji section markers back to plain bold text so DejaVu doesn't
# render them as tofu boxes.
_EMOJI_HEADER_MAP = {
    "📐": "•",
    "💪": "•",
    "🔥": "•",
    "⚠️": "•",
    "⚖️": "•",
    "🧍": "•",
    "📊": "•",
    "📅": "",
    "📏": "•",
    "📸": "•",
    "🏋️": "•",
    "💬": "—",
    "✏️": "—",
    "🎤": "—",
    "🤖": "—",
    "🧠": "—",
}


def _strip_emoji_section_headers(s: str) -> str:
    """Replace the leading emoji used in our Vision/Coach prompts with a plain
    bullet so the PDF renders cleanly even on fonts without emoji glyphs.
    """
    out = s
    for emo, repl in _EMOJI_HEADER_MAP.items():
        out = out.replace(emo, repl)
    return out


# ─────────────────────────── HTML escape helpers ───────────────────────────

def _escape(s: str) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


_ALLOWED_TAGS = ("b", "/b", "i", "/i", "br/", "br")


def _safe_html_for_pdf(s: str) -> str:
    """Allow only <b>/<i>/<br/>; escape everything else."""
    if not s:
        return ""
    import re
    out = []
    last = 0
    for m in re.finditer(r"<([^>]+)>", s):
        out.append(_escape(s[last:m.start()]))
        tag = m.group(1).strip().lower()
        if tag in _ALLOWED_TAGS:
            out.append(f"<{tag}>")
        else:
            out.append(_escape(m.group(0)))
        last = m.end()
    out.append(_escape(s[last:]))
    return "".join(out)
