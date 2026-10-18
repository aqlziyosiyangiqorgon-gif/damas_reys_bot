from utils.clock import now_tz
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from models.callbacks import (
    AbsentButtonCallback,
    AbsentSelectCallback,
    ConfirmChildCallback,
    RejectChildCallback,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from config import settings
from database.db import Database, normalize_phone
from models.enums import UserRole
from utils.text import escape_html
from services.telegram_safe import safe_send

parent_router = Router()


async def _notify_center_admin_absence(bot, info: dict, reason: str = "") -> None:
    """Notify the center's operational admin, independently of bot admins."""
    student_name = escape_html(info.get("student_name", "O'quvchi"))
    village_name = escape_html(info.get("village_name", ""))
    parent_phone = escape_html(info.get("parent_phone", ""))
    text = (
        "⚠️ <b>Kelmaslik xabarnomasi!</b>\n"
        f"👤 O'quvchi: <b>{student_name}</b> ({village_name})\n"
        f"📞 Ota-ona: <code>{parent_phone}</code>\n"
    )
    if reason:
        text += f"💬 Sababi: <i>{escape_html(reason)}</i>\n"
    text += "🚐 Bugungi reysdan chiqarildi."
    await safe_send(bot, settings.CENTER_ADMIN_TG_ID, text, parse_mode="HTML")

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

class ParentStates(StatesGroup):
    waiting_for_absence_reason = State()

async def _is_own_child(db: Database, tg_id: int, student_id: int) -> bool:
    """IDOR himoyasi: bola shu ota-onaga tasdiqlangan bo'lishi shart."""
    parent = await db.get_parent_by_telegram_id(tg_id)
    if not parent:
        return False
    return await db.is_student_linked_to_parent(student_id, parent["id"])


async def _clear_absence_reason_state(state: FSMContext | None) -> None:
    if state and await state.get_state() == ParentStates.waiting_for_absence_reason.state:
        await state.clear()

def get_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_parent_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Farzandim qayerda?"), KeyboardButton(text="🚫 Bugun kelmaydi")],
            [KeyboardButton(text="📅 Dars jadvali"), KeyboardButton(text="📞 Markaz bilan aloqa")]
        ],
        resize_keyboard=True
    )

# --- 2. Kontakt qabul qilish va tekshirish ---
@parent_router.message(F.contact)
async def process_parent_contact(message: types.Message, db: Database):
    contact = message.contact

    # SATR: XAVFSIZLIK TEKSHIRUVI (Faqat o'zining kontaktini yuborishi shart)
    if contact.user_id != message.from_user.id:
        await message.answer(
            "❌ Faqat o'zingizning raqamingizni yuborishingiz mumkin! Begona kontaktlar qabul qilinmaydi.",
            reply_markup=get_contact_keyboard()
        )
        return

    norm_phone = normalize_phone(contact.phone_number)
    if not norm_phone:
        await message.answer(
            "❌ Telefon raqami formati noto'g'ri. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_contact_keyboard()
        )
        return

    # students jadvalidan qidirish
    matched_students = await db.find_students_by_phone(norm_phone)
    if not matched_students:
        await message.answer(
            "❌ Raqamingiz tizimdan topilmadi. Iltimos, o'quv markaz ma'muriyatiga murojaat qiling:\n📞 +998772602020",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Ota-onani bazaga saqlash
    now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
    parent_id = await db.add_parent(message.from_user.id, norm_phone, now_str)

    await message.answer(
        "✅ Telefon raqamingiz tasdiqlandi!\n"
        "Quyidagi o'quvchi(lar) sizning farzandingizmi? Iltimos, tasdiqlang:",
        reply_markup=get_parent_menu_kb()
    )

    # Topilgan har bir bola uchun alohida tasdiqlash tugmasi chiqarish
    for st in matched_students:
        is_already_linked = await db.is_student_linked_to_parent(st["id"], parent_id)
        if is_already_linked:
            continue

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha", callback_data=ConfirmChildCallback(student_id=st["id"], parent_id=parent_id).pack()),
                InlineKeyboardButton(text="❌ Yo'q", callback_data=RejectChildCallback(student_id=st["id"], parent_id=parent_id).pack())
            ]
        ])
        await message.answer(
            f"👤 Farzandingiz <b>{escape_html(st['full_name'])}</b> ({escape_html(st['village_name'])}, {escape_html(st.get('school_name', ''))})mi?",
            reply_markup=kb,
            parse_mode="HTML"
        )

# --- 3. Farzandni tasdiqlash callbacki ---
@parent_router.callback_query(ConfirmChildCallback.filter())
async def cb_confirm_child(callback: types.CallbackQuery, db: Database, callback_data: ConfirmChildCallback | None = None):
    callback_data = callback_data or ConfirmChildCallback.unpack(callback.data)
    student_id = callback_data.student_id
    parent_id = callback_data.parent_id

    parent = await db.get_parent_by_telegram_id(callback.from_user.id)
    if not parent or parent["id"] != parent_id:
        await callback.answer("Ruxsat berilmagan amal!", show_alert=True)
        return

    is_linked = await db.is_student_linked_to_parent(student_id, parent_id)
    if is_linked:
        await callback.answer("Ushbu farzand allaqachon tasdiqlangan!", show_alert=True)
        return

    student = await db.get_student_by_id(student_id)
    if not student:
        await callback.answer("O'quvchi topilmadi!", show_alert=True)
        return

    # IDOR himoyasi: ota-ona telefoni bola ota-ona raqamlaridan biriga mos bo'lishi shart
    if parent["phone"] not in (student["parent_phone_1"], student["parent_phone_2"]):
        await callback.answer("Ruxsat berilmagan amal!", show_alert=True)
        return

    # Bog'lash va audit logga yozish
    await db.link_student_parent(student_id, parent_id)
    await db.log_event(
        actor_role="parent",
        actor_id=callback.from_user.id,
        action="confirm_child",
        student_id=student_id
    )

    await callback.message.edit_text(
        f"✅ <b>{escape_html(student['full_name'])}</b> muvaffaqiyatli tasdiqlandi!\n\n"
        f"Endi har kuni farzandingizning mashinaga chiqqanligi, markazga yetib borgani "
        f"va uyga yetkazilgani haqida avtomatik bildirishnomalar olasiz.",
        parse_mode="HTML"
    )
    await callback.answer("Tasdiqlandi!")

# --- 4. Farzandni rad etish callbacki ---
@parent_router.callback_query(RejectChildCallback.filter())
async def cb_reject_child(callback: types.CallbackQuery, db: Database, callback_data: RejectChildCallback | None = None):
    callback_data = callback_data or RejectChildCallback.unpack(callback.data)
    student_id = callback_data.student_id
    parent_id = callback_data.parent_id

    parent = await db.get_parent_by_telegram_id(callback.from_user.id)
    if not parent or parent["id"] != parent_id:
        await callback.answer("Ruxsat berilmagan amal!", show_alert=True)
        return

    student = await db.get_student_by_id(student_id)
    st_name = student["full_name"] if student else "O'quvchi"

    await db.log_event(
        actor_role="parent",
        actor_id=callback.from_user.id,
        action="reject_child",
        student_id=student_id
    )

    await callback.message.edit_text(
        f"❌ <b>{escape_html(st_name)}</b> farzandingiz emas deb belgilandi.",
        parse_mode="HTML"
    )
    await callback.answer("Rad etildi.")

# --- 5. /farzandlarim: Farzandlar kartasi va ma'lumotlar ---
@parent_router.message(Command("farzandlarim"))
async def cmd_my_children(message: types.Message, db: Database, user_role: UserRole):
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Siz hali ota-ona sifatida ro'yxatdan o'tmagansiz. /start ni bosing.")
        return

    students = await db.get_parent_students(parent["id"])
    if not students:
        await message.answer("Sizga biriktirilgan farzandlar topilmadi.")
        return

    await show_parent_dashboard(message, students, db)

async def show_parent_dashboard(message: types.Message, students: list, db: Database):
    text = "👨‍👩‍👧‍👦 <b>Sizning farzandlaringiz:</b>\n\n"
    for s in students:
        text += f"👤 <b>{escape_html(s['full_name'])}</b>\n"
        text += f"🏘 Mahalla: <b>{escape_html(s['village_name'])}</b> ({escape_html(s.get('school_name', '-'))})\n"
        schedules = await db.get_student_schedules(s["id"])
        if schedules:
            text += "📅 Dars vaqtlari:\n"
            for sch in schedules:
                day_name = WEEKDAYS_UZ[sch["weekday"]]
                text += f"  • {escape_html(day_name)}: <code>{escape_html(sch['start_time'])} - {escape_html(sch['end_time'])}</code>\n"
        else:
            text += "📅 Dars jadvali kiritilmagan.\n"
        text += "\n"

    text += "<i>Bot orqali farzandingiz qatnovini kuzatishingiz yoki kelmasligini bildirishingiz mumkin.</i>"

    rows = []
    for s in students:
        rows.append([InlineKeyboardButton(text=f"🚫 {s['full_name']} bugun kelmaydi", callback_data=AbsentButtonCallback(student_id=s["id"]).pack())])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# --- 6. DOIMIY REPLY MENYU HANDLERLARI ---

# a) 📍 Farzandim qayerda?
@parent_router.message(F.text == "📍 Farzandim qayerda?")
async def parent_track_children(message: types.Message, db: Database, state: FSMContext | None = None):
    await _clear_absence_reason_state(state)
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Siz hali ota-ona sifatida ro'yxatdan o'tmagansiz. /start ni bosing.")
        return

    students = await db.get_parent_students(parent["id"])
    if not students:
        await message.answer("Sizga biriktirilgan farzandlar topilmadi.", reply_markup=get_parent_menu_kb())
        return

    date_str = now_tz().strftime("%Y-%m-%d")
    text = "📍 <b>Farzandingizning hozirgi holati:</b>\n\n"

    for s in students:
        status_info = await db.get_student_current_status(s["id"], date_str)
        text += f"👤 <b>{escape_html(s['full_name'])}</b> ({escape_html(s['village_name'])})\n"
        text += f"📌 Holati: <b>{escape_html(status_info['status_text'])}</b>\n"

        trip = status_info.get("trip")
        if trip:
            dir_str = "Markazga kelish" if trip.get("direction") == "to_center" else "Uyga qaytish"
            text += f"🚐 Yo'nalish: <b>{escape_html(dir_str)}</b> (Vaqti: <code>{escape_html(trip.get('planned_time'))}</code>)\n"
            if trip.get("driver_name"):
                driver_phone = f" (Tel: {trip['driver_phone']})" if trip.get("driver_phone") else ""
                text += f"🚗 Haydovchi: <b>{escape_html(trip['driver_name'])}</b>{escape_html(driver_phone)}\n"
        elif status_info.get("reason"):
            text += f"💬 Izoh: <i>{escape_html(status_info['reason'])}</i>\n"

        text += "\n"

    await message.answer(text, reply_markup=get_parent_menu_kb(), parse_mode="HTML")

# b) 📅 Dars jadvali
@parent_router.message(F.text == "📅 Dars jadvali")
async def parent_view_schedule(message: types.Message, db: Database, state: FSMContext | None = None):
    await _clear_absence_reason_state(state)
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Siz hali ota-ona sifatida ro'yxatdan o'tmagansiz. /start ni bosing.")
        return

    students = await db.get_parent_students(parent["id"])
    if not students:
        await message.answer("Sizga biriktirilgan farzandlar topilmadi.", reply_markup=get_parent_menu_kb())
        return

    await show_parent_dashboard(message, students, db)

# c) 📞 Markaz bilan aloqa
@parent_router.message(F.text == "📞 Markaz bilan aloqa")
async def parent_center_contact(message: types.Message, state: FSMContext | None = None):
    await _clear_absence_reason_state(state)
    await message.answer(
        "📞 <b>AQL ZIYOSI o'quv markazi ma'muriyati:</b>\n\n"
        "Telefon: <b>+998 77 260 20 20</b>\n\n"
        "Savollar yoki takliflar bo'lsa markaz ma'muriyatiga qo'ng'iroq qilishingiz mumkin.",
        reply_markup=get_parent_menu_kb(),
        parse_mode="HTML"
    )

# d) 🚫 Bugun kelmaydi (Reply menu orqali FSM boshlash)
@parent_router.message(F.text == "🚫 Bugun kelmaydi")
async def parent_report_absence_start(message: types.Message, state: FSMContext, db: Database):
    await _clear_absence_reason_state(state)
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Siz hali ota-ona sifatida ro'yxatdan o'tmagansiz. /start ni bosing.")
        return

    students = await db.get_parent_students(parent["id"])
    if not students:
        await message.answer("Sizga biriktirilgan farzandlar topilmadi.", reply_markup=get_parent_menu_kb())
        return

    if len(students) == 1:
        st = students[0]
        await state.update_data(absent_student_id=st["id"])
        await state.set_state(ParentStates.waiting_for_absence_reason)
        await message.answer(
            f"👤 <b>{escape_html(st['full_name'])}</b> bugun darsga kela olmaslik sababini qisqacha yozib yuboring:\n"
            f"<i>(masalan: Shamollab qoldi / To‘yga ketdik / Shaxsiy sabab)</i>",
            parse_mode="HTML"
        )
    else:
        rows = []
        for st in students:
            rows.append([InlineKeyboardButton(text=f"🚫 {st['full_name']}", callback_data=AbsentSelectCallback(student_id=st["id"]).pack())])
        rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="absent_cancel")])
        await message.answer(
            "Qaysi farzandingiz bugun darsga kelmaydi?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

@parent_router.callback_query(AbsentSelectCallback.filter())
async def cb_absent_select(callback: types.CallbackQuery, state: FSMContext, db: Database, callback_data: AbsentSelectCallback | None = None):
    callback_data = callback_data or AbsentSelectCallback.unpack(callback.data)
    student_id = callback_data.student_id
    if not await _is_own_child(db, callback.from_user.id, student_id):
        await callback.answer("Bu sizning farzandingiz emas!", show_alert=True)
        return
    student = await db.get_student_by_id(student_id)
    st_name = student["full_name"] if student else "Farzandingiz"

    await state.update_data(absent_student_id=student_id)
    await state.set_state(ParentStates.waiting_for_absence_reason)
    await callback.message.edit_text(
            f"👤 <b>{escape_html(st_name)}</b> bugun darsga kela olmaslik sababini qisqacha yozib yuboring:\n"
        f"<i>(masalan: Shamollab qoldi / To‘yga ketdik / Shaxsiy sabab)</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@parent_router.callback_query(F.data == "absent_cancel")
async def cb_absent_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Kelmaslik bildirishnomasi bekor qilindi.")
    await callback.answer()

# --- 7. FSM: Sabab qabul qilish handleri ---
@parent_router.message(ParentStates.waiting_for_absence_reason, F.text)
async def process_absence_reason(message: types.Message, state: FSMContext, db: Database):
    data = await state.get_data()
    student_id = data.get("absent_student_id")
    if not student_id:
        await state.clear()
        return

    if not await _is_own_child(db, message.from_user.id, student_id):
        await state.clear()
        await message.answer("Bu sizning farzandingiz emas!", reply_markup=get_parent_menu_kb())
        return

    reason = message.text.strip()[:100]
    date_str = now_tz().strftime("%Y-%m-%d")

    success, err_code, info = await db.mark_student_absent_with_reason(
        student_id=student_id,
        date_str=date_str,
        role="parent",
        actor_id=message.from_user.id,
        reason=reason
    )

    await state.clear()

    if not success:
        if err_code == "already_picked_up":
            await message.answer(
                "Farzandingiz allaqachon mashinaga chiqqan, uni bekor qilib bo'lmaydi.",
                reply_markup=get_parent_menu_kb()
            )
        elif err_code == "already_marked":
            await message.answer(
                "Farzandingiz bugun darsga kelmaydi deb allaqachon belgilangan.",
                reply_markup=get_parent_menu_kb()
            )
        else:
            await message.answer(
                "Kelmaslikni belgilab bo'lmadi. Qaytadan urinib ko'ring.",
                reply_markup=get_parent_menu_kb()
            )
        return

    # 1. Ota-onaga tasdiq
    await message.answer(
        "✅ Farzandingiz bugun darsga kelmaydi deb belgilandi. Haydovchi va ma'muriyat ogohlantirildi.",
        reply_markup=get_parent_menu_kb()
    )

    st_name = info.get("student_name", "O'quvchi")
    v_name = info.get("village_name", "")

    # 2. Haydovchiga xabar (agar reys va haydovchi bo'lsa)
    if info.get("driver_tg_id") and message.bot:
        await safe_send(
            message.bot,
            info["driver_tg_id"],
            f"ℹ️ <b>{escape_html(st_name)}</b> ({escape_html(v_name)}) bugun kelmaydi deb belgilandi.\n💬 <b>Sababi:</b> {escape_html(reason)}",
            parse_mode="HTML",
        )

    # 3. Faqat markaz adminiga yuboriladi; bot adminlari ro'yxati alohida.
    if message.bot:
        await _notify_center_admin_absence(message.bot, info, reason)

@parent_router.message(ParentStates.waiting_for_absence_reason, ~F.text)
async def reject_non_text_absence_reason(message: types.Message):
    """Sabab kutilayotganda rasm/stiker/ovoz kelsa, bot jim qolmasin."""
    await message.answer("Iltimos, sababni faqat matn ko'rinishida yozib yuboring.")

# --- 8. Mavjud inline tugma orqali kelmaslikni belgilash (Phase 8 test mosligi) ---
@parent_router.callback_query(AbsentButtonCallback.filter())
async def cb_student_absent(callback: types.CallbackQuery, db: Database, callback_data: AbsentButtonCallback | None = None):
    callback_data = callback_data or AbsentButtonCallback.unpack(callback.data)
    parent = await db.get_parent_by_telegram_id(callback.from_user.id)
    if not parent:
        await callback.answer("Faqat ro'yxatdan o'tgan ota-onalar uchun!", show_alert=True)
        return

    student_id = callback_data.student_id
    is_linked = await db.is_student_linked_to_parent(student_id, parent["id"])
    if not is_linked:
        await callback.answer("Bu sizning farzandingiz emas!", show_alert=True)
        return

    date_str = now_tz().strftime("%Y-%m-%d")
    success, reason, info = await db.mark_student_absent(
        student_id=student_id,
        date_str=date_str,
        role="parent",
        actor_id=callback.from_user.id
    )

    if not success:
        if reason == "already_picked_up":
            await callback.answer("Farzandingiz allaqachon mashinaga chiqqan, uni bekor qilib bo'lmaydi.", show_alert=True)
        elif reason == "already_marked":
            await callback.answer("Farzandingiz bugun kelmaydi deb allaqachon belgilangan!", show_alert=True)
        else:
            await callback.answer("Kelmaslikni belgilab bo'lmadi!", show_alert=True)
        return

    # Haydovchiga darhol xabar yuborish
    if info and info.get("driver_tg_id") and callback.bot:
        await safe_send(
            callback.bot,
            info["driver_tg_id"],
            f"ℹ️ <b>{escape_html(info['student_name'])}</b> ({escape_html(info['village_name'])}) bugun kelmaydi deb belgilandi.",
            parse_mode="HTML",
        )

    if info and callback.bot:
        await _notify_center_admin_absence(callback.bot, info)

    await callback.answer("Farzandingiz bugun kelmaydi deb belgilandi.", show_alert=True)
