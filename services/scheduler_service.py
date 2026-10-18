from utils.clock import now_tz
import asyncio
import os
import logging
import re
import tempfile
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
from services.driver_route_pdf import build_driver_daily_pdf, estimate_pickup_times
from services.driver_route_controls import build_driver_trip_keyboard, format_trip_card_text
from models.enums import TripDirection, TripStatus
from models.callbacks import ApproveTripsCallback, DriverStartTripCallback
from utils.text import escape_html
from services.telegram_safe import safe_send, safe_send_document

logger = logging.getLogger(__name__)


def _online_backup_sync(source: str, tmp: str, dest: str) -> None:
    """Sinxron SQLite online backup. Faqat asyncio.to_thread ichidan chaqiriladi."""
    import sqlite3
    try:
        src_conn = sqlite3.connect(source)
        try:
            dst_conn = sqlite3.connect(tmp)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class SchedulerService:
    def __init__(self, db: Database, bot: Optional[Bot] = None, tz_str: str = settings.TIMEZONE):
        self.db = db
        self.bot = bot
        self.tz_str = tz_str
        self.timezone = ZoneInfo(tz_str)
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self._daily_plan_lock = asyncio.Lock()

    async def check_and_send_reminders(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """
        Eslatma va eskalatsiya reysning rejalashtirilgan vaqtiga bog'liq.
        """
        if now_dt is None:
            now_dt = now_tz()

        stats = {
            "first_reminders": 0,
            "second_reminders": 0,
            "escalations": 0,
            "advance_pdf_reminders": 0,
        }

        trips = await self.db.get_trips_needing_reminder(now_dt)

        for trip in trips:
            try:
                if trip["status"] != TripStatus.APPROVED.value:
                    continue

                planned_dt = datetime.strptime(f"{trip['date']} {trip['planned_time']}", "%Y-%m-%d %H:%M")
                reminder_count = trip.get("reminder_count", 0)
                now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

                if reminder_count == 0 and now_dt >= planned_dt - timedelta(minutes=30):
                    await self._send_driver_reminder(trip, reminder_num=1)
                    await self.db.update_trip_reminder(trip["id"], 1, now_str)
                    stats["first_reminders"] += 1
                elif reminder_count == 1 and now_dt >= planned_dt:
                    await self._send_driver_reminder(trip, reminder_num=2)
                    await self.db.update_trip_reminder(trip["id"], 2, now_str)
                    stats["second_reminders"] += 1
                elif reminder_count == 2 and now_dt >= planned_dt + timedelta(minutes=10):
                    await self._send_admin_escalation(trip)
                    await self.db.update_trip_reminder(trip["id"], 3, now_str)
                    stats["escalations"] += 1
            except Exception as exc:
                logger.error("Reminder processing failed for trip %s (%s)", trip.get("id"), type(exc).__name__)

        try:
            stats["advance_pdf_reminders"] = await self.send_driver_advance_reminders(now_dt)
        except Exception as exc:
            logger.error("Driver advance PDF reminders failed (%s)", type(exc).__name__)

        return stats

    async def send_driver_advance_reminders(self, now_dt: Optional[datetime] = None) -> int:
        """Send route PDFs 50 minutes before the earliest lesson in each trip."""
        if self.bot is None:
            return 0
        now_dt = now_dt or now_tz()
        trips = await self.db.get_driver_advance_reminder_trips(now_dt)
        grouped: Dict[int, List[dict]] = {}
        for trip in trips:
            driver_tg_id = trip.get("driver_tg_id")
            if driver_tg_id:
                grouped.setdefault(int(driver_tg_id), []).append(trip)

        sent_count = 0
        sent_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        for driver_tg_id, driver_trips in grouped.items():
            date_str = driver_trips[0]["date"]
            driver_name = driver_trips[0].get("driver_name") or "Haydovchi"
            trip_ids = [int(trip["id"]) for trip in driver_trips]
            try:
                pdf_data = await asyncio.to_thread(
                    build_driver_daily_pdf, driver_trips, driver_name, date_str
                )
                with tempfile.TemporaryDirectory(prefix="aql-driver-advance-") as temp_dir:
                    path = os.path.join(temp_dir, f"darsdan_50_daqiqa_oldin_{date_str}.pdf")
                    with open(path, "wb") as pdf_file:
                        pdf_file.write(pdf_data)
                    status = await safe_send_document(
                        self.bot,
                        driver_tg_id,
                        path,
                        f"📋 Dars boshlanishidan 50 daqiqa oldingi reja | {date_str}\n"
                        "Mahalla, taxminiy olish vaqti va dars boshlanish vaqti PDFda.",
                    )
                if status != "sent":
                    logger.warning("Driver advance PDF delivery failed (%s)", status)
                    continue
                for trip_id in trip_ids:
                    await self.db.mark_driver_advance_reminder_sent(trip_id, sent_at)
                for trip in driver_trips:
                    try:
                        control_status = await safe_send(
                            self.bot,
                            driver_tg_id,
                            format_trip_card_text(trip),
                            reply_markup=build_driver_trip_keyboard(trip),
                            parse_mode="HTML",
                        )
                        if control_status != "sent":
                            logger.warning("Driver route controls delivery failed (%s)", control_status)
                    except Exception as control_exc:
                        logger.error("Driver route controls failed (%s)", type(control_exc).__name__)
                sent_count += len(trip_ids)
            except Exception as exc:
                logger.error("Driver advance PDF failed for route group (%s)", type(exc).__name__)
        return sent_count

    async def send_billing_reminders(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """Send one grouped payment reminder per parent on the due-date schedule."""
        now_dt = now_dt or now_tz()
        day = now_dt.day
        reminder_day = 5 if day == 5 else day if day >= 10 and (day - 10) % 7 == 0 else None
        stats = {"eligible_parents": 0, "sent": 0, "failed": 0, "skipped": 0}
        if reminder_day is None or self.bot is None:
            return stats

        period = now_dt.strftime("%Y-%m")
        rows = await self.db.get_billing_reminder_candidates(period)
        if not rows:
            return stats

        grouped: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            parent_id = int(row["parent_id"])
            group = grouped.setdefault(parent_id, {
                "telegram_id": int(row["telegram_id"]),
                "students": {},
            })
            due = max(0, int(row["amount"]) - int(row["discount"]) - int(row["paid_total"]))
            if due:
                student_id = int(row["student_id"])
                student = group["students"].setdefault(student_id, {
                    "name": row["student_name"], "current_due": 0, "previous_due": 0,
                })
                key = "current_due" if row["period"] == period else "previous_due"
                student[key] += due

        month_names = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")

        def money(value: int) -> str:
            return f"{int(value):,}".replace(",", " ")

        current_month = month_names[now_dt.month - 1]
        for parent_id, group in grouped.items():
            students = list(group["students"].values())
            if not students:
                continue
            stats["eligible_parents"] += 1
            try:
                claimed = await self.db.claim_billing_reminder(parent_id, period, reminder_day)
                if not claimed:
                    stats["skipped"] += 1
                    continue

                if day == 5:
                    heading = f"⏰ Oylik to'lov muddati yaqinlashmoqda. {current_month} oyi to'lovi 10-sanasigacha amalga oshiriladi."
                elif day == 10:
                    heading = f"📌 Bugun — {current_month} oyi to'lovi uchun oxirgi kun."
                else:
                    heading = "⚠️ To'lov muddati o'tgan qarzdorlik bo'yicha eslatma."

                lines = ["💰 Oylik to'lov eslatmasi", heading, "", "Farzandlar bo'yicha qolgan summalar:"]
                total_due = 0
                for student in students:
                    child_due = student["current_due"] + student["previous_due"]
                    total_due += child_due
                    child_parts = []
                    if student["current_due"]:
                        child_parts.append(f"{current_month}: {money(student['current_due'])} so'm")
                    if student["previous_due"]:
                        child_parts.append(f"oldingi oylar qarzi: {money(student['previous_due'])} so'm")
                    lines.append(f"• {student['name']} — {'; '.join(child_parts)} · jami {money(child_due)} so'm")
                lines.extend(("", f"Jami qolgan qarz: {money(total_due)} so'm"))

                status = await safe_send(self.bot, group["telegram_id"], "\n".join(lines), parse_mode=None)
                await self.db.update_billing_reminder_status(parent_id, period, reminder_day, status)
                if status == "sent":
                    stats["sent"] += 1
                else:
                    stats["failed"] += 1
                    logger.warning("Billing reminder delivery failed (%s)", status)
            except Exception as exc:
                stats["failed"] += 1
                logger.error("Billing reminder failed for parent %s (%s)", parent_id, type(exc).__name__)

        return stats

    async def run_monthly_billing(self, period: Optional[str] = None) -> Dict[str, int]:
        """Create or reconcile monthly invoices by enrollment date and calendar days."""
        result = await self.db.ensure_monthly_billing(period)
        if result["created"]:
            logger.info("Avtomatik dars hisoblari yaratildi: %s ta", result["created"])
        return result

    async def _send_driver_reminder(self, trip: dict, reminder_num: int):
        trip_id = trip["id"]
        dir_text = "Markazga kelish" if trip["direction"] == TripDirection.TO_CENTER.value else "Uyga qaytish"
        driver_tg_id = trip.get("driver_tg_id")

        header = "⏰ <b>1-eslatma: Reys vaqti yaqinlashmoqda!</b>" if reminder_num == 1 else "⚠️ <b>2-eslatma: Shoshiling! Reys vaqti yetib keldi.</b>"
        items_text = ""
        for idx, it in enumerate(trip.get("items", []), 1):
            pickup = f" ({escape_html(it['pickup_point'])})" if it.get("pickup_point") else ""
            items_text += f"  {idx}. {escape_html(it['full_name'])} — {escape_html(it['village_name'])}{pickup}\n"

        text = (
            f"{header}\n\n"
            f"🚐 <b>Reys #{trip_id}</b> ({dir_text})\n"
            f"⏱ Rejalashtirilgan vaqt: <b>{escape_html(trip['planned_time'])}</b>\n"
            f"👥 O'quvchilar ro'yxati:\n{items_text}\n"
            f"Iltimos, harakatni boshlash uchun [🚗 Yo'ldaman] tugmasini bosing:"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Yo'ldaman", callback_data=DriverStartTripCallback(trip_id=trip_id).pack())]
        ])

        if self.bot and driver_tg_id:
            await safe_send(self.bot, driver_tg_id, text, reply_markup=kb, parse_mode="HTML")

        # Ota-onaga shu reysdagi o'z farzandining taxminiy olish vaqtini ko'rsatamiz.
        parent_lines: Dict[int, List[str]] = {}
        if trip["direction"] == TripDirection.TO_CENTER.value:
            pickup_times = estimate_pickup_times(trip)
            for item in trip.get("items", []):
                pickup_time = pickup_times.get(int(item.get("id", 0))) or trip.get("planned_time")
                if not pickup_time:
                    continue
                line = (
                    f"• <b>{escape_html(item.get('full_name'))}</b> — taxminan "
                    f"<b>{escape_html(pickup_time)}</b> da "
                    f"{escape_html(item.get('village_name'))} mahallasidan olinadi"
                )
                for parent_id in str(item.get("parent_telegram_ids") or "").split(","):
                    if parent_id.strip().isdigit():
                        parent_lines.setdefault(int(parent_id.strip()), []).append(line)
        else:
            parent_ids = {
                int(parent_id)
                for item in trip.get("items", [])
                for parent_id in str(item.get("parent_telegram_ids") or "").split(",")
                if parent_id.strip().isdigit()
            }
            for parent_id in parent_ids:
                parent_lines[parent_id] = []

        if self.bot:
            for parent_id, lines in parent_lines.items():
                if trip["direction"] == TripDirection.TO_CENTER.value:
                    children_text = "\n".join(dict.fromkeys(lines))
                    parent_text = (
                        f"{header}\n\n"
                        "🚐 Farzandingizni markazga olib borish reysi.\n"
                        f"{children_text}\n\n"
                        "Ko'rsatilgan vaqt — haydovchining mahallangizga yetib kelish taxmini. "
                        "Iltimos, shu vaqtdan oldin tayyor bo'ling."
                    )
                else:
                    parent_text = (
                        f"{header}\n\n"
                        f"🚐 Farzandingizning uyga qaytish rejalashtirilgan vaqti: "
                        f"<b>{escape_html(trip['planned_time'])}</b>.\n"
                        "Iltimos, tayyor bo'lib turing."
                    )
                await safe_send(self.bot, parent_id, parent_text, parse_mode="HTML")

    async def _send_admin_escalation(self, trip: dict):
        driver_name = trip.get("driver_name") or "Biriktirilgan haydovchi"
        driver_phone = trip.get("driver_phone") or "Noma'lum"
        dir_text = "Markazga kelish" if trip["direction"] == TripDirection.TO_CENTER.value else "Uyga qaytish"
        alert_text = (
            f"⚠️ <b>DIQQAT! Reysga javob berilmadi (Eskalatsiya)!</b>\n\n"
            f"Haydovchi: <b>{escape_html(driver_name)}</b> (Tel: {escape_html(driver_phone)})\n"
            f"Reys: <b>#{trip['id']}</b> ({dir_text})\n"
            f"Vaqti: <b>{escape_html(trip['planned_time'])}</b>\n\n"
            f"Haydovchi 2 ta eslatmaga ham javob bermadi va [🚗 Yo'ldaman] tugmasini bosmadi!"
        )

        if self.bot:
            for admin_id in settings.admin_ids_list:
                await safe_send(self.bot, admin_id, alert_text, parse_mode="HTML")

    async def run_daily_plan(self, target_date: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
        async with self._daily_plan_lock:
            return await self._run_daily_plan(target_date)

    async def _run_daily_plan(self, target_date: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
        if target_date is None:
            now = now_tz()
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

        # K1: haydovchi biriktirilmagan reyslarga, faqat bitta faol haydovchi bo'lsa, unga biriktiriladi
        active_drivers = [d for d in await self.db.get_drivers() if d["is_active"]]
        fallback_driver = active_drivers[0]["id"] if len(active_drivers) == 1 else None
        for t in trips:
            if not t.get("driver_id"):
                t["driver_id"] = fallback_driver
        unassigned = [t for t in trips if not t.get("driver_id")]
        if unassigned:
            warnings.append(f"⚠️ {len(unassigned)} ta reysga haydovchi biriktirilmagan.")

        saved = await self.db.save_generated_trips(trips, date_str)
        if not saved:
            return False, "save_failed", []

        logger.info(f"{date_str} uchun {len(trips)} ta reys shakllantirildi.")

        if self.bot and trips:
            text = (
                f"📋 <b>{date_str} kunlik reyslar rejasi shakllantirildi:</b>\n"
                f"Jami reyslar soni: <b>{len(trips)} ta</b>.\n"
                + ("\n".join(warnings) + "\n" if warnings else "")
                + "\nRejalarni ko'rib chiqib tasdiqlang:"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=ApproveTripsCallback(date=date_str).pack())]
            ])
            for admin_id in settings.admin_ids_list:
                await safe_send(self.bot, admin_id, text, reply_markup=kb, parse_mode="HTML")

        return True, "success", trips

    async def run_auto_approve(self, target_date: Optional[str] = None) -> int:
        if target_date is None:
            date_str = now_tz().strftime("%Y-%m-%d")
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
                await safe_send(self.bot, admin_id, msg, parse_mode="HTML")

        return count

    def cleanup_old_backups(self, backup_dir: Optional[str] = None, retention_days: Optional[int] = None) -> int:
        """
        7 kundan eski zaxira .db fayllarini avtomatik tozalash (Railway xotirasini tejash).
        """
        target_dir = backup_dir or settings.get_backup_dir()
        if not os.path.exists(target_dir):
            return 0

        days = retention_days if retention_days is not None else settings.BACKUP_RETENTION_DAYS
        cutoff_date = now_tz().date() - timedelta(days=days)
        deleted_count = 0

        for fname in os.listdir(target_dir):
            match = re.fullmatch(r"damas_reys_(\d{8})\.db", fname)
            if not match:
                continue
            try:
                backup_date = datetime.strptime(match.group(1), "%Y%m%d").date()
                if backup_date < cutoff_date:
                    os.remove(os.path.join(target_dir, fname))
                    deleted_count += 1
                    logger.info("Eski zaxira fayli o'chirildi: %s", fname)
            except (OSError, ValueError) as exc:
                logger.error("Eski zaxirani o'chirishda xatolik (%s)", type(exc).__name__)

        return deleted_count

    async def run_backup_db(self, db_path: Optional[str] = None, backup_dir: Optional[str] = None) -> str:
        """
        Doimiy xotiraga (/app/data/backups/ yoki backups/) baza nusxasini olish va eski fayllarni tozalash.
        """
        target_dir = backup_dir or settings.get_backup_dir()
        os.makedirs(target_dir, exist_ok=True)
        backup_time = now_tz()
        now_str = backup_time.strftime("%Y%m%d")
        dest = os.path.join(target_dir, f"damas_reys_{now_str}.db")
        source = db_path or getattr(self.db, "db_path", settings.get_db_path())
        if not os.path.exists(source):
            raise FileNotFoundError(f"Ma'lumotlar bazasi topilmadi: {source}")

        # WAL-xavfsiz online nusxa. Sinxron I/O botni bloklamasligi uchun fonda bajariladi.
        tmp = dest + ".tmp"
        await asyncio.to_thread(_online_backup_sync, source, tmp, dest)
        logger.info("Baza zaxiralandi: %s", dest)

        # Keep only the configured retention period of dated local snapshots.
        self.cleanup_old_backups(backup_dir=target_dir)

        backup_chat_id = settings.BACKUP_CHAT_ID or next(iter(settings.admin_ids_list), None)
        if self.bot and backup_chat_id is not None:
            status = await safe_send_document(
                self.bot,
                backup_chat_id,
                dest,
                caption=f"Zaxira: {backup_time.strftime('%Y-%m-%d')}",
            )
            if status != "sent":
                logger.error("Telegram zaxira yuborilmadi (%s); lokal nusxa saqlandi", status)
        elif self.bot:
            logger.error("Telegram zaxira yuborilmadi: BACKUP_CHAT_ID yoki ADMIN_IDS belgilanmagan")
        return dest

    def start(self):
        plan_hour, plan_minute = map(int, settings.DAILY_PLAN_TIME.split(":"))
        approve_hour, approve_minute = map(int, settings.AUTO_APPROVE_TIME.split(":"))
        if not (0 <= plan_hour <= 23 and 0 <= plan_minute <= 59):
            raise ValueError("DAILY_PLAN_TIME HH:MM formatida bo'lishi kerak")
        if not (0 <= approve_hour <= 23 and 0 <= approve_minute <= 59):
            raise ValueError("AUTO_APPROVE_TIME HH:MM formatida bo'lishi kerak")

        # Kunlik reja
        self.scheduler.add_job(
            self.run_daily_plan,
            CronTrigger(hour=plan_hour, minute=plan_minute, timezone=self.timezone),
            id="daily_plan",
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        # Avto-tasdiq
        self.scheduler.add_job(
            self.run_auto_approve,
            CronTrigger(hour=approve_hour, minute=approve_minute, timezone=self.timezone),
            id="auto_approve",
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        # Har 1 daqiqada eslatmalar tekshiruvi
        self.scheduler.add_job(
            self.check_and_send_reminders,
            IntervalTrigger(minutes=1, timezone=self.timezone),
            id="reminders",
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        # Qarzdorlik eslatmasi: 5-kuni, 10-kuni va undan keyin har 7 kunda.
        self.scheduler.add_job(
            self.send_billing_reminders,
            CronTrigger(hour=9, minute=0, timezone=self.timezone),
            id="billing_reminders",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        # Har kuni tekshiradi: oy boshidagi uzilish bo'lsa ham yangi oy hisobi ochiladi.
        self.scheduler.add_job(
            self.run_monthly_billing,
            CronTrigger(hour=0, minute=5, timezone=self.timezone),
            id="monthly_billing",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        # 23:00 Baza zaxirasi
        self.scheduler.add_job(
            self.run_backup_db,
            CronTrigger(hour=23, minute=0, timezone=self.timezone),
            id="backup_db",
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info("Scheduler ishga tushirildi (Asia/Tashkent).")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler to'xtatildi.")
