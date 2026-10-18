from utils.clock import now_tz
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from models.callbacks import (
    AbsentButtonCallback,
    AbsentSelectCallback,
    ConfirmChildCallback,
    ParentChildCallback,
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
MONTHS_UZ = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")


def _billing_period_label(period: str) -> str:
    try:
        year, month = period.split("-")
        return f"{MONTHS_UZ[int(month) - 1]} {year}"
    except (ValueError, IndexError, AttributeError):
        return str(period)


def _billing_status(invoice: dict) -> str:
    if invoice.get("status") == "waived":
        return "Hisob bekor qilingan"
    if int(invoice.get("due", 0)) == 0:
        return "To‘langan" if int(invoice.get("amount", 0)) > 0 else "Qarz yo‘q"
    if int(invoice.get("paid_total", 0)) > 0:
        return "Qisman to‘langan"
    return "To‘lanmagan"

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
            [KeyboardButton(text="📅 Dars jadvali"), KeyboardButton(text="💰 Oylik to‘lovlar")],
            [KeyboardButton(text="👨‍👩‍👧‍👦 Farzandlarim"), KeyboardButton(text="➕ Farzandni bog‘lash")],
            [KeyboardButton(text="📞 Markaz bilan aloqa")],
        ],
        resize_keyboard=True
    )


async def prompt_unlinked_children(
    message: types.Message,
    db: Database,
    parent_id: int,
    phone: str,
    candidates: list[dict] | None = None,
) -> tuple[int, int]:
    """Ask the parent to confirm every newly matched child sharing their registered phone."""
    matches = candidates if candidates is not None else await db.find_students_by_phone(phone)
    prompted = 0
    for student in matches:
        if await db.is_student_linked_to_parent(student["id"], parent_id):
            continue
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Ha",
                callback_data=ConfirmChildCallback(student_id=student["id"], parent_id=parent_id).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Yo'q",
                callback_data=RejectChildCallback(student_id=student["id"], parent_id=parent_id).pack(),
            ),
        ]])
        await message.answer(
            f"👤 Farzandingiz <b>{escape_html(student['full_name'])}</b> "
            f"({escape_html(student['village_name'])}, {escape_html(student.get('school_name', ''))})mi?",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        prompted += 1
    return len(matches), prompted

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

    # Bir raqamga ulangan barcha farzandlar alohida tasdiqlanadi.
    await prompt_unlinked_children(message, db, parent_id, norm_phone, matched_students)

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
    text = (
        "👨‍👩‍👧‍👦 <b>Oila kabineti</b>\n\n"
        "Har bir farzandingiz ma’lumoti alohida kabinetda ko‘rsatiladi. "
        "Kerakli farzandingizni tanlang:"
    )
    rows = [[InlineKeyboardButton(
        text=f"👤 {str(s['full_name'])[:48]}",
        callback_data=ParentChildCallback(action="open", student_id=s["id"]).pack(),
    )] for s in students]
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


def _parent_child_actions_keyboard(student_id: int) -> InlineKeyboardMarkup:
    actions = [
        ("👤 Kabinet bosh sahifasi", "open"),
        ("📍 Hozirgi holati", "track"),
        ("📅 Dars jadvali", "schedule"),
        ("💰 To‘lovlari", "billing"),
        ("🚫 Bugun kelmaydi", "absence"),
        ("👨‍👩‍👧‍👦 Boshqa farzandni tanlash", "list"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=label,
            callback_data=ParentChildCallback(action=action, student_id=student_id).pack(),
        )
    ] for label, action in actions])


async def _parent_child_profile_text(db: Database, student: dict, tg_id: int) -> str:
    today = now_tz().strftime("%Y-%m-%d")
    status = await db.get_student_current_status(student["id"], today)
    schedules = await db.get_student_schedules(student["id"])
    parent = await db.get_parent_by_telegram_id(tg_id)
    if not parent:
        return "Ota-ona akkaunti topilmadi. Qaytadan /start bosing."
    billing = await db.get_parent_billing_summary(parent["id"], now_tz().strftime("%Y-%m"))
    child_bill = next(
        (item for item in billing.get("children", []) if int(item.get("student_id", -1)) == int(student["id"])),
        None,
    )
    text = (
        f"👤 <b>{escape_html(student['full_name'])}</b>\n"
        f"🏘 Mahalla: {escape_html(student.get('village_name') or 'Ko‘rsatilmagan')}\n\n"
        f"📍 <b>Bugungi holat:</b> {escape_html(status.get('status_text', 'Holat mavjud emas'))}\n"
    )
    trip = status.get("trip")
    if trip:
        direction = "Markazga kelish" if trip.get("direction") == "to_center" else "Uyga qaytish"
        text += f"🚐 {escape_html(direction)} · {escape_html(trip.get('planned_time') or 'vaqt belgilanmagan')}\n"
        if trip.get("driver_name"):
            text += f"🚗 Haydovchi: {escape_html(trip['driver_name'])}"
            if trip.get("driver_phone"):
                text += f" · {escape_html(trip['driver_phone'])}"
            text += "\n"
    if status.get("reason"):
        text += f"💬 Izoh: {escape_html(status['reason'])}\n"

    text += "\n📅 <b>Dars jadvali</b>\n"
    if schedules:
        for row in sorted(schedules, key=lambda item: item["weekday"]):
            weekday = row["weekday"]
            day_name = WEEKDAYS_UZ[weekday] if 0 <= weekday < len(WEEKDAYS_UZ) else "Kun"
            text += f"• {escape_html(day_name)}: <code>{escape_html(row['start_time'])}–{escape_html(row['end_time'])}</code>\n"
    else:
        text += "Dars jadvali kiritilmagan.\n"

    text += "\n💰 <b>To‘lov holati</b>\n"
    if child_bill:
        invoice = child_bill.get("current_invoice")
        if invoice:
            current_due = int(invoice.get("due", 0))
            invoice_amount = int(invoice.get("amount", 0)) - int(invoice.get("discount", 0))
            text += f"{escape_html(_billing_period_label(invoice.get('period', '')))} · {_billing_status(invoice)}\n"
            text += f"Hisob: {invoice_amount:,} so‘m · To‘langan: {int(invoice.get('paid_total', 0)):,} so‘m\n".replace(",", " ")
            text += f"Joriy oy qoldig‘i: <b>{current_due:,} so‘m</b>\n".replace(",", " ")
        else:
            text += "Joriy oy hisobi hali ochilmagan.\n"
        overdue = child_bill.get("overdue_invoices", [])
        if overdue:
            old_due = sum(int(item.get("due", 0)) for item in overdue)
            text += f"Oldingi oylar qarzi: <b>{old_due:,} so‘m</b>\n".replace(",", " ")
        total_due = int(child_bill.get("total_due", 0))
        text += f"Jami shu farzand bo‘yicha qoldiq: <b>{total_due:,} so‘m</b>\n".replace(",", " ")
    else:
        text += "Hisob ma’lumoti hozircha yo‘q.\n"
    text += "\nQuyidagi tugmalardan kerakli bo‘limni tanlang."
    return text


async def _parent_child_section_text(db: Database, student: dict, tg_id: int, action: str) -> str:
    if action == "open":
        return await _parent_child_profile_text(db, student, tg_id)
    if action == "track":
        status = await db.get_student_current_status(student["id"], now_tz().strftime("%Y-%m-%d"))
        text = (
            f"📍 <b>{escape_html(student['full_name'])} · bugungi holat</b>\n\n"
            f"Holati: <b>{escape_html(status.get('status_text', 'Holat mavjud emas'))}</b>"
        )
        trip = status.get("trip")
        if trip:
            direction = "Markazga kelish" if trip.get("direction") == "to_center" else "Uyga qaytish"
            text += f"\n🚐 {escape_html(direction)} · {escape_html(trip.get('planned_time') or 'vaqt belgilanmagan')}"
            if trip.get("driver_name"):
                text += f"\n🚗 Haydovchi: {escape_html(trip['driver_name'])}"
                if trip.get("driver_phone"):
                    text += f" · {escape_html(trip['driver_phone'])}"
        if status.get("reason"):
            text += f"\n💬 Izoh: {escape_html(status['reason'])}"
        return text
    if action == "schedule":
        schedules = await db.get_student_schedules(student["id"])
        text = f"📅 <b>{escape_html(student['full_name'])} · Dars vaqtlari</b>\n\n"
        if not schedules:
            return text + "Dars jadvali kiritilmagan."
        for row in sorted(schedules, key=lambda item: item["weekday"]):
            weekday = row["weekday"]
            day_name = WEEKDAYS_UZ[weekday] if 0 <= weekday < len(WEEKDAYS_UZ) else "Kun"
            text += f"• {escape_html(day_name)}: <code>{escape_html(row['start_time'])} - {escape_html(row['end_time'])}</code>\n"
        return text
    if action == "billing":
        parent = await db.get_parent_by_telegram_id(tg_id)
        if not parent:
            return "Ota-ona akkaunti topilmadi. Qaytadan /start bosing."
        summary = await db.get_parent_billing_summary(parent["id"], now_tz().strftime("%Y-%m"))
        child = next(
            (item for item in summary.get("children", []) if int(item.get("student_id", -1)) == int(student["id"])),
            None,
        )
        text = (
            f"💰 <b>{escape_html(student['full_name'])} · to‘lovlari</b>\n"
            "To‘lov muddati: har oyning 10-sanasigacha.\n\n"
        )
        if not child:
            return text + "Hisob ma’lumoti hozircha yo‘q."
        invoice = child.get("current_invoice")
        if invoice:
            paid = int(invoice.get("paid_total", 0))
            due = int(invoice.get("due", 0))
            amount = int(invoice.get("amount", 0)) - int(invoice.get("discount", 0))
            invoice_status = _billing_status(invoice)
            text += (
                f"📅 {escape_html(_billing_period_label(invoice.get('period', '')))} · {invoice_status}\n"
                f"Hisob: <b>{amount:,} so‘m</b>\n"
                f"To‘langan: {paid:,} so‘m\n"
                f"Qolgan: <b>{due:,} so‘m</b>\n"
            ).replace(",", " ")
        else:
            text += "Joriy oy uchun hisob hali ochilmagan.\n"
        overdue = child.get("overdue_invoices", [])
        if overdue:
            total_old = sum(int(item.get("due", 0)) for item in overdue)
            text += f"⏳ Oldingi oylar qarzi: <b>{total_old:,} so‘m</b>\n".replace(",", " ")
            for item in overdue:
                text += f"• {escape_html(item.get('period', ''))}: {int(item.get('due', 0)):,} so‘m\n".replace(",", " ")
        return text
    return "Kerakli bo‘lim topilmadi."


@parent_router.callback_query(ParentChildCallback.filter())
async def cb_parent_child(
    callback: types.CallbackQuery,
    db: Database,
    state: FSMContext,
    callback_data: ParentChildCallback | None = None,
):
    callback_data = callback_data or ParentChildCallback.unpack(callback.data)
    parent = await db.get_parent_by_telegram_id(callback.from_user.id)
    if not parent:
        await callback.answer("Avval ota-ona sifatida ro‘yxatdan o‘ting.", show_alert=True)
        return
    if callback_data.action != "absence":
        await _clear_absence_reason_state(state)
    students = await db.get_parent_students(parent["id"])
    if callback_data.action == "list":
        if not students:
            await callback.message.edit_text("Sizga biriktirilgan farzandlar topilmadi.")
        else:
            rows = [[InlineKeyboardButton(
                text=f"👤 {str(student['full_name'])[:48]}",
                callback_data=ParentChildCallback(action="open", student_id=student["id"]).pack(),
            )] for student in students]
            await callback.message.edit_text(
                "👨‍👩‍👧‍👦 <b>Qaysi farzandingiz kabinetini ochamiz?</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
        await callback.answer()
        return
    student = next((row for row in students if int(row["id"]) == callback_data.student_id), None)
    if not student:
        await callback.answer("Bu farzand sizning kabinetingizga biriktirilmagan.", show_alert=True)
        return
    if callback_data.action == "absence":
        await state.update_data(absent_student_id=student["id"])
        await state.set_state(ParentStates.waiting_for_absence_reason)
        await callback.message.edit_text(
            f"👤 <b>{escape_html(student['full_name'])}</b> bugun darsga kela olmaslik sababini qisqacha yozing:\n"
            "<i>(masalan: shamollab qoldi / oilaviy sabab)</i>",
            parse_mode="HTML",
        )
        await callback.answer()
        return
    text = await _parent_child_section_text(db, student, callback.from_user.id, callback_data.action)
    await callback.message.edit_text(
        text,
        reply_markup=_parent_child_actions_keyboard(student["id"]),
        parse_mode="HTML",
    )
    await callback.answer()

# --- 6. DOIMIY REPLY MENYU HANDLERLARI ---

@parent_router.message(F.text == "👨‍👩‍👧‍👦 Farzandlarim")
async def parent_family_home(message: types.Message, db: Database, state: FSMContext | None = None):
    await _clear_absence_reason_state(state)
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Siz hali ota-ona sifatida ro‘yxatdan o‘tmagansiz. /start ni bosing.")
        return
    students = await db.get_parent_students(parent["id"])
    if not students:
        await message.answer("Sizga biriktirilgan farzandlar topilmadi.", reply_markup=get_parent_menu_kb())
        return
    await show_parent_dashboard(message, students, db)

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
    if len(students) == 1:
        student = students[0]
        text = await _parent_child_section_text(db, student, message.from_user.id, "track")
        await message.answer(text, reply_markup=_parent_child_actions_keyboard(student["id"]), parse_mode="HTML")
        return
    status_lines = ["📍 <b>Farzandlarning bugungi qisqa holati</b> (batafsil ko‘rish uchun tanlang):\n"]
    rows = [[InlineKeyboardButton(
        text=f"📍 {str(student['full_name'])[:45]}",
        callback_data=ParentChildCallback(action="track", student_id=student["id"]).pack(),
    )] for student in students]
    for student in students:
        status = await db.get_student_current_status(student["id"], now_tz().strftime("%Y-%m-%d"))
        status_lines.append(f"• <b>{escape_html(student['full_name'])}</b> — {escape_html(status.get('status_text', 'Holat mavjud emas'))}")
    await message.answer(
        "\n".join(status_lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )

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
    if len(students) == 1:
        student = students[0]
        text = await _parent_child_section_text(db, student, message.from_user.id, "schedule")
        await message.answer(text, reply_markup=_parent_child_actions_keyboard(student["id"]), parse_mode="HTML")
        return

    rows = [[InlineKeyboardButton(
        text=f"📅 {str(student['full_name'])[:45]}",
        callback_data=ParentChildCallback(action="schedule", student_id=student["id"]).pack(),
    )] for student in students]
    await message.answer(
        "📅 <b>Qaysi farzandingiz dars jadvalini ko‘rmoqchisiz?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )

# c) ➕ Yangi farzandni bog'lash
@parent_router.message(F.text == "➕ Farzandni bog‘lash")
async def parent_link_another_child(message: types.Message, db: Database, state: FSMContext | None = None):
    await _clear_absence_reason_state(state)
    parent = await db.get_parent_by_telegram_id(message.from_user.id)
    if not parent:
        await message.answer("Avval /start orqali telefon raqamingizni tasdiqlang.")
        return

    matched_count, prompted_count = await prompt_unlinked_children(
        message, db, parent["id"], parent["phone"]
    )
    if prompted_count:
        await message.answer(
            f"{prompted_count} ta yangi farzandni tasdiqlash uchun yuqoridagi ✅ Ha tugmasini bosing.",
            reply_markup=get_parent_menu_kb(),
        )
    elif matched_count:
        await message.answer(
            "Telefon raqamingizga mos barcha faol farzandlar allaqachon bog‘langan.",
            reply_markup=get_parent_menu_kb(),
        )
    else:
        await message.answer(
            "Bu telefon raqamiga mos faol o‘quvchi topilmadi. Raqam bazada to‘g‘ri kiritilganini markaz adminidan tekshirtiring.",
            reply_markup=get_parent_menu_kb(),
        )

# d) 💰 Oylik to'lovlar va farzandlar bo'yicha qarzdorlik
@parent_router.message(F.text == "💰 Oylik to‘lovlar")
async def parent_view_billing(message: types.Message, db: Database, state: FSMContext | None = None):
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
        student = students[0]
        text = await _parent_child_section_text(db, student, message.from_user.id, "billing")
        await message.answer(text, reply_markup=_parent_child_actions_keyboard(student["id"]), parse_mode="HTML")
        return
    rows = [[InlineKeyboardButton(
        text=f"💰 {str(student['full_name'])[:45]}",
        callback_data=ParentChildCallback(action="billing", student_id=student["id"]).pack(),
    )] for student in students]
    await message.answer(
        "💰 <b>To‘lovini ko‘rish uchun farzandingizni tanlang.</b>\n"
        "Har bir farzandning hisob-kitobi alohida ko‘rsatiladi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )

# e) 📞 Markaz bilan aloqa
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
