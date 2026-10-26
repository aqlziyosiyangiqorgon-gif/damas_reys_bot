from __future__ import annotations

from aiogram import Router, types

from database.db import Database
from models.callbacks import DriverDropoffVillageCallback, DriverPickupVillageCallback
from models.enums import TripDirection, TripItemStatus, TripStatus
from services.notify_service import NotifyService
from services.driver_route_controls import build_driver_trip_keyboard, format_trip_card_text
from utils.telegram import safe_refresh_edit
from utils.clock import now_tz


driver_bulk_router = Router()


def _next_home_village(trip: dict) -> int | None:
    for item in trip.get("items", []):
        if item.get("status") not in (TripItemStatus.DROPPED_OFF.value, TripItemStatus.ABSENT.value):
            return int(item["village_id"])
    return None


@driver_bulk_router.callback_query(DriverPickupVillageCallback.filter())
async def cb_driver_pickup_village_bulk(
    callback: types.CallbackQuery,
    db: Database,
    callback_data: DriverPickupVillageCallback | None = None,
):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    parsed = callback_data or DriverPickupVillageCallback.unpack(callback.data)
    trip = await db.get_trip_details(parsed.trip_id)
    if not trip or trip.get("driver_id") != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return
    if trip.get("direction") != TripDirection.TO_CENTER.value or trip.get("status") != TripStatus.ON_WAY.value:
        await callback.answer("Reys hali yo'lda emas yoki holati o'zgargan!", show_alert=True)
        return

    stop = next(
        (row for row in trip.get("route_stops", []) if int(row["village_id"]) == int(parsed.village_id)),
        None,
    )
    if not stop or stop.get("status") != "arrived":
        await callback.answer("Avval shu mahallaga yetib kelganingizni belgilang.", show_alert=True)
        return

    picked_items = await db.pickup_village_students(
        parsed.trip_id,
        parsed.village_id,
        driver["id"],
    )
    if not picked_items:
        await callback.answer("Bu mahalladagi bolalar allaqachon olingan yoki kelmagan.", show_alert=True)
        return

    for item in picked_items:
        await NotifyService.send_student_notification(
            db=db,
            student_id=item["student_id"],
            text=f"Farzandingiz mashinaga o'tirdi. Taxminan {item.get('travel_minutes', 15)} daqiqada markazga yetadi.",
            bot=callback.bot,
        )

    updated_trip = await db.get_trip_details(parsed.trip_id)
    await safe_refresh_edit(
        callback,
        format_trip_card_text(updated_trip),
        build_driver_trip_keyboard(updated_trip),
    )
    village_name = picked_items[0].get("village_name", "Mahalla")
    await callback.answer(f"{village_name}: {len(picked_items)} ta o'quvchi olindi!")


@driver_bulk_router.callback_query(DriverDropoffVillageCallback.filter())
async def cb_driver_dropoff_village_bulk(
    callback: types.CallbackQuery,
    db: Database,
    callback_data: DriverDropoffVillageCallback | None = None,
):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    parsed = callback_data or DriverDropoffVillageCallback.unpack(callback.data)
    trip = await db.get_trip_details(parsed.trip_id)
    if not trip or trip.get("driver_id") != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return
    if trip.get("direction") != TripDirection.TO_HOME.value or trip.get("status") != TripStatus.ON_WAY.value:
        await callback.answer("Reys hali yo'lga chiqmagan yoki holati o'zgargan!", show_alert=True)
        return

    next_village_id = _next_home_village(trip)
    if next_village_id != int(parsed.village_id):
        await callback.answer("Avval navbatdagi mahalladagi bolalarni yetkazing.", show_alert=True)
        return

    dropped_items = await db.dropoff_village_students(
        parsed.trip_id,
        parsed.village_id,
        driver["id"],
    )
    if not dropped_items:
        await callback.answer("Ushbu mahalladagi barcha bolalar allaqachon yetkazilgan!", show_alert=True)
        return

    for item in dropped_items:
        await NotifyService.send_student_notification(
            db=db,
            student_id=item["student_id"],
            text="Farzandingiz xavfsiz va salomat yetkazildi.",
            bot=callback.bot,
        )

    updated_trip = await db.get_trip_details(parsed.trip_id)
    await safe_refresh_edit(
        callback,
        format_trip_card_text(updated_trip),
        build_driver_trip_keyboard(updated_trip),
    )
    village_name = dropped_items[0].get("village_name", "Mahalla")
    await callback.answer(f"{village_name}: {len(dropped_items)} ta o'quvchi yetkazildi!")
