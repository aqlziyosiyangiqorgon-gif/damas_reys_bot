"""Build and deliver branded payment receipts to every linked parent."""

from __future__ import annotations

import html
import logging
import os
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import reportlab
from aiogram import Bot
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database.db import Database
from services.billing import LESSON_RATE
from services.telegram_safe import safe_send_document

logger = logging.getLogger(__name__)
NAVY = colors.HexColor("#052E4B")
GOLD = colors.HexColor("#F8B516")
PALE = colors.HexColor("#F3F7FA")
GREEN = colors.HexColor("#26734D")
LOGO_PATH = Path(__file__).resolve().parent.parent / "miniapp" / "assets" / "aql-ziyosi-logo.png"


def _fonts() -> tuple[str, str]:
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular, bold = fonts_dir / "Vera.ttf", fonts_dir / "VeraBd.ttf"
    if regular.exists() and bold.exists():
        if "AqlReceipt" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AqlReceipt", str(regular)))
        if "AqlReceiptBold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AqlReceiptBold", str(bold)))
        pdfmetrics.registerFontFamily(
            "AqlReceipt", normal="AqlReceipt", bold="AqlReceiptBold",
            italic="AqlReceipt", boldItalic="AqlReceiptBold",
        )
        return "AqlReceipt", "AqlReceiptBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _fonts()


def _printable(value: Any) -> str:
    text = str(value or "")
    replacements = str.maketrans({
        "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "`": "'", "´": "'",
        "–": "-", "—": "-", "‑": "-",
    })
    text = text.translate(replacements)
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


def _p(value: Any) -> str:
    return html.escape(_printable(value), quote=True) or "-"


def _money(value: Any) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " so'm"


def _date_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y  %H:%M")
    except (TypeError, ValueError):
        return _printable(value)


class PaymentStamp(Flowable):
    """A decorative center payment stamp, clearly marked as a receipt mark."""

    def __init__(self, receipt_number: str):
        super().__init__()
        self.receipt_number = receipt_number
        self.width = 4.3 * cm
        self.height = 2.35 * cm

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        canvas.translate(self.width / 2, self.height / 2)
        canvas.rotate(-7)
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1.8)
        canvas.ellipse(-2.05 * cm, -0.9 * cm, 2.05 * cm, 0.9 * cm, stroke=1, fill=0)
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.7)
        canvas.ellipse(-1.9 * cm, -0.75 * cm, 1.9 * cm, 0.75 * cm, stroke=1, fill=0)
        canvas.setFillColor(NAVY)
        canvas.setFont(FONT_BOLD, 7.1)
        canvas.drawCentredString(0, 0.28 * cm, "AQL ZIYOSI")
        canvas.setFillColor(GREEN)
        canvas.setFont(FONT_BOLD, 6.2)
        canvas.drawCentredString(0, -0.05 * cm, "TO'LOV QABUL QILINDI")
        canvas.setFillColor(NAVY)
        canvas.setFont(FONT, 5.5)
        canvas.drawCentredString(0, -0.38 * cm, self.receipt_number)
        canvas.restoreState()


def build_payment_receipt_pdf(data: dict[str, Any]) -> bytes:
    """Return a compact A5 PDF receipt for one recorded payment."""
    from io import BytesIO

    output = BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A5, rightMargin=1.35 * cm, leftMargin=1.35 * cm,
        topMargin=1.0 * cm, bottomMargin=1.0 * cm,
        title=f"AQL ZIYOSI payment receipt {data['payment_id']}",
        author="AQL ZIYOSI o'quv markazi",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AZReceiptTitle", parent=base["Title"], fontName=FONT_BOLD, fontSize=15,
        leading=19, textColor=NAVY, alignment=TA_CENTER, spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "AZReceiptSubtitle", parent=base["Normal"], fontName=FONT, fontSize=8,
        leading=11, textColor=colors.HexColor("#627789"), alignment=TA_CENTER,
    )
    label_style = ParagraphStyle(
        "AZReceiptLabel", parent=base["Normal"], fontName=FONT, fontSize=8.3,
        leading=11, textColor=colors.HexColor("#627789"),
    )
    value_style = ParagraphStyle(
        "AZReceiptValue", parent=label_style, fontName=FONT_BOLD, textColor=NAVY,
    )
    story: list[Any] = []

    if LOGO_PATH.is_file():
        logo = Image(str(LOGO_PATH), width=6.0 * cm, height=1.45 * cm, kind="proportional", hAlign="CENTER")
        story.extend([logo, Spacer(1, 5)])
    story.extend([
        Paragraph("TO'LOV CHEKI", title_style),
        Paragraph("AQL ZIYOSI o'quv markazi", subtitle_style),
        Spacer(1, 9),
    ])

    receipt_number = f"AZ-{int(data['payment_id']):08d}"
    method = {"cash": "Naqd pul", "card": "Karta orqali"}.get(str(data.get("method")), _printable(data.get("method")))
    info_rows = [
        [Paragraph("Chek raqami", label_style), Paragraph(_p(receipt_number), value_style)],
        [Paragraph("Qabul qilingan sana", label_style), Paragraph(_p(_date_time(data.get("paid_at", ""))), value_style)],
        [Paragraph("O'quvchi", label_style), Paragraph(_p(data.get("student_name")), value_style)],
        [Paragraph("Mahalla", label_style), Paragraph(_p(data.get("village_name") or "Ko'rsatilmagan"), value_style)],
        [Paragraph("To'lov davri", label_style), Paragraph(_p(data.get("period")), value_style)],
        [Paragraph("To'lov usuli", label_style), Paragraph(_p(method), value_style)],
    ]
    info = Table(info_rows, colWidths=[4.25 * cm, 8.0 * cm], hAlign="LEFT")
    info.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#DDE6EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([info, Spacer(1, 9)])

    breakdown = [
        [Paragraph("Oylik hisob", label_style), Paragraph(_p(_money(data.get("invoice_amount"))), value_style)],
    ]
    if int(data.get("discount") or 0) > 0:
        breakdown.append([Paragraph("Chegirma", label_style), Paragraph(_p("- " + _money(data["discount"])), value_style)])
    if str(data.get("billing_mode")) == "attendance":
        lesson_line = f"Reja bo'yicha darslar: {int(data.get('lesson_count') or 0)} ta x {_money(LESSON_RATE)}"
        breakdown.append([Paragraph("Hisoblash", label_style), Paragraph(_p(lesson_line), value_style)])
    breakdown.extend([
        [Paragraph("Oldingi to'lovlar", label_style), Paragraph(_p(_money(data.get("paid_before"))), value_style)],
        [Paragraph("Ushbu chek summasi", value_style), Paragraph(_p(_money(data.get("payment_amount"))), value_style)],
        [Paragraph("Jami to'langan", label_style), Paragraph(_p(_money(data.get("paid_total"))), value_style)],
        [Paragraph("Qolgan qarz", label_style), Paragraph(_p(_money(data.get("remaining_due"))), value_style)],
    ])
    amounts = Table(breakdown, colWidths=[7.0 * cm, 5.25 * cm], hAlign="LEFT")
    amounts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BACKGROUND", (0, -3), (-1, -3), colors.HexColor("#FFF7E3")),
        ("BACKGROUND", (0, -1), (-1, -1), PALE),
        ("LINEABOVE", (0, -3), (-1, -3), 1, GOLD),
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#E5EBEF")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([amounts, Spacer(1, 7)])

    stamp_and_note = Table([[
        PaymentStamp(receipt_number),
        Paragraph(
            "To'lov AQL ZIYOSI o'quv markazi hisobiga qabul qilindi. "
            "Ushbu elektron chek to'lov qaydini tasdiqlaydi.",
            ParagraphStyle("AZReceiptNote", parent=label_style, fontSize=7.2, leading=10),
        ),
    ]], colWidths=[4.3 * cm, 7.95 * cm], hAlign="LEFT")
    stamp_and_note.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.extend([stamp_and_note, Spacer(1, 5)])
    story.append(Paragraph(
        "Savollar bo'lsa, AQL ZIYOSI markaziga murojaat qiling.",
        ParagraphStyle("AZReceiptFooter", parent=subtitle_style, fontSize=7, leading=9),
    ))

    doc.build(story)
    return output.getvalue()


async def send_payment_receipt(db: Database, bot: Bot | None, payment_id: int) -> str:
    """Send a payment receipt to all Telegram parents linked to the student."""
    try:
        details = await db.get_payment_receipt_details(payment_id)
        if not details:
            logger.error("Payment receipt data missing (payment_id=%s)", payment_id)
            return "failed"
        parents = [
            parent for parent in await db.get_student_parent_telegram_ids(details["student_id"])
            if parent.get("telegram_id")
        ]
        if not parents:
            return "no_parent"
        if bot is None:
            return "unavailable"

        pdf_data = build_payment_receipt_pdf(details)
        receipt_number = f"AZ-{int(details['payment_id']):08d}"
        caption = (
            f"To'lov cheki {receipt_number}\n"
            f"O'quvchi: {_printable(details['student_name'])}\n"
            f"To'langan: {_money(details['payment_amount'])}"
        )
        sent_count = 0
        with tempfile.TemporaryDirectory(prefix="aql-payment-receipt-") as temp_dir:
            path = Path(temp_dir) / f"{receipt_number}.pdf"
            path.write_bytes(pdf_data)
            for parent in parents:
                status = await safe_send_document(bot, parent["telegram_id"], str(path), caption)
                if status == "sent":
                    sent_count += 1
                try:
                    await db.log_notification(
                        parent_id=parent["parent_id"], student_id=details["student_id"],
                        text=f"To'lov cheki {receipt_number}", status=status,
                    )
                except Exception as exc:
                    logger.warning("Payment receipt delivery log failed (%s)", type(exc).__name__)
        if sent_count == len(parents):
            return "sent"
        if sent_count:
            return "partial"
        return "failed"
    except Exception as exc:
        logger.error("Payment receipt delivery failed (%s)", type(exc).__name__)
        return "failed"
