import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from config import settings
from database.db import Database
from services.miniapp_auth import MiniAppAuthError, validate_init_data
from services.miniapp_server import MiniAppServer, create_miniapp_app
from services.scheduler_service import SchedulerService
from utils.clock import now_tz


BOT_TOKEN = "123456:test-miniapp-token"


def signed_init_data(telegram_id: int, *, first_name: str = "Test", auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "user": json.dumps({"id": telegram_id, "first_name": first_name}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def make_client(db, *, bot=None, scheduler=None):
    client = TestClient(TestServer(create_miniapp_app(
        db, bot=bot, scheduler=scheduler, bot_token=BOT_TOKEN
    )))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_init_data_rejects_bad_signature_and_stale_auth():
    valid = signed_init_data(73, auth_date=1_800_000_000)
    assert validate_init_data(valid, BOT_TOKEN, now=1_800_000_001)["id"] == 73

    with pytest.raises(MiniAppAuthError):
        validate_init_data(valid + "x", BOT_TOKEN, now=1_800_000_001)
    with pytest.raises(MiniAppAuthError):
        validate_init_data(valid, BOT_TOKEN, now=1_800_100_000)


@pytest.mark.asyncio
async def test_parent_dashboard_only_shows_linked_children_and_sunday_schedule(tmp_path):
    db = Database(str(tmp_path / "miniapp_parent.db"))
    await db.init_db()
    linked_id = await db.add_student("Mini App Farzand", 1, "+998901234567")
    unlinked_id = await db.add_student("Boshqa Oquvchi", 1, "+998901112233")
    await db.add_schedule(linked_id, 6, "09:00", "11:00")
    parent_id = await db.add_parent(810000001, "+998901234567")
    await db.link_student_parent(linked_id, parent_id)

    client = await make_client(db)
    try:
        response = await client.get(
            "/api/miniapp/dashboard",
            headers={"Authorization": f"tma {signed_init_data(810000001)}"},
        )
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["role"] == "parent"
    assert [child["id"] for child in body["data"]["children"]] == [linked_id]
    assert body["data"]["children"][0]["schedules"][0]["day"] == "Yakshanba"
    assert unlinked_id not in [child["id"] for child in body["data"]["children"]]


@pytest.mark.asyncio
async def test_parent_cannot_report_absence_for_unlinked_student(tmp_path):
    db = Database(str(tmp_path / "miniapp_ownership.db"))
    await db.init_db()
    own_student = await db.add_student("Ozimniki", 1, "+998901234567")
    other_student = await db.add_student("Boshqa", 1, "+998901112233")
    parent_id = await db.add_parent(810000002, "+998901234567")
    await db.link_student_parent(own_student, parent_id)

    client = await make_client(db)
    try:
        response = await client.post(
            "/api/miniapp/action",
            headers={"Authorization": f"tma {signed_init_data(810000002)}"},
            json={"action": "report_absence", "student_id": other_student, "reason": "test"},
        )
    finally:
        await client.close()

    assert response.status == 403
    assert await db.get_absent_student_ids("2026-10-18") == []


@pytest.mark.asyncio
async def test_parent_can_report_absence_for_linked_child(tmp_path):
    db = Database(str(tmp_path / "miniapp_parent_absence.db"))
    await db.init_db()
    student_id = await db.add_student("Boglangan Bola", 1, "+998901234567")
    parent_id = await db.add_parent(810000007, "+998901234567")
    await db.link_student_parent(student_id, parent_id)
    client = await make_client(db)
    try:
        response = await client.post(
            "/api/miniapp/action",
            headers={"Authorization": f"tma {signed_init_data(810000007)}"},
            json={"action": "report_absence", "student_id": student_id, "reason": "Kasallik"},
        )
        body = await response.json()
    finally:
        await client.close()

    today = now_tz().strftime("%Y-%m-%d")
    assert response.status == 200
    assert "qayd etildi" in body["message"]
    assert await db.get_absent_student_ids(today) == [student_id]


@pytest.mark.asyncio
async def test_guest_is_rejected_and_html_is_served(tmp_path):
    db = Database(str(tmp_path / "miniapp_guest.db"))
    await db.init_db()
    client = await make_client(db)
    try:
        page = await client.get("/")
        page_text = await page.text()
        response = await client.get(
            "/api/miniapp/dashboard",
            headers={"Authorization": f"tma {signed_init_data(810000003)}"},
        )
        body = await response.json()
    finally:
        await client.close()

    assert page.status == 200
    assert "AQL ZIYOSI" in page_text
    assert response.status == 403
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_driver_staff_and_admin_get_their_own_panels(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "miniapp_roles.db"))
    await db.init_db()
    await db.add_driver(810000004, "Mini App Haydovchi", "+998901234567")
    await db.add_staff(810000005, "Mini App Xodim")
    monkeypatch.setattr(settings, "ADMIN_IDS", "810000006")

    client = await make_client(db)
    try:
        results = {}
        for telegram_id in (810000004, 810000005, 810000006):
            response = await client.get(
                "/api/miniapp/dashboard",
                headers={"Authorization": f"tma {signed_init_data(telegram_id)}"},
            )
            results[telegram_id] = (response.status, await response.json())
    finally:
        await client.close()

    assert results[810000004][0] == 200
    assert results[810000004][1]["role"] == "driver"
    assert results[810000005][0] == 200
    assert results[810000005][1]["role"] == "staff"
    assert results[810000006][0] == 200
    assert results[810000006][1]["role"] == "admin"


@pytest.mark.asyncio
async def test_miniapp_server_can_start_and_stop(tmp_path):
    db = Database(str(tmp_path / "miniapp_server_lifecycle.db"))
    await db.init_db()
    server = MiniAppServer(db, bot_token=BOT_TOKEN)

    await server.start(host="127.0.0.1", port=0)
    assert server.runner is not None

    await server.stop()
    assert server.runner is None


@pytest.mark.asyncio
async def test_driver_and_staff_can_complete_a_trip_from_miniapp(tmp_path):
    db = Database(str(tmp_path / "miniapp_trip_actions.db"))
    await db.init_db()
    driver_tg_id = 810000008
    staff_tg_id = 810000009
    driver_id = await db.add_driver(driver_tg_id, "Mini Haydovchi", "+998901234567")
    staff_id = await db.add_staff(staff_tg_id, "Mini Xodim")
    student_id = await db.add_student("Mini Reys Bola", 1, "+998901112233")
    today = now_tz().strftime("%Y-%m-%d")
    await db.add_schedule(student_id, now_tz().weekday(), "09:00", "11:00")
    scheduler = SchedulerService(db)
    success, reason, _trips = await scheduler.run_daily_plan(target_date=today)
    assert success is True
    assert reason == "success"
    await db.approve_all_daily_trips(today, 810000006)
    trips = await db.get_driver_today_trips(driver_id, today)
    to_center = next(trip for trip in trips if trip["direction"] == "to_center")
    to_home = next(trip for trip in trips if trip["direction"] == "to_home")
    center_item_id = to_center["items"][0]["id"]
    home_item_id = to_home["items"][0]["id"]

    client = await make_client(db, scheduler=scheduler)
    try:
        async def do_action(telegram_id, **payload):
            response = await client.post(
                "/api/miniapp/action",
                headers={"Authorization": f"tma {signed_init_data(telegram_id)}"},
                json=payload,
            )
            return response.status, await response.json()

        assert (await do_action(driver_tg_id, action="start", trip_id=to_center["id"]))[0] == 200
        assert (await do_action(driver_tg_id, action="pickup", trip_id=to_center["id"], item_id=center_item_id))[0] == 200
        assert (await do_action(driver_tg_id, action="arrive_center", trip_id=to_center["id"]))[0] == 200
        assert (await do_action(staff_tg_id, action="accept", item_id=center_item_id))[0] == 200

        assert (await do_action(staff_tg_id, action="handover", item_id=home_item_id))[0] == 200
        assert (await do_action(driver_tg_id, action="start", trip_id=to_home["id"]))[0] == 200
        assert (await do_action(driver_tg_id, action="dropoff", trip_id=to_home["id"], item_id=home_item_id))[0] == 200
        assert (await do_action(driver_tg_id, action="finish", trip_id=to_home["id"]))[0] == 200
    finally:
        await client.close()

    assert (await db.get_trip_details(to_center["id"]))["status"] == "done"
    home_result = await db.get_trip_details(to_home["id"])
    assert home_result["status"] == "done"
    assert home_result["items"][0]["status"] == "dropped_off"


@pytest.mark.asyncio
async def test_admin_can_create_and_approve_plan_from_miniapp(tmp_path, monkeypatch):
    admin_id = 810000010
    monkeypatch.setattr(settings, "ADMIN_IDS", str(admin_id))
    db = Database(str(tmp_path / "miniapp_admin_actions.db"))
    await db.init_db()
    student_id = await db.add_student("Admin Mini Test", 1, "+998901234567")
    today = now_tz().strftime("%Y-%m-%d")
    await db.add_schedule(student_id, now_tz().weekday(), "09:00", "11:00")
    scheduler = SchedulerService(db)
    client = await make_client(db, scheduler=scheduler)
    try:
        headers = {"Authorization": f"tma {signed_init_data(admin_id)}"}
        create_response = await client.post(
            "/api/miniapp/action", headers=headers, json={"action": "create_plan"}
        )
        create_body = await create_response.json()
        approve_response = await client.post(
            "/api/miniapp/action", headers=headers, json={"action": "approve_trips"}
        )
        approve_body = await approve_response.json()
    finally:
        await client.close()

    assert create_response.status == 200
    assert create_body["ok"] is True
    assert approve_response.status == 200
    assert "2 ta" in approve_body["message"]
    assert all(trip["status"] == "approved" for trip in await db.get_trips_by_date(today))
