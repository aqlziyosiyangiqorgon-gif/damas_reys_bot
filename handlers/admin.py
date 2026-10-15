from utils.clock import now_tz
import os
import time
from datetime import datetime
from typing import Optional, List
from sqlite3 import IntegrityError
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import settings
from database.db import Database, normalize_phone
from models.callbacks import (
    AdminAssignTripCallback, AdminDelayApplyCallback, AdminDelayConfirmCallback,
    AdminDelayCustomCallback, AdminDelayTripCallback, AdminSetTripDriverCallback,
    ApproveTripCallback, ApproveTripsCallback, CancelTripCallback,
    BroadcastAudienceCallback, BroadcastCancelBatchCallback, BroadcastSendCallback,
    BroadcastStudentCallback, BroadcastValueCallback, PageBroadcastStudentCallback,
    AdminNavigationCallback,
    AssignStudentDriverCallback, DeactivateStudentCallback, DeleteStudentSelectCallback,
    EditScheduleDayCallback, EditStudentSelectCallback, EditVillageCallback,
    ExcelCancelCallback, ExcelSaveCallback,
    PageDeleteStudentCallback, PageEditStudentCallback, PageScheduleStudentCallback,
    ScheduleStudentSelectCallback, SelectVillageCallback, SetNewVillageCallback,
    SetStudentDriverCallback, ToggleDriverCallback, ToggleStaffCallback,
)
from utils.text import escape_html
from utils.time_validation import validate_schedule_times
from utils.telegram import safe_telegram_call
from services.scheduler_service import SchedulerService
from services.notify_service import NotifyService
from services.telegram_safe import safe_send, safe_send_photo

admin_router = Router()
admin_router.message.filter(F.from_user.id.in_(settings.admin_ids_list))
admin_router.callback_query.filter(F.from_user.id.in_(settings.admin_ids_list))

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids_list

def get_admin_menu_kb() -> ReplyKeyboardMarkup:
    """Admin uchun doimiy pastki Reply klaviatura."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📤 O'quvchilar ro'yxatini Excel"),
        KeyboardButton(text="📥 Excel boshqaruvi")
    )
    builder.row(
        KeyboardButton(text="🚐 Bugungi reyslar"),
        KeyboardButton(text="📊 Statistika / Hisobot")
    )
    builder.row(
        KeyboardButton(text="📢 Xabar yuborish"),
        KeyboardButton(text="👥 Foydalanuvchilar")
    )
    builder.row(KeyboardButton(text="🚫 Bugun kelmaganlar"), KeyboardButton(text="🧩 Bugungi reyslarni tuzish"))
    return builder.as_markup(resize_keyboard=True)

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


async def _edit_broadcast_message(message: types.Message, text: str) -> bool:
    """Edit broadcast preview/status correctly for text and photo messages."""
    photos = getattr(message, "photo", None)
    if isinstance(photos, (list, tuple)) and photos:
        return await safe_telegram_call(message.edit_caption, caption=text, parse_mode="HTML")
    return await safe_telegram_call(message.edit_text, text=text, parse_mode="HTML")

# --- Helper: Student Pagination Keyboard ---
def build_students_pagination_kb(students: list, page: int, action_prefix: str) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(students) + PAGE_SIZE - 1) // PAGE_SIZE)
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_students = students[start_idx:end_idx]

    rows = []
    for s in page_students:
        v_name = s.get("village_name", "")
        if action_prefix == "edit_st":
            callback = EditStudentSelectCallback(student_id=s["id"], page=page).pack()
        elif action_prefix == "del_st":
            callback = DeleteStudentSelectCallback(student_id=s["id"], page=page).pack()
        elif action_prefix == "sch_st":
            callback = ScheduleStudentSelectCallback(student_id=s["id"], page=page).pack()
        elif action_prefix == "bc_student":
            callback = BroadcastStudentCallback(student_id=s["id"], page=page).pack()
        else:
            callback = f"{action_prefix}:{s['id']}:{page}"
        rows.append([InlineKeyboardButton(text=f"👤 {s['full_name']} ({v_name})", callback_data=callback)])

    nav_row = []
    if page > 0:
        page_value = page - 1
        if action_prefix == "edit_st":
            page_callback = PageEditStudentCallback(page=page_value).pack()
        elif action_prefix == "del_st":
            page_callback = PageDeleteStudentCallback(page=page_value).pack()
        elif action_prefix == "sch_st":
            page_callback = PageScheduleStudentCallback(page=page_value).pack()
        elif action_prefix == "bc_student":
            page_callback = PageBroadcastStudentCallback(page=page_value).pack()
        else:
            page_callback = f"page_{action_prefix}:{page_value}"
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=page_callback))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        page_value = page + 1
        if action_prefix == "edit_st":
            page_callback = PageEditStudentCallback(page=page_value).pack()
        elif action_prefix == "del_st":
            page_callback = PageDeleteStudentCallback(page=page_value).pack()
        elif action_prefix == "sch_st":
            page_callback = PageScheduleStudentCallback(page=page_value).pack()
        elif action_prefix == "bc_student":
            page_callback = PageBroadcastStudentCallback(page=page_value).pack()
        else:
            page_callback = f"page_{action_prefix}:{page_value}"
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=page_callback))
    
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

    text, kb = await render_villages(db)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def render_villages(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    """Build the villages view independently from the incoming update type."""
    villages = await db.get_villages()
    text = "🏘 <b>Mahallalar va marshrut zonalari:</b>\n\n"
    current_zone = None
    rows = []

    for v in villages:
        if v["zone_name"] != current_zone:
            current_zone = v["zone_name"]
            text += f"\n📍 <b>{escape_html(current_zone)}:</b>\n"
        pickup = f" (To'xtash: {v['pickup_point']})" if v.get("pickup_point") else ""
        text += f"  {v['route_order']}. <b>{escape_html(v['name'])}</b> ({escape_html(v.get('school_name', '-'))}) — ⏱ {v['travel_minutes']} daq{escape_html(pickup)}\n"
        rows.append([InlineKeyboardButton(text=f"✏️ {v['name']} ({v['zone_name']})", callback_data=EditVillageCallback(village_id=v["id"]).pack())])

    text += "\n<i>Tahrirlash uchun mahallani tanlang:</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb

@admin_router.callback_query(EditVillageCallback.filter())
async def cb_edit_village_start(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: EditVillageCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    v_id = (callback_data or EditVillageCallback.unpack(callback.data)).village_id
    village = await db.get_village_by_id(v_id)
    if not village:
        await callback.answer("Mahalla topilmadi!", show_alert=True)
        return

    await state.update_data(editing_village_id=v_id, orig_village=village)
    await state.set_state(EditVillageStates.waiting_for_name)
    await callback.message.edit_text(
        f"✏️ <b>{escape_html(village['name'])} ({escape_html(village['zone_name'])}) tahrirlash</b>\n\n"
        f"1. Yangi mahalla nomini kiriting (hozirgi: <code>{escape_html(village['name'])}</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(EditVillageStates.waiting_for_name, F.text)
async def process_village_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_name = message.text.strip()
    await state.update_data(new_name=new_name)
    await state.set_state(EditVillageStates.waiting_for_school)
    await message.answer("2. Maktab nomini kiriting (masalan: <code>4-maktab</code>):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_school, F.text)
async def process_village_school(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    school_name = message.text.strip()
    await state.update_data(school_name=school_name)
    await state.set_state(EditVillageStates.waiting_for_travel_minutes)
    await message.answer("3. Markazgacha bo'lgan yo'l vaqtini daqiqada kiriting (masalan: <code>25</code>):", parse_mode="HTML")

@admin_router.message(EditVillageStates.waiting_for_travel_minutes, F.text)
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

@admin_router.message(EditVillageStates.waiting_for_route_order, F.text)
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

@admin_router.message(EditVillageStates.waiting_for_pickup_point, F.text)
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
        f"Nomi: <b>{escape_html(data['new_name'])}</b> ({escape_html(data['school_name'])})\n"
        f"Yo'l vaqti: <b>{data['travel_minutes']} daqiqa</b>\n"
        f"Tartibi: <b>{data['route_order']}</b>\n"
        f"To'planish nuqtasi: <b>{escape_html(pickup_point)}</b>\n\n"
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

    text, kb = await render_drivers(db)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def render_drivers(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    """Build the drivers view independently from the incoming update type."""
    drivers = await db.get_drivers()
    text = "🚗 <b>Haydovchilar ro'yxati:</b>\n\n"
    rows = []

    if not drivers:
        text += "Hozircha haydovchilar qo'shilmagan.\n"
    else:
        for d in drivers:
            status_ico = "✅ Faol" if d["is_active"] else "❌ Nofaol"
            text += f"• <b>{escape_html(d['full_name'])}</b> — 📞 {escape_html(d['phone'])} | TG: <code>{d['telegram_id']}</code> [{status_ico}]\n"
            btn_text = f"🛑 Nofaol qilish" if d["is_active"] else f"🟢 Faollashtirish"
            new_status = 0 if d["is_active"] else 1
            rows.append([InlineKeyboardButton(text=f"{btn_text}: {d['full_name']}", callback_data=ToggleDriverCallback(driver_id=d["id"], active=new_status).pack())])

    rows.append([InlineKeyboardButton(text="➕ Yangi haydovchi qo'shish", callback_data="add_driver_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb

@admin_router.callback_query(ToggleDriverCallback.filter())
async def cb_toggle_driver(callback: types.CallbackQuery, db: Database, callback_data: ToggleDriverCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = callback_data or ToggleDriverCallback.unpack(callback.data)
    await db.toggle_driver_status(parsed.driver_id, parsed.active)
    await callback.answer("Haydovchi holati yangilandi!")
    drivers = await db.get_drivers()
    text = "🚗 <b>Haydovchilar ro'yxati:</b>\n\n"
    rows = []
    for d in drivers:
        status_ico = "✅ Faol" if d["is_active"] else "❌ Nofaol"
        text += f"• <b>{escape_html(d['full_name'])}</b> — 📞 {escape_html(d['phone'])} | TG: <code>{d['telegram_id']}</code> [{status_ico}]\n"
        btn_text = f"🛑 Nofaol qilish" if d["is_active"] else f"🟢 Faollashtirish"
        nxt = 0 if d["is_active"] else 1
        rows.append([InlineKeyboardButton(text=f"{btn_text}: {d['full_name']}", callback_data=ToggleDriverCallback(driver_id=d["id"], active=nxt).pack())])
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

@admin_router.message(AddDriverStates.waiting_for_tg_id, F.text)
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

@admin_router.message(AddDriverStates.waiting_for_name, F.text)
async def process_driver_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(AddDriverStates.waiting_for_phone)
    await message.answer("3. Haydovchining telefon raqamini kiriting (masalan: <code>90 123 45 67</code> yoki <code>+998901234567</code>):", parse_mode="HTML")

@admin_router.message(AddDriverStates.waiting_for_phone, F.text)
async def process_driver_phone(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    phone_text = message.text.strip()
    norm = normalize_phone(phone_text)
    if not norm:
        await message.answer("❌ Telefon raqami formati noto'g'ri! Iltimos, 9 xonali yoki +998 bilan kiriting:")
        return
    data = await state.get_data()
    try:
        driver_id = await db.add_driver(data["tg_id"], data["full_name"], norm)
    except IntegrityError:
        await state.clear()
        await message.answer("Bu Telegram ID allaqachon ro'yxatda.")
        return
    await state.clear()
    await message.answer(
        f"✅ <b>Haydovchi muvaffaqiyatli qo'shildi!</b>\n\n"
        f"ID: {driver_id} | Ismi: <b>{escape_html(data['full_name'])}</b>\n"
        f"Telefon: <b>{escape_html(norm)}</b> | TG ID: <code>{data['tg_id']}</code>\n\n"
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

    text, kb = await render_staff(db)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def render_staff(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    """Build the staff view independently from the incoming update type."""
    staff_list = await db.get_all_staff()
    text = "👥 <b>Xodimlar (Staff) ro'yxati:</b>\n\n"
    rows = []

    if not staff_list:
        text += "Hozircha xodimlar qo'shilmagan.\n"
    else:
        for s in staff_list:
            status_ico = "✅ Faol" if s["is_active"] else "❌ Nofaol"
            phone_str = f" | 📞 {s['phone']}" if s.get("phone") else ""
            text += f"• <b>{escape_html(s['full_name'])}</b>{escape_html(phone_str)} | TG: <code>{s['telegram_id']}</code> [{status_ico}]\n"
            btn_text = f"🛑 Nofaol qilish" if s["is_active"] else f"🟢 Faollashtirish"
            nxt = 0 if s["is_active"] else 1
            rows.append([InlineKeyboardButton(text=f"{btn_text}: {s['full_name']}", callback_data=ToggleStaffCallback(staff_id=s["id"], active=nxt).pack())])

    rows.append([InlineKeyboardButton(text="➕ Yangi xodim qo'shish", callback_data="add_staff_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb

@admin_router.callback_query(ToggleStaffCallback.filter())
async def cb_toggle_staff(callback: types.CallbackQuery, db: Database, callback_data: ToggleStaffCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = callback_data or ToggleStaffCallback.unpack(callback.data)
    await db.toggle_staff_status(parsed.staff_id, parsed.active)
    await callback.answer("Xodim holati yangilandi!")
    staff_list = await db.get_all_staff()
    text = "👥 <b>Xodimlar (Staff) ro'yxati:</b>\n\n"
    rows = []
    for s in staff_list:
        status_ico = "✅ Faol" if s["is_active"] else "❌ Nofaol"
        phone_str = f" | 📞 {s['phone']}" if s.get("phone") else ""
        text += f"• <b>{escape_html(s['full_name'])}</b>{escape_html(phone_str)} | TG: <code>{s['telegram_id']}</code> [{status_ico}]\n"
        btn_text = f"🛑 Nofaol qilish" if s["is_active"] else f"🟢 Faollashtirish"
        nxt = 0 if s["is_active"] else 1
        rows.append([InlineKeyboardButton(text=f"{btn_text}: {s['full_name']}", callback_data=ToggleStaffCallback(staff_id=s["id"], active=nxt).pack())])
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

@admin_router.message(AddStaffStates.waiting_for_tg_id, F.text)
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

@admin_router.message(AddStaffStates.waiting_for_name, F.text)
async def process_staff_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(AddStaffStates.waiting_for_phone)
    await message.answer("3. Xodimning telefon raqamini kiriting (yoki ixtiyoriy bo'lsa 'yo'q' deb yozing):")

@admin_router.message(AddStaffStates.waiting_for_phone, F.text)
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
    try:
        staff_id = await db.add_staff(data["tg_id"], data["full_name"], norm)
    except IntegrityError:
        await state.clear()
        await message.answer("Bu Telegram ID allaqachon ro'yxatda.")
        return
    await state.clear()
    await message.answer(
        f"✅ <b>Xodim muvaffaqiyatli qo'shildi!</b>\n\n"
        f"ID: {staff_id} | Ismi: <b>{escape_html(data['full_name'])}</b>\n"
        f"Telefon: <b>{escape_html(norm or 'Kiritilmagan')}</b> | TG ID: <code>{data['tg_id']}</code>\n\n"
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

@admin_router.message(AddStudentStates.waiting_for_name, F.text)
async def process_student_name(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(full_name=message.text.strip())
    villages = await db.get_villages()
    rows = []
    for v in villages:
        rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['zone_name']})", callback_data=SelectVillageCallback(village_id=v["id"]).pack())])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(AddStudentStates.waiting_for_village)
    await message.answer("2. O'quvchi qaysi mahallada yashaydi?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@admin_router.callback_query(AddStudentStates.waiting_for_village, SelectVillageCallback.filter())
async def process_student_village(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: SelectVillageCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    village_id = (callback_data or SelectVillageCallback.unpack(callback.data)).village_id
    village = await db.get_village_by_id(village_id)
    await state.update_data(village_id=village_id, village_name=village["name"] if village else "")
    await state.set_state(AddStudentStates.waiting_for_phone_1)
    await callback.message.edit_text(
        f"Tanlandi: <b>{escape_html(village['name'] if village else '')}</b>\n\n"
        "3. <b>Asosiy ota-ona telefon raqami</b>ni kiriting (masalan: <code>90 123 45 67</code> yoki <code>+998901234567</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddStudentStates.waiting_for_phone_1, F.text)
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

@admin_router.message(AddStudentStates.waiting_for_phone_2, F.text)
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
        f"Kunlar: <b>{escape_html(label)}</b>\n\n"
        "6. Dars vaqtini kiriting (Boshlanish va Tugash vaqti, masalan: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>):",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(AddStudentStates.waiting_for_time, F.text)
async def process_student_time(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Noto'g'ri format! Masalan: <code>09:00-11:00</code>", parse_mode="HTML")
        return
    try:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError("invalid interval")
        start_time, end_time = (part.strip() for part in parts)
        validate_schedule_times(start_time, end_time)
    except ValueError:
        await message.answer("Vaqt noto'g'ri. HH:MM formatini kiriting va boshlanish tugashdan oldin bo'lsin. Masalan: <code>09:00-11:00</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    try:
        student_id = await db.add_student_with_schedules(
            student_fields={
                "full_name": data["full_name"],
                "village_id": data["village_id"],
                "parent_phone_1": data["parent_phone_1"],
                "parent_phone_2": data.get("parent_phone_2"),
            },
            schedules=[
                {"weekday": day, "start_time": start_time, "end_time": end_time}
                for day in data["selected_days"]
            ],
        )
    except IntegrityError:
        await state.clear()
        await message.answer("Bu o'quvchi allaqachon ro'yxatda.")
        return

    await state.clear()
    phone2_str = f"\n2-Telefon: {data.get('parent_phone_2')}" if data.get("parent_phone_2") else ""
    assign_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Haydovchi biriktirish", callback_data=AssignStudentDriverCallback(student_id=student_id).pack())]
    ])
    await message.answer(
        f"✅ <b>O'quvchi muvaffaqiyatli saqlandi!</b>\n\n"
        f"👤 F.I.Sh: <b>{escape_html(data['full_name'])}</b>\n"
        f"🏘 Mahalla: <b>{escape_html(data.get('village_name', ''))}</b>\n"
        f"📞 Asosiy telefon: <b>{escape_html(data['parent_phone_1'])}</b>{escape_html(phone2_str)}\n"
        f"📅 Kunlar: <b>{escape_html(data.get('days_label', ''))}</b>\n"
        f"⏱ Dars vaqti: <b>{escape_html(start_time)} - {escape_html(end_time)}</b>\n\n"
        f"Haydovchi biriktirilmasa, reys tuzilganda qishloq haydovchisi yoki yagona faol haydovchi ishlatiladi.",
        reply_markup=assign_kb,
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

@admin_router.callback_query(EditStudentStates.selecting_student, PageEditStudentCallback.filter())
async def cb_page_edit_student(callback: types.CallbackQuery, db: Database, callback_data: PageEditStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = (callback_data or PageEditStudentCallback.unpack(callback.data)).page
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="edit_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(EditStudentStates.selecting_student, EditStudentSelectCallback.filter())
async def cb_select_student_to_edit(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: EditStudentSelectCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = (callback_data or EditStudentSelectCallback.unpack(callback.data)).student_id
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    await state.update_data(student_id=student_id, student_data=student)
    await state.set_state(EditStudentStates.selecting_field)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ism-familiyani o'zgartirish", callback_data="field_name")],
        [InlineKeyboardButton(text="🏘 Mahallani o'zgartirish", callback_data="field_village")],
        [InlineKeyboardButton(text="🚗 Haydovchini biriktirish", callback_data="field_driver")],
        [InlineKeyboardButton(text="📞 Telefon raqamlarni o'zgartirish", callback_data="field_phone")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    p2 = f" | 2: {student['parent_phone_2']}" if student.get("parent_phone_2") else ""
    await callback.message.edit_text(
        f"Tanlandi: <b>{escape_html(student['full_name'])}</b>\n"
        f"Mahalla: <b>{escape_html(student['village_name'])}</b>\n"
        f"Haydovchi: <b>{escape_html(student.get('assigned_driver_name') or 'Avtomatik biriktirish')}</b>\n"
        f"Telefon: <b>{escape_html(student['parent_phone_1'])}</b>{escape_html(p2)}\n\n"
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

@admin_router.message(EditStudentStates.waiting_for_new_name, F.text)
async def process_new_student_name(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    new_name = message.text.strip()
    data = await state.get_data()
    st = data["student_data"]
    await db.update_student(st["id"], new_name, st["village_id"], st["parent_phone_1"], st["parent_phone_2"])
    await state.clear()
    await message.answer(f"✅ O'quvchi ismi yangilandi: <b>{escape_html(new_name)}</b>", parse_mode="HTML")

@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_village")
async def cb_edit_field_village(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    villages = await db.get_villages()
    rows = []
    for v in villages:
        rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['zone_name']})", callback_data=SetNewVillageCallback(village_id=v["id"]).pack())])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(EditStudentStates.waiting_for_new_village)
    await callback.message.edit_text("Yangi mahallani tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def show_student_driver_choices(message: types.Message, student: dict, db: Database, *, edit: bool = False):
    drivers = [driver for driver in await db.get_drivers() if driver["is_active"]]
    rows = [
        [InlineKeyboardButton(text="♻️ Avtomatik (qishloq / yagona haydovchi)",
                              callback_data=SetStudentDriverCallback(student_id=student["id"], driver_id="none").pack())]
    ]
    for driver in drivers:
        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {driver['full_name']}",
                callback_data=SetStudentDriverCallback(student_id=student["id"], driver_id=driver["id"]).pack(),
            )
        ])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    prompt = "O'quvchiga biriktiriladigan faol haydovchini tanlang:"
    if not drivers:
        prompt = "Faol haydovchi topilmadi. Avval /haydovchilar bo'limidan haydovchi qo'shing."
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await message.edit_text(prompt, reply_markup=keyboard)
    else:
        await message.answer(prompt, reply_markup=keyboard)


@admin_router.callback_query(AssignStudentDriverCallback.filter())
async def cb_assign_new_student_driver(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: AssignStudentDriverCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = (callback_data or AssignStudentDriverCallback.unpack(callback.data)).student_id
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi", show_alert=True)
        return
    await state.update_data(student_id=student_id, student_data=student)
    await state.set_state(EditStudentStates.selecting_field)
    await show_student_driver_choices(callback.message, student, db, edit=True)
    await callback.answer()


@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_driver")
async def cb_edit_field_driver(callback: types.CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    data = await state.get_data()
    student = data.get("student_data")
    if not student:
        await callback.answer("O'quvchi tanlanmagan", show_alert=True)
        return
    await show_student_driver_choices(callback.message, student, db, edit=True)
    await callback.answer()


@admin_router.callback_query(EditStudentStates.selecting_field, SetStudentDriverCallback.filter())
async def cb_save_student_driver(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: SetStudentDriverCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = callback_data or SetStudentDriverCallback.unpack(callback.data)
    student_id = parsed.student_id
    driver_id = None if parsed.driver_id == "none" else parsed.driver_id
    if not await db.set_student_driver(student_id, driver_id):
        await callback.answer("O'quvchi yoki faol haydovchi topilmadi", show_alert=True)
        return
    await state.clear()
    if driver_id is None:
        status = "Avtomatik biriktirish yoqildi (qishloq haydovchisi yoki yagona faol haydovchi)."
    else:
        driver = await db.get_driver_by_id(driver_id)
        status = f"Haydovchi biriktirildi: {escape_html(driver['full_name'] if driver else '')}"
    await callback.message.edit_text(f"✅ {status}\nYangi reyslar qayta tuzilganda qo'llanadi.", parse_mode="HTML")
    await callback.answer("Saqlandi")

@admin_router.callback_query(EditStudentStates.waiting_for_new_village, SetNewVillageCallback.filter())
async def process_new_student_village(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: SetNewVillageCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    new_v_id = (callback_data or SetNewVillageCallback.unpack(callback.data)).village_id
    data = await state.get_data()
    st = data["student_data"]
    await db.update_student(st["id"], st["full_name"], new_v_id, st["parent_phone_1"], st["parent_phone_2"])
    v = await db.get_village_by_id(new_v_id)
    await state.clear()
    await callback.message.edit_text(f"✅ O'quvchi mahallasi yangilandi: <b>{escape_html(v['name'] if v else '')}</b>", parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(EditStudentStates.selecting_field, F.data == "field_phone")
async def cb_edit_field_phone(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(EditStudentStates.waiting_for_new_phone1)
    await callback.message.edit_text("Yangi asosiy telefon raqamini kiriting (masalan: <code>90 123 45 67</code>):", parse_mode="HTML")
    await callback.answer()

@admin_router.message(EditStudentStates.waiting_for_new_phone1, F.text)
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
    await message.answer(f"✅ O'quvchi asosiy telefoni yangilandi: <b>{escape_html(norm)}</b>", parse_mode="HTML")

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

@admin_router.callback_query(DeleteStudentStates.selecting_student, PageDeleteStudentCallback.filter())
async def cb_page_del_student(callback: types.CallbackQuery, db: Database, callback_data: PageDeleteStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = (callback_data or PageDeleteStudentCallback.unpack(callback.data)).page
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="del_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(DeleteStudentStates.selecting_student, DeleteStudentSelectCallback.filter())
async def cb_confirm_delete_student(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: DeleteStudentSelectCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = (callback_data or DeleteStudentSelectCallback.unpack(callback.data)).student_id
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    await state.update_data(delete_student_id=student_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Ha, nofaol qilish", callback_data=DeactivateStudentCallback(student_id=student_id).pack())],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")]
    ])
    await callback.message.edit_text(
        f"Haqiqatan ham <b>{escape_html(student['full_name'])}</b> ({escape_html(student['village_name'])}) ni tizimdan o'chirmoqchimisiz?\n\n"
        "<i>(O'quvchi nofaol qilinadi va yangi reyslarga qo'shilmaydi)</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.callback_query(DeactivateStudentCallback.filter())
async def cb_execute_delete(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: DeactivateStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = (callback_data or DeactivateStudentCallback.unpack(callback.data)).student_id
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

@admin_router.callback_query(EditScheduleStates.selecting_student, PageScheduleStudentCallback.filter())
async def cb_page_sch_student(callback: types.CallbackQuery, db: Database, callback_data: PageScheduleStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    page = (callback_data or PageScheduleStudentCallback.unpack(callback.data)).page
    students = await db.get_active_students()
    kb = build_students_pagination_kb(students, page=page, action_prefix="sch_st")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(EditScheduleStates.selecting_student, ScheduleStudentSelectCallback.filter())
async def cb_show_student_schedule(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: ScheduleStudentSelectCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    student_id = (callback_data or ScheduleStudentSelectCallback.unpack(callback.data)).student_id
    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    schedules = await db.get_student_schedules(student_id)
    await state.update_data(schedule_student_id=student_id, student_name=student["full_name"])

    text = f"📅 <b>{escape_html(student['full_name'])}</b> dars jadvali:\n\n"
    rows = []

    sch_dict = {s["weekday"]: f"{s['start_time']} - {s['end_time']}" for s in schedules}
    for idx, day_name in enumerate(WEEKDAYS_UZ[:6]):
        time_str = sch_dict.get(idx, "Dars yo'q")
        text += f"• <b>{day_name}</b>: {escape_html(time_str)}\n"
        rows.append([InlineKeyboardButton(text=f"✏️ {day_name} ({time_str})", callback_data=EditScheduleDayCallback(student_id=student_id, weekday=idx).pack())])

    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await state.set_state(EditScheduleStates.selecting_weekday)
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(EditScheduleStates.selecting_weekday, EditScheduleDayCallback.filter())
async def cb_select_schedule_day(callback: types.CallbackQuery, state: FSMContext, callback_data: EditScheduleDayCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = callback_data or EditScheduleDayCallback.unpack(callback.data)
    student_id, weekday = parsed.student_id, parsed.weekday
    day_name = WEEKDAYS_UZ[weekday]

    await state.update_data(edit_weekday=weekday)
    await state.set_state(EditScheduleStates.waiting_for_new_time)
    await callback.message.edit_text(
        f"📅 <b>{escape_html(day_name)}</b> kuni uchun yangi vaqtni kiriting:\n\n"
        f"Format: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>:",
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.message(EditScheduleStates.waiting_for_new_time, F.text)
async def process_save_schedule_time(message: types.Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Noto'g'ri format! Masalan: <code>10:00-12:00</code>", parse_mode="HTML")
        return
    try:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError("invalid interval")
        start_time, end_time = (part.strip() for part in parts)
        validate_schedule_times(start_time, end_time)
    except ValueError:
        await message.answer("Vaqt noto'g'ri. HH:MM formatini kiriting va boshlanish tugashdan oldin bo'lsin. Masalan: <code>10:00-12:00</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    student_id = data["schedule_student_id"]
    weekday = data["edit_weekday"]
    day_name = WEEKDAYS_UZ[weekday]

    await db.update_student_schedule_time(student_id, weekday, start_time, end_time)
    await state.clear()
    await message.answer(
        f"✅ <b>{escape_html(data.get('student_name', ''))}</b> ning <b>{escape_html(day_name)}</b> kungi darsi yangilandi:\n"
        f"⏱ <b>{escape_html(start_time)} - {escape_html(end_time)}</b>\n\n"
        f"Jadvalni ko'rish: /jadval",
        parse_mode="HTML"
    )

# --- Kunlik reyslarni tasdiqlash ---
@admin_router.callback_query(ApproveTripsCallback.filter())
async def cb_admin_approve_trips(callback: types.CallbackQuery, db: Database, callback_data: ApproveTripsCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    date_str = (callback_data or ApproveTripsCallback.unpack(callback.data)).date
    count = await db.approve_all_daily_trips(date_str, callback.from_user.id)
    if count == 0:
        await callback.answer("Reyslar allaqachon tasdiqlangan yoki topilmadi!", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>{date_str}</b> sanasi uchun barcha reyslar ({count} ta) muvaffaqiyatli tasdiqlandi!",
        parse_mode="HTML"
    )
    await callback.answer("Reyslar tasdiqlandi!")


@admin_router.callback_query(ApproveTripCallback.filter())
async def cb_admin_approve_trip(callback: types.CallbackQuery, db: Database, callback_data: ApproveTripCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    trip_id = (callback_data or ApproveTripCallback.unpack(callback.data)).trip_id
    if not await db.approve_trip(trip_id, callback.from_user.id):
        await callback.answer("Reys allaqachon tasdiqlangan yoki holati o'zgargan!", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ <b>Reys #{trip_id}</b> tasdiqlandi.", parse_mode="HTML"
    )
    await callback.answer("Reys tasdiqlandi!")

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

    path = ExcelService.generate_template()
    try:
        await message.answer_document(
            FSInputFile(path, filename=os.path.basename(path)),
            caption=(
                "📊 <b>O'quvchilarni ommaviy yuklash uchun Excel shablon.</b>\n\n"
                "Ustunlar: F.I.Sh | Mahalla | Ota telefoni | Ona telefoni | Dushanba-Shanba dars vaqtlari.\n"
                "Format namunasi: <code>09:00-11:00</code> yoki <code>14:00-16:00</code>."
            ),
            parse_mode="HTML"
        )
    finally:
        if os.path.exists(path):
            os.remove(path)

@admin_router.message(F.document)
async def process_admin_excel_upload(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    doc = message.document
    extension = os.path.splitext(doc.file_name or "")[1].lower()
    if extension not in {".xlsx", ".xls"}:
        await message.answer(
            "Excel fayl .xlsx yoki .xls formatida bo'lishi kerak. "
            "Bot shablonini to'ldirib, faylni hujjat sifatida qayta yuboring."
        )
        return
    if getattr(doc, "file_size", None) is not None and doc.file_size > 10 * 1024 * 1024:
        await message.answer("Excel fayl hajmi 10 MB dan oshmasligi kerak.")
        return

    import tempfile
    import uuid
    from services.excel_service import ExcelService

    with tempfile.NamedTemporaryFile(prefix="damas_excel_", suffix=extension, delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        await message.bot.download(doc, destination=temp_path)
        preview = await ExcelService.parse_and_preview_excel(temp_path, db)
        batch_id = uuid.uuid4().hex[:8]
        PENDING_EXCEL_IMPORTS[batch_id] = preview["valid_records"]
        await db.save_workflow_payload(batch_id, message.from_user.id, "excel", preview["valid_records"])

        err_text = ""
        if preview["errors"]:
            err_text = "\n⚠️ <b>Xatolik bor qatorlar (saqlanmaydi):</b>\n"
            for err in preview["errors"][:5]:
                err_text += f"  • {err['row']}-qator ({escape_html(err['full_name'])}): {escape_html(err['error'])}\n"
                if err.get("candidates"):
                    candidates = ", ".join(escape_html(name) for name in err["candidates"])
                    err_text += f"    Mos kelishi mumkin (tanlab tasdiqlang): <b>{candidates}</b>\n"
            if len(preview["errors"]) > 5:
                err_text += f"  <i>... va yana {len(preview['errors']) - 5} ta xato.</i>\n"

        text = (
            f"📊 <b>Fayl tahlili ({escape_html(doc.file_name)}):</b>\n\n"
            f"➕ Yangi o'quvchilar: <b>{preview['new_count']} ta</b>\n"
            f"🔄 Yangilanadiganlar: <b>{preview['update_count']} ta</b>\n"
            f"❌ Xatolik bor qatorlar: <b>{preview['error_count']} ta</b>\n"
            f"{err_text}\n"
            f"To'g'ri qatorlarni bazaga saqlashni tasdiqlaysizmi?"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
            InlineKeyboardButton(text="✅ Bazaga saqlash", callback_data=ExcelSaveCallback(batch_id=batch_id).pack()),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=ExcelCancelCallback(batch_id=batch_id).pack())
            ]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@admin_router.callback_query(ExcelSaveCallback.filter())
async def cb_excel_save(callback: types.CallbackQuery, db: Database, callback_data: ExcelSaveCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    batch_id = (callback_data or ExcelSaveCallback.unpack(callback.data)).batch_id
    cached_records = PENDING_EXCEL_IMPORTS.pop(batch_id, None)
    records = await db.take_workflow_payload(batch_id, callback.from_user.id, "excel")
    if records is None:
        records = cached_records
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

@admin_router.callback_query(ExcelCancelCallback.filter())
async def cb_excel_cancel(callback: types.CallbackQuery, db: Optional[Database] = None, callback_data: ExcelCancelCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    batch_id = (callback_data or ExcelCancelCallback.unpack(callback.data)).batch_id
    PENDING_EXCEL_IMPORTS.pop(batch_id, None)
    if db:
        await db.delete_workflow_payload(batch_id, callback.from_user.id)
    await callback.message.edit_text("❌ Excel import bekor qilindi.")
    await callback.answer("Bekor qilindi.")

# --- 9-faza: Kechikish xabari (faqat admin) ---
@admin_router.callback_query(AdminDelayTripCallback.filter())
async def cb_admin_delay_trip(callback: types.CallbackQuery, db: Database, callback_data: AdminDelayTripCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    trip_id = (callback_data or AdminDelayTripCallback.unpack(callback.data)).trip_id
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    text = (
        f"⏰ <b>Reys #{trip_id} kechikishi:</b>\n"
        f"Rejalashtirilgan vaqt: <b>{escape_html(trip['planned_time'])}</b>\n\n"
        f"Kechikish vaqtini tanlang:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10 daq", callback_data=AdminDelayConfirmCallback(trip_id=trip_id, minutes=10).pack()),
            InlineKeyboardButton(text="15 daq", callback_data=AdminDelayConfirmCallback(trip_id=trip_id, minutes=15).pack()),
            InlineKeyboardButton(text="20 daq", callback_data=AdminDelayConfirmCallback(trip_id=trip_id, minutes=20).pack()),
        ],
        [
            InlineKeyboardButton(text="30 daq", callback_data=AdminDelayConfirmCallback(trip_id=trip_id, minutes=30).pack()),
            InlineKeyboardButton(text="Boshqa", callback_data=AdminDelayCustomCallback(trip_id=trip_id).pack())
        ],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(AdminDelayCustomCallback.filter())
async def cb_admin_delay_custom(callback: types.CallbackQuery, state: FSMContext, callback_data: AdminDelayCustomCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    trip_id = (callback_data or AdminDelayCustomCallback.unpack(callback.data)).trip_id
    await state.set_state(DelayStates.waiting_for_custom_minutes)
    await state.update_data(delay_trip_id=trip_id)
    await callback.message.edit_text("Kechikish vaqtini daqiqalarda kiriting (masalan: <code>25</code>):", parse_mode="HTML")
    await callback.answer()

@admin_router.message(DelayStates.waiting_for_custom_minutes, F.text)
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
    villages = ", ".join(sorted({escape_html(it["village_name"]) for it in trip.get("items", [])}))
    num_students = len(trip.get("items", []))

    prompt_text = (
        f"Reys: #{trip_id} {escape_html(trip['planned_time'])} ({villages}).\n"
        f"{num_students} ta bolaning ota-onasiga {minutes} daqiqalik kechikish xabari boradi. Yuborilsinmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=AdminDelayApplyCallback(trip_id=trip_id, minutes=minutes).pack()),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")
        ]
    ])
    await message.answer(prompt_text, reply_markup=kb)

@admin_router.callback_query(AdminDelayConfirmCallback.filter())
async def cb_admin_delay_confirm(callback: types.CallbackQuery, db: Database, callback_data: AdminDelayConfirmCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    parsed = callback_data or AdminDelayConfirmCallback.unpack(callback.data)
    trip_id, minutes = parsed.trip_id, parsed.minutes

    trip = await db.get_trip_details(trip_id)
    villages = ", ".join(sorted({escape_html(it["village_name"]) for it in trip.get("items", [])}))
    num_students = len(trip.get("items", []))

    prompt_text = (
        f"Reys: #{trip_id} {escape_html(trip['planned_time'])} ({villages}).\n"
        f"{num_students} ta bolaning ota-onasiga {minutes} daqiqalik kechikish xabari boradi. Yuborilsinmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=AdminDelayApplyCallback(trip_id=trip_id, minutes=minutes).pack()),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_delay_cancel")
        ]
    ])
    await callback.message.edit_text(prompt_text, reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(AdminDelayApplyCallback.filter())
async def cb_admin_delay_apply(callback: types.CallbackQuery, db: Database, callback_data: AdminDelayApplyCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    parsed = callback_data or AdminDelayApplyCallback.unpack(callback.data)
    trip_id, minutes = parsed.trip_id, parsed.minutes

    success, reason, delay_data = await db.apply_trip_delay(trip_id, minutes, callback.from_user.id)
    if not success:
        await callback.answer("Xatolik: reys topilmadi!", show_alert=True)
        return

    # 1. Haydovchiga xabar
    if delay_data.get("driver_tg_id") and callback.bot:
        await safe_send(
            callback.bot,
            delay_data["driver_tg_id"],
            f"⚠️ Admin reysni {minutes} daqiqaga kechiktirdi. Yangi rejalashtirilgan vaqt: <b>{escape_html(delay_data['new_planned_time'])}</b>",
            parse_mode="HTML",
        )

    # 2. Ota-onalarga xabar (bola olingan va olinmaganiga qarab)
    for it in delay_data.get("items", []):
        if it.get("is_picked_up"):
            parent_msg = f"Farzandingiz mashinada, yetib borish taxminan {minutes} daqiqaga kechikadi."
        else:
            parent_msg = f"Mashina taxminan {minutes} daqiqaga kechikadi. Noqulaylik uchun uzr."

        for p in it.get("parents", []):
            if callback.bot and p.get("telegram_id"):
                send_status = await safe_send(callback.bot, p["telegram_id"], parent_msg)
            else:
                send_status = "failed"
            await db.log_notification(
                parent_id=p["parent_id"], student_id=it["student_id"], text=parent_msg,
                status=send_status,
            )

    await callback.message.edit_text(
        f"✅ <b>Reys #{trip_id}</b> muvaffaqiyatli {minutes} daqiqaga kechiktirildi!\n"
        f"Yangi vaqt: <b>{escape_html(delay_data['new_planned_time'])}</b>\n"
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
BROADCAST_TTL_SECONDS = 15 * 60


def _purge_expired_broadcasts(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    for batch_id, payload in list(PENDING_BROADCASTS.items()):
        if now - payload.get("created_at", 0) >= BROADCAST_TTL_SECONDS:
            PENDING_BROADCASTS.pop(batch_id, None)

@admin_router.message(Command("xabar"))
@admin_router.message(F.text == "📢 Xabar yuborish")
async def cmd_admin_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Hammaga", callback_data=BroadcastAudienceCallback(audience_type="all").pack())],
        [InlineKeyboardButton(text="🏘 Mahalla bo'yicha", callback_data=BroadcastAudienceCallback(audience_type="village").pack())],
        [InlineKeyboardButton(text="📍 Zona bo'yicha", callback_data=BroadcastAudienceCallback(audience_type="zone").pack())],
        [InlineKeyboardButton(text="👤 Bitta o'quvchi ota-onasiga", callback_data=BroadcastAudienceCallback(audience_type="student").pack())],
        [InlineKeyboardButton(text="🚗 Haydovchilarga", callback_data=BroadcastAudienceCallback(audience_type="drivers").pack())],
        [InlineKeyboardButton(text="👥 Xodimlarga", callback_data=BroadcastAudienceCallback(audience_type="staff").pack())],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="bc_cancel")]
    ])
    await message.answer("📢 <b>Xabar yuborish uchun auditoriyani tanlang:</b>", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(BroadcastAudienceCallback.filter())
async def cb_broadcast_select_audience(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: BroadcastAudienceCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()

    aud_type = (callback_data or BroadcastAudienceCallback.unpack(callback.data)).audience_type

    if aud_type == "village":
        villages = await db.get_villages()
        rows = []
        for v in villages:
            rows.append([InlineKeyboardButton(text=f"{v['name']} ({v['school_name']})", callback_data=BroadcastValueCallback(audience_type="village", value=str(v["id"])).pack())])
        rows.append([InlineKeyboardButton(text="⬅️ Ortga", callback_data="bc_cancel")])
        await callback.message.edit_text("🏘 <b>Mahallani tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        return

    elif aud_type == "zone":
        rows = [
            [InlineKeyboardButton(text="A-zona", callback_data=BroadcastValueCallback(audience_type="zone", value="1").pack())],
            [InlineKeyboardButton(text="B-zona", callback_data=BroadcastValueCallback(audience_type="zone", value="2").pack())],
            [InlineKeyboardButton(text="C-zona", callback_data=BroadcastValueCallback(audience_type="zone", value="3").pack())],
            [InlineKeyboardButton(text="D-zona", callback_data=BroadcastValueCallback(audience_type="zone", value="4").pack())],
            [InlineKeyboardButton(text="⬅️ Ortga", callback_data="bc_cancel")]
        ]
        await callback.message.edit_text("📍 <b>Zonani tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        return

    elif aud_type == "student":
        students = await db.get_active_students()
        if not students:
            await callback.message.edit_text("Faol o'quvchilar ro'yxati bo'sh.")
            return
        await state.set_state(BroadcastStates.selecting_audience)
        keyboard = build_students_pagination_kb(students, page=0, action_prefix="bc_student")
        await callback.message.edit_text(
            "👤 Xabar yuboriladigan o'quvchini ro'yxatdan tanlang:",
            reply_markup=keyboard,
        )
        return

    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type=aud_type, bc_aud_val=None)
    await callback.message.edit_text("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")


@admin_router.callback_query(BroadcastStates.selecting_audience, PageBroadcastStudentCallback.filter())
async def cb_page_broadcast_student(callback: types.CallbackQuery, db: Database, callback_data: PageBroadcastStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()
    page = (callback_data or PageBroadcastStudentCallback.unpack(callback.data)).page
    students = await db.get_active_students()
    keyboard = build_students_pagination_kb(students, page=page, action_prefix="bc_student")
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@admin_router.callback_query(BroadcastStates.selecting_audience, BroadcastStudentCallback.filter())
async def cb_broadcast_select_student(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: BroadcastStudentCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()
    student_id = (callback_data or BroadcastStudentCallback.unpack(callback.data)).student_id
    student = await db.get_student_by_id(student_id)
    if not student or not student.get("is_active"):
        await callback.message.edit_text("O'quvchi topilmadi yoki faol emas.")
        return
    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type="student", bc_aud_val=str(student_id))
    await callback.message.edit_text(
        f"Tanlandi: <b>{escape_html(student['full_name'])}</b> (ID: {student_id})\n"
        "✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:",
        parse_mode="HTML",
    )

@admin_router.callback_query(BroadcastValueCallback.filter())
async def cb_broadcast_set_value(callback: types.CallbackQuery, state: FSMContext, callback_data: BroadcastValueCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()
    parsed = callback_data or BroadcastValueCallback.unpack(callback.data)
    aud_type, aud_val = parsed.audience_type, parsed.value
    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type=aud_type, bc_aud_val=aud_val)
    await callback.message.edit_text("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")

@admin_router.message(BroadcastStates.waiting_for_value, F.text)
async def process_broadcast_student_value(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = message.text.strip()
    await state.set_state(BroadcastStates.waiting_for_content)
    await state.update_data(bc_aud_type="student", bc_aud_val=query)
    await message.answer("✍️ Yuboriladigan xabar matnini (yoki rasm + matn) kiriting:", parse_mode="HTML")

@admin_router.message(BroadcastStates.waiting_for_content, F.text | F.photo)
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

    if len(text) > 4096:
        await message.answer("Xabar juda uzun. Iltimos, 4096 belgidan oshirmang.")
        return

    audience = await db.get_broadcast_audience(aud_type, aud_val)
    batch_id = uuid.uuid4().hex[:8]

    _purge_expired_broadcasts()
    PENDING_BROADCASTS[batch_id] = {
        "created_at": time.time(),
        "admin_id": message.from_user.id,
        "audience_type": aud_type,
        "audience_value": aud_val,
        "audience": audience,
        "text": text,
        "media_id": media_id
    }
    await db.save_workflow_payload(batch_id, message.from_user.id, "broadcast", PENDING_BROADCASTS[batch_id])
    await state.clear()

    preview_text = (
        f"📢 <b>Xabar ko'rinishi:</b>\n\n{escape_html(text)}\n\n"
        f"👥 Qabul qiluvchilar soni: <b>{len(audience)} kishi</b>.\n\n"
        f"Xabarni tarqatishni tasdiqlaysizmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Yuborish", callback_data=BroadcastSendCallback(batch_id=batch_id).pack()),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=BroadcastCancelBatchCallback(batch_id=batch_id).pack())
        ]
    ])
    if media_id and len(preview_text) <= 1024:
        await message.answer_photo(photo=media_id, caption=preview_text, reply_markup=kb, parse_mode="HTML")
    elif media_id:
        await message.answer(preview_text, reply_markup=kb, parse_mode="HTML")
        await message.answer_photo(photo=media_id)
    else:
        await message.answer(preview_text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(BroadcastSendCallback.filter())
async def cb_broadcast_send(callback: types.CallbackQuery, db: Database, callback_data: BroadcastSendCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()

    import asyncio
    batch_id = (callback_data or BroadcastSendCallback.unpack(callback.data)).batch_id
    _purge_expired_broadcasts()
    cached_data = PENDING_BROADCASTS.pop(batch_id, None)
    bc_data = await db.take_workflow_payload(batch_id, callback.from_user.id, "broadcast")
    if bc_data is None:
        bc_data = cached_data
    if not bc_data:
        await _edit_broadcast_message(callback.message, "Xabar eskirgan yoki topilmadi. Qayta tayyorlang.")
        return

    if time.time() - bc_data.get("created_at", 0) >= BROADCAST_TTL_SECONDS:
        await db.delete_workflow_payload(batch_id, callback.from_user.id)
        await _edit_broadcast_message(callback.message, "Xabar eskirgan yoki topilmadi. Qayta tayyorlang.")
        return

    audience = bc_data["audience"]
    text = bc_data["text"]
    media_id = bc_data["media_id"]

    await _edit_broadcast_message(callback.message, "⏳ Xabar tarqatilmoqda, iltimos kuting...")

    sent = 0
    failed = 0
    for recipient in audience:
        tg_id = recipient.get("telegram_id")
        if not tg_id or not callback.bot:
            failed += 1
            continue
        if media_id and len(text) > 1024:
            text_status = await safe_send(callback.bot, tg_id, text, parse_mode=None)
            media_status = await safe_send_photo(callback.bot, tg_id, media_id)
            status = "sent" if text_status == media_status == "sent" else "failed"
        else:
            status = await safe_send(
                callback.bot,
                tg_id,
                text,
                **({"photo": media_id, "parse_mode": None} if media_id else {"parse_mode": None}),
            )
        if status == "sent":
            sent += 1
        else:
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

    await _edit_broadcast_message(callback.message,
        f"📊 <b>Xabar tarqatish yakunlandi:</b>\n\n"
        f"✅ Yuborildi: <b>{sent} ta</b>\n"
        f"❌ Yetmadi (bloklagan/kirmagan): <b>{failed} ta</b>\n"
        f"Jami auditoriya: <b>{len(audience)} ta</b>"
    )

@admin_router.callback_query(F.data == "bc_cancel")
@admin_router.callback_query(BroadcastCancelBatchCallback.filter())
async def cb_broadcast_cancel(callback: types.CallbackQuery, state: FSMContext, db: Optional[Database] = None, callback_data: BroadcastCancelBatchCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await callback.answer()
    _purge_expired_broadcasts()
    await state.clear()
    if callback.data.startswith("bc_cancel_batch:"):
        batch_id = (callback_data or BroadcastCancelBatchCallback.unpack(callback.data)).batch_id
        PENDING_BROADCASTS.pop(batch_id, None)
        if db:
            await db.delete_workflow_payload(batch_id, callback.from_user.id)
    await _edit_broadcast_message(callback.message, "❌ Xabar yuborish bekor qilindi.")

# ============================================================================
# Reply Menu Handlerlari (3-qadam yangilanishi)
# ============================================================================

@admin_router.message(F.text == "🚐 Bugungi reyslar")
async def admin_today_trips_menu(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    today_str = now_tz().strftime("%Y-%m-%d")
    trips = await db.get_trips_by_date(today_str)
    unapproved = await db.get_unapproved_trips(today_str)

    if not trips:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Bugungi reyslarni tuzish", callback_data="admin_generate_today")]
        ])
        await message.answer(
            f"📅 <b>Bugun ({today_str}) uchun reyslar topilmadi.</b>\n"
            f"Avtomatik reja {escape_html(settings.DAILY_PLAN_TIME)} da tuziladi. Agar bot o'sha vaqtda ishlamagan bo'lsa, reyslarni hozir tuzing.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    header_text = f"🚐 <b>Bugungi ({today_str}) reyslar ro'yxati (Jami: {len(trips)} ta):</b>"
    if unapproved:
        approve_all_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Barcha reyslarni tasdiqlash ({len(unapproved)} ta)", callback_data=ApproveTripsCallback(date=today_str).pack())]
        ])
        await message.answer(header_text, reply_markup=approve_all_kb, parse_mode="HTML")
    else:
        await message.answer(header_text, reply_markup=get_admin_menu_kb(), parse_mode="HTML")

    for t in trips:
        dir_label = "🚖 Markazga kelish" if t["direction"] == "to_center" else "🏠 Uyga qaytish"
        driver_str = t.get("driver_name") or "Biriktirilmagan"
        items_cnt = len(t.get("items", []))
        st_label = t["status"]
        approved_ico = "✅ Tasdiqlangan" if t.get("approved") else "⏳ Tasdiqlanmagan"
        students_text = "\n".join(
            f"  • {escape_html(item['full_name'])} — {escape_html(item['village_name'])}"
            for item in t.get("items", [])
        ) or "  • O'quvchilar ro'yxati bo'sh"

        card_text = (
            f"📋 <b>Reys #{t['id']} — {dir_label}</b>\n"
            f"⏰ Reja vaqti: <b>{escape_html(t['planned_time'])}</b>\n"
            f"🚗 Haydovchi: <b>{escape_html(driver_str)}</b>\n"
            f"👥 O'quvchilar ({items_cnt} ta):\n{students_text}\n"
            f"🚦 Holati: <b>{escape_html(st_label)}</b> ({approved_ico})"
        )

        rows = [
            [InlineKeyboardButton(text="⏰ Kechikish e'lon qilish", callback_data=AdminDelayTripCallback(trip_id=t["id"]).pack())],
            [InlineKeyboardButton(
                text="🚗 Haydovchini biriktirish" if not t.get("driver_id") else "🔄 Haydovchini o'zgartirish",
                callback_data=AdminAssignTripCallback(trip_id=t["id"]).pack(),
            )],
        ]
        if t["status"] == "planned" and not t.get("approved"):
            rows.append([InlineKeyboardButton(text="✅ Reysni tasdiqlash", callback_data=ApproveTripCallback(trip_id=t["id"]).pack())])
        if t["status"] not in {"on_way", "done", "cancelled"}:
            rows.append([InlineKeyboardButton(text="❌ Reysni bekor qilish", callback_data=CancelTripCallback(trip_id=t["id"]).pack())])

        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(card_text, reply_markup=kb, parse_mode="HTML")


@admin_router.callback_query(CancelTripCallback.filter())
async def cb_admin_cancel_trip(callback: types.CallbackQuery, db: Database, callback_data: CancelTripCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    trip_id = (callback_data or CancelTripCallback.unpack(callback.data)).trip_id
    if not await db.cancel_trip(trip_id, callback.from_user.id):
        await callback.answer("Reys yo'lda, yakunlangan yoki allaqachon bekor qilingan.", show_alert=True)
        return
    await NotifyService.notify_trip_students(
        db=db,
        trip_id=trip_id,
        text_template=f"⚠️ #{trip_id} reys bekor qilindi. Batafsil ma'lumot uchun AQL ZIYOSI markaziga murojaat qiling.",
        bot=callback.bot,
    )
    await callback.message.edit_text(f"❌ <b>Reys #{trip_id} bekor qilindi.</b>", parse_mode="HTML")
    await callback.answer("Reys bekor qilindi")


@admin_router.callback_query(AdminAssignTripCallback.filter())
async def cb_admin_assign_trip(callback: types.CallbackQuery, db: Database, callback_data: AdminAssignTripCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    trip_id = (callback_data or AdminAssignTripCallback.unpack(callback.data)).trip_id
    trip = await db.get_trip_details(trip_id)
    if not trip or trip["status"] not in {"planned", "approved"}:
        await callback.answer("Bu reysga haydovchini endi o'zgartirib bo'lmaydi", show_alert=True)
        return
    drivers = [driver for driver in await db.get_drivers() if driver["is_active"]]
    if not drivers:
        await callback.answer("Faol haydovchilar topilmadi", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=f"🚗 {escape_html(driver['full_name'])}",
            callback_data=AdminSetTripDriverCallback(trip_id=trip_id, driver_id=driver["id"]).pack(),
        )]
        for driver in drivers
    ]
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])
    await callback.message.edit_text(
        f"Reys #{trip_id} uchun faol haydovchini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@admin_router.callback_query(AdminSetTripDriverCallback.filter())
async def cb_admin_set_trip_driver(callback: types.CallbackQuery, db: Database, callback_data: AdminSetTripDriverCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    parsed = callback_data or AdminSetTripDriverCallback.unpack(callback.data)
    trip_id, driver_id = parsed.trip_id, parsed.driver_id
    if not await db.assign_driver_to_trip(trip_id, driver_id):
        await callback.answer("Reys boshlanib ketgan yoki haydovchi faol emas", show_alert=True)
        return
    driver = await db.get_driver_by_id(driver_id)
    await callback.message.edit_text(
        f"✅ Reys #{trip_id} ga haydovchi biriktirildi: <b>{escape_html(driver['full_name'] if driver else '')}</b>.",
        parse_mode="HTML",
    )
    await callback.answer("Biriktirildi")


async def _generate_today_plan(message: types.Message, db: Database, scheduler: SchedulerService) -> None:
    now = now_tz()
    date_str = now.strftime("%Y-%m-%d")
    schedules = await db.get_day_schedules(now.weekday())
    success, reason, trips = await scheduler.run_daily_plan(target_date=date_str)
    if not success:
        if reason == "save_failed":
            await message.answer(
                "⚠️ Reys rejasi saqlanmadi: takroriy o'quvchi yoki noto'g'ri haydovchi/mahalla bog'lanishi bor. "
                "Ma'lumotlarni tekshirib, qayta urinib ko'ring.",
                reply_markup=get_admin_menu_kb(),
            )
            return
        await message.answer(
            "⚠️ Bugungi reja qayta tuzilmadi: bugungi reyslar tasdiqlangan, boshlangan yoki yakunlangan. Mavjud reja o'zgartirilmadi.",
            reply_markup=get_admin_menu_kb(),
        )
        return
    if not trips:
        if not schedules:
            detail = "Bugungi hafta kuni uchun birorta faol o'quvchining dars jadvali yo'q."
        else:
            absent_ids = set(await db.get_absent_student_ids(date_str))
            if all(row["student_id"] in absent_ids for row in schedules):
                detail = "Bugungi jadvaldagi barcha o'quvchilar kelmaslikka belgilangan."
            else:
                detail = "Bugun reysga mos keladigan dars vaqtlari topilmadi."
        await message.answer(f"📅 {escape_html(date_str)}: {detail}", reply_markup=get_admin_menu_kb())
        return

    unassigned_count = sum(1 for trip in trips if not trip.get("driver_id"))
    text = (
        f"✅ <b>{escape_html(date_str)} uchun reja tuzildi.</b>\n"
        f"🚐 Reyslar: <b>{len(trips)}</b>\n"
        f"👥 Jadvaldan olingan o'quvchilar: <b>{sum(len(trip.get('items', [])) for trip in trips)}</b>"
    )
    if unassigned_count:
        text += (
            f"\n⚠️ Haydovchisiz reyslar: <b>{unassigned_count}</b>. "
            "O'quvchini /ozgartirish → Haydovchini biriktirish orqali tayinlang."
        )
    text += "\n\nReyslarni ko'rish uchun «🚐 Bugungi reyslar» tugmasini bosing."
    await message.answer(text, reply_markup=get_admin_menu_kb(), parse_mode="HTML")


@admin_router.message(F.photo | F.sticker)
async def reject_non_text_fsm_input(message: types.Message, state: FSMContext):
    """Reject unsupported media while an admin workflow expects text."""
    if await state.get_state() is not None:
        await message.answer("Iltimos, rasm yoki stiker emas, matn yuboring.")


@admin_router.message(F.text == "🧩 Bugungi reyslarni tuzish")
async def admin_generate_today_plan(message: types.Message, db: Database, scheduler: SchedulerService):
    if not is_admin(message.from_user.id):
        return
    await _generate_today_plan(message, db, scheduler)


@admin_router.callback_query(F.data == "admin_generate_today")
async def cb_admin_generate_today_plan(callback: types.CallbackQuery, db: Database, scheduler: SchedulerService):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    await callback.answer("Bugungi reja tuzilmoqda…")
    await _generate_today_plan(callback.message, db, scheduler)

@admin_router.message(F.text == "📊 Statistika / Hisobot")
async def admin_statistics_menu(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    today_str = now_tz().strftime("%Y-%m-%d")
    stats = await db.get_daily_transport_stats(today_str)

    text = (
        f"📊 <b>Bugungi transport hisoboti ({escape_html(stats['date'])}):</b>\n\n"
        f"🚐 <b>Jami reyslar:</b> {stats['total_trips']} ta\n"
        f"  • 🚖 Markazga kelish: <b>{stats['to_center_trips']} ta</b>\n"
        f"  • 🏠 Uyga qaytish: <b>{stats['to_home_trips']} ta</b>\n\n"
        f"👥 <b>O'quvchilar holati:</b>\n"
        f"  • 🏁 Markazga yetib kelganlar: <b>{stats['arrived_count']} nafar</b>\n"
        f"  • 📚 Hozir darsda: <b>{stats['in_class_count']} nafar</b>\n"
        f"  • 🚫 Bugun darsga kelmaganlar: <b>{stats['absent_count']} nafar</b>\n\n"
        f"🚗 <b>Faol haydovchilar soni:</b> <b>{stats['active_drivers']} nafar</b>"
    )
    await message.answer(text, reply_markup=get_admin_menu_kb(), parse_mode="HTML")

@admin_router.message(F.text == "📥 Excel boshqaruvi")
async def admin_excel_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Bo'sh shablon olish", callback_data="admin_excel_template")],
        [InlineKeyboardButton(text="📤 Mavjud bazani Excelda yuklab olish", callback_data="admin_excel_export")],
        [InlineKeyboardButton(text="📥 Yangi Excel yuklash yo'riqnomasi", callback_data="admin_excel_upload_info")]
    ])
    await message.answer(
        "📥 <b>Excel boshqaruvi bo'limi:</b>\n\n"
        "Quyidagi tugmalar orqali bo'sh shablon olishingiz, bazani to'liq Excelda yuklab olishingiz yoki yangi fayl yuklashingiz mumkin:",
        reply_markup=kb,
        parse_mode="HTML"
    )


async def _send_students_excel(destination, db: Database) -> None:
    import os
    from aiogram.types import FSInputFile
    from services.excel_service import ExcelService

    file_path = await ExcelService.export_students_to_excel(db)
    try:
        await destination.answer_document(
            FSInputFile(file_path, filename="baza_oquvchilar.xlsx"),
            caption="📤 <b>Baza to'liq Excel formatida eksport qilindi.</b>\n\nBarcha faol o'quvchilar va ularning dars jadvallari mavjud.",
            parse_mode="HTML"
        )
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@admin_router.message(F.text == "📤 O'quvchilar ro'yxatini Excel")
async def admin_export_students_direct(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    await _send_students_excel(message, db)

@admin_router.callback_query(F.data == "admin_excel_template")
async def cb_admin_excel_template(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    from aiogram.types import FSInputFile
    from services.excel_service import ExcelService
    import os
    path = ExcelService.generate_template()
    try:
        await callback.message.answer_document(
            FSInputFile(path, filename=os.path.basename(path)),
            caption=(
                "📄 <b>O'quvchilarni ommaviy yuklash uchun Excel shablon.</b>\n\n"
                "Ustunlar: F.I.Sh | Mahalla | Ota telefoni | Ona telefoni | Dushanba-Shanba dars vaqtlari."
            ),
            parse_mode="HTML"
        )
    finally:
        if os.path.exists(path):
            os.remove(path)
    await callback.answer()

@admin_router.callback_query(F.data == "admin_excel_export")
async def cb_admin_excel_export(callback: types.CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    await callback.answer("Baza eksport qilinmoqda...")
    await _send_students_excel(callback.message, db)

@admin_router.callback_query(F.data == "admin_excel_upload_info")
async def cb_admin_excel_upload_info(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    text = (
        "📥 <b>Yangi Excel yuklash bo'yicha yo'riqnoma:</b>\n\n"
        "1. Avval [📄 Bo'sh shablon olish] tugmasi orqali shablonni yuklab oling.\n"
        "2. Shablon ustunlarini to'ldiring (vaqtlarni <code>09:00-11:00</code> formatida kiriting).\n"
        "3. Tayyor bo'lgan <b>.xlsx yoki .xls</b> faylni to'g'ridan-to'g'ri ushbu bot chatiga fayl (document) sifatida tashlang.\n\n"
        "<i>Bot avtomatik tarzda ma'lumotlarni tekshirib, saqlashdan oldin sizga hisobot taqdim etadi.</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

@admin_router.message(F.text == "👥 Foydalanuvchilar")
async def admin_users_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            f"Bu bo'lim adminlar uchun. Sizning Telegram ID: <code>{message.from_user.id}</code>.\n"
            "ID ni ADMIN_IDS sozlamasiga qo'shib, botni qayta ishga tushiring yoki /myid buyrug'idan foydalaning."
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚗 Haydovchilar (/haydovchilar)", callback_data=AdminNavigationCallback(target="drivers").pack()),
            InlineKeyboardButton(text="👥 Xodimlar (/xodimlar)", callback_data=AdminNavigationCallback(target="staff").pack())
        ],
        [
            InlineKeyboardButton(text="🏘 Mahallalar (/qishloqlar)", callback_data=AdminNavigationCallback(target="villages").pack()),
            InlineKeyboardButton(text="👤 O'quvchilar ro'yxati", callback_data=AdminNavigationCallback(target="students").pack())
        ]
    ])
    await message.answer(
        "👥 <b>Foydalanuvchilar va tizim ma'lumotlari boshqaruvi:</b>\n\n"
        "Kerakli bo'limni tanlang:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@admin_router.callback_query(AdminNavigationCallback.filter())
async def cb_admin_nav(callback: types.CallbackQuery, db: Database, state: FSMContext, callback_data: AdminNavigationCallback | None = None):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    target = (callback_data or AdminNavigationCallback.unpack(callback.data)).target
    if target == "drivers":
        text, keyboard = await render_drivers(db)
        await safe_telegram_call(callback.message.edit_text, text, reply_markup=keyboard, parse_mode="HTML")
    elif target == "staff":
        text, keyboard = await render_staff(db)
        await safe_telegram_call(callback.message.edit_text, text, reply_markup=keyboard, parse_mode="HTML")
    elif target == "villages":
        text, keyboard = await render_villages(db)
        await safe_telegram_call(callback.message.edit_text, text, reply_markup=keyboard, parse_mode="HTML")
    elif target == "students":
        students = await db.get_active_students()
        if not students:
            await callback.answer("Hozircha birorta ham faol o'quvchi yo'q!", show_alert=True)
            return
        kb = build_students_pagination_kb(students, 0, "edit_st")
        await state.set_state(EditStudentStates.selecting_student)
        await safe_telegram_call(
            callback.message.edit_text,
            "👤 <b>Faol o'quvchilar ro'yxati:</b>",
            reply_markup=kb,
            parse_mode="HTML",
        )
    await safe_telegram_call(callback.answer)

@admin_router.message(F.text == "🚫 Bugun kelmaganlar")
async def admin_today_absences(message: types.Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    today_str = now_tz().strftime("%Y-%m-%d")
    absences = await db.get_today_absences_detailed(today_str)

    if not absences:
        await message.answer(
            f"✅ <b>Bugun ({today_str}) kelmagan deb belgilangan o'quvchilar yo'q.</b>\n"
            f"Barcha o'quvchilar darsda yoki qatnovda!",
            reply_markup=get_admin_menu_kb(),
            parse_mode="HTML"
        )
        return

    text = f"🚫 <b>Bugun ({today_str}) kelmagan o'quvchilar ro'yxati ({len(absences)} ta):</b>\n\n"
    for idx, a in enumerate(absences, start=1):
        v_name = a.get("village_name") or "Noma'lum mahalla"
        reason = a.get("reason") or "Sabab ko'rsatilmagan"
        reported_by = a.get("reported_by_role") or "Noma'lum"
        phone = a.get("parent_phone") or "-"
        text += (
            f"{idx}. <b>{escape_html(a['student_name'])}</b> ({escape_html(v_name)})\n"
            f"   • Sabab: <i>{escape_html(reason)}</i>\n"
            f"   • Bildiruvchi: <b>{escape_html(reported_by.capitalize())}</b> (Tel: {escape_html(phone)})\n\n"
        )

    await message.answer(text, reply_markup=get_admin_menu_kb(), parse_mode="HTML")

