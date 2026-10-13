from enum import StrEnum

class UserRole(StrEnum):
    ADMIN = "admin"
    STAFF = "staff"
    DRIVER = "driver"
    PARENT = "parent"
    GUEST = "guest"

class TripDirection(StrEnum):
    TO_CENTER = "to_center"
    TO_HOME = "to_home"

class TripStatus(StrEnum):
    PLANNED = "planned"
    APPROVED = "approved"
    ON_WAY = "on_way"
    AT_CENTER = "at_center"       # to_center da
    HANDED_OVER = "handed_over"   # to_home da markazdan topshirildi
    DONE = "done"
    CANCELLED = "cancelled"

class TripItemStatus(StrEnum):
    PENDING = "pending"
    # to_center
    PICKED_UP = "picked_up"
    ARRIVED = "arrived"
    ACCEPTED = "accepted"
    # to_home
    HANDED_OVER = "handed_over"
    DROPPED_OFF = "dropped_off"
    # umumiy
    ABSENT = "absent"
