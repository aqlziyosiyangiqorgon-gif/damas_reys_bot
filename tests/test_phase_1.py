import pytest
import os
import uuid
import aiosqlite
from config import settings
from database.db import Database, normalize_phone
from models.enums import UserRole

@pytest.mark.asyncio
async def test_phase_1_full_verification():
    test_db = f"test_phase1_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)
    
    try:
        # 1. Toza baza yaratish
        await db.init_db()

        # 2. Yangi bazada amaliy va persistent state jadvallari yaratilganini tekshirish
        async with aiosqlite.connect(test_db) as conn:
            cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [row[0] for row in await cur.fetchall()]
        
        expected_tables = {
            "zones", "villages", "drivers", "staff", "students", "schedules",
            "parents", "student_parents", "absences", "trips", "trip_items",
            "event_log", "notify_log", "broadcasts", "fsm_storage", "workflow_payloads",
            "billing_plans", "invoices", "payments", "discount_log"
        }
        assert len(tables) == 20
        assert set(tables) == expected_tables

        # 3. 10 ta mahalla va 4 ta zona
        zones = await db.get_zones()
        assert len(zones) == 4
        villages = await db.get_villages()
        assert len(villages) == 10

        # 4. Admin / Staff / Driver / Parent / Guest rollari
        admin_id = 6273865667
        staff_id = 111111111
        driver_id = 222222222
        parent_id = 333333333
        guest_id = 444444444

        # Admin
        assert await db.get_user_role(admin_id, settings.admin_ids_list) == UserRole.ADMIN

        # Staff
        staff_db_id = await db.add_staff(staff_id, "Fotima Xodima")
        assert staff_db_id > 0
        assert await db.get_user_role(staff_id, settings.admin_ids_list) == UserRole.STAFF

        # Driver
        driver_db_id = await db.add_driver(driver_id, "Sobir Haydovchi", "901112233")
        assert driver_db_id > 0
        assert await db.get_user_role(driver_id, settings.admin_ids_list) == UserRole.DRIVER

        # 4b. Bir xil telegram_id bilan add_driver ikki marta chaqirilsa, id bir xil va > 0 bo'lsin
        driver_db_id_2 = await db.add_driver(driver_id, "Sobir Yangilangan", "+998 90 111 22 33")
        assert driver_db_id_2 == driver_db_id
        assert driver_db_id_2 > 0

        # Parent
        parent_db_id = await db.add_parent(parent_id, "909998877", "2026-10-13 22:00:00")
        assert parent_db_id > 0
        assert await db.get_user_role(parent_id, settings.admin_ids_list) == UserRole.PARENT

        # Guest
        assert await db.get_user_role(guest_id, settings.admin_ids_list) == UserRole.GUEST

        # 5. students ga insert (parent_phone_1 bilan) ishlashi va normalize_phone
        v1_id = villages[0]["id"]
        student_id = await db.add_student(
            full_name="Akrom Aliyev",
            village_id=v1_id,
            parent_phone_1="90 123 45 67",
            parent_phone_2="998907654321"
        )
        assert student_id is not None
        assert student_id > 0

        # students jadvali ustunlari va kiritilgan telefon normalizatsiyasi
        async with aiosqlite.connect(test_db) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM students WHERE id = ?", (student_id,))
            student = dict(await cur.fetchone())
            assert student["full_name"] == "Akrom Aliyev"
            assert student["parent_phone_1"] == "+998901234567"
            assert student["parent_phone_2"] == "+998907654321"
            assert student["is_active"] == 1
            assert "parent_phone" not in student
            assert "parent_tg_id" not in student
            assert "active" not in student

        # 6. Foreign key (PRAGMA foreign_keys=ON): mavjud bo'lmagan village_id bo'lsa aiosqlite.IntegrityError qaytarishi kerak
        with pytest.raises(aiosqlite.IntegrityError):
            await db.add_student("Xato Bola", 999999, "+998900000000")

        # 6b. add_student: parent_phone_1 noto'g'ri bo'lsa ValueError ko'tarishi kerak
        with pytest.raises(ValueError):
            await db.add_student("Xato Telefonli Bola", v1_id, "123")
        with pytest.raises(ValueError):
            await db.add_student("Xato Telefonli Bola 2", v1_id, "abc")
        with pytest.raises(ValueError):
            await db.add_student("Xato Telefonli Bola 3", v1_id, "")

        # 7. normalize_phone funksiyasi alohida testlari
        assert normalize_phone("901234567") == "+998901234567"
        assert normalize_phone("+998 (90) 123-45-67") == "+998901234567"
        assert normalize_phone("998901234567") == "+998901234567"
        assert normalize_phone("+998901234567") == "+998901234567"
        assert normalize_phone("123") is None
        assert normalize_phone("abc") is None
        assert normalize_phone("") is None
        assert normalize_phone("8901234567") is None
        assert normalize_phone(None) is None

        # 8. WAL rejimi va busy_timeout=5000 tekshiruvi
        conn = await db.get_connection()
        cur = await conn.execute("PRAGMA journal_mode;")
        journal_mode = (await cur.fetchone())[0]
        assert journal_mode.lower() == "wal"
        cur = await conn.execute("PRAGMA busy_timeout;")
        busy_timeout = (await cur.fetchone())[0]
        assert busy_timeout == 5000
        await conn.close()

        # 9. add_driver, add_staff, add_parent: noto'g'ri telefon bo'lsa ValueError
        with pytest.raises(ValueError):
            await db.add_driver(99901, "Xato Driver", "123")
        with pytest.raises(ValueError):
            await db.add_staff(99902, "Xato Staff", "abc")
        with pytest.raises(ValueError):
            await db.add_parent(99903, "noto'g'ri", "2026-10-13 22:00:00")

        # 10. schedules: UNIQUE(student_id, weekday) tekshiruvi
        conn = await db.get_connection()
        await conn.execute("INSERT INTO schedules (student_id, weekday, start_time, end_time) VALUES (?, 1, '09:00', '11:00')", (student_id,))
        await conn.commit()
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute("INSERT INTO schedules (student_id, weekday, start_time, end_time) VALUES (?, 1, '10:00', '12:00')", (student_id,))
            await conn.commit()
        await conn.close()

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
