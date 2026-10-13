from datetime import datetime
from typing import Optional, List
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database.db import Database, normalize_phone

admin_router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids_list

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
PAGE_SIZE = 10

# --- FSM States ---
class AddStudentStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_village = State()
    waiting_for_phone_1 = State()
    waiting_for_phone_2 = State()
    waiting_for_days = State()
    waiting_for_time = State()

class EditStudentStates(StatesGroup):
    selecting_student = State()
    selecting_field = State()
    waiting_for_new_name = State()
    waiting_for_new_village = State()
    waiting_for_new_phone1 = State()
    waiting_for_new_phone2 = State()

class DeleteStudentStates(StatesGroup):
    selecting_student = State()

class EditScheduleStates(StatesGroup):
    selecting_student = State()
    selecting_weekday = State()
    waiting_for_new_time = State()

class EditVillageStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_school = State()
    waiting_for_travel_minutes = State()
    waiting_for_route_order = State()
    waiting_for_pickup_point = State()

class AddDriverStates(StatesGroup):
    waiting_for_tg_id = State()
    waiting_for_name = State()
    waiting_for_phone = State()

class AddStaffStates(StatesGroup):
    waiting_for_tg_id = State()
    waiting_for_name = State()
    waiting_for_phone = State()

class DelayStates(StatesGroup):
    waiting_for_custom_minutes = State()

class BroadcastStates(StatesGroup):
    selecting_audience = State()
    waiting_for_value = State()
    waiting_for_content = State()
    confirming = State()

# --- Helper: Student Pagination Keyboard ---
def build_students_pagination_kb(students: list, page: int, action_prefix: str) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(students) + PAGE_SIZE - 1) // PAGE_SIZE)
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_students = students[start_idx:end_idx]

    rows = []
    for s in page_students:
        v_name = s.get("village_name", "")
        rows.append([InlineKeyboardButton(text=f"👤 {s['full_name']} ({v_name})", callback_data=f"{action_prefix}:{s['id']}:{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"page_{action_prefix}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"page_{action_prefix}:{page + 1}"))
    
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Cancel Callback ---
@admin_router.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Amal bekor qilindi.")
    await callback.answer()

@admin_router.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await callback.answer()

# ============================================================================
# 1. /qishloqlar — Mahallalar va marshrut tartibi
# ============================================================================
@admin_router.message(Command("qishloqlar"))
@admin_router.message(F.text == "🏘 Mahallalar")
async def cmd_qishloqlar(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    villages = await db.get_villages()
    text = "🏘 <b>Mahallalar va marshrut zonalari:</b>\n\n"
    current_zone = None
    rows = []

    for v in villages:
        if v["zone_name"] != current_zone:
            current_zone = v["zone_name"]
            text += f"\n📍 <b>{current_zone}:</b>\n"
        pickup = f" (To'xtash: {v['pickup_point']})" if v.get("pickup_point") else ""
        text += f"  {v['route_order']}. <b>{v['name']}</b> ({v.get('school_name', '-')}) — ⏱ {v['travel_minutes']} daq{pickup}\n"
        rows.append([InlineKeyboardButton(text=f"✏️ {v['name']} ({v['zone_name']})", callback_data=f"edit_v:{v['id']}")])

    text += "\n<i>Tahrirlash uchun mahallani tanlang:</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("edit_v:"))
async def cb_edit_village_start(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    v_id = int(callback.data.split(":")[1])
    village = await db.get_village_by_id(v_id)
    if not village:
        await callback.answer("Mahalla topilmadi!", show_alert=True)
        return

    await state.update_data(editing_village_id=v_id, orig_village=village)
    await state.set_state(EditVillageStates.waiting_for_name)
    await callback.message.edit_text(
        f"✏️ <b>{village['name']} ({village['zone_name']}) tahrirlash</b>\n\n"
        f"1. Yangi mahalla nomini kiriting (hozirgi: <code>{village['name']}</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(EditVillageStates.waiting_for_name)
async def process_village_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_name = message.text.strip()
    await state.update_data(new_name=new_name)
    await state.set_state(EditVillageStates.waiting_for_school)
    await message.answer("2. Maktab nomini kiriting (masalan: <code>4-maktab</code>):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_school)
async def process_village_school(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    school_name = message.text.strip()
    await state.update_data(school_name=school_name)
    await state.set_state(EditVillageStates.waiting_for_travel_minutes)
    await message.answer("3. Markazgacha bo'lgan yo'l vaqtini daqiqada kiriting (masalan: <code>25</code>):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_travel_minutes)
async def process_village_minutes(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat butun son kiriting (masalan: 25):")
        return
    await state.update_data(travel_minutes=int(text))
    await state.set_state(EditVillageStates.waiting_for_route_order)
    await message.answer("4. Zona ichidagi tartib raqamini kiriting (1, 2, 3... uzoqdan yaqinga):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_route_order)
async def process_village_order(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat raqam kiriting (masalan: 1):")
        return
    await state.update_data(route_order=int(text))
    await state.set_state(EditVillageStates.waiting_for_pickup_point)
    await message.answer("5. To'planish nuqtasini kiriting (masalan: <code>Maktab darvozasi oldi</code>):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_pickup_point)
async def process_village_pickup(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    pickup_point = message.text.strip()
    data = await state.get_data()
    v_id = data["editing_village_id"]
    await db.update_village(
        village_id=v_id,
        name=data["new_name"],
        travel_minutes=data["travel_minutes"],
        school_name=data["school_name"],
        route_order=data["route_order"],
        pickup_point=pickup_point
    )
    await state.clear()
    await message.answer(
        f"✅ <b>Mahalla muvaffaqiyatli yangilandi!</b>\n\n"
        f"Nomi: <b>{data['new_name']}</b> ({data['school_name']})\n"
        f"Yo'l vaqti: <b>{data['travel_minutes']} daqiqa</b>\n"
        f"Tartibi: <b>{data['route_order']}</b>\n"
        f"To'planish nuqtasi: <b>{pickup_point}</b>\n\n"
        f"Ko'rish: /qishloqlar",
        parse_mode="HTML"
    )

# ============================================================================
# 2. /haydovchilar — Haydovchilar boshqaruvi
# ============================================================================
@admin_router.message(Command("haydovchilar"))
@admin_router.message(F.text == "🚗 Haydovchilar")
async def cmd_haydovchilar(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    drivers = await db.get_drivers()
    text = "🚗 <b>Haydovchilar ro'yxati:</b>\n\n"
    rows = []

    if not drivers:
        text += "Hozircha haydovchilar qo'shilmagan.\n"
    else:
        for d in drivers:
            status_ico = "✅ Faol" if d["is_active"] else "❌ Nofaol"
            text += f"• <b>{d['full_name']}</b> — 📞 {d['phone']} | TG: <code>{d['telegram_id']}</code> [{status_ico}]\n"
            btn_text = f"🛑 Nofaol qilish" if d["is_active"] else f"🟢 Faollashtirish"
            new_status = 0 if d["is_active"] else 1
            rows.append([InlineKeyboardButton(text=f"{btn_text}: {d['full_name']}", callback_data=f"toggle_driver:{d['id']}:{new_status}")])

    rows.append([InlineKeyboardButton(text="➕ Yangi haydovchi qo'shish", callback_data="add_driver_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_driver:"))
async def cb_toggle_driver(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    _, d_id, new_status = callback.data.split(":")
    await db.toggle_driver_status(int(d_id), int(new_status))
    await callback.answer("Haydovchi holati yangilandi!")
    drivers = await db.get_drivers()
    text = "🚗 <b>Haydovchilar ro'yxati:</b>\n\n"
    rows = []
    for d in drivers:
        status_ico = "✅ Faol" if d["is_active"] else "❌ Nofaol"
        text += f"• <b>{d['full_name']}</b> — 📞 {d['phone']} | TG: <code>{d['telegram_id']}</code> [{status_ico}]\n"
        btn_text = f"🛑 Nofaol qilish" if d["is_active"] else f"🟢 Faollashtirish"
        nxt = 0 if d["is_active"] else 1
        rows.append([InlineKeyboardButton(text=f"{btn_text}: {d['full_name']}", callback_data=f"toggle_driver:{d['id']}:{nxt}")])
    rows.append([InlineKeyboardButton(text="➕ Yangi haydovchi qo'shish", callback_data="add_driver_start")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@admin_router.callback_query(F.data == "add_driver_start")
async def cb_add_driver_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(AddDriverStates.waiting_for_tg_id)
    await callback.message.edit_text(
        "➕ <b>Yangi haydovchi qo'shish:</b>\n\n1. Haydovchining Telegram ID sini kiriting (masalan: <code>123456789</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddDriverStates.waiting_for_tg_id)
async def process_driver_tg_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Iltimos, faqat raqamli Telegram ID kiriting:")
        return
    await state.update_data(tg_id=int(text))
    await state.set_state(AddDriverStates.waiting_for_name)
    await message.answer("2. Haydovchining to'liq ism-familiyasini kiriting:")

@admin_router.message(AddDriverStates.waiting_for_name)
async def process_driver_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(AddDriverStates.waiting_for_phone)
    await message.answer("3. Haydovchining telefon raqamini kiriting (masalan: <code>90 123 45 67</code> yoki <code>+998901234567</code>):", parse_mode="HTML")

@admin_router.message(AddDriverStates.waiting_for_phone)
async def process_driver_phone(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    phone_text = message.text.strip()
    norm = normalize_phone(phone_text)
    if not norm:
        await message.answer("❌ Telefon raqami formati noto'g'ri! Iltimos, 9 xonali yoki +998 bilan kiriting:")
        return
    data = await state.get_data()
    driver_id = await db.add_driver(data["tg_id"], data["full_name"], norm)
    await state.clear()
    await message.answer(
        f"✅ <b>Haydovchi muvaffaqiyatli qo'shildi!</b>\n\n"
        f"ID: {driver_id} | Ismi: <b>{data['full_name']}</b>\n"
        f"Telefon: <b>{norm}</b> | TG ID: <code>{data['tg_id']}</code>\n\n"
        f"Ro'yxat: /haydovchilar",
        parse_mode="HTML"
    )

# ============================================================================
# 3. /xodimlar — Xodimlar boshqaruvi
# ============================================================================
@admin_router.message(Command("xodimlar"))
@admin_router.message(F.text == "👥 Xodimlar")
async def cmd_xodimlar(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    staff_list = await db.get_all_staff()
    text = "👥 <b>Xodimlar (Staff) ro'yxati:</b>\n\n"
    rows = []

    if not staff_list:
        text += "Hozircha xodimlar qo'shilmagan.\n"
    else:
        for s in staff_list:
            status_ico = "✅ Faol" if s["is_active"] else "❌ Nofaol"
            phone_str = f" | 📞 {s['phone']}" if s.get("phone") else ""
            text += f"• <b>{s['full_name']}</b>{phone_str} | TG: <code>{s['telegram_id']}</code> [{status_ico}]\n"
            btn_text = f"🛑 Nofaol qilish" if s["is_active"] else f"🟢 Faollashtirish"
            nxt = 0 if s["is_active"] else 1
            rows.append([InlineKeyboardButton(text=f"{btn_text}: {s['full_name']}", callback_data=f"toggle_staff:{s['id']}:{nxt}")])

    rows.append([InlineKeyboardButton(text="➕ Yangi xodim qo'shish", callback_data="add_staff_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_staff:"))
async def cb_toggle_staff(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    _, s_id, new_status = callback.data.split(":")
    await db.toggle_staff_status(int(s_id), int(new_status))
    await callback.answer("Xodim holati yangilandi!")
    staff_list = await db.get_all_staff()
    text = "👥 <b>Xodimlar (Staff) ro'yxati:</b>\n\n"
    rows = []
    for s in staff_list:
        status_ico = "✅ Faol" if s["is_active"] else "❌ Nofaol"
        phone_str = f" | 📞 {s['phone']}" if s.get("phone") else ""
        text += f"• <b>{s['full_name']}</b>{phone_str} | TG: <code>{s['telegram_id']}</code> [{status_ico}]\n"
        btn_text = f"🛑 Nofaol qilish" if s["is_active"] else f"🟢 Faollashtirish"
        nxt = 0 if s["is_active"] else 1
        rows.append([InlineKeyboardButton(text=f"{btn_text}: {s['full_name']}", callback_data=f"toggle_staff:{s['id']}:{nxt}")])
    rows.append([InlineKeyboardButton(text="➕ Yangi xodim qo'shish", callback_data="add_staff_start")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@admin_router.callback_query(F.data == "add_staff_start")
async def cb_add_staff_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(AddStaffStates.waiting_for_tg_id)
    await callback.message.edit_text(
        "➕ <b>Yangi xodim qo'shish:</b>\n\n1. Xodimning Telegram ID sini kiriting (masalan: <code>1122334455</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddStaffStates.waiting_for_tg_id)
async def process_staff_tg_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Iltimos, faqat raqamli Telegram ID kiriting:")
        return
    await state.update_data(tg_id=int(text))
    await state.set_state(AddStaffStates.waiting_for_name)
    await message.answer("2. Xodimning to'liq ism-familiyasini kiriting:")

@admin_router.message(AddStaffStates.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(AddStaffStates.waiting_for_phone)
    await message.answer("3. Xodimning telefon raqamini kiriting (yoki ixtiyoriy bo'lsa 'yo'q' deb yozing):")

@admin_router.message(AddStaffStates.waiting_for_phone)
async def process_staff_phone(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    phone_text = message.text.strip()
    norm = None
    if phone_text.lower() not in ["yo'q", "yoq", "none", "-"]:
        norm = normalize_phone(phone_text)
        if not norm:
            await message.answer("❌ Telefon raqami formati noto'g'ri! Iltimos, qaytadan kiriting (yoki 'yo'q' yozing):")
            return
    data = await state.get_data()
    staff_id = await db.add_staff(data["tg_id"], data["full_name"], norm)
    await state.clear()
    await message.answer(
        f"✅ <b>Xodim muvaffaqiyatli qo'shildi!</b>\n\n"
        f"ID: {staff_id} | Ismi: <b>{data['full_name']}</b>\n"
        f"Telefon: <b>{norm or 'Kiritilmagan'}</b> | TG ID: <code>{data['tg_id']}</code>\n\n"
        f"Ro'yxat: /xodimlar",
        parse_mode="HTML"
    )

# ============================================================================
# 4. /qoshish — O'quvchi qo'shish (FSM)
# ============================================================================
@admin_router.message(Command("qoshish"))
@admin_router.message(F.text == "➕ O'quvchi qo'shish")
async def cmd_qoshish(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return
    await state.set_state(AddStudentStates.waiting_for_name)
    await message.answer("📝 <b>Yangi o'quvchi qo'shish:</b>\n\n1. O'quvchining <b>F.I.SH</b> (Ism Familiyasi)ni kiriting:", parse_mode="HTML")

@admin_router.message(AddStudentStates.waiting_for_name)
async def process_student_name(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    villages = await db.get_villages()
    rows = []
    for v in villages:
        rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['zone_name']})", callback_data=f"sel_vil:{v['id']}")])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(AddStudentStates.waiting_for_village)
    await message.answer("2. O'quvchi qaysi mahallada yashaydi?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@admin_router.callback_query(AddStudentStates.waiting_for_village, F.data.startswith("sel_vil:"))
async def process_student_village(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    village_id = int(callback.data.split(":")[1])
    village = await db.get_village_by_id(village_id)
    await state.update_data(village_id=village_id, village_name=village["name"] if village else "")
    await state.set_state(AddStudentStates.waiting_for_phone_1)
    await callback.message.edit_text(
        f"Tanlandi: <b>{village['name'] if village else ''}</b>\n\n"
        "3. <b>Asosiy ota-ona telefon raqami</b>ni kiriting (masalan: <code>90 123 45 67</code> yoki <code>+998901234567</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddStudentStates.waiting_for_phone_1)
async def process_student_phone_1(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    norm = normalize_phone(message.text.strip())
    if not norm:
        await message.answer("❌ Telefon raqami noto'g'ri! Iltimos, 9 xonali (901234567) yoki +998 bilan kiriting:")
        return
    await state.update_data(parent_phone_1=norm)
    await state.set_state(AddStudentStates.waiting_for_phone_2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="skip_phone_2")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    await message.answer("4. <b>Ikkinchi ota-ona telefoni</b>ni kiriting (ixtiyoriy, masalan onasi):", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(AddStudentStates.waiting_for_phone_2, F.data == "skip_phone_2")
async def process_student_skip_phone_2(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.update_data(parent_phone_2=None)
    await ask_schedule_days(callback.message, state, is_edit=True)
    await callback.answer()

@admin_router.message(AddStudentStates.waiting_for_phone_2)
async def process_student_phone_2(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    norm = normalize_phone(message.text.strip())
    if not norm:
        await message.answer("❌ Telefon raqami noto'g'ri! Iltimos, to'g'ri kiriting yoki quyidagi tugmani bosing:")
        return
    await state.update_data(parent_phone_2=norm)
    await ask_schedule_days(message, state, is_edit=False)

async def ask_schedule_days(target_message: types.Message, state: FSMContext, is_edit: bool = False):
    await state.set_state(AddStudentStates.waiting_for_days)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Dush - Chor - Juma (Toq kunlar)", callback_data="days_mwf")],
        [InlineKeyboardButton(text="📅 Sesh - Pay - Shan (Juft kunlar)", callback_data="days_tts")],
        [InlineKeyboardButton(text="📅 Dush - Shan (Har kuni)", callback_data="days_all")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    text = "5. <b>Dars kunlarini tanlang:</b>"
    if is_edit:
        await target_message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target_message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(AddStudentStates.waiting_for_days, F.data.startswith("days_"))
async def process_student_days(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    mode = callback.data
    if mode == "days_mwf":
        days = [0, 2, 4]
        label = "Dush-Chor-Juma"
    elif mode == "days_tts":
        days = [1, 3, 5]
        label = "Sesh-Pay-Shan"
    else:
        days = [0, 1, 2, 3, 4, 5]
        label = "Dushanba - Shanba"
    await state.update_data(selected_days=days, days_label=label)
    await state.set_state(AddStudentStates.waiting_for_time)
    await callback.message.edit_text(
        f"Kunlar: <b>{label}</b>\n\n"
        "6. Dars vaqtini kiriting (Boshlanish va Tugash vaqti, masalan: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddStudentStates.waiting_for_time)
async def process_student_time(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Noto'g'ri format! Masalan: <code>09:00-11:00</code>", parse_mode="HTML")
        return
    try:
        parts = text.split("-")
        start_time = parts[0].strip()
        end_time = parts[1].strip()
        datetime.strptime(start_time, "%H:%M")
        datetime.strptime(end_time, "%H:%M")
    except ValueError:
        await message.answer("Vaqt noto'g'ri! Masalan: <code>09:00-11:00</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    student_id = await db.add_student(
        full_name=data["full_name"],
        village_id=data["village_id"],
        parent_phone_1=data["parent_phone_1"],
        parent_phone_2=data.get("parent_phone_2")
    )

    for day in data["selected_days"]:
        await db.add_schedule(student_id, day, start_time, end_time)

    await state.clear()
    phone2_str = f"\n2-Telefon: {data.get('parent_phone_2')}" if data.get("parent_phone_2") else ""
    await message.answer(
        f"✅ <b>O'quvchi muvaffaqiyatli saqlandi!</b>\n\n"
        f"👤 F.I.Sh: <b>{data['full_name']}</b>\n"
        f"🏘 Mahalla: <b>{data.get('village_name', '')}</b>\n"
        f"📞 Asosiy telefon: <b>{data['parent_phone_1']}</b>{phone2_str}\n"
        f"📅 Kunlar: <b>{data.get('days_label', '')}</b>\n"
        f"⏱ Dars vaqti: <b>{start_time} - {end_time}</b>",
        parse_mode="HTML"
    )

# ============================================================================
# 5. /ozgartirish — O'quvchi ma'lumotlarini o'zgartirish
# ============================================================================
@admin_router.message(Command("ozgartirish"))
async def cmd_ozgartirish(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    students = await db.get_active_students()
    if not students:
        await message.answer("O'quvchilar ro'yxati bo'sh.")
        return

    kb = build_students_pagination_kb(students, page=0, action_prefix="edit_st")
    await state.set_state(EditStudentStates.selecting_student)
    await message.answer("✏️ Tahrirlash uchun o'quvchini tanlang:", reply_markup=kb)

@admin_router.callback_query(EditStudentStates.selecting_student, F.data.startswith("page_edit_st:"))
async def cb_page_edit_student(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="edit_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(EditStudentStates.selecting_student, F.data.startswith("edit_st:"))
async def cb_select_student_to_edit(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = callback.data.split(":")
    student_id = int(parts[1])
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    await state.update_data(student_id=student_id, student_data=student)
    await state.set_state(EditStudentStates.selecting_field)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ism-familiyani o'zgartirish", callback_data="field_name")],
        [InlineKeyboardButton(text="🏘 Mahallani o'zgartirish", callback_data="field_village")],
        [InlineKeyboardButton(text="📞 Telefon raqamlarni o'zgartirish", callback_data="field_phone")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    p2 = f" | 2: {student['parent_phone_2']}" if student.get("parent_phone_2") else ""
    await callback.message.edit_text(
        f"Tanlandi: <b>{student['full_name']}</b>\n"
        f"Mahalla: <b>{student['village_name']}</b>\n"
        f"Telefon: <b>{student['parent_phone_1']}</b>{p2}\n\n"
        f"Qaysi ma'lumotni o'zgartirmoqchisiz?",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_name")
async def cb_edit_field_name(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(EditStudentStates.waiting_for_new_name)
    await callback.message.edit_text("Yangi to'liq ism-familiyani kiriting:")
    await callback.answer()

@admin_router.message(EditStudentStates.waiting_for_new_name)
async def process_new_student_name(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    new_name = message.text.strip()
    data = await state.get_data()
    st = data["student_data"]
    await db.update_student(st["id"], new_name, st["village_id"], st["parent_phone_1"], st["parent_phone_2"])
    await state.clear()
    await message.answer(f"✅ O'quvchi ismi yangilandi: <b>{new_name}</b>", parse_mode="HTML")

@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_village")
async def cb_edit_field_village(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    villages = await db.get_villages()
    rows = []
    for v in villages:
        rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['zone_name']})", callback_data=f"set_new_vil:{v['id']}")])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(EditStudentStates.waiting_for_new_village)
    await callback.message.edit_text("Yangi mahallani tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@admin_router.callback_query(EditStudentStates.waiting_for_new_village, F.data.startswith("set_new_vil:"))
async def process_new_student_village(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    new_v_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    st = data["student_data"]
    await db.update_student(st["id"], st["full_name"], new_v_id, st["parent_phone_1"], st["parent_phone_2"])
    v = await db.get_village_by_id(new_v_id)
    await state.clear()
    await callback.message.edit_text(f"✅ O'quvchi mahallasi yangilandi: <b>{v['name'] if v else ''}</b>", parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_phone")
async def cb_edit_field_phone(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(EditStudentStates.waiting_for_new_phone1)
    await callback.message.edit_text("Yangi asosiy telefon raqamini kiriting (masalan: <code>90 123 45 67</code>):", parse_mode="HTML")
    await callback.answer()

@admin_router.message(EditStudentStates.waiting_for_new_phone1)
async def process_new_phone1(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    norm = normalize_phone(message.text.strip())
    if not norm:
        await message.answer("❌ Raqam noto'g'ri! Qaytadan kiriting:")
        return
    data = await state.get_data()
    st = data["student_data"]
    await db.update_student(st["id"], st["full_name"], st["village_id"], norm, st["parent_phone_2"])
    await state.clear()
    await message.answer(f"✅ O'quvchi asosiy telefoni yangilandi: <b>{norm}</b>", parse_mode="HTML")

# ============================================================================
# 6. /ochirish — O'quvchini tizimdan nofaol qilish (o'chirish)
# ============================================================================
@admin_router.message(Command("ochirish"))
@admin_router.message(F.text == "🗑 O'quvchini o'chirish")
async def cmd_ochirish(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    students = await db.get_active_students()
    if not students:
        await message.answer("Faol o'quvchilar mavjud emas.")
        return

    kb = build_students_pagination_kb(students, page=0, action_prefix="del_st")
    await state.set_state(DeleteStudentStates.selecting_student)
    await message.answer("🗑 O'chirmoqchi bo'lgan o'quvchini tanlang:", reply_markup=kb)

@admin_router.callback_query(DeleteStudentStates.selecting_student, F.data.startswith("page_del_st:"))
async def cb_page_del_student(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="del_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(DeleteStudentStates.selecting_student, F.data.startswith("del_st:"))
async def cb_confirm_delete_student(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = int(callback.data.split(":")[1])
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    await state.update_data(delete_student_id=student_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Ha, nofaol qilish", callback_data=f"do_del:{student_id}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    await callback.message.edit_text(
        f"Haqiqatan ham <b>{student['full_name']}</b> ({student['village_name']}) ni tizimdan o'chirmoqchimisiz?\n\n"
        "<i>(O'quvchi nofaol qilinadi va yangi reyslarga qo'shilmaydi)</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("do_del:"))
async def cb_execute_delete(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = int(callback.data.split(":")[1])
    await db.deactivate_student(student_id)
    await state.clear()
    await callback.message.edit_text("✅ O'quvchi muvaffaqiyatli nofaol qilindi (o'chirildi).")
    await callback.answer("O'chirildi!")

# ============================================================================
# 7. /jadval — O'quvchi dars jadvalini ko'rish va tahrirlash
# ============================================================================
@admin_router.message(Command("jadval"))
@admin_router.message(F.text == "📅 Dars jadvali")
async def cmd_jadval(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await message.answer("Ushbu buyruq faqat adminlar uchun!")
        return

    students = await db.get_active_students()
    if not students:
        await message.answer("O'quvchilar ro'yxati bo'sh.")
        return

    kb = build_students_pagination_kb(students, page=0, action_prefix="sch_st")
    await state.set_state(EditScheduleStates.selecting_student)
    await message.answer("📅 Jadvalini ko'rish va tahrirlash uchun o'quvchini tanlang:", reply_markup=kb)

@admin_router.callback_query(EditScheduleStates.selecting_student, F.data.startswith("page_sch_st:"))
async def cb_page_sch_student(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="sch_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(EditScheduleStates.selecting_student, F.data.startswith("sch_st:"))
async def cb_show_student_schedule(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = int(callback.data.split(":")[1])
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    schedules = await db.get_student_schedules(student_id)
    await state.update_data(schedule_student_id=student_id, student_name=student["full_name"])

    text = f"📅 <b>{student['full_name']}</b> dars jadvali:\n\n"
    rows = []

    sch_dict = {s["weekday"]: f"{s['start_time']} - {s['end_time']}" for s in schedules}
    for idx, day_name in enumerate(WEEKDAYS_UZ[:6]):
        time_str = sch_dict.get(idx, "Dars yo'q")
        text += f"• <b>{day_name}</b>: {time_str}\n"
        rows.append([InlineKeyboardButton(text=f"✏️ {day_name} ({time_str})", callback_data=f"edit_day_sch:{student_id}:{idx}")])

    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(EditScheduleStates.selecting_weekday)
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(EditScheduleStates.selecting_weekday, F.data.startswith("edit_day_sch:"))
async def cb_select_schedule_day(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = callback.data.split(":")
    student_id = int(parts[1])
    weekday = int(parts[2])
    day_name = WEEKDAYS_UZ[weekday]

    await state.update_data(edit_weekday=weekday)
    await state.set_state(EditScheduleStates.waiting_for_new_time)
    await callback.message.edit_text(
        f"📅 <b>{day_name}</b> kuni uchun yangi vaqtni kiriting:\n\n"
        f"Format: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>:",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(EditScheduleStates.waiting_for_new_time)
async def process_save_schedule_time(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Noto'g'ri format! Masalan: <code>10:00-12:00</code>", parse_mode="HTML")
        return
    try:
        parts = text.split("-")
        start_time = parts[0].strip()
        end_time = parts[1].strip()
        datetime.strptime(start_time, "%H:%M")
        datetime.strptime(end_time, "%H:%M")
    except ValueError:
        await message.answer("Vaqt noto'g'ri! Masalan: <code>10:00-12:00</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    student_id = data["schedule_student_id"]
    weekday = data["edit_weekday"]
    day_name = WEEKDAYS_UZ[weekday]

    await db.update_student_schedule_time(student_id, weekday, start_time, end_time)
    await state.clear()
    await message.answer(
        f"✅ <b>{data.get('student_name', '')}</b> ning <b>{day_name}</b> kungi darsi yangilandi:\n"
        f"⏱ <b>{start_time} - {end_time}</b>\n\n"
        f"Jadvalni ko'rish: /jadval",
        parse_mode="HTML"
    )

# --- Kunlik reyslarni tasdiqlash ---
@admin_router.callback_query(F.data.startswith("admin_approve_trips:"))
async def cb_admin_approve_trips(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    date_str = callback.data.split(":")[1]
    count = await db.approve_all_daily_trips(date_str, callback.from_user.id)
    if count == 0:
        await callback.answer("Reyslar allaqachon tasdiqlangan yoki topilmadi!", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>{date_str}</b> sanasi uchun barcha reyslar ({count} ta) muvaffaqiyatli tasdiqlandi!",
        parse_mode="HTML"
    )
    await callback.answer("Reyslar tasdiqlandi!")

# --- 8-faza: Excel shablon va Ommaviy yuklash ---
PENDING_EXCEL_IMPORTS = {}

@admin_router.message(Command("shablon"))
@admin_router.message(F.text == "📊 Excel shablon")
async def cmd_admin_template(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    import os
    from aiogram.types import FSInputFile
    from services.excel_service import ExcelService

    path = "shablon_oquvchilar.xlsx"
    ExcelService.generate_template(path)
    await message.answer_document(
        FSInputFile(path, filename="shablon_oquvchilar.xlsx"),
        caption=(
            "📊 <b>O'quvchilarni ommaviy yuklash uchun Excel shablon.</b>\n\n"
            "Ustunlar: F.I.Sh | Mahalla | Ota telefoni | Ona telefoni | Dushanba-Shanba dars vaqtlari.\n"
            "Format namunasi: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>."
        ),
        parse_mode="HTML"
    )

@admin_router.message(F.document)
async def process_admin_excel_upload(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    doc = message.document
    if not doc.file_name or not doc.file_name.endswith(".xlsx"):
        return

    import os
    import uuid
    from services.excel_service import ExcelService

    temp_path = f"temp_{uuid.uuid4().hex[:8]}_{doc.file_name}"
    await message.bot.download(doc, destination=temp_path)

    try:
        preview = await ExcelService.parse_and_preview_excel(temp_path, db)
        batch_id = uuid.uuid4().hex[:8]
        PENDING_EXCEL_IMPORTS[batch_id] = preview["valid_records"]

        err_text = ""
        if preview["errors"]:
            err_text = "\n⚠️ <b>Xatolik bor qatorlar (saqlanmaydi):</b>\n"
            for err in preview["errors"][:5]:
                err_text += f"  • {err['row']}-qator ({err['full_name']}): {err['error']}\n"
            if len(preview["errors"]) > 5:
                err_text += f"  <i>... va yana {len(preview['errors']) - 5} ta xato.</i>\n"

        text = (
            f"📊 <b>Fayl tahlili ({doc.file_name}):</b>\n\n"
            f"➕ Yangi o'quvchilar: <b>{preview['new_count']} ta</b>\n"
            f"🔄 Yangilanadiganlar: <b>{preview['update_count']} ta</b>\n"
            f"❌ Xatolik bor qatorlar: <b>{preview['error_count']} ta</b>\n"
            f"{err_text}\n"
            f"To'g'ri qatorlarni bazaga saqlashni tasdiqlaysizmi?"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Bazaga saqlash", callback_data=f"excel_save:{batch_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"excel_cancel:{batch_id}")
            ]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@admin_router.callback_query(F.data.startswith("excel_save:"))
async def cb_excel_save(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    batch_id = callback.data.split(":")[1]
    records = PENDING_EXCEL_IMPORTS.pop(batch_id, None)
    if not records:
        await callback.answer("Ma'lumotlar eskirgan yoki topilmadi!", show_alert=True)
        return

    res = await db.bulk_upsert_students_and_schedules(records)
    await callback.message.edit_text(
        f"✅ <b>Muvaffaqiyatli saqlandi!</b>\n\n"
        f"➕ Yangi qo'shildi: <b>{res['created']} ta</b>\n"
        f"🔄 Yangilandi: <b>{res['updated']} ta</b>",
        parse_mode="HTML"
    )
    await callback.answer("Saqlandi!")

@admin_router.callback_query(F.data.startswith("excel_cancel:"))
async def cb_excel_cancel(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    batch_id = callback.data.split(":")[1]
    PENDING_EXCEL_IMPORTS.pop(batch_id, None)
    await callback.message.edit_text("❌ Excel import bekor qilindi.")
    await callback.answer("Bekor qilindi.")

# --- 9-faza: Kechikish xabari (faqat admin) ---
@admin_router.callback_query(F.data.startswith("admin_delay_trip:"))
async def cb_admin_delay_trip(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    trip_id = int(callback.data.split(":")[1])
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    text = (
        f"⏰ <b>Reys #{trip_id} kechikishi:</b>\n"
        f"Rejalashtirilgan vaqt: <b>{trip['planned_time']}</b>\n\n"
        f"Kechikish vaqtini tanlang:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10 daq", callback_data=f"admin_delay_confirm:{trip_id}:10"),
            InlineKeyboardButton(text="15 daq", callback_data=f"admin_delay_confirm:{trip_id}:15"),
            InlineKeyboardButton(text="20 daq", callback_data=f"admin_delay_confirm:{trip_id}:20"),
        ],
        [
            InlineKeyboardButton(text="30 daq", callback_data=f"admin_delay_confirm:{trip_id}:30"),
            InlineKeyboardButton(text="Boshqa", callback_data=f"admin_delay_custom:{trip_id}")
        ],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(F.data.startswith("admin_delay_custom:"))
async def cb_admin_delay_custom(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    await state.set_state(DelayStates.waiting_for_custom_minutes)
    await state.update_data(delay_trip_id=trip_id)
    await callback.message.edit_text("Kechikish vaqtini daqiqalarda kiriting (masalan: <code>25</code>):", parse_mode="HTML")
    await callback.answer()

@admin_router.message(DelayStates.waiting_for_custom_minutes)
async def process_custom_delay_minutes(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat raqam kiriting (masalan: 25):")
        return
    minutes = int(text)
    data = await state.get_data()
    trip_id = data["delay_trip_id"]
    await state.clear()

    trip = await db.get_trip_details(trip_id)
    villages = ", ".join(list(set(it["village_name"] for it in trip.get("items", []))))
    num_students = len(trip.get("items", []))

    prompt_text = (
        f"Reys: #{trip_id} {trip['planned_time']} ({villages}).\n"
        f"{num_students} ta bolaning ota-onasiga {minutes} daqiqalik kechikish xabari boradi. Yuborilsinmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=f"admin_delay_apply:{trip_id}:{minutes}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")
        ]
    ])
    await message.answer(prompt_text, reply_markup=kb)

@admin_router.callback_query(F.data.startswith("admin_delay_confirm:"))
async def cb_admin_delay_confirm(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = callback.data.split(":")
    trip_id = int(parts[1])
    minutes = int(parts[2])

    trip = await db.get_trip_details(trip_id)
    villages = ", ".join(list(set(it["village_name"] for it in trip.get("items", []))))
    num_students = len(trip.get("items", []))

    prompt_text = (
        f"Reys: #{trip_id} {trip['planned_time']} ({villages}).\n"
        f"{num_students} ta bolaning ota-onasiga {minutes} daqiqalik kechikish xabari boradi. Yuborilsinmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=f"admin_delay_apply:{trip_id}:{minutes}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")
        ]
    ])
    await callback.message.edit_text(prompt_text, reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(F.data.startswith("admin_delay_apply:"))
async def cb_admin_delay_apply(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = callback.data.split(":")
    trip_id = int(parts[1])
    minutes = int(parts[2])

    success, reason, delay_data = await db.apply_trip_delay(trip_id, minutes, callback.from_user.id)
    if not success:
        await callback.answer("Xatolik: reys topilmadi!", show_alert=True)
        return

    # 1. Haydovchiga xabar
    if delay_data.get("driver_tg_id") and callback.bot:
        try:
            await callback.bot.send_message(
                chat_id=delay_data["driver_tg_id"],
                text=f"⚠️ Admin reysni {minutes} daqiqaga kechiktirdi. Yangi rejalashtirilgan vaqt: <b>{delay_data['new_planned_time']}</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # 2. Ota-onalarga xabar (bola olingan va olinmaganiga qarab)
    for it in delay_data.get("items", []):
        if it.get("is_picked_up"):
            parent_msg = f"Farzandingiz mashinada, yetib borish taxminan {minutes} daqiqaga kechikadi."
        else:
            parent_msg = f"Mashina taxminan {minutes} daqiqaga kechikadi. Noqulaylik uchun uzr."

        for p in it.get("parents", []):
            if callback.bot and p.get("telegram_id"):
                try:
                    await callback.bot.send_message(chat_id=p["telegram_id"], text=parent_msg)
                except Exception:
                    pass
            await db.log_notification(parent_id=p["parent_id"], student_id=it["student_id"], text=parent_msg, status="sent")

    await callback.message.edit_text(
        f"✅ <b>Reys #{trip_id}</b> muvaffaqiyatli {minutes} daqiqaga kechiktirildi!\n"
        f"Yangi vaqt: <b>{delay_data['new_planned_time']}</b>\n"
        f"Barcha ota-onalar va haydovchi ogohlantirildi.",
        parse_mode="HTML"
    )
    await callback.answer("Kechikish tasdiqlandi!")

@admin_router.callback_query(F.data == "admin_delay_cancel")
async def cb_admin_delay_cancel(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.message.edit_text("❌ Kechikish bekor qilindi.")
    await callback.answer("Bekor qilindi.")

# --- 9-faza: Admin xabarlari (/xabar, broadcasts) ---
PENDING_BROADCASTS = {}

@admin_router.message(Command("xabar"))
@admin_router.message(F.text == "📢 Xabar yuborish")
async def cmd_admin_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Hammaga", callback_data="bc_aud:all")],
        [InlineKeyboardButton(text="🏘 Mahalla bo'yicha", callback_data="bc_aud:village")],
        [InlineKeyboardButton(text="📍 Zona bo'yicha", callback_data="bc_aud:zone")],
        [InlineKeyboardButton(text="👤 Bitta o'quvchi ota-onasiga", callback_data="bc_aud:student")],
        [InlineKeyboardButton(text="🚗 Haydovchilarga", callback_data="bc_aud:drivers")],
        [InlineKeyboardButton(text="👥 Xodimlarga", callback_data="bc_aud:staff")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="bc_cancel")]
    ])
    await message.answer("📢 <b>Xabar yuborish uchun auditoriyani tanlang:</b>", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("bc_aud:"))
async def cb_broadcast_select_audience(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    aud_type = callback.data.split(":")[1]

    if aud_type == "village":
        villages = await db.get_villages()
        rows = []
        for v in villages:
            rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['school_name']})", callback_data=f"bc_val:village:{v['id']}")])
        rows.append([InlineKeyboardButton(text="⬅️ Ortga", callback_data="bc_cancel")])
        await callback.message.edit_text("🏘 <b>Mahallani tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        await callback.answer()
        return

    elif aud_type == "zone":
        rows = [
            [InlineKeyboardButton(text="A-zona", callback_data="bc_val:zone:1")],
            [InlineKeyboardButton(text="B-zona", callback_data="bc_val:zone:2")],
            [InlineKeyboardButton(text="C-zona", callback_data="bc_val:zone:3")],
            [InlineKeyboardButton(text="D-zona", callback_data="bc_val:zone:4")],
            [InlineKeyboardButton(text="⬅️ Ortga", callback_data="bc_cancel")]
        ]
        await callback.message.edit_text("📍 <b>Zonani tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        await callback.answer()
        return

    elif aud_type == "student":
        await state.set_state(BroadcastStates.waiting_for_value)
        await state.update_data(bc_aud_type="student")
        await callback.message.edit_text("👤 O'quvchi F.I.Sh yoki ID sini kiriting:", parse_mode="HTML")
        await callback.answer()
        return

    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type=aud_type, bc_aud_val=None)
    await callback.message.edit_text("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(F.data.startswith("bc_val:"))
async def cb_broadcast_set_value(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    parts = callback.data.split(":")
    aud_type = parts[1]
    aud_val = parts[2]
    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type=aud_type, bc_aud_val=aud_val)
    await callback.message.edit_text("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")
    await callback.answer()

@admin_router.message(BroadcastStates.waiting_for_value)
async def process_broadcast_student_value(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = message.text.strip()
    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type="student", bc_aud_val=query)
    await message.answer("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")

@admin_router.message(BroadcastStates.waiting_for_content)
async def process_broadcast_content(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    import uuid
    data = await state.get_data()
    aud_type = data["bc_aud_type"]
    aud_val = data.get("bc_aud_val")

    text = message.text or message.caption or ""
    media_id = message.photo[-1].file_id if message.photo else None

    if not text and not media_id:
        await message.answer("Xabar bo'sh bo'lishi mumkin emas!")
        return

    audience = await db.get_broadcast_audience(aud_type, aud_val)
    batch_id = uuid.uuid4().hex[:8]

    PENDING_BROADCASTS[batch_id] = {
        "admin_id": message.from_user.id,
        "audience_type": aud_type,
        "audience_value": aud_val,
        "audience": audience,
        "text": text,
        "media_id": media_id
    }
    await state.clear()

    preview_text = (
        f"📢 <b>Xabar ko'rinishi:</b>\n\n{text}\n\n"
        f"👥 Qabul qiluvchilar soni: <b>{len(audience)} kishi</b>.\n\n"
        f"Xabarni tarqatishni tasdiqlaysizmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Yuborish", callback_data=f"bc_send:{batch_id}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"bc_cancel_batch:{batch_id}")
        ]
    ])
    if media_id:
        await message.answer_photo(photo=media_id, caption=preview_text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(preview_text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("bc_send:"))
async def cb_broadcast_send(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    import asyncio
    batch_id = callback.data.split(":")[1]
    bc_data = PENDING_BROADCASTS.pop(batch_id, None)
    if not bc_data:
        await callback.answer("Xabar eskirgan yoki topilmadi!", show_alert=True)
        return

    audience = bc_data["audience"]
    text = bc_data["text"]
    media_id = bc_data["media_id"]

    await callback.message.edit_text("⏳ Xabar tarqatilmoqda, iltimos kuting...")

    sent = 0
    failed = 0
    for recipient in audience:
        tg_id = recipient.get("telegram_id")
        if not tg_id or not callback.bot:
            failed += 1
            continue
        try:
            if media_id:
                await callback.bot.send_photo(chat_id=tg_id, photo=media_id, caption=text, parse_mode="HTML")
            else:
                await callback.bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await db.log_broadcast(
        admin_id=callback.from_user.id,
        audience_type=bc_data["audience_type"],
        audience_value=bc_data["audience_value"],
        text=text,
        media=media_id,
        total=len(audience),
        sent=sent,
        failed=failed
    )

    await callback.message.edit_text(
        f"📊 <b>Xabar tarqatish yakunlandi:</b>\n\n"
        f"✅ Yuborildi: <b>{sent} ta</b>\n"
        f"❌ Yetmadi (bloklagan/kirmagan): <b>{failed} ta</b>\n"
        f"Jami auditoriya: <b>{len(audience)} ta</b>",
        parse_mode="HTML"
    )
    await callback.answer("Tarqatildi!")

@admin_router.callback_query(F.data == "bc_cancel")
@admin_router.callback_query(F.data.startswith("bc_cancel_batch:"))
async def cb_broadcast_cancel(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Xabar yuborish bekor qilindi.")
    await callback.answer("Bekor qilindi.")
