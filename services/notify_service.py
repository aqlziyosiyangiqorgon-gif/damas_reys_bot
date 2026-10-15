from typing import Optional, List
from aiogram import Bot
from database.db import Database
from utils.text import escape_html
from services.telegram_safe import safe_send

class NotifyService:
    @staticmethod
    async def send_student_notification(
        db: Database,
        student_id: int,
        text: str,
        bot: Optional[Bot] = None
    ) -> List[int]:
        """
        O'quvchining tasdiqlangan barcha ota-onalariga xabar yuborish va notify_log ga yozish.
        """
        parents = await db.get_student_parent_telegram_ids(student_id)
        logged_ids = []

        if not parents:
            # Ota-onasi ulanmagan bo'lsa ham logda aks ettirish
            log_id = await db.log_notification(parent_id=None, student_id=student_id, text=text, status="no_parent")
            logged_ids.append(log_id)
            return logged_ids

        for p in parents:
            if bot and p.get("telegram_id"):
                status = await safe_send(
                    bot, p["telegram_id"], text, parse_mode="HTML"
                )
            else:
                status = "failed"
            
            log_id = await db.log_notification(
                parent_id=p["parent_id"],
                student_id=student_id,
                text=text,
                status=status
            )
            logged_ids.append(log_id)

        return logged_ids

    @staticmethod
    async def notify_trip_students(
        db: Database,
        trip_id: int,
        text_template: str,
        bot: Optional[Bot] = None,
        only_picked: bool = False
    ) -> List[int]:
        """
        Reysdagi barcha (yoki faqat olingan) o'quvchilar ota-onalariga xabar yuborish.
        text_template ichida {full_name}, {village_name}, {travel_minutes} bo'lishi mumkin.
        """
        trip = await db.get_trip_details(trip_id)
        if not trip:
            return []

        all_log_ids = []
        for item in trip["items"]:
            if only_picked and item["status"] != "picked_up":
                continue
            if item["status"] == "absent":
                continue

            msg_text = text_template.format(
                full_name=escape_html(item["full_name"]),
                village_name=escape_html(item["village_name"]),
                travel_minutes=item.get("travel_minutes", 15)
            )

            log_ids = await NotifyService.send_student_notification(
                db=db,
                student_id=item["student_id"],
                text=msg_text,
                bot=bot
            )
            all_log_ids.extend(log_ids)

        return all_log_ids
