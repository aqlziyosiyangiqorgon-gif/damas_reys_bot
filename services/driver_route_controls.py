"""Render compact driver controls for chronological village stops."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from models.callbacks import (
    DriverAlreadyDoneCallback,
    DriverArriveCenterCallback,
    DriverDropoffVillageCallback,
    DriverFinishTripCallback,
    DriverPickupVillageCallback,
    DriverRefreshTripCallback,
    DriverRouteProgressCallback,
    DriverStartTripCallback,
)
from models.enums import TripDirection, TripItemStatus, TripStatus
from utils.text import escape_html


def _stops_from_items(trip: dict[str, Any]) -> list[dict[str, Any]]:
    stops: dict[int, dict[str, Any]] = {}
    for item in trip.get("items", []):
        if item.get("status") == TripItemStatus.ABSENT.value:
            continue
        village_id = int(item["village_id"])
        stop = stops.setdefault(village_id, {
            "village_id": village_id,
            "village_name": item.get("village_name", f"Mahalla {village_id}"),
            "route_order": item.get("route_order", 0),
            "status": "planned",
        })
    return sorted(stops.values(), key=lambda stop: (stop["route_order"], stop["village_id"]))


def _next_home_village(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the next village that still has a student to deliver.

    We preserve the trip item's existing order instead of inventing a new route,
    then group students by village so the driver gets one action per stop.
    """
    groups: dict[int, dict[str, Any]] = {}
    for item in items:
        if item.get("status") == TripItemStatus.ABSENT.value:
            continue
        if item.get("status") == TripItemStatus.DROPPED_OFF.value:
            continue
        village_id = int(item["village_id"])
        group = groups.setdefault(village_id, {
            "village_id": village_id,
            "village_name": item.get("village_name", f"Mahalla {village_id}"),
            "items": [],
        })
        group["items"].append(item)
    return next(iter(groups.values()), None)


def build_driver_trip_keyboard(trip: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    trip_id = int(trip["id"])
    direction = trip["direction"]
    status = trip["status"]
    items = list(trip.get("items", []))

    if direction == TripDirection.TO_CENTER.value:
        stops = trip.get("route_stops") or _stops_from_items(trip)
        active_stop = next((stop for stop in stops if stop.get("status") != "left"), None)

        if active_stop and status in (TripStatus.PLANNED.value, TripStatus.APPROVED.value, TripStatus.ON_WAY.value):
            village_id = int(active_stop["village_id"])
            village_name = str(active_stop.get("village_name", f"Mahalla {village_id}"))
            stop_status = active_stop.get("status", "planned")
            if stop_status == "planned":
                rows.append([InlineKeyboardButton(
                    text=f"🚗 Yo'lga chiqish · {village_name}"[:64],
                    callback_data=DriverRouteProgressCallback(
                        action="start", trip_id=trip_id, village_id=village_id,
                    ).pack(),
                )])
            elif stop_status == "en_route":
                rows.append([InlineKeyboardButton(
                    text=f"📍 Yetib keldim · {village_name}"[:64],
                    callback_data=DriverRouteProgressCallback(
                        action="arrive", trip_id=trip_id, village_id=village_id,
                    ).pack(),
                )])
            elif stop_status == "arrived":
                pending = [
                    item for item in items
                    if int(item.get("village_id", -1)) == village_id
                    and item.get("status") == TripItemStatus.PENDING.value
                ]
                if pending:
                    rows.append([InlineKeyboardButton(
                        text=f"✅ {village_name}: {len(pending)} ta bolani oldim"[:64],
                        callback_data=DriverPickupVillageCallback(
                            trip_id=trip_id, village_id=village_id,
                        ).pack(),
                    )])
                else:
                    rows.append([InlineKeyboardButton(
                        text="🚙 Bolalarni oldim · keyingi mahalla",
                        callback_data=DriverRouteProgressCallback(
                            action="leave", trip_id=trip_id, village_id=village_id,
                        ).pack(),
                    )])

        if not active_stop and status == TripStatus.ON_WAY.value:
            all_collected = all(
                item.get("status") in (TripItemStatus.PICKED_UP.value, TripItemStatus.ABSENT.value)
                for item in items
            )
            if all_collected:
                rows.append([InlineKeyboardButton(
                    text="🏁 Markazga yetib keldim",
                    callback_data=DriverArriveCenterCallback(trip_id=trip_id).pack(),
                )])

        # Old cached messages may still carry this generic trip-start action.
        if status in (TripStatus.PLANNED.value, TripStatus.APPROVED.value) and not stops:
            rows.append([InlineKeyboardButton(
                text="🚗 Reysni boshlash",
                callback_data=DriverStartTripCallback(trip_id=trip_id).pack(),
            )])

    elif direction == TripDirection.TO_HOME.value:
        if status in (TripStatus.PLANNED.value, TripStatus.APPROVED.value, TripStatus.HANDED_OVER.value):
            rows.append([InlineKeyboardButton(
                text="🚗 Yo'lga chiqdim",
                callback_data=DriverStartTripCallback(trip_id=trip_id).pack(),
            )])
        elif status == TripStatus.ON_WAY.value:
            next_village = _next_home_village(items)
            if next_village:
                village_id = int(next_village["village_id"])
                village_name = str(next_village["village_name"])
                count = len(next_village["items"])
                rows.append([InlineKeyboardButton(
                    text=f"🏠 {village_name}: {count} ta bolani yetkazdim"[:64],
                    callback_data=DriverDropoffVillageCallback(
                        trip_id=trip_id, village_id=village_id,
                    ).pack(),
                )])
            elif all(item.get("status") in (TripItemStatus.DROPPED_OFF.value, TripItemStatus.ABSENT.value) for item in items):
                rows.append([InlineKeyboardButton(
                    text="🏁 Reysni yakunlash",
                    callback_data=DriverFinishTripCallback(trip_id=trip_id).pack(),
                )])

    rows.append([InlineKeyboardButton(
        text="🔄 Yangilash",
        callback_data=DriverRefreshTripCallback(trip_id=trip_id).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_trip_card_text(trip: dict[str, Any]) -> str:
    direction = "🚖 Markazga kelish" if trip["direction"] == TripDirection.TO_CENTER.value else "🏠 Uyga qaytish"
    status_dict = {
        TripStatus.PLANNED.value: "Rejalashtirilgan ⏳",
        TripStatus.APPROVED.value: "Tasdiqlangan ✅",
        TripStatus.ON_WAY.value: "Yo'lda 🚗",
        TripStatus.AT_CENTER.value: "Markazga yetib kelgan 🏁",
        TripStatus.HANDED_OVER.value: "Bolalar topshirilgan 🤝",
        TripStatus.DONE.value: "Yakunlangan 🎉",
        TripStatus.CANCELLED.value: "Bekor qilingan ❌",
    }
    status_label = escape_html(status_dict.get(trip["status"], trip["status"]))
    items = trip.get("items", [])
    lines = [
        f"📋 <b>Reys #{trip['id']} — {direction}</b>",
        f"⏰ Reja vaqti: <b>{escape_html(trip['planned_time'])}</b>",
        f"🚦 Holati: <b>{status_label}</b>",
        "",
    ]
    if trip["direction"] == TripDirection.TO_CENTER.value:
        stops = trip.get("route_stops") or _stops_from_items(trip)
        lines.append("<b>Mahalla bo'yicha tartib:</b>")
        for index, stop in enumerate(stops, start=1):
            state = {
                "planned": "navbatda",
                "en_route": "yo'lda",
                "arrived": "yetib keldi",
                "left": "olib ketildi",
            }.get(stop.get("status", "planned"), "navbatda")
            lines.append(
                f"{index}. {escape_html(stop.get('village_name'))} — {escape_html(state)}"
            )
        lines.append("")
    lines.append(f"<b>O'quvchilar ({len(items)} ta):</b>")
    for index, item in enumerate(items, start=1):
        item_status = {
            TripItemStatus.PENDING.value: "kutilmoqda",
            TripItemStatus.PICKED_UP.value: "mashinaga mindi",
            TripItemStatus.DROPPED_OFF.value: "yetkazildi",
            TripItemStatus.ABSENT.value: "kelmaydi",
        }.get(item.get("status"), escape_html(item.get("status", "")))
        pickup = f" ({escape_html(item['pickup_point'])})" if item.get("pickup_point") else ""
        lines.append(
            f"{index}. <b>{escape_html(item['full_name'])}</b> — "
            f"{escape_html(item['village_name'])}{pickup} · {escape_html(item_status)}"
        )
    return "\n".join(lines)
