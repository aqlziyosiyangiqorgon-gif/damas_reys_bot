from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from database.db import Database, normalize_phone
from models.enums import UserRole

parent_router = Router()

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

def get_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# --- 1. /start: Ota-ona yoki Mehmon uchun kirish ---
@parent_router.message(CommandStart(), F.func(lambda msg, user_role=None: user_role in [UserRole.PARENT, UserRole.GUEST] if user_role else True))
async def parent_start(message: types.Message, db: Database, user_role: UserRole):
    # Agar allaqachon ota-ona sifatida ro'yxatdan o'tgan bo'lsa
    if user_role == UserRole.PARENT:
        parent = await db.get_parent_by_telegram_id(message.from_user.id)
        if parent:
            students = await db.get_parent_students(parent["id"])
            if students:
                await show_parent_dashboard(message, students, db)
                return

    # Ro'yxatdan o'tmagan (GUEST) bo'lsa telefon raqamini so'rash
    await message.answer(
        "Assalomu alaykum! <b>AQL ZIYOSI</b> o'quvchilar transport botiga xush kelibsiz.\n\n"
        "Farzandingizning qatnovini real vaqtda kuzatish uchun iltimos, "
        "quyidagi tugma orqali <b>telefon raqamingizni yuboring</b>:",
        reply_markup=get_contact_keyboard(),
        parse_mode="HTML"
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
            "❌ Raqamingiz tizimdan topilmadi. Iltimos, o'quv markaz ma'muriyatiga murojaat qiling.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Ota-onani bazaga saqlash
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parent_id = await db.add_parent(message.from_user.id, norm_phone, now_str)

    await message.answer(
        "✅ Telefon raqamingiz tasdiqlandi!\n"
        "Quyidagi o'quvchi(lar) sizning farzandingizmi? Iltimos, tasdiqlang:",
        reply_markup=ReplyKeyboardRemove()
    )

    # Topilgan har bir bola uchun alohida tasdiqlash tugmasi chiqarish
    for st in matched_students:
        is_already_linked = await db.is_student_linked_to_parent(st["id"], parent_id)
        if is_already_linked:
            continue

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha", callback_data=f"confirm_ch:{st['id']}:{parent_id}"),
                InlineKeyboardButton(text="❌ Yo'q", callback_data=f"reject_ch:{st['id']}:{parent_id}")
            ]
        ])
        await message.answer(
            f"👤 Farzandingiz <b>{st['full_name']}</b> ({st['village_name']}, {st.get('school_name', '')})mi?",
            reply_markup=kb,
            parse_mode="HTML"
        )

# --- 3. Farzandni tasdiqlash callbacki ---
@parent_router.callback_query(F.data.startswith("confirm_ch:"))
async def cb_confirm_child(callback: types.CallbackQuery, db: Database):
    parts = callback.data.split(":")
    student_id = int(parts[1])
    parent_id = int(parts[2])

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

    # Bog'lash va audit logga yozish
    await db.link_student_parent(student_id, parent_id)
    await db.log_event(
        actor_role="parent",
        actor_id=callback.from_user.id,
        action="confirm_child",
        student_id=student_id
    )

    await callback.message.edit_text(
        f"✅ <b>{student['full_name']}</b> muvaffaqiyatli tasdiqlandi!\n\n"
        f"Endi har kuni farzandingizning mashinaga chiqqanligi, markazga yetib borgani "
        f"va uyga yetkazilgani haqida avtomatik bildirishnomalar olasiz.",
        parse_mode="HTML"
    )
    await callback.answer("Tasdiqlandi!")

# --- 4. Farzandni rad etish callbacki ---
@parent_router.callback_query(F.data.startswith("reject_ch:"))
async def cb_reject_child(callback: types.CallbackQuery, db: Database):
    parts = callback.data.split(":")
    student_id = int(parts[1])
    parent_id = int(parts[2])

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
        f"❌ <b>{st_name}</b> farzandingiz emas deb belgilandi.",
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
        text += f"👤 <b>{s['full_name']}</b>\n"
        text += f"🏘 Mahalla: <b>{s['village_name']}</b> ({s.get('school_name', '-')})\n"
        schedules = await db.get_student_schedules(s["id"])
        if schedules:
            text += "📅 Dars vaqtlari:\n"
            for sch in schedules:
                day_name = WEEKDAYS_UZ[sch["weekday"]]
                text += f"  • {day_name}: <code>{sch['start_time']} - {sch['end_time']}</code>\n"
        else:
            text += "📅 Dars jadvali kiritilmagan.\n"
        text += "\n"

    text += "<i>Bot faqat qatnov holati haqida ma'lumot beradi. Savollar bo'lsa o'quv markaz ma'muriyatiga murojaat qiling.</i>"

    rows = []
    for s in students:
        rows.append([InlineKeyboardButton(text=f"🚫 {s['full_name']} bugun kelmaydi", callback_data=f"absent_btn:{s['id']}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@parent_router.callback_query(F.data.startswith("absent_btn:"))
async def cb_student_absent(callback: types.CallbackQuery, db: Database):
    parent = await db.get_parent_by_telegram_id(callback.from_user.id)
    if not parent:
        await callback.answer("Faqat ro'yxatdan o'tgan ota-onalar uchun!", show_alert=True)
        return

    student_id = int(callback.data.split(":")[1])
    is_linked = await db.is_student_linked_to_parent(student_id, parent["id"])
    if not is_linked:
        await callback.answer("Bu sizning farzandingiz emas!", show_alert=True)
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
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
        try:
            await callback.bot.send_message(
                chat_id=info["driver_tg_id"],
                text=f"ℹ️ <b>{info['student_name']}</b> ({info['village_name']}) bugun kelmaydi deb belgilandi.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("Farzandingiz bugun kelmaydi deb belgilandi.", show_alert=True)
