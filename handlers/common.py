from aiogram import Router, types, F
from aiogram.filters import CommandStart
from models.enums import UserRole

common_router = Router()

@common_router.message(CommandStart(), F.func(lambda msg, user_role=None: user_role in [UserRole.ADMIN, UserRole.STAFF, UserRole.DRIVER] if user_role else False))
async def cmd_start(message: types.Message, user_role: UserRole):
    role_titles = {
        UserRole.ADMIN: "👑 Admin",
        UserRole.STAFF: "🏫 Xodim (Staff)",
        UserRole.DRIVER: "🚐 Haydovchi",
        UserRole.PARENT: "👨‍👩‍👧‍👦 Ota-ona",
        UserRole.GUEST: "👤 Mehmon (Ro'yxatdan o'tmagan)"
    }
    
    role_name = role_titles.get(user_role, "Foydalanuvchi")
    
    await message.answer(
        f"👋 Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n"
        f"<b>AQL ZIYOSI</b> transport tizimiga xush kelibsiz.\n\n"
        f"Sizning rolingiz: <b>{role_name}</b>\n\n"
        "Tizim 1-Fazada: Ma'lumotlar bazasi va rollar to'liq ishga tushirildi.",
        parse_mode="HTML"
    )
