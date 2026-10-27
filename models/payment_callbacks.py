"""Callback payloads dedicated to the Payment Center UI."""

from typing import Literal
from aiogram.filters.callback_data import CallbackData


class PaymentDashboardCallback(CallbackData, prefix="pay_dash"):
    period: str


class PaymentActionCallback(CallbackData, prefix="pay_act"):
    action: Literal["search", "report", "history"]
    period: str


class PaymentStudentHistoryCallback(CallbackData, prefix="pay_hist"):
    student_id: int
    period: str
