from utils.clock import now_tz
from datetime import datetime
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from database.db import Database
from models.enums import UserRole, TripStatus, TripItemStatus
from models.callbacks import (
    StaffAcceptAllCallback, StaffAcceptCallback, StaffHandoverAllCallback,
    StaffHandoverCallback, StaffRefreshCenterCallback, StaffRefreshHomeCallback,
)
from services.notify_service import NotifyService
from utils.text import escape_html
from utils.telegram import safe_refresh_edit

staff_router = Router()


def get_staff_menu_kb() -> ReplyKeyboardMarkup:
    """Markaz xodimlari uchun doimiy kabinet menyusi."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🏫 Bugungi kelish reyslari"),
        KeyboardButton(text="🏠 Bugungi topshirish reyslari"),
    )
    builder.row(KeyboardButton(text="/topshirish"))
    return builder.as_markup(resize_keyboard=True)

def build_staff_center_keyboard(trip: dict) -> InlineKeyboardMarkup:
    rows = []
    trip_id = trip["id"]
    items = trip.get("items", [])

    has_pending = False
    for it in items:
        it_st = it["status"]
        if it_st == TripItemStatus.ACCEPTED.value:
            btn_text = f"✅ {it['full_name']} (Qabul qilingan)"
            cb_data = "staff_already_done"
        elif it_st == TripItemStatus.ABSENT.value:
            btn_text = f"🚫 {it['full_name']} (Kelmaydi)"
            cb_data = "staff_noop"
        else:
            has_pending = True
            btn_text = f"🎓 {it['full_name']} — Qabul qildim"
            cb_data = StaffAcceptCallback(trip_id=trip_id, item_id=it["id"]).pack()
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])

    if has_pending:
        rows.append([InlineKeyboardButton(text="🎓 Hammasini qabul qilish", callback_data=StaffAcceptAllCallback(trip_id=trip_id).pack())])

    rows.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data=StaffRefreshCenterCallback(trip_id=trip_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_staff_home_keyboard(trip: dict) -> InlineKeyboardMarkup:
    rows = []
    trip_id = trip["id"]
    items = trip.get("items", [])

    has_pending = False
    for it in items:
        it_st = it["status"]
        if it_st == TripItemStatus.HANDED_OVER.value or it_st == TripItemStatus.DROPPED_OFF.value:
            btn_text = f"✅ {it['full_name']} (Topshirilgan)"
            cb_data = "staff_already_done"
        elif it_st == TripItemStatus.ABSENT.value:
            btn_text = f"🚫 {it['full_name']} (Kelmaydi)"
            cb_data = "staff_noop"
        else:
            has_pending = True
            btn_text = f"🚗 {it['full_name']} — Topshirdim"
            cb_data = StaffHandoverCallback(trip_id=trip_id, item_id=it["id"]).pack()
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])

    if has_pending:
        rows.append([InlineKeyboardButton(text="🚗 Hammasini topshirish", callback_data=StaffHandoverAllCallback(trip_id=trip_id).pack())])

    rows.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data=StaffRefreshHomeCallback(trip_id=trip_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_staff_trip_card(trip: dict, is_center: bool = True) -> str:
    status_map = {
        TripStatus.PLANNED.value: "Rejalashtirilgan",
        TripStatus.APPROVED.value: "Tasdiqlangan",
        TripStatus.ON_WAY.value: "Yo'lda",
        TripStatus.AT_CENTER.value: "Markazga yetib kelgan",
        TripStatus.HANDED_OVER.value: "Topshirilgan",
        TripStatus.DONE.value: "Yakunlangan"
    }
    st_text = escape_html(status_map.get(trip["status"], trip["status"]))
    title = "Markazga kelish" if is_center else "Uyga qaytish"
    driver_info = trip.get("driver_name") or "Biriktirilmagan"

    text = (
        f"🏫 <b>Reys #{trip['id']}</b> ({title})\n"
        f"🚗 Haydovchi: <b>{escape_html(driver_info)}</b>\n"
        f"📅 Sana: <b>{escape_html(trip['date'])}</b> | Vaqt: <b>{escape_html(trip['planned_time'])}</b>\n"
        f"📌 Holat: <b>{st_text}</b>\n"
        f"👥 O'quvchilar ro'yxati ({len(trip.get('items', []))} nafar):\n"
    )

    for idx, it in enumerate(trip.get("items", []), 1):
        st = it["status"]
        if st in [TripItemStatus.ACCEPTED.value, TripItemStatus.HANDED_OVER.value, TripItemStatus.DROPPED_OFF.value]:
            ico = "✅"
        elif st == TripItemStatus.ABSENT.value:
            ico = "🚫"
        else:
            ico = "⏳"
        text += f"  {idx}. <b>{escape_html(it['full_name'])}</b> ({escape_html(it['village_name'])}) [{ico}]\n"

    return text

# --- 1. /markaz va /qabul buyruqlari ---
@staff_router.message(Command("markaz"))
@staff_router.message(Command("qabul"))
@staff_router.message(F.text == "🏫 Bugungi kelish reyslari")
async def cmd_staff_center(message: types.Message, db: Database, user_role: UserRole):
    if user_role != UserRole.STAFF:
        await message.answer("Ushbu buyruq faqat markaz xodimlari uchun!")
        return

    staff = await db.get_staff_by_tg_id(message.from_user.id)
    if not staff:
        await message.answer("Siz ro'yxatdan o'tgan faol xodim emassiz!")
        return

    date_str = now_tz().strftime("%Y-%m-%d")
    trips = await db.get_active_to_center_trips_for_staff(date_str)

    if not trips:
        await message.answer(
            f"Bugun ({date_str}) markazga yetib kelgan faol reyslar topilmadi.",
            reply_markup=get_staff_menu_kb(),
        )
        return

    await message.answer(
        f"🏫 <b>Markazga yetib kelgan reyslar ({date_str}):</b>",
        reply_markup=get_staff_menu_kb(),
        parse_mode="HTML",
    )
    for trip in trips:
        card = format_staff_trip_card(trip, is_center=True)
        kb = build_staff_center_keyboard(trip)
        await message.answer(card, reply_markup=kb, parse_mode="HTML")

# --- 2. /topshirish buyrug'i ---
@staff_router.message(Command("topshirish"))
@staff_router.message(F.text == "🏠 Bugungi topshirish reyslari")
async def cmd_staff_home(message: types.Message, db: Database, user_role: UserRole):
    if user_role != UserRole.STAFF:
        await message.answer("Ushbu buyruq faqat markaz xodimlari uchun!")
        return

    staff = await db.get_staff_by_tg_id(message.from_user.id)
    if not staff:
        await message.answer("Siz ro'yxatdan o'tgan faol xodim emassiz!")
        return

    date_str = now_tz().strftime("%Y-%m-%d")
    trips = await db.get_active_to_home_trips_for_staff(date_str)

    if not trips:
        await message.answer(
            f"Bugun ({date_str}) qaytish reyslari topilmadi.",
            reply_markup=get_staff_menu_kb(),
        )
        return

    await message.answer(
        f"🚗 <b>Qaytish reyslari ({date_str}):</b>",
        reply_markup=get_staff_menu_kb(),
        parse_mode="HTML",
    )
    for trip in trips:
        card = format_staff_trip_card(trip, is_center=False)
        kb = build_staff_home_keyboard(trip)
        await message.answer(card, reply_markup=kb, parse_mode="HTML")

# --- 3. [🎓 Qabul qildim] ---
@staff_router.callback_query(StaffAcceptCallback.filter())
async def cb_staff_accept(callback: types.CallbackQuery, db: Database, callback_data: StaffAcceptCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat markaz xodimlari uchun!", show_alert=True)
        return

    parsed = callback_data or StaffAcceptCallback.unpack(callback.data)
    trip_id, item_id = parsed.trip_id, parsed.item_id

    target_item = await db.get_trip_item_by_id(item_id)
    if not target_item or target_item["trip_id"] != trip_id:
        await callback.answer("O'quvchi ushbu reysga tegishli emas!", show_alert=True)
        return

    success, reason, item = await db.accept_student(item_id, staff["id"])
    if not success:
        if reason == "already_accepted":
            await callback.answer("Allaqachon bajarilgan!", show_alert=True)
        elif reason == "trip_not_at_center":
            await callback.answer("Reys hali markazga yetib kelmagan!", show_alert=True)
        else:
            await callback.answer("Amalni bajarib bo'lmadi!", show_alert=True)
        return

    # Audit log
    await db.log_event(
        actor_role="staff",
        actor_id=callback.from_user.id,
        action="accept_student",
        trip_id=trip_id,
        student_id=item["student_id"]
    )

    # Ota-onaga bildirishnoma
    await NotifyService.send_student_notification(
        db=db,
        student_id=item["student_id"],
        text="Farzandingiz darsga kirdi.",
        bot=callback.bot
    )

    # Xabarni yangilash
    updated_trip = await db.get_trip_details(trip_id)
    card = format_staff_trip_card(updated_trip, is_center=True)
    kb = build_staff_center_keyboard(updated_trip)
    await callback.message.edit_text(card, reply_markup=kb, parse_mode="HTML")
    await callback.answer("O'quvchi darsga qabul qilindi!")

# --- 4. [🎓 Hammasini qabul qilish] ---
@staff_router.callback_query(StaffAcceptAllCallback.filter())
async def cb_staff_accept_all(callback: types.CallbackQuery, db: Database, callback_data: StaffAcceptAllCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat markaz xodimlari uchun!", show_alert=True)
        return

    trip_id = (callback_data or StaffAcceptAllCallback.unpack(callback.data)).trip_id
    success, reason, accepted_items = await db.accept_all_trip_students(trip_id, staff["id"])

    if not success:
        if reason in ["already_all_accepted", "already_accepted"]:
            await callback.answer("Allaqachon bajarilgan!", show_alert=True)
        elif reason == "trip_not_at_center":
            await callback.answer("Reys hali markazga yetib kelmagan!", show_alert=True)
        else:
            await callback.answer("Qabul qilinadigan o'quvchilar yo'q!", show_alert=True)
        return

    for it in accepted_items:
        await db.log_event(
            actor_role="staff",
            actor_id=callback.from_user.id,
            action="accept_student",
            trip_id=trip_id,
            student_id=it["student_id"]
        )
        await NotifyService.send_student_notification(
            db=db,
            student_id=it["student_id"],
            text="Farzandingiz darsga kirdi.",
            bot=callback.bot
        )

    updated_trip = await db.get_trip_details(trip_id)
    card = format_staff_trip_card(updated_trip, is_center=True)
    kb = build_staff_center_keyboard(updated_trip)
    await callback.message.edit_text(card, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Barcha o'quvchilar darsga qabul qilindi!")

# --- 5. [🚗 Topshirdim] ---
@staff_router.callback_query(StaffHandoverCallback.filter())
async def cb_staff_handover(callback: types.CallbackQuery, db: Database, callback_data: StaffHandoverCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat markaz xodimlari uchun!", show_alert=True)
        return

    parsed = callback_data or StaffHandoverCallback.unpack(callback.data)
    trip_id, item_id = parsed.trip_id, parsed.item_id

    target_item = await db.get_trip_item_by_id(item_id)
    if not target_item or target_item["trip_id"] != trip_id:
        await callback.answer("O'quvchi ushbu reysga tegishli emas!", show_alert=True)
        return

    success, reason, item = await db.handover_student(item_id, staff["id"])
    if not success:
        if reason == "already_handed_over":
            await callback.answer("Allaqachon bajarilgan!", show_alert=True)
        else:
            await callback.answer("Amalni bajarib bo'lmadi!", show_alert=True)
        return

    # Audit log
    await db.log_event(
        actor_role="staff",
        actor_id=callback.from_user.id,
        action="handover_student",
        trip_id=trip_id,
        student_id=item["student_id"]
    )

    # Ota-onaga bildirishnoma
    await NotifyService.send_student_notification(
        db=db,
        student_id=item["student_id"],
        text="Dars tugadi, farzandingiz haydovchiga topshirildi.",
        bot=callback.bot
    )

    updated_trip = await db.get_trip_details(trip_id)
    card = format_staff_trip_card(updated_trip, is_center=False)
    kb = build_staff_home_keyboard(updated_trip)
    await callback.message.edit_text(card, reply_markup=kb, parse_mode="HTML")
    await callback.answer("O'quvchi haydovchiga topshirildi!")

# --- 6. [🚗 Hammasini topshirish] ---
@staff_router.callback_query(StaffHandoverAllCallback.filter())
async def cb_staff_handover_all(callback: types.CallbackQuery, db: Database, callback_data: StaffHandoverAllCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat markaz xodimlari uchun!", show_alert=True)
        return

    trip_id = (callback_data or StaffHandoverAllCallback.unpack(callback.data)).trip_id
    success, reason, handed_items = await db.handover_all_trip_students(trip_id, staff["id"])

    if not success:
        if reason in ["already_all_handed_over", "already_handed_over"]:
            await callback.answer("Allaqachon bajarilgan!", show_alert=True)
        else:
            await callback.answer("Topshiriladigan o'quvchilar yo'q!", show_alert=True)
        return

    for it in handed_items:
        await db.log_event(
            actor_role="staff",
            actor_id=callback.from_user.id,
            action="handover_student",
            trip_id=trip_id,
            student_id=it["student_id"]
        )
        await NotifyService.send_student_notification(
            db=db,
            student_id=it["student_id"],
            text="Dars tugadi, farzandingiz haydovchiga topshirildi.",
            bot=callback.bot
        )

    updated_trip = await db.get_trip_details(trip_id)
    card = format_staff_trip_card(updated_trip, is_center=False)
    kb = build_staff_home_keyboard(updated_trip)
    await callback.message.edit_text(card, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Barcha o'quvchilar haydovchiga topshirildi!")

# --- 7. Yangilash va yordamchilar ---
@staff_router.callback_query(StaffRefreshCenterCallback.filter())
async def cb_staff_refresh_center(callback: types.CallbackQuery, db: Database, callback_data: StaffRefreshCenterCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat xodimlar uchun!", show_alert=True)
        return
    trip_id = (callback_data or StaffRefreshCenterCallback.unpack(callback.data)).trip_id
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return
    card = format_staff_trip_card(trip, is_center=True)
    kb = build_staff_center_keyboard(trip)
    await safe_refresh_edit(callback, card, kb)

@staff_router.callback_query(StaffRefreshHomeCallback.filter())
async def cb_staff_refresh_home(callback: types.CallbackQuery, db: Database, callback_data: StaffRefreshHomeCallback | None = None):
    staff = await db.get_staff_by_tg_id(callback.from_user.id)
    if not staff:
        await callback.answer("Faqat xodimlar uchun!", show_alert=True)
        return
    trip_id = (callback_data or StaffRefreshHomeCallback.unpack(callback.data)).trip_id
    trip = await db.get_trip_details(trip_id)
    if not trip:
        await callback.answer("Reys topilmadi!", show_alert=True)
        return
    card = format_staff_trip_card(trip, is_center=False)
    kb = build_staff_home_keyboard(trip)
    await safe_refresh_edit(callback, card, kb)

@staff_router.callback_query(F.data == "staff_already_done")
async def cb_staff_already_done(callback: types.CallbackQuery):
    await callback.answer("Allaqachon bajarilgan!", show_alert=True)

@staff_router.callback_query(F.data == "staff_noop")
async def cb_staff_noop(callback: types.CallbackQuery):
    await callback.answer()
