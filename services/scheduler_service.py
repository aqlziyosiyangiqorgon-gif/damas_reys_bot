import os
import shutil
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database.db import Database
from services.trip_builder import TripBuilder
from models.enums import TripDirection, TripStatus

logger = logging.getLogger(__name__)

class SchedulerService:
    def __init__(self, db: Database, bot: Optional[Bot] = None, tz_str: str = settings.TIMEZONE):
        self.db = db
        self.bot = bot
        self.tz_str = tz_str
        self.timezone = ZoneInfo(tz_str)
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)

    async def check_and_send_reminders(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """
        Eslatma va Eskalatsiya mantiqi:
        1. 1-eslatma: planned_time dan 30 daqiqa oldin va reminder_count == 0.
        2. 2-eslatma: oxirgi eslatmadan 10 daqiqa o'tgach va reminder_count == 1.
        3. Eskalatsiya (Admin): oxirgi eslatmadan yana 10 daqiqa o'tgach va reminder_count == 2 (reminder_count=3 ga o'tkaziladi).
        """
        if now_dt is None:
            now_dt = datetime.now(self.timezone).replace(tzinfo=None)

        stats = {
            "first_reminders": 0,
            "second_reminders": 0,
            "escalations": 0
        }

        trips = await self.db.get_trips_needing_reminder(now_dt)

        for trip in trips:
            if trip["status"] != TripStatus.APPROVED.value:
                continue

            planned_dt = datetime.strptime(f"{trip['date']} {trip['planned_time']}", "%Y-%m-%d %H:%M")
            reminder_count = trip.get("reminder_count", 0)
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            # --- 1-ESLATMA (30 daqiqa oldin) ---
            if reminder_count == 0:
                if now_dt >= (planned_dt - timedelta(minutes=30)):
                    await self._send_driver_reminder(trip, reminder_num=1)
                    await self.db.update_trip_reminder(trip["id"], 1, now_str)
                    stats["first_reminders"] += 1

            # --- 2-ESLATMA (1-eslatmadan 10 daqiqa o'tgach) ---
            elif reminder_count == 1:
                last_dt = datetime.strptime(trip["last_reminder_at"], "%Y-%m-%d %H:%M:%S")
                if now_dt >= (last_dt + timedelta(minutes=10)):
                    await self._send_driver_reminder(trip, reminder_num=2)
                    await self.db.update_trip_reminder(trip["id"], 2, now_str)
                    stats["second_reminders"] += 1

            # --- ESKALATSIYA (2-eslatmadan yana 10 daqiqa o'tgach adminga ogohlantirish) ---
            elif reminder_count == 2:
                last_dt = datetime.strptime(trip["last_reminder_at"], "%Y-%m-%d %H:%M:%S")
                if now_dt >= (last_dt + timedelta(minutes=10)):
                    await self._send_admin_escalation(trip)
                    await self.db.update_trip_reminder(trip["id"], 3, now_str)
                    stats["escalations"] += 1

        return stats

    async def _send_driver_reminder(self, trip: dict, reminder_num: int):
        trip_id = trip["id"]
        dir_text = "Markazga kelish" if trip["direction"] == TripDirection.TO_CENTER.value else "Uyga qaytish"
        driver_tg_id = trip.get("driver_tg_id")

        header = "⏰ <b>1-eslatma: Reys vaqti yaqinlashmoqda!</b>" if reminder_num == 1 else "⚠️ <b>2-eslatma: Shoshiling! Reys vaqti yetib keldi.</b>"
        items_text = ""
        for idx, it in enumerate(trip.get("items", []), 1):
            pickup = f" ({it['pickup_point']})" if it.get("pickup_point") else ""
            items_text += f"  {idx}. {it['full_name']} — {it['village_name']}{pickup}\n"

        text = (
            f"{header}\n\n"
            f"🚐 <b>Reys #{trip_id}</b> ({dir_text})\n"
            f"⏱ Rejalashtirilgan vaqt: <b>{trip['planned_time']}</b>\n"
            f"👥 O'quvchilar ro'yxati:\n{items_text}\n"
            f"Iltimos, harakatni boshlash uchun [🚗 Yo'ldaman] tugmasini bosing:"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Yo'ldaman", callback_data=f"driver_start_trip:{trip_id}")]
        ])

        if self.bot and driver_tg_id:
            try:
                await self.bot.send_message(chat_id=driver_tg_id, text=text, reply_markup=kb, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Haydovchiga eslatma yuborishda xatolik: {e}")

    async def _send_admin_escalation(self, trip: dict):
        driver_name = trip.get("driver_name") or "Biriktirilgan haydovchi"
        driver_phone = trip.get("driver_phone") or "Noma'lum"
        dir_text = "Markazga kelish" if trip["direction"] == TripDirection.TO_CENTER.value else "Uyga qaytish"
        alert_text = (
            f"⚠️ <b>DIQQAT! Reysga javob berilmadi (Eskalatsiya)!</b>\n\n"
            f"Haydovchi: <b>{driver_name}</b> (Tel: {driver_phone})\n"
            f"Reys: <b>#{trip['id']}</b> ({dir_text})\n"
            f"Vaqti: <b>{trip['planned_time']}</b>\n\n"
            f"Haydovchi 2 ta eslatmaga ham javob bermadi va [🚗 Yo'ldaman] tugmasini bosmadi!"
        )

        if self.bot:
            for admin_id in settings.admin_ids_list:
                try:
                    await self.bot.send_message(chat_id=admin_id, text=alert_text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Adminga eskalatsiya yuborishda xatolik: {e}")

    async def run_daily_plan(self, target_date: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
        if target_date is None:
            now = datetime.now(self.timezone)
            date_str = now.strftime("%Y-%m-%d")
            weekday = now.weekday()
        else:
            date_str = target_date
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            weekday = dt.weekday()

        if await self.db.check_existing_active_trips(date_str):
            logger.warning(f"{date_str} sanasida faol yoki yakunlangan reyslar mavjud. Qayta hisoblash rad etildi.")
            return False, "active_trips_exist", []

        schedules = await self.db.get_day_schedules(weekday)
        absences = await self.db.get_absent_student_ids(date_str)

        builder = TripBuilder()
        trips, warnings = builder.generate_trips_for_day(schedules, absences)

        saved = await self.db.save_generated_trips(trips, date_str)
        if not saved:
            return False, "save_failed", []

        logger.info(f"{date_str} uchun {len(trips)} ta reys shakllantirildi.")

        if self.bot and trips:
            text = (
                f"📋 <b>{date_str} kunlik reyslar rejasi shakllantirildi:</b>\n"
                f"Jami reyslar soni: <b>{len(trips)} ta</b>.\n\n"
                f"Rejalarni ko'rib chiqib tasdiqlang:"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"admin_approve_trips:{date_str}")]
            ])
            for admin_id in settings.admin_ids_list:
                try:
                    await self.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Adminga kunlik reja yuborishda xatolik: {e}")

        return True, "success", trips

    async def run_auto_approve(self, target_date: Optional[str] = None) -> int:
        if target_date is None:
            date_str = datetime.now(self.timezone).strftime("%Y-%m-%d")
        else:
            date_str = target_date

        unapproved = await self.db.get_unapproved_trips(date_str)
        if not unapproved:
            return 0

        count = await self.db.approve_all_daily_trips(date_str, admin_id=0)
        logger.info(f"{date_str} uchun {count} ta reys avtomatik tasdiqlandi.")

        if self.bot and count > 0:
            msg = f"⏰ <b>{date_str}</b> sanasi uchun reyslar avtomatik tasdiqlandi ({count} ta)."
            for admin_id in settings.admin_ids_list:
                try:
                    await self.bot.send_message(chat_id=admin_id, text=msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Adminga avto-tasdiq xabari yuborishda xatolik: {e}")

        return count

    def cleanup_old_backups(self, backup_dir: Optional[str] = None, retention_days: Optional[int] = None) -> int:
        """
        7 kundan eski zaxira .db fayllarini avtomatik tozalash (Railway xotirasini tejash).
        """
        target_dir = backup_dir or settings.get_backup_dir()
        if not os.path.exists(target_dir):
            return 0

        days = retention_days if retention_days is not None else settings.BACKUP_RETENTION_DAYS
        now_ts = datetime.now().timestamp()
        cutoff_sec = days * 86400
        deleted_count = 0

        for fname in os.listdir(target_dir):
            if fname.endswith(".db"):
                fpath = os.path.join(target_dir, fname)
                try:
                    file_mtime = os.path.getmtime(fpath)
                    if (now_ts - file_mtime) > cutoff_sec:
                        os.remove(fpath)
                        deleted_count += 1
                        logger.info(f"Eski zaxira fayl o'chirildi: {fname}")
                except Exception as e:
                    logger.error(f"Eski zaxirani o'chirishda xatolik ({fname}): {e}")

        return deleted_count

    def run_backup_db(self, db_path: Optional[str] = None, backup_dir: Optional[str] = None) -> str:
        """
        Doimiy xotiraga (/app/data/backups/ yoki backups/) baza nusxasini olish va eski fayllarni tozalash.
        """
        target_dir = backup_dir or settings.get_backup_dir()
        os.makedirs(target_dir, exist_ok=True)
        now_str = datetime.now(self.timezone).strftime("%Y%m%d")
        dest = os.path.join(target_dir, f"damas_reys_{now_str}.db")
        source = db_path or getattr(self.db, "db_path", settings.get_db_path())
        if os.path.exists(source):
            shutil.copy2(source, dest)
            logger.info(f"Baza zaxiralandi: {dest}")

        # 7 kundan eski zaxiralarni tozalash
        self.cleanup_old_backups(backup_dir=target_dir)
        return dest

    def start(self):
        # 06:30 Kunlik reja
        self.scheduler.add_job(
            self.run_daily_plan,
            CronTrigger(hour=6, minute=30, timezone=self.timezone),
            id="daily_plan"
        )
        # 07:00 Avto-tasdiq
        self.scheduler.add_job(
            self.run_auto_approve,
            CronTrigger(hour=7, minute=0, timezone=self.timezone),
            id="auto_approve"
        )
        # Har 1 daqiqada eslatmalar tekshiruvi
        self.scheduler.add_job(
            self.check_and_send_reminders,
            IntervalTrigger(minutes=1, timezone=self.timezone),
            id="reminders"
        )
        # 23:00 Baza zaxirasi
        self.scheduler.add_job(
            self.run_backup_db,
            CronTrigger(hour=23, minute=0, timezone=self.timezone),
            id="backup_db"
        )
        self.scheduler.start()
        logger.info("Scheduler ishga tushirildi (Asia/Tashkent).")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler to'xtatildi.")
