import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, User, Message

from database.db import Database
from services.scheduler_service import SchedulerService
from handlers.admin import cb_admin_approve_trips
from models.enums import TripStatus, TripDirection

@pytest.mark.asyncio
async def test_phase_7_scheduler_and_escalation():
    test_db = f"test_phase7_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        mock_bot = AsyncMock()
        scheduler = SchedulerService(db=db, bot=mock_bot)

        driver_tg_id = 99119911
        admin_tg_id = 6273865667  # settings.admin_ids_list dagi id

        driver_id = await db.add_driver(full_name="Haydovchi Botir", phone="+998901234567", telegram_id=driver_tg_id)
        assert driver_id > 0

        # O'quvchilar va dars jadvallari (Dushanba = 0)
        st1_id = await db.add_student("Temur Aliyev", village_id=1, parent_phone_1="901112233")
        st2_id = await db.add_student("Malika Karimova", village_id=2, parent_phone_1="904445566")
        assert st1_id > 0
        assert st2_id > 0

        await db.add_schedule(st1_id, weekday=0, start_time="09:00", end_time="11:00")
        await db.add_schedule(st2_id, weekday=0, start_time="09:00", end_time="11:00")

        target_date = "2026-10-19"  # 2026-10-19 bu Dushanba (weekday=0)

        # =============================================================
        # 1. 06:30 KUNLIK REJA TUZILISHI VA BAZAGA SAQLANISHI
        # =============================================================
        success, reason, trips = await scheduler.run_daily_plan(target_date=target_date)
        assert success is True
        assert reason == "success"
        assert len(trips) >= 2  # to_center va to_home

        unapproved = await db.get_unapproved_trips(target_date)
        assert len(unapproved) == len(trips)
        for t in unapproved:
            assert t["approved"] == 0
            assert t["status"] == TripStatus.PLANNED.value
            assert t["reminder_count"] == 0

        # =============================================================
        # 2. 07:00 AVTO-TASDIQ ISHLASHI
        # =============================================================
        approved_count = await scheduler.run_auto_approve(target_date=target_date)
        assert approved_count == len(trips)

        unapproved_after = await db.get_unapproved_trips(target_date)
        assert len(unapproved_after) == 0

        all_trips = await db.get_trips_by_date(target_date)
        assert len(all_trips) == len(trips)
        for t in all_trips:
            assert t["approved"] == 1
            assert t["status"] == TripStatus.APPROVED.value

        # Qayta avto-tasdiq chaqirilsa 0 qaytishi
        re_approved = await scheduler.run_auto_approve(target_date=target_date)
        assert re_approved == 0

        # Admin qo'lda tasdiqlash callback tekshiruvi (boshqa sana misolida)
        date_manual = "2026-10-26"
        await scheduler.run_daily_plan(target_date=date_manual)
        unapproved_manual = await db.get_unapproved_trips(date_manual)
        assert len(unapproved_manual) >= 2

        admin_cb = MagicMock(spec=CallbackQuery)
        admin_cb.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        admin_cb.data = f"admin_approve_trips:{date_manual}"
        admin_cb.message = MagicMock(spec=Message)
        admin_cb.message.edit_text = AsyncMock()
        admin_cb.answer = AsyncMock()

        await cb_admin_approve_trips(admin_cb, db)
        admin_cb.answer.assert_called_with("Reyslar tasdiqlandi!")
        unapproved_manual_after = await db.get_unapproved_trips(date_manual)
        assert len(unapproved_manual_after) == 0

        # =============================================================
        # 3. ESLATMALAR ZANJIRI VA ESKALATSIYA MANTIQI
        # =============================================================
        # Sinov uchun aniq vaqtli bitta approved reys kiritamiz:
        # planned_time = "08:00"
        test_reminder_date = "2026-10-21"
        conn = await db.get_connection()
        cur_rem = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved, reminder_count)
            VALUES (?, 'to_center', ?, '08:00', 'approved', 1, 0)
        """, (test_reminder_date, driver_id))
        test_trip_id = cur_rem.lastrowid
        await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (test_trip_id, st1_id))
        await conn.commit()
        await conn.close()

        # A) 07:15 (45 daqiqa oldin) -> Hech qanday eslatma ketmasligi kerak
        t_0715 = datetime(2026, 10, 21, 7, 15, 0)
        res_0715 = await scheduler.check_and_send_reminders(now_dt=t_0715)
        assert res_0715["first_reminders"] == 0
        assert res_0715["second_reminders"] == 0
        assert res_0715["escalations"] == 0
        t_check1 = await db.get_trip_details(test_trip_id)
        assert t_check1["reminder_count"] == 0

        # B) 07:30 (Aynan 30 daqiqa oldin) -> 1-ESLATMA ketishi kerak
        t_0730 = datetime(2026, 10, 21, 7, 30, 0)
        res_0730 = await scheduler.check_and_send_reminders(now_dt=t_0730)
        assert res_0730["first_reminders"] == 1
        assert res_0730["second_reminders"] == 0
        assert res_0730["escalations"] == 0
        t_check2 = await db.get_trip_details(test_trip_id)
        assert t_check2["reminder_count"] == 1
        assert t_check2["last_reminder_at"] == "2026-10-21 07:30:00"

        # C) 07:35 (1-eslatmadan 5 daqiqa o'tdi) -> Hali 10 daqiqa bo'lmadi, eslatma yo'q
        t_0735 = datetime(2026, 10, 21, 7, 35, 0)
        res_0735 = await scheduler.check_and_send_reminders(now_dt=t_0735)
        assert res_0735["first_reminders"] == 0
        assert res_0735["second_reminders"] == 0
        assert res_0735["escalations"] == 0
        t_check3 = await db.get_trip_details(test_trip_id)
        assert t_check3["reminder_count"] == 1

        # D) 07:40 (1-eslatmadan 10 daqiqa o'tdi) -> 2-ESLATMA ketishi kerak
        t_0740 = datetime(2026, 10, 21, 7, 40, 0)
        res_0740 = await scheduler.check_and_send_reminders(now_dt=t_0740)
        assert res_0740["first_reminders"] == 0
        assert res_0740["second_reminders"] == 1
        assert res_0740["escalations"] == 0
        t_check4 = await db.get_trip_details(test_trip_id)
        assert t_check4["reminder_count"] == 2
        assert t_check4["last_reminder_at"] == "2026-10-21 07:40:00"

        # E) 07:45 (2-eslatmadan 5 daqiqa o'tdi) -> Hech narsa yo'q
        t_0745 = datetime(2026, 10, 21, 7, 45, 0)
        res_0745 = await scheduler.check_and_send_reminders(now_dt=t_0745)
        assert res_0745["first_reminders"] == 0
        assert res_0745["second_reminders"] == 0
        assert res_0745["escalations"] == 0
        t_check5 = await db.get_trip_details(test_trip_id)
        assert t_check5["reminder_count"] == 2

        # F) 07:50 (2-eslatmadan yana 10 daqiqa o'tdi) -> ADMINGA ESKALATSIYA!
        t_0750 = datetime(2026, 10, 21, 7, 50, 0)
        res_0750 = await scheduler.check_and_send_reminders(now_dt=t_0750)
        assert res_0750["first_reminders"] == 0
        assert res_0750["second_reminders"] == 0
        assert res_0750["escalations"] == 1
        t_check6 = await db.get_trip_details(test_trip_id)
        assert t_check6["reminder_count"] == 3
        assert t_check6["last_reminder_at"] == "2026-10-21 07:50:00"

        # G) 07:55 (Eskalatsiyadan so'ng) -> reminder_count=3 bo'lgani uchun takroriy xabar ketmaydi
        t_0755 = datetime(2026, 10, 21, 7, 55, 0)
        res_0755 = await scheduler.check_and_send_reminders(now_dt=t_0755)
        assert res_0755["first_reminders"] == 0
        assert res_0755["second_reminders"] == 0
        assert res_0755["escalations"] == 0
        t_check7 = await db.get_trip_details(test_trip_id)
        assert t_check7["reminder_count"] == 3

        # =============================================================
        # 4. "YO'LDAMAN" BOSILGANDA ESLATMALAR TO'XTASHI
        # =============================================================
        # Yangi reys ochamiz, unga status 'on_way' beramiz
        test_on_way_date = "2026-10-22"
        conn = await db.get_connection()
        cur_ow = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved, reminder_count)
            VALUES (?, 'to_center', ?, '08:00', 'on_way', 1, 0)
        """, (test_on_way_date, driver_id))
        ow_trip_id = cur_ow.lastrowid
        await conn.commit()
        await conn.close()

        # 07:35 da (vaqt o'tsa ham) status 'on_way' bo'lgani uchun eslatma bormaydi
        t_ow = datetime(2026, 10, 22, 7, 35, 0)
        res_ow = await scheduler.check_and_send_reminders(now_dt=t_ow)
        assert res_ow["first_reminders"] == 0
        assert res_ow["second_reminders"] == 0
        assert res_ow["escalations"] == 0
        ow_check = await db.get_trip_details(ow_trip_id)
        assert ow_check["reminder_count"] == 0

        # =============================================================
        # 5. QAYTA HISOBLASHDAN HIMOYALANISH (ON_WAY YOKI DONE REYS BOR BO'LSA)
        # =============================================================
        # test_on_way_date sanasida allaqachon 'on_way' reys bor
        re_plan_ok, re_plan_reason, re_plan_trips = await scheduler.run_daily_plan(target_date=test_on_way_date)
        assert re_plan_ok is False
        assert re_plan_reason == "active_trips_exist"
        assert len(re_plan_trips) == 0

        # Baza zaxiralash (backup) testi
        backup_path = scheduler.run_backup_db()
        assert os.path.exists(backup_path)

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
