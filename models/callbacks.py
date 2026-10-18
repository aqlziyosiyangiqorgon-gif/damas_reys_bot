"""Typed Telegram callback payloads; keep packed values within Telegram's 64-byte limit."""

from aiogram.filters.callback_data import CallbackData
from typing import Literal


class ConfirmChildCallback(CallbackData, prefix="confirm_ch"):
    student_id: int
    parent_id: int


class RejectChildCallback(CallbackData, prefix="reject_ch"):
    student_id: int
    parent_id: int


class AbsentSelectCallback(CallbackData, prefix="absent_select"):
    student_id: int


class AbsentButtonCallback(CallbackData, prefix="absent_btn"):
    student_id: int


class DriverAlertCallback(CallbackData, prefix="driver_alert"):
    action: str


class DriverStartTripCallback(CallbackData, prefix="driver_start_trip"):
    trip_id: int


class DriverPickupCallback(CallbackData, prefix="driver_pickup"):
    trip_id: int
    item_id: int


class DriverArriveCenterCallback(CallbackData, prefix="driver_arrive_center"):
    trip_id: int


class DriverDropoffCallback(CallbackData, prefix="driver_dropoff"):
    trip_id: int
    item_id: int


class DriverFinishTripCallback(CallbackData, prefix="driver_finish_trip"):
    trip_id: int


class DriverRefreshTripCallback(CallbackData, prefix="driver_refresh_trip"):
    trip_id: int


class DriverAlreadyDoneCallback(CallbackData, prefix="driver_already_done"):
    item_id: int


class DriverPickupVillageCallback(CallbackData, prefix="driver_pickup_village"):
    trip_id: int
    village_id: int


class DriverDropoffVillageCallback(CallbackData, prefix="driver_dropoff_village"):
    trip_id: int
    village_id: int


class StaffAcceptCallback(CallbackData, prefix="staff_accept"):
    trip_id: int
    item_id: int


class StaffAcceptAllCallback(CallbackData, prefix="staff_accept_all"):
    trip_id: int


class StaffHandoverCallback(CallbackData, prefix="staff_handover"):
    trip_id: int
    item_id: int


class StaffHandoverAllCallback(CallbackData, prefix="staff_handover_all"):
    trip_id: int


class StaffRefreshCenterCallback(CallbackData, prefix="staff_refresh_center"):
    trip_id: int


class StaffRefreshHomeCallback(CallbackData, prefix="staff_refresh_home"):
    trip_id: int


class EditVillageCallback(CallbackData, prefix="edit_v"):
    village_id: int


class ToggleDriverCallback(CallbackData, prefix="toggle_driver"):
    driver_id: int
    active: int


class ToggleStaffCallback(CallbackData, prefix="toggle_staff"):
    staff_id: int
    active: int


class SelectVillageCallback(CallbackData, prefix="sel_vil"):
    village_id: int


class PageEditStudentCallback(CallbackData, prefix="page_edit_st"):
    page: int


class EditStudentSelectCallback(CallbackData, prefix="edit_st"):
    student_id: int
    page: int


class PageDeleteStudentCallback(CallbackData, prefix="page_del_st"):
    page: int


class DeleteStudentSelectCallback(CallbackData, prefix="del_st"):
    student_id: int
    page: int


class PageScheduleStudentCallback(CallbackData, prefix="page_sch_st"):
    page: int


class ScheduleStudentSelectCallback(CallbackData, prefix="sch_st"):
    student_id: int
    page: int


class AssignStudentDriverCallback(CallbackData, prefix="assign_student_driver"):
    student_id: int


class SetStudentDriverCallback(CallbackData, prefix="set_student_driver"):
    student_id: int
    driver_id: int | Literal["none"]


class SetNewVillageCallback(CallbackData, prefix="set_new_vil"):
    village_id: int


class DeactivateStudentCallback(CallbackData, prefix="do_del"):
    student_id: int


class EditScheduleDayCallback(CallbackData, prefix="edit_day_sch"):
    student_id: int
    weekday: int


class ApproveTripsCallback(CallbackData, prefix="admin_approve_trips"):
    date: str


class ApproveTripCallback(CallbackData, prefix="admin_approve_trip"):
    trip_id: int


class CancelTripCallback(CallbackData, prefix="admin_cancel_trip"):
    trip_id: int


class ApproveTripCallback(CallbackData, prefix="admin_approve_trip"):
    trip_id: int


class AdminDelayTripCallback(CallbackData, prefix="admin_delay_trip"):
    trip_id: int


class AdminDelayCustomCallback(CallbackData, prefix="admin_delay_custom"):
    trip_id: int


class AdminDelayConfirmCallback(CallbackData, prefix="admin_delay_confirm"):
    trip_id: int
    minutes: int


class AdminDelayApplyCallback(CallbackData, prefix="admin_delay_apply"):
    trip_id: int
    minutes: int


class AdminAssignTripCallback(CallbackData, prefix="admin_assign_trip"):
    trip_id: int


class AdminSetTripDriverCallback(CallbackData, prefix="admin_set_trip_driver"):
    trip_id: int
    driver_id: int


class ExcelSaveCallback(CallbackData, prefix="excel_save"):
    batch_id: str


class ExcelCancelCallback(CallbackData, prefix="excel_cancel"):
    batch_id: str


class BroadcastAudienceCallback(CallbackData, prefix="bc_aud"):
    audience_type: str


class BroadcastValueCallback(CallbackData, prefix="bc_val"):
    audience_type: str
    value: str


class PageBroadcastStudentCallback(CallbackData, prefix="page_bc_student"):
    page: int


class BroadcastStudentCallback(CallbackData, prefix="bc_student"):
    student_id: int
    page: int


class BroadcastSendCallback(CallbackData, prefix="bc_send"):
    batch_id: str


class BroadcastCancelBatchCallback(CallbackData, prefix="bc_cancel_batch"):
    batch_id: str


class AdminNavigationCallback(CallbackData, prefix="admin_nav"):
    target: str


class BillingStudentCallback(CallbackData, prefix="bill_s"):
    student_id: int
    period: str


class BillingPageCallback(CallbackData, prefix="bill_p"):
    page: int
    period: str


class BillingPeriodCallback(CallbackData, prefix="bill_m"):
    period: str
    move: Literal["p", "n"]


class BillingInvoiceCallback(CallbackData, prefix="bill_i"):
    student_id: int
    amount: int
    period: str


class BillingManualAmountCallback(CallbackData, prefix="bill_manual"):
    student_id: int
    period: str


class BillingListCallback(CallbackData, prefix="bill_l"):
    period: str


class BillingPaymentStartCallback(CallbackData, prefix="bill_pay"):
    student_id: int
    period: str


class BillingDiscountStartCallback(CallbackData, prefix="bill_disc"):
    student_id: int
    period: str


class BillingPaymentMethodCallback(CallbackData, prefix="bill_method"):
    student_id: int
    period: str
    method: Literal["cash", "card"]


