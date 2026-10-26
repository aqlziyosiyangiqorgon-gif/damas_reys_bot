from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.db import Database
from models.callbacks import (
    BillingPaymentStartCallback,
    BillingPaymentMethodCallback,
    BillingDiscountStartCallback,
)
from handlers.payments import PaymentCenterStates, _money, _show_student

payment_flow_router = Router()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Bekor qilish", callback_data="pay_cancel")]
    ])


def _cancel_keyboard_with_methods(student_id: int, period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💵 Naqd",
                callback_data=BillingPaymentMethodCallback(
                    student_id=student_id, period=period, method="cash"
                ).pack(),
            ),
            InlineKeyboardButton(
                text="💳 Karta",
                callback_data=BillingPaymentMethodCallback(
                    student_id=student_id, period=period, method="card"
                ).pack(),
            ),
        ],
        [InlineKeyboardButton(text="✖️ Bekor qilish", callback_data="pay_cancel")],
    ])


@payment_flow_router.callback_query(F.data == "noop")
async def payment_noop(callback: types.CallbackQuery):
    await callback.answer()


@payment_flow_router.callback_query(F.data == "pay_cancel")
async def payment_cancel(callback: types.CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    student_id = data.get("student_id")
    period = data.get("period")
    search_period = data.get("payment_period")
    await state.clear()

    if student_id and period:
        await _show_student(callback.message, db, int(student_id), str(period))
    else:
        from handlers.payments import _show_dashboard
        from utils.clock import now_tz
        await _show_dashboard(
            callback.message,
            db,
            str(search_period or now_tz().strftime("%Y-%m")),
            edit=True,
        )
    await callback.answer("Bekor qilindi")


@payment_flow_router.message(F.text == "/cancel")
async def payment_cancel_command(message: types.Message, state: FSMContext, db: Database):
    data = await state.get_data()
    student_id = data.get("student_id")
    period = data.get("period")
    search_period = data.get("payment_period")
    await state.clear()

    if student_id and period:
        await _show_student(message, db, int(student_id), str(period))
    else:
        from handlers.payments import _show_dashboard
        from utils.clock import now_tz
        await _show_dashboard(
            message,
            db,
            str(search_period or now_tz().strftime("%Y-%m")),
        )


@payment_flow_router.callback_query(BillingPaymentStartCallback.filter())
async def payment_start_with_cancel(
    callback: types.CallbackQuery,
    state: FSMContext,
    db: Database,
    callback_data: BillingPaymentStartCallback,
):
    summary = await db.get_invoice_summary(callback_data.student_id, callback_data.period)
    if not summary or summary.get("status") == "waived" or int(summary.get("due") or 0) <= 0:
        await callback.answer("To'lov uchun qarz mavjud emas.", show_alert=True)
        return

    await state.clear()
    await state.update_data(student_id=callback_data.student_id, period=callback_data.period)
    await state.set_state(PaymentCenterStates.waiting_for_payment_amount)
    await callback.message.edit_text(
        f"💳 <b>TO'LOV QABUL QILISH</b>\n\n"
        f"Qolgan qarz: <b>{_money(summary['due'])}</b>\n\n"
        f"To'lov summasini kiriting:",
        reply_markup=_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@payment_flow_router.message(PaymentCenterStates.waiting_for_payment_amount, F.text)
async def payment_amount_with_cancel(message: types.Message, state: FSMContext, db: Database):
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "Summani musbat butun so'mda kiriting. Masalan: 60000",
            reply_markup=_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    summary = await db.get_invoice_summary(data["student_id"], data["period"])
    amount = int(raw)
    if not summary or amount > int(summary.get("due") or 0):
        await message.answer(
            f"Bu summa qolgan qarzdan katta. Qolgan qarz: {_money(summary.get('due') if summary else 0)}",
            reply_markup=_cancel_keyboard(),
        )
        return

    await state.update_data(payment_amount=amount)
    await message.answer(
        f"<b>{_money(amount)}</b>\n\nTo'lov usulini tanlang:",
        reply_markup=_cancel_keyboard_with_methods(data["student_id"], data["period"]),
        parse_mode="HTML",
    )


@payment_flow_router.callback_query(BillingDiscountStartCallback.filter())
async def discount_start_with_cancel(
    callback: types.CallbackQuery,
    state: FSMContext,
    db: Database,
    callback_data: BillingDiscountStartCallback,
):
    summary = await db.get_invoice_summary(callback_data.student_id, callback_data.period)
    if not summary or summary.get("status") == "waived" or int(summary.get("due") or 0) <= 0:
        await callback.answer("Chegirma uchun qarz mavjud emas.", show_alert=True)
        return

    await state.clear()
    await state.update_data(student_id=callback_data.student_id, period=callback_data.period)
    await state.set_state(PaymentCenterStates.waiting_for_discount_amount)
    await callback.message.edit_text(
        "🎁 <b>CHEGIRMA</b>\n\nChegirma summasini kiriting:",
        reply_markup=_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@payment_flow_router.message(PaymentCenterStates.waiting_for_discount_amount, F.text)
async def discount_amount_with_cancel(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "Chegirma summasini musbat butun son qilib kiriting.",
            reply_markup=_cancel_keyboard(),
        )
        return

    await state.update_data(discount_amount=int(raw))
    await state.set_state(PaymentCenterStates.waiting_for_discount_reason)
    await message.answer(
        "🎁 <b>CHEGIRMA SABABI</b>\n\nSababini yozing:",
        reply_markup=_cancel_keyboard(),
        parse_mode="HTML",
    )


@payment_flow_router.message(PaymentCenterStates.waiting_for_discount_reason, F.text)
async def discount_reason_with_cancel(message: types.Message, state: FSMContext, db: Database):
    reason = (message.text or "").strip()
    if not reason:
        await message.answer(
            "Sababni bo'sh qoldirmang.",
            reply_markup=_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    summary = await db.get_invoice_summary(data["student_id"], data["period"])
    if not summary:
        await state.clear()
        await message.answer("Hisob topilmadi.")
        return

    ok, why = await db.apply_discount(
        summary["id"], data["discount_amount"], reason, message.from_user.id
    )
    await state.clear()
    if not ok:
        await message.answer({
            "discount_exceeds_amount": "Chegirma oylik summadan katta bo'lishi mumkin emas.",
            "discount_exceeds_due": "Chegirma qolgan qarzdan katta.",
            "waived": "Hisob bekor qilingan.",
        }.get(why, "Chegirma saqlanmadi."))
        return

    await _show_student(message, db, data["student_id"], data["period"])
