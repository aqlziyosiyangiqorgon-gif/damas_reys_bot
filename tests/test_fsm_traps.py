from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.admin import AddDriverStates, admin_router, reject_non_text_fsm_input
from handlers.common import cmd_cancel
from handlers.parent import (
    ParentStates,
    parent_center_contact,
    parent_report_absence_start,
    parent_track_children,
    parent_view_schedule,
)
from models.enums import UserRole


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [UserRole.ADMIN, UserRole.DRIVER, UserRole.STAFF, UserRole.PARENT, UserRole.GUEST],
)
async def test_cancel_clears_state_and_returns_role_menu(role):
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock())

    await cmd_cancel(message, state, role)

    state.clear.assert_awaited_once()
    _, kwargs = message.answer.await_args
    menu_text = [button.text for row in kwargs["reply_markup"].keyboard for button in row]
    assert menu_text


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["photo", "sticker"])
async def test_admin_non_text_input_is_rejected_during_fsm(media):
    state = SimpleNamespace(get_state=AsyncMock(return_value=AddDriverStates.waiting_for_tg_id.state))
    message = SimpleNamespace(answer=AsyncMock())

    await reject_non_text_fsm_input(message, state)

    message.answer.assert_awaited_once()
    assert "matn yuboring" in message.answer.await_args.args[0]


def test_admin_fsm_message_handlers_require_text_or_supported_photo():
    handlers = {handler.callback.__name__: handler for handler in admin_router.message.handlers}
    text_handler_names = {
        "process_village_name", "process_village_school", "process_village_minutes",
        "process_village_order", "process_village_pickup", "process_driver_tg_id",
        "process_driver_name", "process_driver_phone", "process_staff_tg_id",
        "process_staff_name", "process_staff_phone", "process_student_name",
        "process_student_phone_1", "process_student_phone_2", "process_student_time",
        "process_new_student_name", "process_new_phone1", "process_save_schedule_time",
        "process_custom_delay_minutes", "process_broadcast_student_value",
    }
    assert text_handler_names <= handlers.keys()
    assert all(len(handlers[name].filters) == 2 for name in text_handler_names)
    assert len(handlers["process_broadcast_content"].filters) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "has_db"),
    [
        (parent_track_children, True),
        (parent_view_schedule, True),
        (parent_center_contact, False),
        (parent_report_absence_start, True),
    ],
)
async def test_parent_main_menu_clears_absence_reason_state(handler, has_db):
    parent_id = 778899001
    state = SimpleNamespace(
        get_state=AsyncMock(return_value=ParentStates.waiting_for_absence_reason.state),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=parent_id),
        answer=AsyncMock(),
    )
    db = SimpleNamespace(get_parent_by_telegram_id=AsyncMock(return_value=None))

    if handler is parent_report_absence_start:
        await handler(message, state, db)
    elif has_db:
        await handler(message, db, state)
    else:
        await handler(message, state)

    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once()
