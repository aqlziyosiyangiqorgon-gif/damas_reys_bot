import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from database.db import Database
import handlers.admin as admin_handlers
from handlers.admin import get_admin_menu_kb
from models.callbacks import (
    BillingDiscountStartCallback,
    BillingInvoiceCallback,
    BillingListCallback,
    BillingManualAmountCallback,
    BillingPageCallback,
    BillingPaymentMethodCallback,
    BillingPaymentStartCallback,
    BillingPeriodCallback,
    BillingStudentCallback,
)


@pytest_asyncio.fixture
async def billing_db(tmp_path):
    db = Database(str(tmp_path / "billing.sqlite3"))
    await db.init_db()
    student_id = await db.add_student("Billing Test Student", 1, "+998901234567")
    for weekday in (0, 2, 4):
        await db.add_schedule(student_id, weekday, "09:00", "11:00")
    return db, student_id


@pytest.mark.asyncio
async def test_billing_schema_init_is_idempotent_and_preserves_transport_data(billing_db):
    db, student_id = billing_db
    conn = await db.get_connection()
    try:
        await conn.execute(
            "INSERT INTO trips (date, direction, planned_time) VALUES ('2026-10-18', 'to_center', '08:00')"
        )
        await conn.commit()
        before = {}
        for table in ("students", "schedules", "trips"):
            cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            before[table] = (await cur.fetchone())[0]
    finally:
        await conn.close()

    await db.init_db()

    conn = await db.get_connection()
    try:
        after = {}
        for table in ("students", "schedules", "trips"):
            cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            after[table] = (await cur.fetchone())[0]
        assert after == before
        cur = await conn.execute("SELECT COUNT(*) FROM billing_plans")
        assert (await cur.fetchone())[0] == 0
    finally:
        await conn.close()
    assert student_id > 0


@pytest.mark.asyncio
async def test_create_invoice_is_idempotent(billing_db):
    db, student_id = billing_db

    assert await db.create_invoice_if_missing(student_id, "2026-10", 60_000) is True
    assert await db.create_invoice_if_missing(student_id, "2026-10", 60_000) is False

    conn = await db.get_connection()
    try:
        cur = await conn.execute("SELECT COUNT(*) FROM invoices")
        assert (await cur.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_partial_then_full_payment_updates_due_and_status(billing_db):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    summary = await db.get_invoice_summary(student_id, "2026-10")

    assert await db.record_payment(summary["id"], 30_000, "cash", 123, "2026-10-18 10:00:00") == (True, "ok")
    partial = await db.get_invoice_summary(student_id, "2026-10")
    assert partial["status"] == "unpaid"
    assert partial["paid_total"] == 30_000
    assert partial["due"] == 30_000

    assert await db.record_payment(summary["id"], 30_000, "card", 123, "2026-10-18 10:05:00") == (True, "ok")
    paid = await db.get_invoice_summary(student_id, "2026-10")
    assert paid["status"] == "paid"
    assert paid["paid_total"] == 60_000
    assert paid["due"] == 0


@pytest.mark.asyncio
async def test_payment_rejects_overpayment_invalid_amount_and_method(billing_db):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    invoice_id = (await db.get_invoice_summary(student_id, "2026-10"))["id"]

    assert await db.record_payment(invoice_id, 70_000, "cash", 123, "2026-10-18 10:00:00") == (False, "overpayment")
    assert await db.record_payment(invoice_id, 0, "cash", 123, "2026-10-18 10:00:00") == (False, "invalid_amount")
    assert await db.record_payment(invoice_id, 1, "click", 123, "2026-10-18 10:00:00") == (False, "invalid_method")
    assert await db.record_payment(999999, 1, "cash", 123, "2026-10-18 10:00:00") == (False, "not_found")
    summary = await db.get_invoice_summary(student_id, "2026-10")
    assert summary["paid_total"] == 0


@pytest.mark.asyncio
async def test_discount_is_logged_and_can_complete_the_invoice(billing_db):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    summary = await db.get_invoice_summary(student_id, "2026-10")

    assert await db.apply_discount(summary["id"], 10_000, "Imtiyoz", 123) == (True, "ok")
    assert await db.record_payment(summary["id"], 50_000, "cash", 123, "2026-10-18 10:00:00") == (True, "ok")
    paid = await db.get_invoice_summary(student_id, "2026-10")
    assert paid["amount"] == 60_000
    assert paid["discount"] == 10_000
    assert paid["paid_total"] == 50_000
    assert paid["due"] == 0
    assert paid["status"] == "paid"


@pytest.mark.asyncio
async def test_discount_over_amount_is_rejected_without_log(billing_db):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    summary = await db.get_invoice_summary(student_id, "2026-10")

    assert await db.apply_discount(summary["id"], 60_001, "Xato", 123) == (False, "discount_exceeds_amount")
    assert (await db.get_invoice_summary(student_id, "2026-10"))["discount"] == 0
    conn = await db.get_connection()
    try:
        cur = await conn.execute("SELECT COUNT(*) FROM discount_log")
        assert (await cur.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_database_constraints_and_billing_list_totals(billing_db):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    invoice_id = (await db.get_invoice_summary(student_id, "2026-10"))["id"]
    assert await db.record_payment(invoice_id, 10_000, "cash", 123, "2026-10-18 10:00:00") == (True, "ok")

    overview = await db.get_billing_students("2026-10")
    assert len(overview) == 1
    assert overview[0]["days_per_week"] == 3
    assert overview[0]["paid_total"] == 10_000
    assert overview[0]["due"] == 50_000

    conn = await db.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO payments (invoice_id, amount, method, recorded_by, paid_at) VALUES (?, -5, 'cash', 1, 'now')",
                (invoice_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO invoices (student_id, period, amount, status, created_at) "
                "VALUES (999999, '2026-11', 1000, 'unpaid', 'now')"
            )
        await conn.rollback()
    finally:
        await conn.close()


def test_admin_menu_has_monthly_payment_button_and_callbacks_stay_under_64_bytes():
    texts = [button.text for row in get_admin_menu_kb().keyboard for button in row]
    assert "💰 Oylik to'lovlar" in texts

    max_id = 9_223_372_036_854_775_807
    payloads = [
        BillingStudentCallback(student_id=max_id, period="2026-10").pack(),
        BillingPageCallback(page=max_id, period="2026-10").pack(),
        BillingPeriodCallback(period="2026-10", move="p").pack(),
        BillingInvoiceCallback(student_id=max_id, amount=max_id, period="2026-10").pack(),
        BillingManualAmountCallback(student_id=max_id, period="2026-10").pack(),
        BillingListCallback(period="2026-10").pack(),
        BillingPaymentStartCallback(student_id=max_id, period="2026-10").pack(),
        BillingDiscountStartCallback(student_id=max_id, period="2026-10").pack(),
        BillingPaymentMethodCallback(
            student_id=max_id, period="2026-10", method="cash"
        ).pack(),
    ]
    assert all(len(payload.encode("utf-8")) <= 64 for payload in payloads)


@pytest.mark.asyncio
async def test_admin_payment_callback_records_payment(billing_db, monkeypatch):
    db, student_id = billing_db
    await db.create_invoice_if_missing(student_id, "2026-10", 60_000)
    monkeypatch.setattr(admin_handlers, "is_admin", lambda _user_id: True)

    state = SimpleNamespace(
        get_data=AsyncMock(return_value={
            "student_id": student_id,
            "period": "2026-10",
            "payment_amount": 15_000,
        }),
        clear=AsyncMock(),
    )
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=6273865667),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    callback_data = BillingPaymentMethodCallback(
        student_id=student_id,
        period="2026-10",
        method="cash",
    )

    await admin_handlers.cb_billing_payment_method(callback, state, db, callback_data)

    summary = await db.get_invoice_summary(student_id, "2026-10")
    assert summary["paid_total"] == 15_000
    assert summary["due"] == 45_000
    assert summary["status"] == "unpaid"
    assert callback.answer.await_args.args[0].startswith("To'lov saqlandi")
    assert "To'langan" in callback.message.edit_text.await_args.args[0]
