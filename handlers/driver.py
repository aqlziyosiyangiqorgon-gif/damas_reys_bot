from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.db import Database
from models.enums import UserRole, TripDirection, TripStatus, TripItemStatus
from services.notify_service import NotifyService

driver_router = Router()

def build_driver_trip_keyboard(trip: dict) -> InlineKeyboardMarkup:
    rows = []
    trip_id = trip["id"]
    direction = trip["direction"]
    status = trip["status"]
    items = trip.get("items", [])

    if direction == TripDirection.TO_CENTER.value:
        if status in [TripStatus.PLANNED.value, TripStatus.APPROVED.value]:
            rows.append([InlineKeyboardButton(text="🚗 Yo'ldaman", callback_data=f"driver_start_trip:{trip_id}")])
        elif status == TripStatus.ON_WAY.value:
            for it in items:
                it_st = it["status"]
                if it_st == TripItemStatus.PENDING.value:
                    btn_text = f"⏳ {it['full_name']} ({it['village_name']}) — Oldim"
                    cb_data = f"driver_pickup:{trip_id}:{it['id']}"
                elif it_st == TripItemStatus.PICKED_UP.value:
                    btn_text = f"✅ {it['full_name']} (Olindi)"
                    cb_data = f"driver_already_done:{it['id']}"
                elif it_st == TripItemStatus.ABSENT.value:
                    btn_text = f"🚫 {it['full_name']} (Kelmaydi)"
                    cb_data = "driver_noop"
                else:
                    btn_text = f"ℹ️ {it['full_name']} ({it_st})"
                    cb_data = "driver_noop"
                rows.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])

            # Agar barcha bolalar olingan bo'lsa (yoki absent bo'lsa) markazga yetib keldim tugmasi
            all_collected = all(it["status"] in [TripItemStatus.PICKED_UP.value, TripItemStatus.ABSENT.value] for it in items)
            if all_collected:
                rows.append([InlineKeyboardButton(text="🏁 Yetib keldim", callback_data=f"driver_arrive_center:{trip_id}")])

    elif direction == TripDirection.TO_HOME.value:
        if status in [TripStatus.PLANNED.value, TripStatus.APPROVED.value, TripStatus.HANDED_OVER.value]:
            rows.append([InlineKeyboardButton(text="🚗 Yo'lga chiqdim", callback_data=f"driver_start_trip:{trip_id}")])
        elif status == TripStatus.ON_WAY.value:
            for it in items:
                it_st = it["status"]
                if it_st in [TripItemStatus.PENDING.value, TripItemStatus.HANDED_OVER.value, TripItemStatus.ARRIVED.value, TripItemStatus.ACCEPTED.value]:
                    btn_text = f"⏳ {it['full_name']} ({it['village_name']}) — Yetkazdim"
                    cb_data = f"driver_dropoff:{trip_id}:{it['id']}"
                elif it_st == TripItemStatus.DROPPED_OFF.value:
                    btn_text = f"✅ {it['full_name']} (Yetkazildi)"
                    cb_data = f"driver_already_done:{it['id']}"
                elif it_st == TripItemStatus.ABSENT.value:
                    btn_text = f"🚫 {it['full_name']} (Kelmaydi)"
                    cb_data = "driver_noop"
                else:
                    btn_text = f"ℹ️ {it['full_name']} ({it_st})"
                    cb_data = "driver_noop"
                rows.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])

            # Hamma bolalar yetkazilgan bo'lsa reysni yakunlash
            all_dropped = all(it["status"] in [TripItemStatus.DROPPED_OFF.value, TripItemStatus.ABSENT.value] for it in items)
            if all_dropped:
                rows.append([InlineKeyboardButton(text="🏁 Reysni yakunlash", callback_data=f"driver_finish_trip:{trip_id}")])

    rows.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"driver_refresh_trip:{trip_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_trip_card_text(trip: dict) -> str:
    dir_label = "🚖 Markazga kelish (to_center)" if trip["direction"] == TripDirection.TO_CENTER.value else "🏠 Uyga qaytish (to_home)"
    status_dict = {
        TripStatus.PLANNED.value: "Rejalashtirilgan ⏳",
        TripStatus.APPROVED.value: "Tasdiqlangan ✅",
        TripStatus.ON_WAY.value: "Yo'lda 🚗",
        TripStatus.AT_CENTER.value: "Markazga yetib kelgan 🏁",
        TripStatus.HANDED_OVER.value: "Bolalar topshirilgan 🤝",
        TripStatus.DONE.value: "Yakunlangan 🎉",
        TripStatus.CANCELLED.value: "Bekor qilingan ❌"
    }
    status_label = status_dict.get(trip["status"], trip["status"])

    text = (
        f"📋 <b>Reys #{trip['id']} — {dir_label}</b>\n"
        f"⏰ Reja vaqti: <b>{trip['planned_time']}</b>\n"
        f"🚦 Holati: <b>{status_label}</b>\n\n"
        f"<b>O'quvchilar ro'yxati ({len(trip.get('items', []))} ta):</b>\n"
    )

    for idx, it in enumerate(trip.get("items", []), start=1):
        st_ico = "⏳"
        if it["status"] == TripItemStatus.PICKED_UP.value:
            st_ico = "✅ Olingan"
        elif it["status"] == TripItemStatus.DROPPED_OFF.value:
            st_ico = "🏠 Yetkazilgan"
        elif it["status"] == TripItemStatus.ABSENT.value:
            st_ico = "🚫 Kelmaydi"
        pickup = f" ({it['pickup_point']})" if it.get("pickup_point") else ""
        text += f"  {idx}. <b>{it['full_name']}</b> — {it['village_name']}{pickup} [{st_ico}]\n"

    return text

# --- 1. /mening_reyslarim va /bugun buyruqlari ---
@driver_router.message(Command("mening_reyslarim"))
@driver_router.message(Command("bugun"))
async def cmd_driver_trips(message: types.Message, db: Database, user_role: UserRole):
    if user_role != UserRole.DRIVER:
        await message.answer("Ushbu buyruq faqat haydovchilar uchun!")
        return

    driver = await db.get_driver_by_tg_id(message.from_user.id)
    if not driver:
        await message.answer("Siz ro'yxatdan o'tgan faol haydovchi emassiz!")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    trips = await db.get_driver_today_trips(driver["id"], date_str)

    if not trips:
        await message.answer(f"Bugun ({date_str}) sizga biriktirilgan faol reyslar topilmadi.")
        return

    await message.answer(f"🚐 <b>Bugungi ({date_str}) reyslaringiz:</b>", parse_mode="HTML")
    for trip in trips:
        card_text = format_trip_card_text(trip)
        kb = build_driver_trip_keyboard(trip)
        await message.answer(card_text, reply_markup=kb, parse_mode="HTML")

# --- 2. Yo'lga chiqdim ([🚗 Yo'ldaman] / [🚗 Yo'lga chiqdim]) ---
@driver_router.callback_query(F.data.startswith("driver_start_trip:"))
async def cb_driver_start_trip(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    trip_id = int(callback.data.split(":")[1])
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    # SATR 126: BEGONA HAYDOVCHI TEKSHIRUVI
    if trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return

    # Takroriy bosishdan himoya
    if trip["status"] == TripStatus.ON_WAY.value:
        await callback.answer("Reys allaqachon yo'lda!", show_alert=True)
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.update_trip_status(trip_id, TripStatus.ON_WAY.value, started_at=now_str)
    await db.log_event(
        actor_role="driver",
        actor_id=callback.from_user.id,
        action="start_trip",
        trip_id=trip_id
    )

    # Agar uyga qaytish (to_home) bo'lsa, ota-onalarga yo'lga chiqilgani bildiriladi
    if trip["direction"] == TripDirection.TO_HOME.value:
        await NotifyService.notify_trip_students(
            db=db,
            trip_id=trip_id,
            text_template="Farzandingiz markazdan uyga yo'lga chiqdi. Taxminan {travel_minutes} daqiqada qishloqqa yetadi.",
            bot=callback.bot
        )

    updated_trip = await db.get_trip_details(trip_id)
    card_text = format_trip_card_text(updated_trip)
    kb = build_driver_trip_keyboard(updated_trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Reys boshlandi! Oq yo'l!")

# --- 3. [✅ Oldim] — Bolani mashinaga olish ---
@driver_router.callback_query(F.data.startswith("driver_pickup:"))
async def cb_driver_pickup(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    parts = callback.data.split(":")
    trip_id = int(parts[1])
    item_id = int(parts[2])

    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    # SATR 175: BEGONA HAYDOVCHI TEKSHIRUVI
    if trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return

    item = await db.get_trip_item_by_id(item_id)
    if not item:
        await callback.answer("O'quvchi ma'lumoti topilmadi!", show_alert=True)
        return

    # Takroriy bosishdan himoya
    if item["status"] == TripItemStatus.PICKED_UP.value:
        await callback.answer("Ushbu bola allaqachon olingan!", show_alert=True)
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.update_trip_item_status(item_id, TripItemStatus.PICKED_UP.value, picked_at=now_str, actor_id=driver["id"])
    await db.log_event(
        actor_role="driver",
        actor_id=callback.from_user.id,
        action="pickup_student",
        trip_id=trip_id,
        student_id=item["student_id"]
    )

    # Ota-onaga bildirishnoma
    notify_text = f"Farzandingiz mashinaga o'tirdi. Taxminan {item.get('travel_minutes', 15)} daqiqada markazga yetadi."
    await NotifyService.send_student_notification(
        db=db,
        student_id=item["student_id"],
        text=notify_text,
        bot=callback.bot
    )

    updated_trip = await db.get_trip_details(trip_id)
    card_text = format_trip_card_text(updated_trip)
    kb = build_driver_trip_keyboard(updated_trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"{item['full_name']} olindi!")

# --- 4. [🏁 Yetib keldim] — Markazga yetib kelish ---
@driver_router.callback_query(F.data.startswith("driver_arrive_center:"))
async def cb_driver_arrive_center(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    trip_id = int(callback.data.split(":")[1])
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    # SATR 230: BEGONA HAYDOVCHI TEKSHIRUVI
    if trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return

    if trip["status"] == TripStatus.AT_CENTER.value:
        await callback.answer("Allaqachon markazga yetilgan!", show_alert=True)
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.update_trip_status(trip_id, TripStatus.AT_CENTER.value)
    await db.log_event(
        actor_role="driver",
        actor_id=callback.from_user.id,
        action="arrive_center",
        trip_id=trip_id
    )

    # Ota-onaga bildirishnoma
    await NotifyService.notify_trip_students(
        db=db,
        trip_id=trip_id,
        text_template="Farzandingiz AQL ZIYOSIga xavfsiz va salomat yetib keldi.",
        bot=callback.bot,
        only_picked=True
    )

    updated_trip = await db.get_trip_details(trip_id)
    card_text = format_trip_card_text(updated_trip)
    kb = build_driver_trip_keyboard(updated_trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Markazga yetib kelindi!")

# --- 5. [🏠 Yetkazdim] — Uyga yetkazish ---
@driver_router.callback_query(F.data.startswith("driver_dropoff:"))
async def cb_driver_dropoff(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    parts = callback.data.split(":")
    trip_id = int(parts[1])
    item_id = int(parts[2])

    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    # SATR 281: BEGONA HAYDOVCHI TEKSHIRUVI
    if trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return

    item = await db.get_trip_item_by_id(item_id)
    if not item:
        await callback.answer("O'quvchi ma'lumoti topilmadi!", show_alert=True)
        return

    if item["status"] == TripItemStatus.DROPPED_OFF.value:
        await callback.answer("Ushbu bola allaqachon yetkazilgan!", show_alert=True)
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.update_trip_item_status(item_id, TripItemStatus.DROPPED_OFF.value, dropped_at=now_str, actor_id=driver["id"])
    await db.log_event(
        actor_role="driver",
        actor_id=callback.from_user.id,
        action="dropoff_student",
        trip_id=trip_id,
        student_id=item["student_id"]
    )

    # Ota-onaga bildirishnoma
    await NotifyService.send_student_notification(
        db=db,
        student_id=item["student_id"],
        text="Farzandingiz xavfsiz va salomat yetkazildi.",
        bot=callback.bot
    )

    updated_trip = await db.get_trip_details(trip_id)
    card_text = format_trip_card_text(updated_trip)
    kb = build_driver_trip_keyboard(updated_trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"{item['full_name']} yetkazildi!")

# --- 6. [🏁 Reysni yakunlash] — Reysni yakunlash ---
@driver_router.callback_query(F.data.startswith("driver_finish_trip:"))
async def cb_driver_finish_trip(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat faol haydovchilar uchun!", show_alert=True)
        return

    trip_id = int(callback.data.split(":")[1])
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return

    # SATR 335: BEGONA HAYDOVCHI TEKSHIRUVI
    if trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return

    if trip["status"] == TripStatus.DONE.value:
        await callback.answer("Reys allaqachon yakunlangan!", show_alert=True)
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.update_trip_status(trip_id, TripStatus.DONE.value, finished_at=now_str)
    await db.log_event(
        actor_role="driver",
        actor_id=callback.from_user.id,
        action="finish_trip",
        trip_id=trip_id
    )

    updated_trip = await db.get_trip_details(trip_id)
    card_text = format_trip_card_text(updated_trip)
    kb = build_driver_trip_keyboard(updated_trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Reys muvaffaqiyatli yakunlandi! Rahmat!")

# --- 7. Yangilash va Noop ---
@driver_router.callback_query(F.data.startswith("driver_refresh_trip:"))
async def cb_driver_refresh_trip(callback: types.CallbackQuery, db: Database):
    driver = await db.get_driver_by_tg_id(callback.from_user.id)
    if not driver:
        await callback.answer("Faqat haydovchilar uchun!", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await db.get_trip_details(trip_id)
    if not trip or trip["driver_id"] != driver["id"]:
        await callback.answer("Bu sizning reysingiz emas!", show_alert=True)
        return
    card_text = format_trip_card_text(trip)
    kb = build_driver_trip_keyboard(trip)
    await callback.message.edit_text(card_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Yangilandi!")

@driver_router.callback_query(F.data.startswith("driver_already_done:"))
async def cb_driver_already_done(callback: types.CallbackQuery):
    await callback.answer("Ushbu amal allaqachon bajarilgan!", show_alert=True)

@driver_router.callback_query(F.data == "driver_noop")
async def cb_driver_noop(callback: types.CallbackQuery):
    await callback.answer()
