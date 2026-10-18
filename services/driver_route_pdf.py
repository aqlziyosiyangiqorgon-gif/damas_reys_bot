"""Create a compact, branded PDF with a driver's daily route and pickup times."""

from __future__ import annotations

import html
import io
import logging
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config import settings

logger = logging.getLogger(__name__)
NAVY = colors.HexColor("#052E4B")
GOLD = colors.HexColor("#F8B516")
PALE = colors.HexColor("#F2F6F9")
MUTED = colors.HexColor("#627789")
LOGO_PATH = Path(__file__).resolve().parent.parent / "miniapp" / "assets" / "aql-ziyosi-logo.png"


def _register_fonts() -> tuple[str, str]:
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular_path = fonts_dir / "Vera.ttf"
    bold_path = fonts_dir / "VeraBd.ttf"
    if regular_path.exists() and bold_path.exists():
        for name, path in (("AqlVera", regular_path), ("AqlVeraBold", bold_path)):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
        pdfmetrics.registerFontFamily(
            "AqlVera", normal="AqlVera", bold="AqlVeraBold",
            italic="AqlVera", boldItalic="AqlVeraBold",
        )
        return "AqlVera", "AqlVeraBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()


def _latin_text(value: Any) -> str:
    """Keep reportlab's embedded Vera font readable for Uzbek/Russian names."""
    text = str(value or "")
    punctuation = str.maketrans({
        "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "`": "'", "´": "'",
        "–": "-", "—": "-", "‑": "-", "•": "|", "…": "...",
    })
    text = text.translate(punctuation)
    cyrillic = {
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo",
        "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
        "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
        "Ф": "F", "Х": "X", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
        "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
        "Ў": "O'", "Қ": "Q", "Ғ": "G'", "Ҳ": "H",
    }
    cyrillic.update({key.lower(): value.lower() for key, value in list(cyrillic.items())})
    text = "".join(cyrillic.get(char, char) for char in text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _safe(value: Any) -> str:
    return html.escape(_latin_text(value), quote=True) or "-"


def _minutes(value: Any) -> int | None:
    try:
        parsed = datetime.strptime(str(value), "%H:%M")
        return parsed.hour * 60 + parsed.minute
    except (TypeError, ValueError):
        return None


def _clock(minutes: int | None) -> str:
    if minutes is None:
        return "--:--"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _pickup_times(trip: dict[str, Any]) -> dict[int, str]:
    """Estimate each morning pickup from route order and village-to-center times."""
    current = _minutes(trip.get("planned_time"))
    if current is None:
        return {}
    items = [item for item in trip.get("items", []) if item.get("status") != "absent"]
    stops: list[list[dict[str, Any]]] = []
    for item in items:
        if stops and stops[-1][0].get("village_id") == item.get("village_id"):
            stops[-1].append(item)
        else:
            stops.append([item])

    result: dict[int, str] = {}
    previous_travel: int | None = None
    dwell = max(0, int(settings.STOP_DWELL_MIN))
    for index, stop in enumerate(stops):
        travel = max(0, int(stop[0].get("travel_minutes") or 0))
        if index and previous_travel is not None:
            # Route order runs from farther villages toward the center. The
            # difference in each village's travel-to-center estimate provides
            # the leg time between consecutive stops.
            current += dwell + max(0, previous_travel - travel)
        pickup = _clock(current)
        for item in stop:
            result[int(item["id"])] = pickup
        previous_travel = travel
    return result


def _status(value: str) -> str:
    return {
        "pending": "Kutilmoqda", "picked_up": "Olindi", "arrived": "Yetib keldi",
        "accepted": "Qabul qilindi", "handed_over": "Topshirildi", "dropped_off": "Yetkazildi",
        "absent": "Kelmaydi",
    }.get(value, _latin_text(value).replace("_", " ").title())


def build_driver_daily_pdf(trips: list[dict[str, Any]], driver_name: str, date_str: str) -> bytes:
    """Render all of a driver's trips as one chronological, time-grouped PDF."""
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=1.45 * cm, leftMargin=1.45 * cm,
        topMargin=2.45 * cm, bottomMargin=1.8 * cm,
        title=f"AQL ZIYOSI - {date_str} reyslar",
        author="AQL ZIYOSI transport xizmati",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AZTitle", parent=base["Title"], fontName=FONT_BOLD, fontSize=19,
        leading=23, textColor=NAVY, alignment=TA_LEFT, spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "AZSubtitle", parent=base["Normal"], fontName=FONT, fontSize=9,
        leading=13, textColor=MUTED, spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "AZSection", parent=base["Heading2"], fontName=FONT_BOLD, fontSize=11,
        leading=15, textColor=NAVY, spaceBefore=6, spaceAfter=6,
    )
    cell_style = ParagraphStyle(
        "AZCell", parent=base["Normal"], fontName=FONT, fontSize=8.4,
        leading=11, textColor=NAVY,
    )
    small_style = ParagraphStyle(
        "AZSmall", parent=base["Normal"], fontName=FONT, fontSize=7.5,
        leading=9, textColor=MUTED,
    )
    header_style = ParagraphStyle(
        "AZTableHeader", parent=cell_style, fontName=FONT_BOLD,
        textColor=colors.white,
    )
    story: list[Any] = [
        Paragraph("Bugungi reyslar", title_style),
        Paragraph(f"{_safe(driver_name)} | Sana: {_safe(date_str)}", subtitle_style),
    ]

    totals = Table(
        [[Paragraph(f"<b>{len(trips)}</b><br/>REYS", cell_style),
          Paragraph(f"<b>{sum(len(t.get('items', [])) for t in trips)}</b><br/>O'QUVCHI", cell_style),
          Paragraph("VAQT BO'YICHA TARTIBLANGAN", cell_style)]],
        colWidths=[3.0 * cm, 3.0 * cm, 10.9 * cm],
    )
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DDE6EC")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([totals, Spacer(1, 12)])

    for trip_number, trip in enumerate(sorted(trips, key=lambda row: (row.get("planned_time", ""), row.get("id", 0))), start=1):
        center_trip = trip.get("direction") == "to_center"
        direction = "MARKAZGA KELISH" if center_trip else "UYGA QAYTISH"
        status = _status(str(trip.get("status", "")))
        heading = Table([[
            Paragraph(f"{_safe(trip.get('planned_time'))} | {direction}", section_style),
            Paragraph(f"Reys #{trip.get('id')} · {_safe(status)}", ParagraphStyle(
                f"AZTripStatus{trip_number}", parent=small_style, alignment=TA_RIGHT, textColor=NAVY,
            )),
        ]], colWidths=[9.2 * cm, 7.7 * cm])
        heading.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8E8")),
            ("LINEBEFORE", (0, 0), (0, 0), 3, GOLD),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        pickup_times = _pickup_times(trip) if center_trip else {}
        items = list(trip.get("items", []))
        if center_trip:
            items.sort(key=lambda item: (pickup_times.get(int(item.get("id", 0)), "99:99"), item.get("route_order", 0), item.get("full_name", "")))
            time_header = "Olish vaqti"
        else:
            items.sort(key=lambda item: (item.get("lesson_end_time") or "99:99", item.get("route_order", 0), item.get("full_name", "")))
            time_header = "Dars tugashi"

        rows: list[list[Any]] = [[
            Paragraph("#", header_style), Paragraph(time_header, header_style),
            Paragraph("O'quvchi", header_style), Paragraph("Mahalla / olish joyi", header_style),
            Paragraph("Holat", header_style),
        ]]
        for index, item in enumerate(items, start=1):
            item_time = pickup_times.get(int(item.get("id", 0)), "--:--") if center_trip else (item.get("lesson_end_time") or trip.get("planned_time") or "--:--")
            pickup = " / ".join(part for part in (_latin_text(item.get("village_name", "")), _latin_text(item.get("pickup_point", ""))) if part)
            rows.append([
                Paragraph(str(index), cell_style),
                Paragraph(_safe(item_time), cell_style),
                Paragraph(f"<b>{_safe(item.get('full_name', 'Oquvchi'))}</b>", cell_style),
                Paragraph(_safe(pickup), cell_style),
                Paragraph(_safe(_status(str(item.get("status", "")))), cell_style),
            ])
        table = Table(rows, colWidths=[0.65 * cm, 2.2 * cm, 4.55 * cm, 6.0 * cm, 3.5 * cm], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, GOLD), ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor("#DFE7EC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        trip_story: list[Any] = [heading, table]
        if center_trip:
            trip_story.append(Paragraph("Olish vaqti reysning yo'l va to'xtash rejasidan hisoblangan.", small_style))
        else:
            trip_story.append(Paragraph(f"Markazdan reys boshlanishi: {_safe(trip.get('planned_time'))}.", small_style))
        trip_story.append(Spacer(1, 12))
        story.append(KeepTogether(trip_story))

    if not trips:
        story.append(Paragraph("Bugun sizga biriktirilgan reys yo'q.", cell_style))

    def draw_page(canvas, document) -> None:
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(NAVY)
        canvas.rect(0, page_height - 0.55 * cm, page_width, 0.55 * cm, fill=1, stroke=0)
        if LOGO_PATH.is_file():
            canvas.drawImage(str(LOGO_PATH), 1.45 * cm, page_height - 1.85 * cm,
                             width=4.7 * cm, height=1.13 * cm, preserveAspectRatio=True, anchor="sw", mask="auto")
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7.5)
        canvas.drawRightString(page_width - 1.45 * cm, page_height - 1.35 * cm, "AQL ZIYOSI | TRANSPORT XIZMATI")
        canvas.setStrokeColor(colors.HexColor("#DDE6EC"))
        canvas.line(1.45 * cm, 1.25 * cm, page_width - 1.45 * cm, 1.25 * cm)
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(1.45 * cm, 0.85 * cm, "Ichki foydalanish uchun")
        canvas.drawRightString(page_width - 1.45 * cm, 0.85 * cm, f"Sahifa {document.page}")
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
        return output.getvalue()
    except Exception as exc:
        logger.error("Driver route PDF build failed (%s)", type(exc).__name__)
        raise
