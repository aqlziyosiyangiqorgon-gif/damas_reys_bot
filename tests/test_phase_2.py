import pytest
import os
import uuid
from database.db import Database
from models.enums import UserRole
from handlers.admin import build_students_pagination_kb, admin_router, is_admin

@pytest.mark.asyncio
async def test_phase_2_admin_management():
    test_db = f"test_phase2_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        # 1. Mahallalar va zonalar boshqaruvi (nom, travel_minutes, school_name, route_order, pickup_point)
        villages = await db.get_villages()
        assert len(villages) == 10
        v1 = villages[0]
        v1_id = v1["id"]

        success = await db.update_village(
            village_id=v1_id,
            name="Arpaariq Yangilangan",
            travel_minutes=40,
            school_name="4-maktab (Markaziy)",
            route_order=1,
            pickup_point="Katta yo'l yoqasi"
        )
        assert success is True
        updated_v1 = await db.get_village_by_id(v1_id)
        assert updated_v1["name"] == "Arpaariq Yangilangan"
        assert updated_v1["travel_minutes"] == 40
        assert updated_v1["school_name"] == "4-maktab (Markaziy)"
        assert updated_v1["pickup_point"] == "Katta yo'l yoqasi"

        # 2. Haydovchilar boshqaruvi (phone majburiy va normalize_phone xatolikda ValueError)
        with pytest.raises(ValueError):
            await db.add_driver(1001, "Ali Haydovchi", "noto'g'ri_raqam")

        driver_id = await db.add_driver(1001, "Ali Haydovchi", "901112233")
        assert driver_id > 0
        drivers = await db.get_drivers()
        assert len(drivers) == 1
        assert drivers[0]["telegram_id"] == 1001
        assert drivers[0]["phone"] == "+998901112233"
        assert drivers[0]["is_active"] == 1

        # toggle status
        await db.toggle_driver_status(driver_id, 0)
        d_check = await db.get_driver_by_id(driver_id)
        assert d_check["is_active"] == 0

        await db.toggle_driver_status(driver_id, 1)
        d_check2 = await db.get_driver_by_id(driver_id)
        assert d_check2["is_active"] == 1

        # 3. Xodimlar (Staff) boshqaruvi
        staff_id = await db.add_staff(2001, "Malika Xodima", "903334455")
        assert staff_id > 0
        staff_list = await db.get_all_staff()
        assert len(staff_list) == 1
        assert staff_list[0]["telegram_id"] == 2001
        assert staff_list[0]["phone"] == "+998903334455"
        assert staff_list[0]["is_active"] == 1

        await db.toggle_staff_status(staff_id, 0)
        s_check = await db.get_staff_by_id(staff_id)
        assert s_check["is_active"] == 0

        # 4. O'quvchilar boshqaruvi
        student_id = await db.add_student(
            full_name="Temur Alimov",
            village_id=v1_id,
            parent_phone_1="905556677",
            parent_phone_2="907778899"
        )
        assert student_id > 0

        active_students = await db.get_active_students()
        assert len(active_students) == 1
        st = active_students[0]
        assert st["full_name"] == "Temur Alimov"
        assert st["village_name"] == "Arpaariq Yangilangan"
        assert st["zone_id"] == 1

        v2_id = villages[1]["id"]
        update_st_res = await db.update_student(
            student_id=student_id,
            full_name="Temur Alimov (Katta)",
            village_id=v2_id,
            parent_phone_1="+998901110022",
            parent_phone_2="+998903330044"
        )
        assert update_st_res is True
        st_updated = await db.get_student_by_id(student_id)
        assert st_updated["full_name"] == "Temur Alimov (Katta)"
        assert st_updated["village_id"] == v2_id
        assert st_updated["parent_phone_1"] == "+998901110022"

        # 5. Jadvallar boshqaruvi (add_schedule UPSERT va ORDER BY weekday)
        # Bir kunni (Dushanba - 0) ikki marta qo'shish:
        await db.add_schedule(student_id, 0, "08:30", "10:30")
        await db.add_schedule(student_id, 0, "09:00", "11:00")  # Takroriy kiritish

        sch_1 = await db.get_student_schedules(student_id)
        assert len(sch_1) == 1, "Bir xil kun ikki marta qo'shilganda 1 ta yozuv qolishi kerak"
        assert sch_1[0]["weekday"] == 0
        assert sch_1[0]["start_time"] == "09:00"
        assert sch_1[0]["end_time"] == "11:00"

        # Boshqa kunlarni aralash tartibda qo'shish va ORDER BY weekday tekshirish
        await db.add_schedule(student_id, 4, "08:30", "10:30")  # Juma (4)
        await db.add_schedule(student_id, 2, "08:30", "10:30")  # Chorshanba (2)

        schedules = await db.get_student_schedules(student_id)
        assert len(schedules) == 3
        weekdays = [s["weekday"] for s in schedules]
        assert weekdays == [0, 2, 4], "Schedules weekday bo'yicha o'sish tartibida saralangan bo'lishi kerak"

        # 6. deactivate_student
        deact_res = await db.deactivate_student(student_id)
        assert deact_res is True
        assert len(await db.get_active_students()) == 0

        # 7. is_admin funksiyasi tekshiruvi
        assert is_admin(6273865667) is True
        assert is_admin(7446734373) is True
        assert is_admin(999999999) is False

        # 8. Pagination testi (20 ta o'quvchi, PAGE_SIZE=10)
        twenty_students = [{"id": i, "full_name": f"O'quvchi {i}", "village_name": "Qishloq"} for i in range(20)]
        
        # 1-sahifa (page=0): 10 ta o'quvchi, "keyingi" bor, "oldingi" yo'q
        kb_page_0 = build_students_pagination_kb(twenty_students, page=0, action_prefix="st")
        student_buttons_p0 = [r for r in kb_page_0.inline_keyboard if r[0].callback_data.startswith("st:")]
        assert len(student_buttons_p0) == 10
        nav_buttons_p0 = [b.text for r in kb_page_0.inline_keyboard for b in r if "Oldingi" in b.text or "Keyingi" in b.text]
        assert any("Keyingi" in t for t in nav_buttons_p0), "1-sahifada 'keyingi' bo'lishi kerak"
        assert not any("Oldingi" in t for t in nav_buttons_p0), "1-sahifada 'oldingi' bo'lmasligi kerak"

        # 2-sahifa (page=1, oxirgi): 10 ta o'quvchi, "oldingi" bor, "keyingi" yo'q
        kb_page_1 = build_students_pagination_kb(twenty_students, page=1, action_prefix="st")
        student_buttons_p1 = [r for r in kb_page_1.inline_keyboard if r[0].callback_data.startswith("st:")]
        assert len(student_buttons_p1) == 10
        nav_buttons_p1 = [b.text for r in kb_page_1.inline_keyboard for b in r if "Oldingi" in b.text or "Keyingi" in b.text]
        assert any("Oldingi" in t for t in nav_buttons_p1), "2-sahifada 'oldingi' bo'lishi kerak"
        assert not any("Keyingi" in t for t in nav_buttons_p1), "2-sahifada (oxirgi) 'keyingi' bo'lmasligi kerak"

        # 30 ta o'quvchi bo'lsa (3 ta sahifa), 2-sahifada (page=1, o'rtada) ikkalasi ham bor:
        thirty_students = [{"id": i, "full_name": f"O'quvchi {i}", "village_name": "Qishloq"} for i in range(30)]
        kb_page_mid = build_students_pagination_kb(thirty_students, page=1, action_prefix="st")
        nav_buttons_mid = [b.text for r in kb_page_mid.inline_keyboard for b in r if "Oldingi" in b.text or "Keyingi" in b.text]
        assert any("Oldingi" in t for t in nav_buttons_mid), "O'rta sahifada 'oldingi' bo'lishi kerak"
        assert any("Keyingi" in t for t in nav_buttons_mid), "O'rta sahifada 'keyingi' bo'lishi kerak"

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
