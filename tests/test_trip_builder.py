import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from config import settings
from services.trip_builder import TripBuilder

def test_single_student_to_center_and_to_home():
    """Bitta o'quvchi uchun planned_time formulasi va kelish/qaytish tekshiruvi."""
    builder = TripBuilder(
        capacity=9,
        group_window_min=20,
        min_gap_between_trips_min=40,
        pickup_lead_min=30,
        stop_dwell_min=2,
        max_wait_min=30
    )

    # Arpaariq: travel_minutes=35, route_order=1, zone_id=1
    schedules = [
        {
            "student_id": 1,
            "full_name": "Ali Valiyev",
            "village_id": 1,
            "village_name": "Arpaariq",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        }
    ]

    trips, warnings = builder.generate_trips_for_day(schedules)
    assert len(trips) == 2

    # to_center: planned_time = 09:00 (540m) - 35m - 2m dwell - 5m buffer = 498m = 08:18
    to_center_trip = trips[0]
    assert to_center_trip["direction"] == "to_center"
    assert to_center_trip["planned_time"] == "08:18"
    assert to_center_trip["zone_id"] == 1
    assert len(to_center_trip["items"]) == 1
    assert to_center_trip["items"][0]["student_id"] == 1

    # to_home: planned_time = 11:00
    to_home_trip = trips[1]
    assert to_home_trip["direction"] == "to_home"
    assert to_home_trip["planned_time"] == "11:00"
    assert to_home_trip["zone_id"] == 1
    assert len(to_home_trip["items"]) == 1
    assert to_home_trip["items"][0]["student_id"] == 1


def test_capacity_exceeded_split_9_and_1():
    """Bitta zonada 10 ta bola bo'lsa, CAPACITY=9 bo'yicha 9 va 1 qilib 2 ta reysga bo'linishi."""
    builder = TripBuilder(capacity=9, group_window_min=20)
    
    schedules = []
    for i in range(1, 11):  # 10 ta o'quvchi
        schedules.append({
            "student_id": i,
            "full_name": f"O'quvchi {i}",
            "village_id": 1,
            "village_name": "Arpaariq",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        })

    trips, _ = builder.generate_trips_for_day(schedules)
    
    center_trips = [t for t in trips if t["direction"] == "to_center"]
    home_trips = [t for t in trips if t["direction"] == "to_home"]

    # 10 ta bola 9 va 1 qilib bo'linishi kerak
    assert len(center_trips) == 2
    assert len(center_trips[0]["items"]) == 9
    assert len(center_trips[1]["items"]) == 1

    assert len(home_trips) == 2
    assert len(home_trips[0]["items"]) == 9
    assert len(home_trips[1]["items"]) == 1


def test_pickup_reverse_route_order():
    """
    Yo'nalish tartibi:
    - Kelishda (to_center): route_order ASC (1 -> 2 -> 3, uzoqdan markazga tomon).
    - Qaytishda (to_home): route_order DESC (3 -> 2 -> 1, yaqindan uzoqqa tomon).
    """
    builder = TripBuilder(capacity=9, group_window_min=20, stop_dwell_min=2)

    # 3 ta turli mahalladan bolalar aralash tartibda kiritiladi
    schedules = [
        {
            "student_id": 101,
            "full_name": "Galaravatlik Bola",
            "village_id": 3,
            "village_name": "Galaravat",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 3,  # markazga eng yaqin
            "travel_minutes": 15,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 102,
            "full_name": "Arpaariqlik Bola",
            "village_id": 1,
            "village_name": "Arpaariq",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 1,  # markazdan eng uzoq
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 103,
            "full_name": "Galaravat 2 lik Bola",
            "village_id": 2,
            "village_name": "Galaravat 2",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 2,  # o'rtadagi
            "travel_minutes": 25,
            "start_time": "09:00",
            "end_time": "11:00"
        }
    ]

    trips, _ = builder.generate_trips_for_day(schedules)
    assert len(trips) == 2

    # to_center da route_order ASC (1, 2, 3)
    to_center = trips[0]
    center_route_orders = [item["route_order"] for item in to_center["items"]]
    assert center_route_orders == [1, 2, 3]

    # to_center da planned_time formulasi:
    # eng_erta_start = 09:00 (540m)
    # max(travel_minutes) = 35m (Arpaariq)
    # to'xtashlar_soni = 3 ta unikal qishloq
    # dwell = 3 * 2 = 6m
    # planned_time = 540 - 35 - 6 - 5 = 494m = 08:14
    assert to_center["planned_time"] == "08:14"

    # to_home da route_order DESC (3, 2, 1)
    to_home = trips[1]
    home_route_orders = [item["route_order"] for item in to_home["items"]]
    assert home_route_orders == [3, 2, 1]
    assert to_home["planned_time"] == "11:00"


def test_zones_isolation_not_merged():
    """Turli zonadagi o'quvchilar vaqti bir xil bo'lsa ham avtomatik birlashmasligi kerak."""
    builder = TripBuilder(capacity=9, group_window_min=20)

    schedules = [
        {
            "student_id": 201,
            "full_name": "A-zona Bolasi",
            "village_id": 1,
            "village_name": "Arpaariq",
            "zone_id": 1,
            "zone_name": "A-zona",
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 202,
            "full_name": "B-zona Bolasi",
            "village_id": 4,
            "village_name": "Pirimkent",
            "zone_id": 2,
            "zone_name": "B-zona",
            "route_order": 1,
            "travel_minutes": 30,
            "start_time": "09:00",
            "end_time": "11:00"
        }
    ]

    trips, _ = builder.generate_trips_for_day(schedules)
    
    # 2 ta alohida to_center reysi va 2 ta alohida to_home reysi bo'lishi shart
    center_trips = [t for t in trips if t["direction"] == "to_center"]
    assert len(center_trips) == 2
    assert center_trips[0]["zone_id"] != center_trips[1]["zone_id"]

    home_trips = [t for t in trips if t["direction"] == "to_home"]
    assert len(home_trips) == 2


def test_absences_filtration():
    """Absences ro'yxatidagi o'quvchilar reysdan chiqarilishi."""
    builder = TripBuilder(capacity=9)

    schedules = [
        {
            "student_id": 1,
            "full_name": "Kelgan Bola 1",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 2,
            "full_name": "Kelmagan Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 3,
            "full_name": "Kelgan Bola 2",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        }
    ]

    trips, _ = builder.generate_trips_for_day(schedules, absences=[2])
    center_trip = trips[0]
    student_ids_in_trip = [item["student_id"] for item in center_trip["items"]]

    assert len(student_ids_in_trip) == 2
    assert 2 not in student_ids_in_trip
    assert student_ids_in_trip == [1, 3]


def test_max_wait_min_split():
    """Kutish vaqti MAX_WAIT_MIN (30 daqiqa) dan oshsa alohida reysga ajratilishi."""
    builder = TripBuilder(capacity=9, group_window_min=20, max_wait_min=30)

    schedules = [
        {
            "student_id": 1,
            "full_name": "Erta Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 2,
            "full_name": "Kech Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:40",  # 40 daqiqa farq (> 30 daqiqa)
            "end_time": "11:40"
        }
    ]

    trips, _ = builder.generate_trips_for_day(schedules)
    center_trips = [t for t in trips if t["direction"] == "to_center"]
    assert len(center_trips) == 2
    assert center_trips[0]["planned_time"] != center_trips[1]["planned_time"]


def test_to_center_arrives_five_minutes_before_first_lesson():
    builder = TripBuilder(
        stop_dwell_min=2,
        max_wait_min=30,
        route_buffer_min=5,
    )
    schedules = [{
        "student_id": 1,
        "full_name": "Ali Valiyev",
        "village_id": 1,
        "zone_id": 1,
        "route_order": 1,
        "travel_minutes": 25,
        "start_time": "09:00",
        "end_time": "11:00",
    }]

    trips, _ = builder.generate_trips_for_day(schedules)
    to_center = next(trip for trip in trips if trip["direction"] == "to_center")

    assert to_center["planned_time"] == "08:28"
    arrival_at_center = 8 * 60 + 28 + 25 + 2
    assert arrival_at_center == 8 * 60 + 55
    assert 9 * 60 - arrival_at_center == 5


def test_to_center_wait_rule_keeps_0925_but_splits_0930():
    builder = TripBuilder(
        capacity=9,
        group_window_min=30,
        max_wait_min=30,
        route_buffer_min=5,
    )
    schedules = [
        {
            "student_id": 1,
            "full_name": "Erta Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 25,
            "start_time": "09:00",
            "end_time": "11:00",
        },
        {
            "student_id": 2,
            "full_name": "Chegara Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 25,
            "start_time": "09:25",
            "end_time": "11:25",
        },
        {
            "student_id": 3,
            "full_name": "Kech Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 25,
            "start_time": "09:30",
            "end_time": "11:30",
        },
    ]

    trips, _ = builder.generate_trips_for_day(schedules)
    center_trips = [trip for trip in trips if trip["direction"] == "to_center"]

    trip_for_first_two = next(trip for trip in center_trips if 1 in [item["student_id"] for item in trip["items"]])
    assert {item["student_id"] for item in trip_for_first_two["items"]} == {1, 2}
    trip_for_0930 = next(trip for trip in center_trips if 3 in [item["student_id"] for item in trip["items"]])
    assert {item["student_id"] for item in trip_for_0930["items"]} == {3}


def test_to_center_student_40_minutes_later_is_never_in_same_trip():
    builder = TripBuilder(
        group_window_min=60,
        max_wait_min=30,
        route_buffer_min=5,
    )
    schedules = [
        {
            "student_id": 1,
            "full_name": "Erta Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 25,
            "start_time": "09:00",
            "end_time": "11:00",
        },
        {
            "student_id": 2,
            "full_name": "Kech Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 25,
            "start_time": "09:40",
            "end_time": "11:40",
        },
    ]

    trips, _ = builder.generate_trips_for_day(schedules)
    center_trips = [trip for trip in trips if trip["direction"] == "to_center"]

    assert len(center_trips) == 2
    assert all(len(trip["items"]) == 1 for trip in center_trips)


def test_min_gap_warning():
    """Reyslar orasidagi oraliq 40 daqiqadan kam bo'lsa ogohlantirish berilishi."""
    builder = TripBuilder(capacity=9, min_gap_between_trips_min=40)

    # 2 xil zonadagi reyslar orasi 15 daqiqa farq bilan tushsa
    schedules = [
        {
            "student_id": 1,
            "full_name": "A Bola",
            "village_id": 1,
            "zone_id": 1,
            "route_order": 1,
            "travel_minutes": 35,
            "start_time": "09:00",
            "end_time": "11:00"
        },
        {
            "student_id": 2,
            "full_name": "B Bola",
            "village_id": 4,
            "zone_id": 2,
            "route_order": 1,
            "travel_minutes": 15,
            "start_time": "09:00",
            "end_time": "11:00"
        }
    ]

    trips, warnings = builder.generate_trips_for_day(schedules)
    assert len(warnings) > 0
    assert any("orasidagi oraliq" in w for w in warnings)


def test_tashkent_timezone():
    """Asia/Tashkent vaqt mintaqasi to'g'ri ishlashi."""
    tz = ZoneInfo(settings.TIMEZONE)
    assert tz.key == "Asia/Tashkent"
    now = datetime.now(tz)
    assert now.tzinfo is not None
