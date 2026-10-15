import pytest
import os
import shutil
import time
import uuid
import sqlite3
from datetime import datetime
from unittest.mock import AsyncMock
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from config import settings, get_default_db_path, get_default_backup_dir
from database.db import Database
from services.scheduler_service import SchedulerService
from handlers.common import common_router
from handlers.admin import admin_router
from handlers.driver import driver_router
from handlers.staff import staff_router
from handlers.parent import parent_router
from middlewares.roles import RoleMiddleware

@pytest.mark.asyncio
async def test_phase_10_railway_deploy_and_volume(tmp_path):
    # =============================================================
    # 1. RAILWAY VOLUME VA DINAMIK DB YO'LI TEKSHIRUVI
    # =============================================================
    test_volume_dir = f"temp_vol_{uuid.uuid4().hex[:8]}"
    test_vol_db_path = os.path.join(test_volume_dir, "damas_reys.db")

    try:
        # DB avtomatik o'z papkasini yaratib initsializatsiya qilishi
        db = Database(test_vol_db_path)
        await db.init_db()
        assert os.path.exists(test_vol_db_path)

        villages = await db.get_villages()
        assert len(villages) == 10
        zones = await db.get_zones()
        assert len(zones) == 4

        # Settings dagi yo'l funksiyalari
        assert settings.get_db_path() is not None
        assert settings.get_backup_dir() is not None
        assert settings.BACKUP_RETENTION_DAYS == 7

        # =============================================================
        # 2. ZAXIRALASH VA 7 KUNLIK ESKI FAYLLARNI TOZALASH TEKSHIRUVI
        # =============================================================
        test_backup_dir = str(tmp_path / "backups")
        scheduler = SchedulerService(db=db)

        # 2.1 Yangi zaxira olish
        created_backup = await scheduler.run_backup_db(db_path=test_vol_db_path, backup_dir=test_backup_dir)
        assert os.path.exists(created_backup)
        with sqlite3.connect(created_backup) as backup_conn:
            assert backup_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup_conn.execute("SELECT COUNT(*) FROM villages").fetchone()[0] == 10

        # 2.2 Soxta eski (10 kunlik) va yangi (2 kunlik) zaxira fayllar yaratish
        old_file = os.path.join(test_backup_dir, "damas_reys_20261001.db")
        recent_file = os.path.join(test_backup_dir, "damas_reys_20261012.db")

        with open(old_file, "w") as f:
            f.write("old-backup")
        with open(recent_file, "w") as f:
            f.write("recent-backup")

        now_ts = time.time()
        # 10 kun oldingi vaqt (10 * 86400 sekund)
        ten_days_ago = now_ts - (10 * 86400)
        os.utime(old_file, (ten_days_ago, ten_days_ago))

        # 2 kun oldingi vaqt (2 * 86400 sekund)
        two_days_ago = now_ts - (2 * 86400)
        os.utime(recent_file, (two_days_ago, two_days_ago))

        # Tozalash funksiyasini chaqirish (7 kundan eski fayllarni o'chirish)
        deleted_count = scheduler.cleanup_old_backups(backup_dir=test_backup_dir, retention_days=7)

        # 10 kunlik fayl o'chirilishi, 2 kunlik va bugungi zaxira saqlanib qolishi kerak
        assert deleted_count == 1
        assert not os.path.exists(old_file), "10 kundan eski fayl o'chirilmadi!"
        assert os.path.exists(recent_file), "2 kunlik fayl saqlanib qolmadi!"
        assert os.path.exists(created_backup), "Bugungi zaxira saqlanib qolmadi!"

        # =============================================================
        # 3. PROCFILE VA REQUIREMENTS.TXT TEKSHIRUVI
        # =============================================================
        assert os.path.exists("Procfile"), "Procfile mavjud emas!"
        with open("Procfile", "r", encoding="utf-8") as f:
            procfile_content = f.read().strip()
        assert "worker: python main.py" in procfile_content, "Procfile da worker to'g'ri ko'rsatilmagan!"

        assert os.path.exists("requirements.txt"), "requirements.txt mavjud emas!"
        with open("requirements.txt", "r", encoding="utf-8") as f:
            req_content = f.read()

        required_packages = [
            "aiogram",
            "aiosqlite",
            "apscheduler",
            "pydantic-settings",
            "openpyxl",
            "tzdata",
            "pytest",
            "pytest-asyncio"
        ]
        for pkg in required_packages:
            assert pkg in req_content, f"requirements.txt da {pkg} mavjud emas!"

        # =============================================================
        # 4. DISPATCHER VA ROUTERLAR IERARXIYASI TEKSHIRUVI
        # =============================================================
        # Barcha routerlar mavjud va Router obyektlari ekanligi
        dp = Dispatcher()

        dp.message.middleware(RoleMiddleware(db))
        dp.callback_query.middleware(RoleMiddleware(db))

        # Routerlarni ulash ketma-ketligi
        dp.include_router(common_router)
        dp.include_router(admin_router)
        dp.include_router(driver_router)
        dp.include_router(staff_router)
        dp.include_router(parent_router)

        registered_routers = dp.sub_routers
        assert len(registered_routers) == 5
        router_names = [r.__class__.__name__ for r in registered_routers]
        assert all(name == "Router" for name in router_names)

        # Bir xil jadval tugmasi bosilganda ota-ona admin routerdan o'tib parent handleriga yetadi.
        parent_tg_id = max(settings.admin_ids_list or [0]) + 1
        await db.add_parent(parent_tg_id, "+998901234567")
        db.get_parent_by_telegram_id = AsyncMock(wraps=db.get_parent_by_telegram_id)
        bot = Bot(token="123456:TESTTOKEN")
        bot.session.make_request = AsyncMock(return_value=Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=parent_tg_id, type="private"),
            text="ok",
        ))
        update = Update(
            update_id=1,
            message=Message(
                message_id=2,
                date=datetime.now(),
                chat=Chat(id=parent_tg_id, type="private"),
                from_user=User(id=parent_tg_id, is_bot=False, first_name="Parent"),
                text="📅 Dars jadvali",
            ),
        )
        try:
            await dp.feed_update(bot, update, db=db)
            db.get_parent_by_telegram_id.assert_awaited_once_with(parent_tg_id)
            bot.session.make_request.assert_awaited_once()
            sent_method = bot.session.make_request.await_args.args[1]
            assert isinstance(sent_method, SendMessage)
            assert sent_method.text == "Sizga biriktirilgan farzandlar topilmadi."

            # Admin callbacks arrive with the bot as callback.message.from_user;
            # route them through the dispatcher and verify the correct view edits
            # the existing bot message (rather than invoking message handlers).
            admin_id = settings.admin_ids_list[0]
            await db.add_driver(987654321, "Test Haydovchi", "+998901234567")
            await db.add_staff(987654322, "Test Xodim", "+998911234567")
            await db.add_student("Test O'quvchi", 1, "+998901234567")

            for update_id, target, expected_title in (
                (10, "drivers", "Haydovchilar ro'yxati"),
                (11, "staff", "Xodimlar (Staff) ro'yxati"),
                (12, "villages", "Mahallalar va marshrut zonalari"),
                (13, "students", "Faol o'quvchilar ro'yxati"),
            ):
                nav_update = Update(
                    update_id=update_id,
                    callback_query=CallbackQuery(
                        id=f"callback-{update_id}",
                        from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                        chat_instance="admin-chat",
                        data=f"admin_nav:{target}",
                        message=Message(
                            message_id=100 + update_id,
                            date=datetime.now(),
                            chat=Chat(id=admin_id, type="private"),
                            from_user=User(id=999999999, is_bot=True, first_name="Bot"),
                            text="Admin bo'limlari",
                        ),
                    ),
                )
                bot.session.make_request.reset_mock()
                await dp.feed_update(bot, nav_update, db=db)
                edit_method = next(
                    method
                    for call in bot.session.make_request.await_args_list
                    for method in (call.args[1],)
                    if isinstance(method, EditMessageText)
                )
                assert isinstance(edit_method, EditMessageText)
                assert expected_title in edit_method.text

            state_context = dp.fsm.get_context(bot=bot, chat_id=admin_id, user_id=admin_id)
            from handlers.admin import EditStudentStates
            assert await state_context.get_state() == EditStudentStates.selecting_student.state
        finally:
            await bot.session.close()

    finally:
        if os.path.exists(test_volume_dir):
            try:
                shutil.rmtree(test_volume_dir)
            except Exception:
                pass
