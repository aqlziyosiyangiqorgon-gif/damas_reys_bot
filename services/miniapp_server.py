"""HTTPS-facing Telegram Mini App API and static-file server."""

from __future__ import annotations

import logging
import os
import asyncio
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web
from aiogram.types import FSInputFile

from config import settings
from models.enums import TripDirection, TripItemStatus, TripStatus, UserRole
from services.miniapp_auth import MiniAppAuthError, validate_init_data
from services.notify_service import NotifyService
from services.payment_receipt import send_payment_receipt
from services.telegram_safe import safe_send, safe_send_photo
from utils.clock import now_tz
from utils.text import escape_html
from utils.time_validation import validate_schedule_times

logger = logging.getLogger(__name__)
WEEKDAYS_UZ = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")
STATIC_DIR = Path(__file__).resolve().parent.parent / "miniapp"
DB_KEY = web.AppKey("miniapp_db", object)
BOT_KEY = web.AppKey("miniapp_bot", object)
SCHEDULER_KEY = web.AppKey("miniapp_scheduler", object)
BOT_TOKEN_KEY = web.AppKey("miniapp_bot_token", str)


class MiniAppRequestError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


@web.middleware
async def _error_and_cache_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except MiniAppRequestError as exc:
        response = _json_error(exc.message, exc.status)
    except Exception as exc:
        # Do not write request headers, initData, tokens, or exception text to logs.
        logger.error("Mini App request failed (%s)", type(exc).__name__)
        response = _json_error("Ichki xatolik. Keyinroq qayta urinib ko'ring.", 500)
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _identity(request: web.Request) -> tuple[int, UserRole, dict[str, Any]]:
    authorization = request.headers.get("Authorization", "")
    init_data = authorization[4:] if authorization.startswith("tma ") else ""
    try:
        telegram_user = validate_init_data(init_data, request.app[BOT_TOKEN_KEY])
    except (MiniAppAuthError, ValueError):
        raise MiniAppRequestError("Telegram ilovasini qayta oching va qayta urinib ko'ring.", 401)

    db = request.app[DB_KEY]
    role = await db.get_user_role(telegram_user["id"], settings.admin_ids_list)
    if role == UserRole.GUEST:
        raise MiniAppRequestError("Bu Telegram hisob botda ro'yxatdan o'tmagan.", 403)
    return telegram_user["id"], role, telegram_user


async def _request_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (web.HTTPBadRequest, ValueError):
        raise MiniAppRequestError("So'rov ma'lumoti noto'g'ri.", 400)
    if not isinstance(body, dict):
        raise MiniAppRequestError("So'rov ma'lumoti noto'g'ri.", 400)
    return body


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "student_id": item.get("student_id"),
        "name": item.get("full_name", item.get("student_name", "O'quvchi")),
        "village": item.get("village_name", ""),
        "pickup_point": item.get("pickup_point", ""),
        "status": item.get("status", item.get("item_status", "")),
        "travel_minutes": item.get("travel_minutes"),
        "route_order": item.get("route_order"),
    }


def _public_trip(trip: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trip["id"],
        "date": trip.get("date", ""),
        "direction": trip.get("direction", trip.get("type", "")),
        "planned_time": trip.get("planned_time", ""),
        "status": trip.get("status", ""),
        "driver": trip.get("driver_name") or "Biriktirilmagan",
        "driver_id": trip.get("driver_id"),
        "approved": bool(trip.get("approved", 0)),
        "delay_min": trip.get("delay_min", 0),
        "items": [_public_item(item) for item in trip.get("items", [])],
    }


async def _parent_dashboard(db, telegram_id: int, date_str: str, period: str) -> dict[str, Any]:
    parent = await db.get_parent_by_telegram_id(telegram_id)
    if not parent:
        raise MiniAppRequestError("Avval botda ota-ona sifatida ro'yxatdan o'ting.", 403)
    students = await db.get_parent_students(parent["id"])
    children = []
    for student in students:
        schedules = await db.get_student_schedules(student["id"])
        status = await db.get_student_current_status(student["id"], date_str)
        children.append({
            "id": student["id"],
            "name": student["full_name"],
            "village": student.get("village_name", ""),
            "today_status": status.get("status_text", "Holat mavjud emas"),
            "today_status_code": status.get("status_code", "no_trip"),
            "schedules": [{
                "weekday": row["weekday"],
                "day": WEEKDAYS_UZ[row["weekday"]] if 0 <= row["weekday"] <= 6 else "",
                "start": row["start_time"],
                "end": row["end_time"],
            } for row in schedules],
        })
    billing = await db.get_parent_billing_summary(parent["id"], period)
    return {"children": children, "billing": billing}


async def _driver_dashboard(db, telegram_id: int, date_str: str) -> dict[str, Any]:
    driver = await db.get_driver_by_tg_id(telegram_id)
    if not driver:
        raise MiniAppRequestError("Faol haydovchi hisobi topilmadi.", 403)
    trips = await db.get_driver_today_trips(driver["id"], date_str)
    return {"name": driver["full_name"], "trips": [_public_trip(trip) for trip in trips]}


async def _staff_dashboard(db, telegram_id: int, date_str: str) -> dict[str, Any]:
    staff = await db.get_staff_by_tg_id(telegram_id)
    if not staff:
        raise MiniAppRequestError("Faol xodim hisobi topilmadi.", 403)
    to_center = await db.get_active_to_center_trips_for_staff(date_str)
    to_home = await db.get_active_to_home_trips_for_staff(date_str)
    return {
        "name": staff["full_name"],
        "center_trips": [_public_trip(trip) for trip in to_center],
        "home_trips": [_public_trip(trip) for trip in to_home],
    }


async def _admin_dashboard(db, date_str: str, period: str) -> dict[str, Any]:
    students = await db.get_admin_students()
    trips = await db.get_trips_by_date(date_str)
    unapproved = await db.get_unapproved_trips(date_str)
    absences = await db.get_today_absences_detailed(date_str)
    student_data = [{
            "id": student["id"],
            "name": student["full_name"],
            "phone_1": student["parent_phone_1"],
            "phone_2": student.get("parent_phone_2"),
            "village_id": student["village_id"],
            "village": student.get("village_name", ""),
            "zone": student.get("zone_name", ""),
            "driver_id": student.get("explicit_driver_id"),
            "driver_name": student.get("assigned_driver_name"),
            "schedules": [{
                "weekday": row["weekday"], "start": row["start_time"], "end": row["end_time"],
            } for row in student["schedules"]],
        } for student in students]
    return {
        "active_students": len(students),
        "trip_count": len(trips),
        "unapproved_count": len(unapproved),
        "stats": await db.get_daily_transport_stats(date_str),
        "trips": [_public_trip(trip) for trip in trips],
        "absences": [{
            "id": row.get("id"),
            "student_id": row.get("student_id"),
            "student": row.get("student_name", "O'quvchi"),
            "village": row.get("village_name", ""),
            "reason": row.get("reason") or "Sabab ko'rsatilmagan",
        } for row in absences],
        "students": student_data,
        "drivers": [{
            "id": row["id"], "telegram_id": row["telegram_id"], "name": row["full_name"],
            "phone": row.get("phone", ""), "active": bool(row.get("is_active")),
        } for row in await db.get_drivers()],
        "staff": [{
            "id": row["id"], "telegram_id": row["telegram_id"], "name": row["full_name"],
            "phone": row.get("phone", ""), "active": bool(row.get("is_active")),
        } for row in await db.get_all_staff()],
        "villages": [{
            "id": row["id"], "name": row["name"], "school": row.get("school_name") or "",
            "zone_id": row["zone_id"], "zone": row.get("zone_name") or "",
            "travel_minutes": row["travel_minutes"], "route_order": row["route_order"],
            "pickup_point": row.get("pickup_point") or "",
        } for row in await db.get_villages()],
        "zones": [{"id": row["id"], "name": row["name"]} for row in await db.get_zones()],
        "billing_period": period,
        "billing_students": await db.get_billing_students(period),
    }


async def dashboard(request: web.Request) -> web.Response:
    telegram_id, role, telegram_user = await _identity(request)
    db = request.app[DB_KEY]
    date_str = now_tz().strftime("%Y-%m-%d")
    period = request.query.get("period", now_tz().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise MiniAppRequestError("Oy formati YYYY-MM ko'rinishida bo'lishi kerak.", 400)
    if role == UserRole.PARENT:
        data = await _parent_dashboard(db, telegram_id, date_str, period)
    elif role == UserRole.DRIVER:
        data = await _driver_dashboard(db, telegram_id, date_str)
    elif role == UserRole.STAFF:
        data = await _staff_dashboard(db, telegram_id, date_str)
    elif role == UserRole.ADMIN:
        data = await _admin_dashboard(db, date_str, period)
    else:
        raise MiniAppRequestError("Bu bo'limga ruxsat yo'q.", 403)
    return web.json_response({
        "ok": True,
        "role": str(role),
        "name": telegram_user["first_name"],
        "date": date_str,
        "data": data,
    })


async def _parent_action(request: web.Request, telegram_id: int, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("action") != "report_absence":
        raise MiniAppRequestError("Bu amal ota-onaga ruxsat etilmagan.", 403)
    try:
        student_id = int(body.get("student_id"))
    except (TypeError, ValueError):
        raise MiniAppRequestError("O'quvchi tanlanmadi.", 400)
    reason = str(body.get("reason", "")).strip()[:100]
    db = request.app[DB_KEY]
    parent = await db.get_parent_by_telegram_id(telegram_id)
    if not parent or not await db.is_student_linked_to_parent(student_id, parent["id"]):
        raise MiniAppRequestError("Bu o'quvchi sizga biriktirilmagan.", 403)

    success, code, info = await db.mark_student_absent_with_reason(
        student_id=student_id,
        date_str=now_tz().strftime("%Y-%m-%d"),
        role="parent",
        actor_id=telegram_id,
        reason=reason,
    )
    if not success:
        messages = {
            "already_picked_up": "Farzandingiz allaqachon mashinaga chiqqan.",
            "already_marked": "Farzandingiz bugun kelmaydi deb oldin belgilangan.",
            "student_not_found": "O'quvchi topilmadi.",
        }
        raise MiniAppRequestError(messages.get(code, "Kelmaslikni belgilab bo'lmadi."), 409)

    bot = request.app.get(BOT_KEY)
    if bot:
        from handlers.parent import _notify_center_admin_absence
        from services.telegram_safe import safe_send

        if info.get("driver_tg_id"):
            await safe_send(
                bot,
                info["driver_tg_id"],
                f"ℹ️ <b>{escape_html(info.get('student_name', 'O‘quvchi'))}</b> bugun kelmaydi deb belgilandi.",
                parse_mode="HTML",
            )
        await _notify_center_admin_absence(bot, info, reason)
    return {"message": "Farzandingiz kelmasligi qayd etildi. Haydovchi va markaz ogohlantirildi."}


async def _driver_action(request: web.Request, telegram_id: int, body: dict[str, Any]) -> dict[str, Any]:
    db = request.app[DB_KEY]
    driver = await db.get_driver_by_tg_id(telegram_id)
    if not driver:
        raise MiniAppRequestError("Faol haydovchi hisobi topilmadi.", 403)
    action = body.get("action")
    try:
        trip_id = int(body.get("trip_id"))
    except (TypeError, ValueError):
        raise MiniAppRequestError("Reys topilmadi.", 400)
    trip = await db.get_trip_details(trip_id)
    if not trip or trip["driver_id"] != driver["id"]:
        raise MiniAppRequestError("Bu sizga biriktirilgan reys emas.", 403)

    now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
    direction = trip["direction"]
    if action == "start":
        allowed = [TripStatus.PLANNED.value, TripStatus.APPROVED.value]
        if direction == TripDirection.TO_HOME.value:
            allowed.append(TripStatus.HANDED_OVER.value)
        changed = await db.transition_trip(
            trip_id, allowed, TripStatus.ON_WAY.value, started_at=now_str,
            direction=direction, actor_tg_id=telegram_id, event_action="start_trip",
        )
        if not changed:
            raise MiniAppRequestError("Reys holati o'zgargan. Yangilab ko'ring.", 409)
        if direction == TripDirection.TO_HOME.value and request.app.get(BOT_KEY):
            await NotifyService.notify_trip_students(
                db=db, trip_id=trip_id,
                text_template="Farzandingiz markazdan uyga yo'lga chiqdi. Taxminan {travel_minutes} daqiqada qishloqqa yetadi.",
                bot=request.app[BOT_KEY],
            )
        return {"message": "Reys boshlandi."}

    if action == "arrive_center":
        if direction != TripDirection.TO_CENTER.value:
            raise MiniAppRequestError("Bu markazga kelish reysi emas.", 409)
        changed = await db.transition_trip(
            trip_id, [TripStatus.ON_WAY.value], TripStatus.AT_CENTER.value,
            direction=direction, actor_tg_id=telegram_id, event_action="arrive_center",
        )
        if not changed:
            raise MiniAppRequestError("Reys holati o'zgargan. Yangilab ko'ring.", 409)
        if request.app.get(BOT_KEY):
            await NotifyService.notify_trip_students(
                db=db, trip_id=trip_id,
                text_template="Farzandingiz AQL ZIYOSIga xavfsiz va salomat yetib keldi.",
                bot=request.app[BOT_KEY], only_picked=True,
            )
        return {"message": "Reys markazga yetib keldi."}

    if action in {"pickup", "dropoff"}:
        try:
            item_id = int(body.get("item_id"))
        except (TypeError, ValueError):
            raise MiniAppRequestError("O'quvchi topilmadi.", 400)
        item = await db.get_trip_item_by_id(item_id)
        if not item or item["trip_id"] != trip_id:
            raise MiniAppRequestError("O'quvchi bu reysga tegishli emas.", 403)
        if trip["status"] != TripStatus.ON_WAY.value:
            raise MiniAppRequestError("Avval reysni yo'lga chiqaring.", 409)

        if action == "pickup" and direction == TripDirection.TO_CENTER.value:
            claimed = await db.claim_item(
                item_id, trip_id, [TripItemStatus.PENDING.value], TripItemStatus.PICKED_UP.value,
                "picked_at", now_str, driver["id"], trip_statuses=[TripStatus.ON_WAY.value],
                direction=direction, actor_tg_id=telegram_id, event_action="pickup_student",
            )
            if not claimed:
                raise MiniAppRequestError("O'quvchi holati o'zgargan. Yangilab ko'ring.", 409)
            if request.app.get(BOT_KEY):
                await NotifyService.send_student_notification(
                    db=db, student_id=item["student_id"],
                    text=f"Farzandingiz mashinaga o'tirdi. Taxminan {item.get('travel_minutes', 15)} daqiqada markazga yetadi.",
                    bot=request.app[BOT_KEY],
                )
            return {"message": "O'quvchi mashinaga olindi."}

        if action == "dropoff" and direction == TripDirection.TO_HOME.value:
            claimed = await db.claim_item(
                item_id, trip_id,
                [TripItemStatus.PENDING.value, TripItemStatus.HANDED_OVER.value,
                 TripItemStatus.ARRIVED.value, TripItemStatus.ACCEPTED.value],
                TripItemStatus.DROPPED_OFF.value, "dropped_at", now_str, driver["id"],
                trip_statuses=[TripStatus.ON_WAY.value], direction=direction,
                actor_tg_id=telegram_id, event_action="dropoff_student",
            )
            if not claimed:
                raise MiniAppRequestError("O'quvchi holati o'zgargan. Yangilab ko'ring.", 409)
            if request.app.get(BOT_KEY):
                await NotifyService.send_student_notification(
                    db=db, student_id=item["student_id"],
                    text="Farzandingiz xavfsiz va salomat yetkazildi.", bot=request.app[BOT_KEY],
                )
            return {"message": "O'quvchi uyiga yetkazildi."}
        raise MiniAppRequestError("Bu amal reys turiga mos emas.", 409)

    if action == "finish":
        if direction != TripDirection.TO_HOME.value:
            raise MiniAppRequestError("Faqat uyga qaytish reysi yakunlanadi.", 409)
        changed = await db.transition_trip(
            trip_id, [TripStatus.ON_WAY.value], TripStatus.DONE.value,
            finished_at=now_str, direction=direction, actor_tg_id=telegram_id,
            event_action="finish_trip", require_all_items_complete=True,
        )
        if not changed:
            raise MiniAppRequestError("Barcha bolalar yetkazilmagan yoki reys holati o'zgargan.", 409)
        return {"message": "Reys yakunlandi."}

    raise MiniAppRequestError("Noma'lum amal.", 400)


async def _staff_action(request: web.Request, telegram_id: int, body: dict[str, Any]) -> dict[str, Any]:
    db = request.app[DB_KEY]
    staff = await db.get_staff_by_tg_id(telegram_id)
    if not staff:
        raise MiniAppRequestError("Faol xodim hisobi topilmadi.", 403)
    action = body.get("action")
    try:
        item_id = int(body.get("item_id"))
    except (TypeError, ValueError):
        raise MiniAppRequestError("O'quvchi topilmadi.", 400)
    today = now_tz().strftime("%Y-%m-%d")
    trips = await db.get_active_to_center_trips_for_staff(today)
    trips += await db.get_active_to_home_trips_for_staff(today)
    if not any(item.get("id") == item_id for trip in trips for item in trip.get("items", [])):
        raise MiniAppRequestError("Bu o'quvchi bugungi xodimlar ro'yxatida yo'q.", 403)
    if action == "accept":
        success, reason, _item = await db.accept_student(item_id, staff["id"])
        if success:
            return {"message": "O'quvchi markazga qabul qilindi."}
    elif action == "handover":
        success, reason, _item = await db.handover_student(item_id, staff["id"])
        if success:
            return {"message": "O'quvchi haydovchiga topshirildi."}
    else:
        raise MiniAppRequestError("Noma'lum amal.", 400)
    raise MiniAppRequestError("Amal bajarilmadi. Reys yoki o'quvchi holatini yangilang.", 409)


async def action(request: web.Request) -> web.Response:
    telegram_id, role, _telegram_user = await _identity(request)
    body = await _request_body(request)
    if role == UserRole.PARENT:
        result = await _parent_action(request, telegram_id, body)
    elif role == UserRole.DRIVER:
        result = await _driver_action(request, telegram_id, body)
    elif role == UserRole.STAFF:
        result = await _staff_action(request, telegram_id, body)
    elif role == UserRole.ADMIN:
        kind = body.get("action")
        date_str = now_tz().strftime("%Y-%m-%d")
        if kind == "create_plan":
            scheduler = request.app.get(SCHEDULER_KEY)
            if scheduler is None:
                raise MiniAppRequestError("Reja tuzish xizmati hozir mavjud emas.", 503)
            success, reason, trips = await scheduler.run_daily_plan(target_date=date_str)
            if not success:
                raise MiniAppRequestError("Bugungi reja tuzilmadi. Botdagi reys holatini tekshiring.", 409)
            result = {"message": f"Bugungi reja tuzildi: {len(trips)} ta reys."}
        elif kind == "approve_trips":
            approved = await request.app[DB_KEY].approve_all_daily_trips(date_str, telegram_id)
            result = {"message": f"Tasdiqlangan reyslar: {approved} ta."}
        else:
            raise MiniAppRequestError("Noma'lum amal.", 400)
    else:
        raise MiniAppRequestError("Bu bo'limga ruxsat yo'q.", 403)
    return web.json_response({"ok": True, **result})


async def _admin_identity(request: web.Request) -> tuple[int, dict[str, Any]]:
    telegram_id, role, telegram_user = await _identity(request)
    if role != UserRole.ADMIN:
        raise MiniAppRequestError("Bu bo'lim faqat administrator uchun.", 403)
    return telegram_id, telegram_user


async def _admin_today_trip(db, trip_id: int) -> dict[str, Any]:
    trip = await db.get_trip_details(trip_id)
    if not trip or trip.get("date") != now_tz().strftime("%Y-%m-%d"):
        raise MiniAppRequestError("Faqat bugungi reyslarni boshqarish mumkin.", 404)
    return trip


def _admin_int(body: dict[str, Any], key: str, *, minimum: int | None = None,
               maximum: int | None = None) -> int:
    value = body.get(key)
    if isinstance(value, bool):
        raise MiniAppRequestError("Kiritilgan raqam noto'g'ri.", 400)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise MiniAppRequestError("Kiritilgan raqam noto'g'ri.", 400)
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise MiniAppRequestError("Kiritilgan qiymat ruxsat etilgan chegaradan tashqarida.", 400)
    return number


def _admin_text(body: dict[str, Any], key: str, *, required: bool = True,
                maximum: int = 200) -> str | None:
    value = body.get(key)
    text = str(value or "").strip()
    if required and not text:
        raise MiniAppRequestError("Majburiy maydonni to'ldiring.", 400)
    if len(text) > maximum:
        raise MiniAppRequestError("Kiritilgan matn juda uzun.", 400)
    return text if text else None


def _valid_period(value: Any) -> str:
    period = str(value or "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise MiniAppRequestError("Oy YYYY-MM ko'rinishida bo'lishi kerak.", 400)
    return period


def _admin_schedules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 7:
        raise MiniAppRequestError("Dars kunlari ro'yxati noto'g'ri.", 400)
    result = []
    used_days = set()
    for row in value:
        if not isinstance(row, dict):
            raise MiniAppRequestError("Dars jadvali ma'lumoti noto'g'ri.", 400)
        try:
            weekday = int(row.get("weekday"))
        except (TypeError, ValueError):
            raise MiniAppRequestError("Hafta kuni noto'g'ri tanlangan.", 400)
        if weekday not in range(7) or weekday in used_days:
            raise MiniAppRequestError("Hafta kunlari takrorlanmasligi kerak.", 400)
        start_time = str(row.get("start_time") or "").strip()
        end_time = str(row.get("end_time") or "").strip()
        try:
            validate_schedule_times(start_time, end_time)
        except ValueError:
            raise MiniAppRequestError("Dars vaqti HH:MM formatida bo'lsin; tugash vaqti boshlanishidan keyin bo'lishi kerak.", 400)
        used_days.add(weekday)
        result.append({"weekday": weekday, "start_time": start_time, "end_time": end_time})
    return result


async def admin_action(request: web.Request) -> web.Response:
    telegram_id, _user = await _admin_identity(request)
    body = await _request_body(request)
    db = request.app[DB_KEY]
    kind = str(body.get("action") or "")
    today = now_tz().strftime("%Y-%m-%d")

    if kind == "create_plan":
        scheduler = request.app.get(SCHEDULER_KEY)
        if scheduler is None:
            raise MiniAppRequestError("Reja tuzish xizmati hozir mavjud emas.", 503)
        success, reason, trips = await scheduler.run_daily_plan(target_date=today)
        if not success:
            raise MiniAppRequestError("Bugungi reja tuzilmadi. Reys holatini tekshiring.", 409)
        result = {"message": f"Bugungi reja tuzildi: {len(trips)} ta reys."}

    elif kind == "approve_trips":
        count = await db.approve_all_daily_trips(today, telegram_id)
        result = {"message": f"Tasdiqlangan reyslar: {count} ta."}

    elif kind == "approve_trip":
        trip_id = _admin_int(body, "trip_id", minimum=1)
        trip = await _admin_today_trip(db, trip_id)
        if trip.get("status") != "planned" or trip.get("approved"):
            raise MiniAppRequestError("Faqat rejalashtirilgan reys tasdiqlanadi.", 409)
        if not await db.approve_trip(trip_id, telegram_id):
            raise MiniAppRequestError("Bu reysni tasdiqlab bo'lmaydi. Holatini yangilang.", 409)
        result = {"message": f"#{trip_id} reys tasdiqlandi."}

    elif kind == "cancel_trip":
        trip_id = _admin_int(body, "trip_id", minimum=1)
        await _admin_today_trip(db, trip_id)
        if not await db.cancel_trip(trip_id, telegram_id):
            raise MiniAppRequestError("Yo'ldagi, yakunlangan yoki bekor qilingan reysni bekor qilib bo'lmaydi.", 409)
        await NotifyService.notify_trip_students(
            db=db, trip_id=trip_id,
            text_template=f"⚠️ #{trip_id} reys bekor qilindi. Batafsil ma'lumot uchun AQL ZIYOSI markaziga murojaat qiling.",
            bot=request.app.get(BOT_KEY),
        )
        result = {"message": f"#{trip_id} reys bekor qilindi, ota-onalarga xabar berildi."}

    elif kind == "assign_trip_driver":
        trip_id = _admin_int(body, "trip_id", minimum=1)
        await _admin_today_trip(db, trip_id)
        driver_id = _admin_int(body, "driver_id", minimum=1)
        if not await db.assign_driver_to_trip(trip_id, driver_id):
            raise MiniAppRequestError("Haydovchi faol emas yoki reys boshlangan.", 409)
        driver = await db.get_driver_by_id(driver_id)
        result = {"message": f"Reysga haydovchi biriktirildi: {driver['full_name'] if driver else ''}."}

    elif kind == "delay_trip":
        trip_id = _admin_int(body, "trip_id", minimum=1)
        trip = await _admin_today_trip(db, trip_id)
        if trip.get("status") in {"done", "cancelled"}:
            raise MiniAppRequestError("Yakunlangan yoki bekor qilingan reysni kechiktirib bo'lmaydi.", 409)
        minutes = _admin_int(body, "minutes", minimum=1, maximum=180)
        success, reason, delay_data = await db.apply_trip_delay(trip_id, minutes, telegram_id)
        if not success or not delay_data:
            raise MiniAppRequestError("Reys kechiktirilmadi. Reysni tekshiring.", 409)
        bot = request.app.get(BOT_KEY)
        if bot and delay_data.get("driver_tg_id"):
            await safe_send(
                bot, delay_data["driver_tg_id"],
                f"⚠️ Admin reysni {minutes} daqiqaga kechiktirdi. Yangi vaqt: <b>{escape_html(delay_data['new_planned_time'])}</b>",
                parse_mode="HTML",
            )
        for item in delay_data.get("items", []):
            parent_text = (f"Farzandingiz mashinada, yetib borish taxminan {minutes} daqiqaga kechikadi."
                           if item.get("is_picked_up") else
                           f"Mashina taxminan {minutes} daqiqaga kechikadi. Noqulaylik uchun uzr.")
            for parent in item.get("parents", []):
                status = await safe_send(bot, parent["telegram_id"], parent_text) if bot and parent.get("telegram_id") else "failed"
                await db.log_notification(
                    parent_id=parent["parent_id"], student_id=item["student_id"],
                    text=parent_text, status=status,
                )
        result = {"message": f"Reys {minutes} daqiqaga kechiktirildi. Ota-ona va haydovchilarga xabar yuborildi."}

    elif kind == "backup_now":
        scheduler = request.app.get(SCHEDULER_KEY)
        if scheduler is None:
            raise MiniAppRequestError("Zaxiralash xizmati hozir mavjud emas.", 503)
        await scheduler.run_backup_db()
        result = {"message": "Zaxira tayyorlandi va sozlamaga ko'ra yuborildi."}

    elif kind == "add_student":
        full_name = _admin_text(body, "full_name", maximum=150)
        phone_1 = _admin_text(body, "parent_phone_1", maximum=40)
        phone_2 = _admin_text(body, "parent_phone_2", required=False, maximum=40)
        village_id = _admin_int(body, "village_id", minimum=1)
        schedules = _admin_schedules(body.get("schedules", []))
        try:
            student_id = await db.add_student_with_schedules({
                "full_name": full_name, "village_id": village_id,
                "parent_phone_1": phone_1, "parent_phone_2": phone_2,
            }, schedules, created_by=telegram_id)
        except ValueError as exc:
            raise MiniAppRequestError(str(exc), 400)
        summary = await db.get_invoice_summary(student_id, now_tz().strftime("%Y-%m"))
        billing_note = (
            f" Avtomatik hisob: {summary['lesson_count']} dars × "
            f"{summary['lesson_rate']:,} = {summary['amount']:,} so'm."
            if summary and summary["billing_mode"] == "attendance" else ""
        )
        result = {"message": f"{full_name} o'quvchi va dars jadvali saqlandi.{billing_note}"}

    elif kind == "update_student":
        student_id = _admin_int(body, "student_id", minimum=1)
        full_name = _admin_text(body, "full_name", maximum=150)
        phone_1 = _admin_text(body, "parent_phone_1", maximum=40)
        phone_2 = _admin_text(body, "parent_phone_2", required=False, maximum=40)
        village_id = _admin_int(body, "village_id", minimum=1)
        try:
            changed = await db.update_student(student_id, full_name, village_id, phone_1, phone_2)
        except ValueError as exc:
            raise MiniAppRequestError(str(exc), 400)
        if not changed:
            raise MiniAppRequestError("O'quvchi topilmadi.", 404)
        result = {"message": "O'quvchi ma'lumotlari yangilandi."}

    elif kind == "deactivate_student":
        student_id = _admin_int(body, "student_id", minimum=1)
        if not await db.deactivate_student(student_id):
            raise MiniAppRequestError("Faol o'quvchi topilmadi.", 404)
        result = {"message": "O'quvchi nofaol qilindi. Bugungi hali boshlanmagan rejadan ham olindi."}

    elif kind == "assign_student_driver":
        student_id = _admin_int(body, "student_id", minimum=1)
        driver_value = body.get("driver_id")
        driver_id = None if driver_value in (None, "", "auto") else _admin_int(body, "driver_id", minimum=1)
        if not await db.set_student_driver(student_id, driver_id):
            raise MiniAppRequestError("O'quvchi yoki faol haydovchi topilmadi.", 404)
        result = {"message": "Haydovchi biriktirildi." if driver_id else "Avtomatik haydovchi tanlash yoqildi."}

    elif kind == "set_schedule":
        student_id = _admin_int(body, "student_id", minimum=1)
        schedule = _admin_schedules([{
            "weekday": body.get("weekday"), "start_time": body.get("start_time"),
            "end_time": body.get("end_time"),
        }])[0]
        if not await db.update_student_schedule_time(
            student_id, schedule["weekday"], schedule["start_time"], schedule["end_time"]
        ):
            raise MiniAppRequestError("Dars jadvali saqlanmadi.", 409)
        result = {"message": "Dars jadvali saqlandi."}

    elif kind == "delete_schedule":
        student_id = _admin_int(body, "student_id", minimum=1)
        weekday = _admin_int(body, "weekday", minimum=0, maximum=6)
        if not await db.delete_student_schedule(student_id, weekday):
            raise MiniAppRequestError("Bu kun uchun jadval topilmadi.", 404)
        result = {"message": "Dars kuni o'chirildi."}

    elif kind == "add_driver":
        telegram_id_value = _admin_int(body, "telegram_id", minimum=1)
        full_name = _admin_text(body, "full_name", maximum=150)
        phone = _admin_text(body, "phone", maximum=40)
        try:
            await db.add_driver(telegram_id_value, full_name, phone)
        except ValueError as exc:
            raise MiniAppRequestError(str(exc), 400)
        result = {"message": "Haydovchi qo'shildi yoki ma'lumotlari yangilandi."}

    elif kind == "toggle_driver":
        driver_id = _admin_int(body, "driver_id", minimum=1)
        active = body.get("active")
        if active not in (True, False, 0, 1, "0", "1"):
            raise MiniAppRequestError("Holat noto'g'ri.", 400)
        if not await db.toggle_driver_status(driver_id, int(active in (True, 1, "1"))):
            raise MiniAppRequestError("Haydovchi topilmadi.", 404)
        result = {"message": "Haydovchi holati o'zgartirildi."}

    elif kind == "add_staff":
        staff_tg_id = _admin_int(body, "telegram_id", minimum=1)
        full_name = _admin_text(body, "full_name", maximum=150)
        phone = _admin_text(body, "phone", required=False, maximum=40)
        try:
            await db.add_staff(staff_tg_id, full_name, phone)
        except ValueError as exc:
            raise MiniAppRequestError(str(exc), 400)
        result = {"message": "Xodim qo'shildi yoki ma'lumotlari yangilandi."}

    elif kind == "toggle_staff":
        staff_id = _admin_int(body, "staff_id", minimum=1)
        active = body.get("active")
        if active not in (True, False, 0, 1, "0", "1"):
            raise MiniAppRequestError("Holat noto'g'ri.", 400)
        if not await db.toggle_staff_status(staff_id, int(active in (True, 1, "1"))):
            raise MiniAppRequestError("Xodim topilmadi.", 404)
        result = {"message": "Xodim holati o'zgartirildi."}

    elif kind == "update_village":
        village_id = _admin_int(body, "village_id", minimum=1)
        name = _admin_text(body, "name", maximum=120)
        school = _admin_text(body, "school", required=False, maximum=120)
        pickup = _admin_text(body, "pickup_point", required=False, maximum=200)
        travel = _admin_int(body, "travel_minutes", minimum=1, maximum=300)
        route_order = _admin_int(body, "route_order", minimum=1, maximum=10000)
        if not await db.update_village(village_id, name, travel, school, route_order, pickup):
            raise MiniAppRequestError("Mahalla topilmadi.", 404)
        result = {"message": "Mahalla ma'lumotlari saqlandi."}

    elif kind == "create_invoice":
        student_id = _admin_int(body, "student_id", minimum=1)
        period = _valid_period(body.get("period"))
        await db.ensure_monthly_billing(period)
        summary = await db.get_invoice_summary(student_id, period)
        if not summary:
            raise MiniAppRequestError("Bu davr uchun dars jadvali yoki hisob topilmadi.", 404)
        result = {"message": (
            "Hisob dars jadvali bo'yicha avtomatik tayyorlandi."
            if summary["billing_mode"] == "attendance" else "Bu davr uchun hisob avvaldan mavjud."
        )}

    elif kind == "record_payment":
        student_id = _admin_int(body, "student_id", minimum=1)
        period = _valid_period(body.get("period"))
        amount = _admin_int(body, "amount", minimum=1, maximum=2_000_000_000)
        method = str(body.get("method") or "")
        summary = await db.get_invoice_summary(student_id, period)
        if not summary:
            raise MiniAppRequestError("Avval oylik hisob oching.", 404)
        ok, reason, payment_id = await db.record_payment_with_id(
            summary["id"], amount, method, telegram_id, now_tz().strftime("%Y-%m-%d %H:%M:%S")
        )
        if not ok:
            messages = {"invalid_amount": "To'lov summasi noto'g'ri.", "invalid_method": "To'lov turini tanlang.",
                        "waived": "Bu hisob bekor qilingan.", "overpayment": "To'lov qolgan qarzdan oshib ketdi."}
            raise MiniAppRequestError(messages.get(reason, "To'lov saqlanmadi."), 409)
        delivery = await send_payment_receipt(
            db, request.app.get(BOT_KEY), payment_id
        ) if payment_id is not None else "failed"
        receipt_messages = {
            "sent": "To'lov qayd etildi, chek ota-onaga yuborildi.",
            "partial": "To'lov qayd etildi, chek ayrim ota-onalarga yuborildi.",
            "no_parent": "To'lov saqlandi, lekin ota-onaning Telegram akkaunti ulanmagan.",
            "unavailable": "To'lov saqlandi, lekin chek yuborish xizmati mavjud emas.",
            "failed": "To'lov saqlandi, lekin chek yuborilmadi.",
        }
        result = {"message": receipt_messages.get(delivery, "To'lov qayd etildi.")}

    elif kind == "apply_discount":
        student_id = _admin_int(body, "student_id", minimum=1)
        period = _valid_period(body.get("period"))
        amount = _admin_int(body, "amount", minimum=1, maximum=2_000_000_000)
        reason = _admin_text(body, "reason", maximum=500)
        summary = await db.get_invoice_summary(student_id, period)
        if not summary:
            raise MiniAppRequestError("Avval oylik hisob oching.", 404)
        ok, why = await db.apply_discount(summary["id"], amount, reason, telegram_id)
        if not ok:
            messages = {"discount_exceeds_amount": "Chegirma oylik summadan katta bo'lishi mumkin emas.",
                        "discount_exceeds_due": "Chegirma to'langan summani hisobga olganda qolgan qarzdan katta.",
                        "waived": "Bu hisob bekor qilingan."}
            raise MiniAppRequestError(messages.get(why, "Chegirma saqlanmadi."), 409)
        result = {"message": "Chegirma qayd etildi."}

    elif kind == "save_excel_import":
        batch_id = str(body.get("batch_id") or "")
        if not re.fullmatch(r"[a-f0-9]{12}", batch_id):
            raise MiniAppRequestError("Excel tasdiqlash kodi noto'g'ri.", 400)
        payload = await db.take_workflow_payload(batch_id, telegram_id, "miniapp_excel")
        if not payload or time.time() - float(payload.get("created_at", 0)) >= 15 * 60:
            raise MiniAppRequestError("Excel oldindan ko'rish muddati tugagan. Faylni qayta yuklang.", 410)
        records = payload.get("valid_records")
        if not isinstance(records, list) or not records:
            raise MiniAppRequestError("Saqlash uchun yaroqli o'quvchi yo'q.", 400)
        counts = await db.bulk_upsert_students_and_schedules(records, created_by=telegram_id)
        result = {"message": f"Excel saqlandi: {counts['created']} ta yangi, {counts['updated']} ta yangilandi."}

    elif kind == "cancel_excel_import":
        batch_id = str(body.get("batch_id") or "")
        if re.fullmatch(r"[a-f0-9]{12}", batch_id):
            await db.delete_workflow_payload(batch_id, telegram_id)
        result = {"message": "Excel import bekor qilindi."}

    elif kind == "send_broadcast":
        batch_id = str(body.get("batch_id") or "")
        if not re.fullmatch(r"[a-f0-9]{12}", batch_id):
            raise MiniAppRequestError("Xabar kodi noto'g'ri.", 400)
        payload = await db.take_workflow_payload(batch_id, telegram_id, "miniapp_broadcast")
        if not payload or time.time() - float(payload.get("created_at", 0)) >= 15 * 60:
            _delete_admin_broadcast_media(payload)
            raise MiniAppRequestError("Xabar tasdiqlash muddati tugagan. Qaytadan tayyorlang.", 410)
        result = await _send_admin_broadcast(request, telegram_id, payload)

    elif kind == "cancel_broadcast":
        batch_id = str(body.get("batch_id") or "")
        if re.fullmatch(r"[a-f0-9]{12}", batch_id):
            payload = await db.take_workflow_payload(batch_id, telegram_id, "miniapp_broadcast")
            _delete_admin_broadcast_media(payload)
        result = {"message": "Xabar yuborish bekor qilindi."}

    else:
        raise MiniAppRequestError("Noma'lum administrator amali.", 400)

    return web.json_response({"ok": True, **result})


def _delete_admin_broadcast_media(payload: dict[str, Any] | None) -> None:
    if not payload:
        return
    path = payload.get("media_path")
    if not isinstance(path, str) or not Path(path).name.startswith("miniapp_broadcast_"):
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Mini App broadcast media cleanup failed")


def _purge_stale_admin_broadcast_media() -> None:
    cutoff = time.time() - 24 * 60 * 60
    for path in Path(tempfile.gettempdir()).glob("miniapp_broadcast_*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Stale Mini App broadcast media cleanup failed")


async def _send_admin_broadcast(request: web.Request, telegram_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    db = request.app[DB_KEY]
    bot = request.app.get(BOT_KEY)
    audience = payload.get("audience") if isinstance(payload.get("audience"), list) else []
    text = str(payload.get("text") or "")
    media_path = payload.get("media_path")
    if media_path and not Path(media_path).is_file():
        raise MiniAppRequestError("Xabar rasmi topilmadi. Xabarni qaytadan tayyorlang.", 410)
    sent = failed = 0
    try:
        for recipient in audience:
            chat_id = recipient.get("telegram_id")
            if not bot or not chat_id:
                status = "failed"
            elif media_path:
                photo = FSInputFile(media_path)
                if len(text) > 1024:
                    text_status = await safe_send(bot, chat_id, text, parse_mode=None) if text else "sent"
                    photo_status = await safe_send_photo(bot, chat_id, photo)
                    status = "sent" if text_status == photo_status == "sent" else "failed"
                else:
                    status = await safe_send(bot, chat_id, text, photo=photo, parse_mode=None)
            else:
                status = await safe_send(bot, chat_id, text, parse_mode=None)
            if status == "sent":
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(0.05)
        await db.log_broadcast(
            admin_id=telegram_id, audience_type=payload["audience_type"],
            audience_value=payload.get("audience_value"), text=text,
            media="miniapp_upload" if media_path else None,
            total=len(audience), sent=sent, failed=failed,
        )
    finally:
        _delete_admin_broadcast_media(payload)
    return {"message": f"Xabar yuborildi: {sent} ta yetdi, {failed} ta yetmadi."}


async def admin_broadcast_preview(request: web.Request) -> web.Response:
    telegram_id, _user = await _admin_identity(request)
    _purge_stale_admin_broadcast_media()
    reader = await request.multipart()
    fields: dict[str, str] = {}
    media_path = None
    media_bytes = None
    media_ext = None
    while part := await reader.next():
        if part.name == "photo" and part.filename:
            content_type = (part.headers.get("Content-Type") or "").lower()
            allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
            if content_type not in allowed:
                raise MiniAppRequestError("Faqat JPG, PNG yoki WEBP rasm yuklash mumkin.", 400)
            media_bytes = await part.read(decode=False)
            if len(media_bytes) > 8 * 1024 * 1024:
                raise MiniAppRequestError("Rasm hajmi 8 MB dan oshmasligi kerak.", 413)
            media_ext = allowed[content_type]
        elif part.name in {"audience_type", "audience_value", "text"}:
            fields[part.name] = (await part.text()).strip()

    audience_type = fields.get("audience_type", "")
    audience_value = fields.get("audience_value") or None
    text = fields.get("text", "")
    if audience_type not in {"all", "village", "zone", "student", "drivers", "staff"}:
        raise MiniAppRequestError("Xabar auditoriyasini tanlang.", 400)
    if len(text) > 4096:
        raise MiniAppRequestError("Xabar matni 4096 belgidan oshmasligi kerak.", 400)
    if not text and media_bytes is None:
        raise MiniAppRequestError("Xabar matni yoki rasm yuboring.", 400)
    if audience_type in {"village", "zone", "student"} and not audience_value:
        raise MiniAppRequestError("Xabar yuboriladigan guruhni tanlang.", 400)

    audience = await request.app[DB_KEY].get_broadcast_audience(audience_type, audience_value)
    if len(audience) > 10000:
        raise MiniAppRequestError("Auditoriya juda katta. Iltimos, kichikroq guruhni tanlang.", 413)
    batch_id = uuid.uuid4().hex[:12]
    if media_bytes is not None:
        media_path = os.path.join(tempfile.gettempdir(), f"miniapp_broadcast_{batch_id}{media_ext}")
        await asyncio.to_thread(Path(media_path).write_bytes, media_bytes)
    payload = {
        "created_at": time.time(), "audience_type": audience_type,
        "audience_value": audience_value, "audience": audience,
        "text": text, "media_path": media_path,
    }
    try:
        await request.app[DB_KEY].save_workflow_payload(batch_id, telegram_id, "miniapp_broadcast", payload)
    except Exception:
        _delete_admin_broadcast_media(payload)
        raise
    return web.json_response({
        "ok": True, "batch_id": batch_id, "recipient_count": len(audience),
        "message": "Xabar ko'rib chiqish uchun tayyor. Yuborishni tasdiqlang.",
        "text_preview": text, "has_photo": media_path is not None,
    })


async def admin_excel_download(request: web.Request) -> web.Response:
    await _admin_identity(request)
    from services.excel_service import ExcelService

    if request.match_info["kind"] not in {"template", "export"}:
        raise MiniAppRequestError("Excel fayl turi topilmadi.", 404)
    suffix = ".xlsx"
    fd, path = tempfile.mkstemp(prefix="miniapp_students_", suffix=suffix)
    os.close(fd)
    try:
        if request.match_info["kind"] == "template":
            await asyncio.to_thread(ExcelService.generate_template, path)
            filename = "oquvchilar_shabloni.xlsx"
        else:
            await ExcelService.export_students_to_excel(request.app[DB_KEY], path)
            filename = "oquvchilar_bazasi.xlsx"
        content = await asyncio.to_thread(Path(path).read_bytes)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return web.Response(
        body=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def admin_excel_import_preview(request: web.Request) -> web.Response:
    telegram_id, _user = await _admin_identity(request)
    reader = await request.multipart()
    upload_path = None
    file_bytes = None
    filename = ""
    while part := await reader.next():
        if part.name == "file" and part.filename:
            filename = part.filename
            file_bytes = await part.read(decode=False)
            break
    if not filename.lower().endswith(".xlsx"):
        raise MiniAppRequestError("Faqat .xlsx formatidagi fayl qabul qilinadi.", 400)
    if file_bytes is None or not file_bytes:
        raise MiniAppRequestError("Excel fayl bo'sh.", 400)
    if len(file_bytes) > 10 * 1024 * 1024:
        raise MiniAppRequestError("Excel fayl hajmi 10 MB dan oshmasligi kerak.", 413)

    fd, upload_path = tempfile.mkstemp(prefix="miniapp_excel_", suffix=".xlsx")
    os.close(fd)
    try:
        await asyncio.to_thread(Path(upload_path).write_bytes, file_bytes)
        from services.excel_service import ExcelService
        preview = await ExcelService.parse_and_preview_excel(upload_path, request.app[DB_KEY])
    except Exception as exc:
        logger.error("Mini App Excel parse failed (%s)", type(exc).__name__)
        raise MiniAppRequestError("Faylni o'qib bo'lmadi. Berilgan shablondan foydalaning.", 400)
    finally:
        try:
            os.remove(upload_path)
        except OSError:
            pass

    batch_id = uuid.uuid4().hex[:12]
    if preview.get("valid_records"):
        payload = {"created_at": time.time(), "valid_records": preview["valid_records"]}
        await request.app[DB_KEY].save_workflow_payload(batch_id, telegram_id, "miniapp_excel", payload)
    village_names = {row["id"]: row["name"] for row in await request.app[DB_KEY].get_villages()}
    students_preview = [{
        "name": record["full_name"],
        "village": village_names.get(record["village_id"], "Mahalla"),
    } for record in preview.get("valid_records", [])[:20]]
    return web.json_response({
        "ok": True, "batch_id": batch_id if preview.get("valid_records") else None,
        "new_count": preview.get("new_count", 0), "update_count": preview.get("update_count", 0),
        "error_count": preview.get("error_count", 0), "errors": preview.get("errors", []),
        "students_preview": students_preview,
        "can_save": bool(preview.get("valid_records")),
    })


def create_miniapp_app(db, *, bot=None, scheduler=None, bot_token: str | None = None) -> web.Application:
    app = web.Application(middlewares=[_error_and_cache_middleware], client_max_size=12 * 1024 * 1024)
    app[DB_KEY] = db
    app[BOT_KEY] = bot
    app[SCHEDULER_KEY] = scheduler
    app[BOT_TOKEN_KEY] = bot_token if bot_token is not None else settings.BOT_TOKEN

    async def serve_index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def serve_script(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "app.js")

    async def serve_style(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "style.css")

    app.router.add_get("/", serve_index)
    app.router.add_get("/app.js", serve_script)
    app.router.add_get("/style.css", serve_style)
    app.router.add_static("/assets/", STATIC_DIR / "assets", show_index=False)
    app.router.add_get("/api/miniapp/dashboard", dashboard)
    app.router.add_post("/api/miniapp/action", action)
    app.router.add_post("/api/miniapp/admin/action", admin_action)
    app.router.add_post("/api/miniapp/admin/broadcast-preview", admin_broadcast_preview)
    app.router.add_post("/api/miniapp/admin/excel/import-preview", admin_excel_import_preview)
    app.router.add_get("/api/miniapp/admin/excel/{kind}", admin_excel_download)
    return app


class MiniAppServer:
    def __init__(self, db, *, bot=None, scheduler=None, bot_token: str | None = None):
        self.app = create_miniapp_app(db, bot=bot, scheduler=scheduler, bot_token=bot_token)
        self.runner: web.AppRunner | None = None

    async def start(self, host: str = "0.0.0.0", port: int | None = None) -> None:
        if self.runner is not None:
            return
        port = int(os.getenv("PORT", "8080")) if port is None else port
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        try:
            await site.start()
        except BaseException:
            await self.runner.cleanup()
            self.runner = None
            raise
        logger.info("Telegram Mini App server started on port %s", port)

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
