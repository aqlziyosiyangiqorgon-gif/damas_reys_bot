from models.callbacks import (
    AbsentButtonCallback,
    AbsentSelectCallback,
    ConfirmChildCallback,
    RejectChildCallback,
    DriverAlertCallback,
    DriverAlreadyDoneCallback,
    DriverArriveCenterCallback,
    DriverDropoffCallback,
    DriverDropoffVillageCallback,
    DriverFinishTripCallback,
    DriverPickupCallback,
    DriverPickupVillageCallback,
    DriverRefreshTripCallback,
    DriverStartTripCallback,
    StaffAcceptAllCallback,
    StaffAcceptCallback,
    StaffHandoverAllCallback,
    StaffHandoverCallback,
    StaffRefreshCenterCallback,
    StaffRefreshHomeCallback,
    EditVillageCallback,
    SelectVillageCallback,
    ToggleDriverCallback,
    ToggleStaffCallback,
    PageEditStudentCallback,
    EditStudentSelectCallback,
    PageDeleteStudentCallback,
    DeleteStudentSelectCallback,
    PageScheduleStudentCallback,
    ScheduleStudentSelectCallback,
    AssignStudentDriverCallback,
    SetStudentDriverCallback,
    SetNewVillageCallback,
    DeactivateStudentCallback,
    EditScheduleDayCallback,
    ApproveTripsCallback,
    ApproveTripCallback,
    CancelTripCallback,
    AdminDelayTripCallback,
    AdminDelayCustomCallback,
    AdminDelayConfirmCallback,
    AdminDelayApplyCallback,
    AdminAssignTripCallback,
    AdminSetTripDriverCallback,
    ExcelSaveCallback,
    ExcelCancelCallback,
    BroadcastAudienceCallback,
    BroadcastValueCallback,
    PageBroadcastStudentCallback,
    BroadcastStudentCallback,
    BroadcastSendCallback,
    BroadcastCancelBatchCallback,
    AdminNavigationCallback,
)


MAX_ID = 9_223_372_036_854_775_807


def test_parent_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        ConfirmChildCallback(student_id=MAX_ID, parent_id=MAX_ID),
        RejectChildCallback(student_id=MAX_ID, parent_id=MAX_ID),
        AbsentSelectCallback(student_id=MAX_ID),
        AbsentButtonCallback(student_id=MAX_ID),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_driver_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        DriverAlertCallback(action="car_breakdown"),
        DriverStartTripCallback(trip_id=MAX_ID),
        DriverPickupCallback(trip_id=MAX_ID, item_id=MAX_ID),
        DriverArriveCenterCallback(trip_id=MAX_ID),
        DriverDropoffCallback(trip_id=MAX_ID, item_id=MAX_ID),
        DriverFinishTripCallback(trip_id=MAX_ID),
        DriverRefreshTripCallback(trip_id=MAX_ID),
        DriverAlreadyDoneCallback(item_id=MAX_ID),
        DriverPickupVillageCallback(trip_id=MAX_ID, village_id=MAX_ID),
        DriverDropoffVillageCallback(trip_id=MAX_ID, village_id=MAX_ID),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_staff_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        StaffAcceptCallback(trip_id=MAX_ID, item_id=MAX_ID),
        StaffAcceptAllCallback(trip_id=MAX_ID),
        StaffHandoverCallback(trip_id=MAX_ID, item_id=MAX_ID),
        StaffHandoverAllCallback(trip_id=MAX_ID),
        StaffRefreshCenterCallback(trip_id=MAX_ID),
        StaffRefreshHomeCallback(trip_id=MAX_ID),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_admin_entity_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        EditVillageCallback(village_id=MAX_ID),
        SelectVillageCallback(village_id=MAX_ID),
        ToggleDriverCallback(driver_id=MAX_ID, active=1),
        ToggleStaffCallback(staff_id=MAX_ID, active=1),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_student_management_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        PageEditStudentCallback(page=MAX_ID),
        EditStudentSelectCallback(student_id=MAX_ID, page=MAX_ID),
        PageDeleteStudentCallback(page=MAX_ID),
        DeleteStudentSelectCallback(student_id=MAX_ID, page=MAX_ID),
        PageScheduleStudentCallback(page=MAX_ID),
        ScheduleStudentSelectCallback(student_id=MAX_ID, page=MAX_ID),
        AssignStudentDriverCallback(student_id=MAX_ID),
        SetStudentDriverCallback(student_id=MAX_ID, driver_id=MAX_ID),
        SetNewVillageCallback(village_id=MAX_ID),
        DeactivateStudentCallback(student_id=MAX_ID),
        EditScheduleDayCallback(student_id=MAX_ID, weekday=6),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)
    assert SetStudentDriverCallback.unpack(f"set_student_driver:{MAX_ID}:none").driver_id == "none"


def test_admin_trip_callback_payloads_fit_telegram_limit_at_max_ids():
    payloads = [
        ApproveTripsCallback(date="9999-12-31"),
        ApproveTripCallback(trip_id=MAX_ID),
        CancelTripCallback(trip_id=MAX_ID),
        AdminDelayTripCallback(trip_id=MAX_ID),
        AdminDelayCustomCallback(trip_id=MAX_ID),
        AdminDelayConfirmCallback(trip_id=MAX_ID, minutes=MAX_ID),
        AdminDelayApplyCallback(trip_id=MAX_ID, minutes=MAX_ID),
        AdminAssignTripCallback(trip_id=MAX_ID),
        AdminSetTripDriverCallback(trip_id=MAX_ID, driver_id=MAX_ID),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_excel_callback_payloads_fit_telegram_limit_at_max_batch_id():
    payloads = [ExcelSaveCallback(batch_id="f" * 40), ExcelCancelCallback(batch_id="f" * 40)]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_broadcast_callback_payloads_fit_telegram_limit_at_max_values():
    payloads = [
        BroadcastAudienceCallback(audience_type="students_and_parents"),
        BroadcastValueCallback(audience_type="village", value="9" * 30),
        PageBroadcastStudentCallback(page=MAX_ID),
        BroadcastStudentCallback(student_id=MAX_ID, page=MAX_ID),
        BroadcastSendCallback(batch_id="f" * 40),
        BroadcastCancelBatchCallback(batch_id="f" * 40),
    ]
    assert all(len(payload.pack().encode("utf-8")) <= 64 for payload in payloads)


def test_admin_navigation_callback_payload_fits_telegram_limit():
    assert len(AdminNavigationCallback(target="students").pack().encode("utf-8")) <= 64
