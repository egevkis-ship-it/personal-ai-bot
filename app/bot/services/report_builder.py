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


def _register_fonts():
    """Try to register a Cyrillic-capable TTF; fall back to Helvetica."""
    global _FONT_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONT_REGISTERED:
        return
    candidates = [
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


def _resize_photo(img_bytes: bytes, max_dim: int = 600, quality: int = 70) -> bytes | None:
    """Shrink to max_dim on the long side, JPEG, return bytes. None on failure."""
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
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=f"Workout report {from_date} - {to_date}",
        author="StonedBot",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontName=_FONT_BOLD,
        fontSize=18, leading=22, spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName=_FONT_BOLD,
        fontSize=14, leading=18, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#1f2937"),
    )
    h3 = ParagraphStyle(
        "H3", parent=styles["Heading3"], fontName=_FONT_BOLD,
        fontSize=11, leading=14, spaceBefore=8, spaceAfter=2,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName=_FONT_NAME,
        fontSize=10, leading=13,
    )
    small = ParagraphStyle(
        "Small", parent=styles["BodyText"], fontName=_FONT_NAME,
        fontSize=8, leading=10, textColor=colors.grey,
    )
    note_style = ParagraphStyle(
        "Note", parent=body, fontName=_FONT_NAME, fontSize=9,
        leading=12, textColor=colors.HexColor("#4b5563"),
        leftIndent=18,
    )

    story = []
    story.append(Paragraph(f"Отчёт за период {from_date} — {to_date}", title_style))

    # ─── Summary ───────────────────────────────────────────────────────────
    total_tonnage = sum(_workout_tonnage(s) for _, s in workouts)
    total_sets = sum(len(s) for _, s in workouts)
    n_unique_dates = len({w["workout_date"] for w, _ in workouts})

    summary_data = [
        ["Тренировок", str(len(workouts))],
        ["Уникальных дней", str(n_unique_dates)],
        ["Подходов всего", str(total_sets)],
        ["Тоннаж, кг", f"{total_tonnage:,.0f}".replace(",", " ")],
        ["Замеров", str(len(measurements))],
        ["Фото", str(len(photos))],
    ]
    summary_table = Table(summary_data, colWidths=[5 * cm, 4 * cm])
    summary_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), _FONT_NAME, 10),
        ("FONT", (0, 0), (0, -1), _FONT_BOLD, 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#374151")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.2, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Paragraph("📊 Сводка", h2))
    story.append(summary_table)

    # ─── Workouts ──────────────────────────────────────────────────────────
    story.append(Paragraph("🏋️ Тренировки", h2))
    if not workouts:
        story.append(Paragraph("<i>Нет завершённых тренировок за период.</i>", body))
    else:
        for w, sets in workouts:
            focus = w.get("focus_label") or "тренировка"
            tonn = _workout_tonnage(sets)
            header = f"{_fmt_date(w['workout_date'])} — <b>{_escape(focus)}</b>  ·  {len(sets)} подх, {tonn:.0f} кг"
            story.append(Paragraph(header, h3))
            if w.get("notes"):
                story.append(Paragraph(f"💬 <i>{_escape(w['notes'])}</i>", note_style))

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
                        pieces.append(f"{int(dur)}с")
                    elif wkg is not None and rps is not None:
                        suffix = " 🔥" if s.get("is_failure") else ""
                        pref = "разм " if s.get("is_warmup") else ""
                        pieces.append(f"{pref}{float(wkg):g}×{int(rps)}{suffix}")
                    elif rps is not None:
                        pieces.append(str(int(rps)))
                    n = s.get("notes")
                    if n:
                        notes_collected.append(n)
                line = f"• <b>{_escape(ex)}</b>: {', '.join(pieces)}"
                story.append(Paragraph(line, body))
                for n in notes_collected:
                    story.append(Paragraph(f"💬 {_escape(n)}", note_style))
            story.append(Spacer(1, 4))

    # ─── Measurements ──────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("📏 Замеры", h2))
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
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
            ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tab)

        # Period delta
        if len(measurements) >= 2:
            first = measurements[0]; last = measurements[-1]
            deltas = []
            for f, label in _M_FIELDS:
                a, b = first.get(f), last.get(f)
                if a is not None and b is not None:
                    diff = float(b) - float(a)
                    if abs(diff) >= 0.05:
                        sign = "+" if diff > 0 else ""
                        deltas.append(f"{label}: <b>{sign}{diff:g}</b>")
            if deltas:
                story.append(Spacer(1, 6))
                story.append(Paragraph("Изменение за период: " + "  ·  ".join(deltas), body))

    # ─── Photos ────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("📸 Фото", h2))
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
                f"{_fmt_date(first['taken_on'])} — серия из {len(grp)} фото",
                h3,
            ))
            ai_desc = first.get("ai_description") or ""
            if ai_desc:
                # Strip Telegram-only HTML tags reportlab doesn't understand;
                # keep <b></b>/<i></i> only.
                clean = ai_desc
                story.append(Paragraph(_safe_html_for_pdf(clean), body))
            notes = [g.get("notes") for g in grp if g.get("notes")]
            for n in notes:
                story.append(Paragraph(f"💬 <i>{_escape(n)}</i>", note_style))

            # Thumbnails in a row (3 per row max)
            thumbs = []
            for p in grp:
                if p["id"] in photo_blobs:
                    try:
                        thumbs.append(Image(io.BytesIO(photo_blobs[p["id"]]),
                                            width=5 * cm, height=5 * cm,
                                            kind="proportional"))
                    except Exception as exc:
                        log.warning("embed image %s failed: %s", p["id"], exc)
            if thumbs:
                # 3 per row
                rows = []
                for i in range(0, len(thumbs), 3):
                    row = thumbs[i:i + 3]
                    while len(row) < 3:
                        row.append("")
                    rows.append(row)
                t = Table(rows, colWidths=[5.5 * cm] * 3)
                t.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(t)
            story.append(Spacer(1, 8))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"Сгенерировано {date.today().isoformat()} · StonedBot",
        small,
    ))

    doc.build(story)
    return buf.getvalue()


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
