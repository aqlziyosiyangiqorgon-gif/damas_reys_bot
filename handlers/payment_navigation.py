from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext

from config import settings
from database.db import Database
from models.payment_callbacks import PaymentDashboardCallback
from handlers.payments import _show_dashboard

payment_navigation_router = Router()
payment_navigation_router.message.filter(F.from_user.id.in_(settings.admin_ids_list))
payment_navigation_router.callback_query.filter(F.from_user.id.in_(settings.admin_ids_list))


@payment_navigation_router.callback_query(PaymentDashboardCallback.filter())
async def payment_dashboard_navigation(
    callback: types.CallbackQuery,
    state: FSMContext,
    db: Database,
    callback_data: PaymentDashboardCallback,
):
    # Returning to the dashboard must always leave any payment/search FSM state.
    await state.clear()
    await _show_dashboard(callback.message, db, callback_data.period, edit=True)
    await callback.answer()
