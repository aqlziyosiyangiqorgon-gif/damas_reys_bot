from typing import Optional

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database.db import Database
from models.callbacks import (
    BillingListCallback,
    BillingPageCallback,
    BillingPeriodCallback,
    BillingStudentCallback,
    BillingPaymentStartCallback,
    BillingPaymentMethodCallback,
    BillingDiscountStartCallback,
)
from services.payment_receipt import send_payment_receipt
from services.telegram_safe import safe_send
from utils.clock import now_tz
from utils.telegram import safe_telegram_call
from utils.text import escape_html

payment_router = Router()
payment_router.message.filter(F.from_user.id.in_(settings.admin_ids_list))
payment_router.callback_query.filter(F.from_user.id.in_(settings.admin_ids_list))

PAGE_SIZE = 8


class PaymentCenterStates(StatesGroup):
    waiting_for_payment_amount = State()
    waiting_for_discount_amount = State()
    waiting_for_discount_reason = State()


def _money(value: int | None) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " so'm"


def _shift_period(period: str, move: str) -> str:
    year, month = (int(part) for part in period.split("-", 1))
    month += -1 if move == "p" else 1
    if month == 0:
        year -= 1
        month = 12
    elif month == 13:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}"


def _status(student: dict) -> tuple[str, str]:
    if student.get("invoice_id") is None:
        if student.get("days_per_week") and not student.get("monthly_amount"):
            return "⚠️", "Tarif kerak"
        if not student.get("days_per_week"):
            return "⚪", "Jadval yo'q"
        return "⚪", "Hisob ochilmagan"
    if student.get("status") == "waived":
        return "⚪", "Bekor qilingan"
    due = int(student.get("due") or 0)
    if due <= 0:
        return "🟢", "To'langan"
    if int(student.get("paid_total") or 0) > 0:
        return "🟡", f"Qisman · {_money(due)} qarz"
    return "🔴", f"Qarz · {_money(due)}"


def _dashboard_keyboard(period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️", callback_data=BillingPeriodCallback(period=period, move="p").pack()),
            InlineKeyboardButton(text=f"📅 {period}", callback_data="noop"),
            InlineKeyboardButton(text="▶️", callback_data=BillingPeriodCallback(period=period, move="n").pack()),
        ],
        [InlineKeyboardButton(text="👥 O'quvchilar bo'yicha", callback_data=BillingListCallback(period=period).pack())],
    ])


def _student_list_keyboard(students: list, period: str, page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(students) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    rows = []
    start = page * PAGE_SIZE
    for student in students[start:start + PAGE_SIZE]:
        icon, label = _status(student)
        rows.append([InlineKeyboardButton(
            text=f"{icon} {student['full_name']} · {label}",
            callback_data=BillingStudentCallback(student_id=student["id"], period=period).pack(),
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=BillingPageCallback(page=page - 1, period=period).pack()))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=BillingPageCallback(page=page + 1, period=period).pack()))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ To'lov markazi", callback_data=BillingListCallback(period=period).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _student_keyboard(student_id: int, period: str, due: int) -> InlineKeyboardMarkup:
    rows = []
    if due > 0:
        rows.append([InlineKeyboardButton(
            text="💳 To'lov qabul qilish",
            callback_data=BillingPaymentStartCallback(student_id=student_id, period=period).pack(),
        )])
        rows.append([InlineKeyboardButton(
            text="🎁 Chegirma berish",
            callback_data=BillingDiscountStartCallback(student_id=student_id, period=period).pack(),
        )])
    rows.append([InlineKeyboardButton(text="⬅️ O'quvchilar", callback_data=BillingListCallback(period=period).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_dashboard(message: types.Message, db: Database, period: str, edit: bool = False):
    await db.ensure_monthly_billing(period)
    students = await db.get_billing_students(period)
    entered = [s for s in students if s.get("invoice_id") is not None]
    paid = sum(int(s.get("paid_total") or 0) for s in entered)
    due = sum(max(0, int(s.get("due") or 0)) for s in entered)
    debtors = sum(1 for s in entered if s.get("status") != "waived" and int(s.get("due") or 0) > 0)
    paid_students = sum(1 for s in entered if s.get("status") != "waived" and int(s.get("due") or 0) <= 0)
    not_ready = len(students) - len(entered)
    collection = (paid / (paid + due) * 100) if paid + due else 0

    text = (
        f"💳 <b>TO'LOV MARKAZI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>{escape_html(period)}</b>\n\n"
        f"💰 <b>{_money(paid)}</b>\n"
        f"<i>Jami tushum</i>\n\n"
        f"⏳ <b>{_money(due)}</b>\n"
        f"<i>Qolgan qarzdorlik</i>\n\n"
        f"👥 <b>{len(students)}</b>  o'quvchi   ·   🧾 <b>{len(entered)}</b>  hisob\n"
        f"🟢 <b>{paid_students}</b> to'lagan   ·   🔴 <b>{debtors}</b> qarzdor\n"
        f"⚠️ <b>{not_ready}</b> hisob tayyor emas\n\n"
        f"📊 Undirish darajasi: <b>{collection:.0f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Quyidan o'quvchilarni tanlab, to'lov yoki chegirmani boshqaring."
    )
    keyboard = _dashboard_keyboard(period)
    if edit:
        await safe_telegram_call(message.edit_text, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_telegram_call(message.answer, text, reply_markup=keyboard, parse_mode="HTML")


async def _show_students(message: types.Message, db: Database, period: str, page: int = 0):
    await db.ensure_monthly_billing(period)
    students = await db.get_billing_students(period)
    students.sort(key=lambda s: (-max(0, int(s.get("due") or 0)), str(s["full_name"]).casefold()))
    text = (
        f"👥 <b>O'QUVCHILAR TO'LOVLARI</b>\n"
        f"📅 {escape_html(period)} · {len(students)} ta o'quvchi\n\n"
        f"🔴 qarzdor · 🟡 qisman · 🟢 to'langan · ⚠️ sozlama kerak"
    )
    await safe_telegram_call(
        message.edit_text, text,
        reply_markup=_student_list_keyboard(students, period, page),
        parse_mode="HTML",
    )


async def _show_student(message: types.Message, db: Database, student_id: int, period: str):
    await db.ensure_monthly_billing(period)
    student = await db.get_student_by_id(student_id)
    summary = await db.get_invoice_summary(student_id, period)
    if not student or not student.get("is_active"):
        await safe_telegram_call(message.edit_text, "❌ Faol o'quvchi topilmadi.")
        return
    if not summary:
        text = (
            f"👤 <b>{escape_html(student['full_name'])}</b>\n"
            f"📅 {escape_html(period)}\n\n"
            f"⚠️ Bu oy uchun hisob hali tayyor emas."
        )
        keyboard = _student_keyboard(student_id, period, 0)
    else:
        due = max(0, int(summary.get("due") or 0))
        if summary.get("status") == "waived":
            status = "⚪ Bekor qilingan"
        elif due == 0:
            status = "🟢 To'liq to'langan"
        elif int(summary.get("paid_total") or 0) > 0:
            status = "🟡 Qisman to'langan"
        else:
            status = "🔴 To'lanmagan"
        text = (
            f"👤 <b>{escape_html(student['full_name'])}</b>\n"
            f"📅 <b>{escape_html(period)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Tarif: <b>{_money(summary.get('amount'))}</b>\n"
            f"🎁 Chegirma: <b>{_money(summary.get('discount'))}</b>\n"
            f"✅ To'langan: <b>{_money(summary.get('paid_total'))}</b>\n"
            f"🔴 Qolgan qarz: <b>{_money(due)}</b>\n"
            f"📌 Holati: <b>{status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Kerakli amalni tanlang:"
        )
        keyboard = _student_keyboard(student_id, period, due)
    await safe_telegram_call(message.edit_text, text, reply_markup=keyboard, parse_mode="HTML")


@payment_router.message(F.text.in_({"💳 To'lov markazi", "💰 Oylik to'lovlar"}))
async def open_payment_center(message: types.Message, state: FSMContext, db: Database):
    await state.clear()
    await _show_dashboard(message, db, now_tz().strftime("%Y-%m"))


@payment_router.callback_query(BillingPeriodCallback.filter())
async def payment_period(callback: types.CallbackQuery, db: Database, callback_data: BillingPeriodCallback):
    period = _shift_period(callback_data.period, callback_data.move)
    await _show_dashboard(callback.message, db, period, edit=True)
    await callback.answer()


@payment_router.callback_query(BillingListCallback.filter())
async def payment_list(callback: types.CallbackQuery, db: Database, callback_data: BillingListCallback):
    await _show_students(callback.message, db, callback_data.period)
    await callback.answer()


@payment_router.callback_query(BillingPageCallback.filter())
async def payment_page(callback: types.CallbackQuery, db: Database, callback_data: BillingPageCallback):
    await _show_students(callback.message, db, callback_data.period, callback_data.page)
    await callback.answer()


@payment_router.callback_query(BillingStudentCallback.filter())
async def payment_student(callback: types.CallbackQuery, db: Database, callback_data: BillingStudentCallback):
    await _show_student(callback.message, db, callback_data.student_id, callback_data.period)
    await callback.answer()


@payment_router.callback_query(BillingPaymentStartCallback.filter())
async def payment_start(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: BillingPaymentStartCallback):
    summary = await db.get_invoice_summary(callback_data.student_id, callback_data.period)
    if not summary or summary.get("status") == "waived" or int(summary.get("due") or 0) <= 0:
        await callback.answer("To'lov uchun qarz mavjud emas.", show_alert=True)
        return
    await state.clear()
    await state.update_data(student_id=callback_data.student_id, period=callback_data.period)
    await state.set_state(PaymentCenterStates.waiting_for_payment_amount)
    await safe_telegram_call(
        callback.message.edit_text,
        f"💳 <b>TO'LOV QABUL QILISH</b>\n\nQolgan qarz: <b>{_money(summary['due'])}</b>\n\nTo'lov summasini kiriting:",
        parse_mode="HTML",
    )
    await callback.answer()


@payment_router.message(PaymentCenterStates.waiting_for_payment_amount, F.text)
async def payment_amount(message: types.Message, state: FSMContext, db: Database):
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Summani musbat butun so'mda kiriting. Masalan: 60000")
        return
    data = await state.get_data()
    summary = await db.get_invoice_summary(data["student_id"], data["period"])
    amount = int(raw)
    if not summary or amount > int(summary.get("due") or 0):
        await message.answer(f"Bu summa qolgan qarzdan katta. Qolgan qarz: {_money(summary.get('due') if summary else 0)}")
        return
    await state.update_data(payment_amount=amount)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💵 Naqd", callback_data=BillingPaymentMethodCallback(student_id=data["student_id"], period=data["period"], method="cash").pack()),
        InlineKeyboardButton(text="💳 Karta", callback_data=BillingPaymentMethodCallback(student_id=data["student_id"], period=data["period"], method="card").pack()),
    ]])
    await message.answer(f"<b>{_money(amount)}</b>\n\nTo'lov usulini tanlang:", reply_markup=keyboard, parse_mode="HTML")


@payment_router.callback_query(BillingPaymentMethodCallback.filter())
async def payment_method(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: BillingPaymentMethodCallback):
    data = await state.get_data()
    if data.get("student_id") != callback_data.student_id or data.get("period") != callback_data.period or type(data.get("payment_amount")) is not int:
        await callback.answer("To'lov oynasi eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    summary = await db.get_invoice_summary(callback_data.student_id, callback_data.period)
    if not summary:
        await state.clear()
        await callback.answer("Hisob topilmadi.", show_alert=True)
        return
    ok, reason, payment_id = await db.record_payment_with_id(
        summary["id"], data["payment_amount"], callback_data.method,
        callback.from_user.id, now_tz().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await state.clear()
    if not ok:
        await callback.answer({"overpayment": "Qarzdan oshib ketdi.", "waived": "Hisob bekor qilingan.", "not_found": "Hisob topilmadi."}.get(reason, "To'lov saqlanmadi."), show_alert=True)
        return
    await _show_student(callback.message, db, callback_data.student_id, callback_data.period)
    bot = getattr(callback, "bot", None) or getattr(callback.message, "bot", None)
    delivery = await send_payment_receipt(db, bot, payment_id) if bot is not None else "failed"
    if delivery != "sent" and bot is not None:
        await safe_send(bot, callback.from_user.id, "⚠️ To'lov saqlandi, lekin chekni yuborib bo'lmadi.")
    await callback.answer("To'lov saqlandi")


@payment_router.callback_query(BillingDiscountStartCallback.filter())
async def discount_start(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: BillingDiscountStartCallback):
    summary = await db.get_invoice_summary(callback_data.student_id, callback_data.period)
    if not summary or summary.get("status") == "waived" or int(summary.get("due") or 0) <= 0:
        await callback.answer("Chegirma uchun qarz mavjud emas.", show_alert=True)
        return
    await state.clear()
    await state.update_data(student_id=callback_data.student_id, period=callback_data.period)
    await state.set_state(PaymentCenterStates.waiting_for_discount_amount)
    await safe_telegram_call(callback.message.edit_text, "🎁 <b>CHEGIRMA</b>\n\nChegirma summasini kiriting:", parse_mode="HTML")
    await callback.answer()


@payment_router.message(PaymentCenterStates.waiting_for_discount_amount, F.text)
async def discount_amount(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Chegirma summasini musbat butun son qilib kiriting.")
        return
    await state.update_data(discount_amount=int(raw))
    await state.set_state(PaymentCenterStates.waiting_for_discount_reason)
    await message.answer("Chegirma sababini yozing:")


@payment_router.message(PaymentCenterStates.waiting_for_discount_reason, F.text)
async def discount_reason(message: types.Message, state: FSMContext, db: Database):
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Sababni bo'sh qoldirmang.")
        return
    data = await state.get_data()
    summary = await db.get_invoice_summary(data["student_id"], data["period"])
    if not summary:
        await state.clear()
        await message.answer("Hisob topilmadi.")
        return
    ok, why = await db.apply_discount(summary["id"], data["discount_amount"], reason, message.from_user.id)
    await state.clear()
    if not ok:
        await message.answer({
            "discount_exceeds_amount": "Chegirma oylik summadan katta bo'lishi mumkin emas.",
            "discount_exceeds_due": "Chegirma qolgan qarzdan katta.",
            "waived": "Hisob bekor qilingan.",
        }.get(why, "Chegirma saqlanmadi."))
        return
    await _show_student(message, db, data["student_id"], data["period"])
