"""HTTPS-facing Telegram Mini App API and static-file server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from aiohttp import web

from config import settings
from models.enums import TripDirection, TripItemStatus, TripStatus, UserRole
from services.miniapp_auth import MiniAppAuthError, validate_init_data
from services.notify_service import NotifyService
from utils.clock import now_tz
from utils.text import escape_html

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
        "items": [_public_item(item) for item in trip.get("items", [])],
    }


async def _parent_dashboard(db, telegram_id: int, date_str: str) -> dict[str, Any]:
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
    return {"children": children}


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


async def _admin_dashboard(db, date_str: str) -> dict[str, Any]:
    students = await db.get_active_students()
    trips = await db.get_trips_by_date(date_str)
    unapproved = await db.get_unapproved_trips(date_str)
    absences = await db.get_today_absences_detailed(date_str)
    return {
        "active_students": len(students),
        "trip_count": len(trips),
        "unapproved_count": len(unapproved),
        "trips": [_public_trip(trip) for trip in trips],
        "absences": [{
            "student": row.get("student_name", "O'quvchi"),
            "village": row.get("village_name", ""),
            "reason": row.get("reason") or "Sabab ko'rsatilmagan",
        } for row in absences],
    }


async def dashboard(request: web.Request) -> web.Response:
    telegram_id, role, telegram_user = await _identity(request)
    db = request.app[DB_KEY]
    date_str = now_tz().strftime("%Y-%m-%d")
    if role == UserRole.PARENT:
        data = await _parent_dashboard(db, telegram_id, date_str)
    elif role == UserRole.DRIVER:
        data = await _driver_dashboard(db, telegram_id, date_str)
    elif role == UserRole.STAFF:
        data = await _staff_dashboard(db, telegram_id, date_str)
    elif role == UserRole.ADMIN:
        data = await _admin_dashboard(db, date_str)
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


def create_miniapp_app(db, *, bot=None, scheduler=None, bot_token: str | None = None) -> web.Application:
    app = web.Application(middlewares=[_error_and_cache_middleware], client_max_size=32 * 1024)
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
