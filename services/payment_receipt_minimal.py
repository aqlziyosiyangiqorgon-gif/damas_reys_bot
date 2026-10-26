from __future__ import annotations

import html
import logging
import tempfile
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import Bot
from reportlab import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database.db import Database
from services.telegram_safe import safe_send_document

logger = logging.getLogger(__name__)

NAVY = colors.HexColor("#052E4B")
MUTED = colors.HexColor("#657687")
GREEN = colors.HexColor("#179B55")
MINT = colors.HexColor("#EFFAF3")
LINE = colors.HexColor("#D7E0E7")
LOGO_PATH = Path(__file__).resolve().parent.parent / "miniapp" / "assets" / "aql-ziyosi-logo.png"


def _fonts() -> tuple[str, str]:
    import reportlab
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular = fonts_dir / "Vera.ttf"
    bold = fonts_dir / "VeraBd.ttf"
    if regular.exists() and bold.exists():
        if "AqlReceipt" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AqlReceipt", str(regular)))
        if "AqlReceiptBold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AqlReceiptBold", str(bold)))
        return "AqlReceipt", "AqlReceiptBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _fonts()


def _clean(value: Any) -> str:
    text = str(value or "")
    text = text.translate(str.maketrans({"ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "–": "-", "—": "-", "‑": "-"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii") or "-"


def _p(value: Any) -> str:
    return html.escape(_clean(value), quote=True)


def _money(value: Any) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " so'm"


def _date_time(value: Any) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y  %H:%M")
    except (TypeError, ValueError):
        return _clean(value)


def _scallop(canvas: Canvas, y: float, width: float, radius: float = 0.11 * cm) -> None:
    canvas.saveState()
    canvas.setFillColor(colors.white)
    x = 0.45 * cm
    step = radius * 2
    count = int(width / step) + 1
    path = canvas.beginPath()
    path.moveTo(x, y)
    for i in range(count):
        cx = x + i * step + radius
        path.curveTo(cx - radius, y, cx - radius, y - radius * 1.35, cx, y - radius * 1.35)
        path.curveTo(cx + radius, y - radius * 1.35, cx + radius, y, cx + radius, y)
    path.lineTo(x + width, y + 0.2 * cm)
    path.lineTo(x, y + 0.2 * cm)
    path.close()
    canvas.drawPath(path, stroke=0, fill=1)
    canvas.restoreState()


def build_payment_receipt_pdf(data: dict[str, Any]) -> bytes:
    output = BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A5, leftMargin=1.35 * cm, rightMargin=1.35 * cm,
        topMargin=0.8 * cm, bottomMargin=1.0 * cm,
        title=f"AQL ZIYOSI payment receipt {data['payment_id']}", author="AQL ZIYOSI",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=18, leading=21, textColor=NAVY, alignment=TA_CENTER)
    subtitle = ParagraphStyle("subtitle", parent=styles["Normal"], fontName=FONT, fontSize=8.5, leading=11, textColor=MUTED, alignment=TA_CENTER, tracking=2)
    label = ParagraphStyle("label", parent=styles["Normal"], fontName=FONT, fontSize=9.2, leading=12, textColor=MUTED)
    value = ParagraphStyle("value", parent=label, fontName=FONT_BOLD, textColor=NAVY, alignment=TA_CENTER)
    amount_label = ParagraphStyle("amount_label", parent=label, fontSize=9.5, tracking=1.4)
    amount = ParagraphStyle("amount", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=25, leading=29, textColor=NAVY, alignment=TA_CENTER)
    success = ParagraphStyle("success", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=11, leading=14, textColor=GREEN, alignment=TA_CENTER)
    footer = ParagraphStyle("footer", parent=styles["Normal"], fontName=FONT, fontSize=7.2, leading=9, textColor=MUTED, alignment=TA_CENTER, tracking=1.2)

    story: list[Any] = []
    if LOGO_PATH.is_file():
        # Use the repository's original logo file and calculate height from its real dimensions.
        source_w, source_h = ImageReader(str(LOGO_PATH)).getSize()
        logo_w = 6.2 * cm
        logo_h = logo_w * source_h / source_w
        story.extend([Image(str(LOGO_PATH), width=logo_w, height=logo_h, hAlign="CENTER"), Spacer(1, 8)])

    story.extend([Paragraph("TO'LOV CHEKI", title), Paragraph("ELEKTRON CHEK", subtitle), Spacer(1, 9)])

    receipt_number = f"AZ-{int(data['payment_id']):08d}"
    rows = [
        [Paragraph("Chek raqami:", label), Paragraph(_p(receipt_number), value)],
        [Paragraph("Sana va vaqt:", label), Paragraph(_p(_date_time(data.get("paid_at"))), value)],
        [Paragraph("O'quvchi:", label), Paragraph(_p(data.get("student_name")), value)],
        [Paragraph("Davr:", label), Paragraph(_p(data.get("period")), value)],
    ]
    info = Table(rows, colWidths=[5.0 * cm, 7.25 * cm], hAlign="LEFT")
    info.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.extend([info, Spacer(1, 9)])

    story.append(Paragraph("TO'LOV SUMMASI", amount_label))
    story.append(Spacer(1, 1))
    story.append(Paragraph(_p(_money(data.get("payment_amount"))), amount))
    story.append(Spacer(1, 9))

    method = {"cash": "Naqd pul", "card": "Karta orqali"}.get(str(data.get("method")), _clean(data.get("method")))
    payment_rows = [
        [Paragraph("To'lov usuli:", label), Paragraph(_p(method), value)],
        [Paragraph("Qolgan qarz:", label), Paragraph(_p(_money(data.get("remaining_due"))), value)],
    ]
    payment_info = Table(payment_rows, colWidths=[5.0 * cm, 7.25 * cm], hAlign="LEFT")
    payment_info.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.45, LINE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.extend([payment_info, Spacer(1, 9)])

    status = Table([[Paragraph("✓   TO'LOV QABUL QILINDI", success)]], colWidths=[12.25 * cm], hAlign="LEFT")
    status.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), MINT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([status, Spacer(1, 10), Paragraph("AQL ZIYOSI O'QUV MARKAZI", footer)])

    def draw_page(canvas: Canvas, document: Any) -> None:
        page_w, page_h = A5
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, page_w, page_h, stroke=0, fill=1)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.45)
        canvas.line(1.35 * cm, page_h - 0.35 * cm, page_w - 1.35 * cm, page_h - 0.35 * cm)
        canvas.line(1.35 * cm, 0.42 * cm, page_w - 1.35 * cm, 0.42 * cm)
        _scallop(canvas, 0.36 * cm, page_w - 0.9 * cm)
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


async def send_payment_receipt(db: Database, bot: Bot | None, payment_id: int) -> str:
    try:
        details = await db.get_payment_receipt_details(payment_id)
        if not details:
            logger.error("Payment receipt data missing (payment_id=%s)", payment_id)
            return "failed"
        parents = [p for p in await db.get_student_parent_telegram_ids(details["student_id"]) if p.get("telegram_id")]
        if not parents:
            return "no_parent"
        if bot is None:
            return "unavailable"
        pdf_data = build_payment_receipt_pdf(details)
        receipt_number = f"AZ-{int(details['payment_id']):08d}"
        caption = f"To'lov cheki {receipt_number}\nO'quvchi: {_clean(details['student_name'])}\nTo'langan: {_money(details['payment_amount'])}"
        sent_count = 0
        with tempfile.TemporaryDirectory(prefix="aql-payment-receipt-") as temp_dir:
            path = Path(temp_dir) / f"{receipt_number}.pdf"
            path.write_bytes(pdf_data)
            for parent in parents:
                status = await safe_send_document(bot, parent["telegram_id"], str(path), caption)
                if status == "sent":
                    sent_count += 1
                try:
                    await db.log_notification(parent_id=parent["parent_id"], student_id=details["student_id"], text=f"To'lov cheki {receipt_number}", status=status)
                except Exception as exc:
                    logger.warning("Payment receipt delivery log failed (%s)", type(exc).__name__)
        if sent_count == len(parents):
            return "sent"
        if sent_count:
            return "partial"
        return "failed"
    except Exception as exc:
        logger.exception("Payment receipt generation/delivery failed: %s", type(exc).__name__)
        return "failed"
