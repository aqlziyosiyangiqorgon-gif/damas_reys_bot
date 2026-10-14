from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from models.enums import UserRole
from database.db import Database
from utils.text import escape_html
from utils.start_banner import answer_start

common_router = Router()


@common_router.message(Command("myid"))
async def cmd_my_id(message: types.Message):
    await message.answer(f"Telegram ID: <code>{message.from_user.id}</code>")

def get_admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚐 Bugungi reyslar"), KeyboardButton(text="📊 Statistika / Hisobot")],
            [KeyboardButton(text="📥 Excel boshqaruvi"), KeyboardButton(text="📢 Xabar yuborish")],
            [KeyboardButton(text="👥 Foydalanuvchilar"), KeyboardButton(text="🚫 Bugun kelmaganlar")],
            [KeyboardButton(text="🧩 Bugungi reyslarni tuzish")]
        ],
        resize_keyboard=True
    )

@common_router.message(CommandStart())
@common_router.message(Command("admin"))
async def cmd_start(message: types.Message, user_role: UserRole, db: Database):
    if user_role == UserRole.ADMIN:
        text = (
            f"👑 <b>Assalomu alaykum, {escape_html(message.from_user.first_name)}!</b>\n"
            f"<b>AQL ZIYOSI</b> transport tizimi <b>Admin boshqaruv paneliga</b> xush kelibsiz.\n\n"
            f"Quyidagi tugmalar yoki buyruqlar orqali tizimni boshqarishingiz mumkin:\n"
            f"• <b>/qoshish</b> — Yangi o'quvchi qo'shish\n"
            f"• <b>/shablon</b> — Excel shablon va ommaviy yuklash\n"
            f"• <b>/haydovchilar</b> — Haydovchilar ro'yxati va holati\n"
            f"• <b>/xodimlar</b> — Xodimlar ro'yxati va holati\n"
            f"• <b>/jadval</b> — O'quvchilar dars jadvali\n"
            f"• <b>/qishloqlar</b> — Mahallalar va yo'nalishlar tartibi\n"
            f"• <b>/xabar</b> — Auditoriya bo'yicha e'lon yuborish\n"
            f"• <b>/ochirish</b> — O'quvchini tizimdan o'chirish"
        )
        await answer_start(message, text, reply_markup=get_admin_menu_kb())
        return

    elif user_role == UserRole.DRIVER:
        from handlers.driver import get_driver_menu_kb
        await answer_start(
            message,
            f"🚐 <b>Assalomu alaykum, {escape_html(message.from_user.first_name)}!</b>\n"
            f"Siz tizimda <b>Haydovchi</b> sifatida ro'yxatdansiz.\n\n"
            f"Reyslaringizni ko'rish yoki markaz bilan bog'lanish uchun pastdagi menyudan foydalaning:",
            reply_markup=get_driver_menu_kb()
        )
        return

    elif user_role == UserRole.STAFF:
        from handlers.staff import get_staff_menu_kb
        await answer_start(
            message,
            f"🏫 <b>Assalomu alaykum, {escape_html(message.from_user.first_name)}!</b>\n"
            f"Siz tizimda <b>Xodim</b> sifatida ro'yxatdansiz.\n\n"
            f"Bugungi kelish va uyga topshirish reyslarini pastdagi tugmalardan oching.\n"
            f"Buyruqlar: <b>/qabul</b>, <b>/markaz</b>, <b>/topshirish</b>",
            reply_markup=get_staff_menu_kb()
        )
        return

    elif user_role == UserRole.PARENT:
        parent = await db.get_parent_by_telegram_id(message.from_user.id)
        if parent:
            students = await db.get_parent_students(parent["id"])
            if students:
                from handlers.parent import show_parent_dashboard, get_parent_menu_kb
                await answer_start(
                    message,
                    "👨‍👩‍👧‍👦 <b>AQL ZIYOSI</b> ota-ona kabinetiga xush kelibsiz!",
                    reply_markup=get_parent_menu_kb()
                )
                await show_parent_dashboard(message, students, db)
                return

    # UserRole.GUEST: Ro'yxatdan o'tmagan ota-onadan telefon so'rash
    from handlers.parent import get_contact_keyboard
    await answer_start(
        message,
        "Assalomu alaykum! <b>AQL ZIYOSI</b> o'quvchilar transport botiga xush kelibsiz.\n\n"
        "Farzandingizning qatnovini real vaqtda kuzatish uchun iltimos, "
        "quyidagi tugma orqali <b>telefon raqamingizni yuboring</b>:",
        reply_markup=get_contact_keyboard()
    )
