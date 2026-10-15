from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from config import settings
from models.enums import TripDirection, TripStatus

def parse_time(time_str: str) -> datetime:
    return datetime.strptime(time_str, "%H:%M")

def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")

def time_to_minutes(time_str: str) -> int:
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])

def minutes_to_time(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

class TripBuilder:
    def __init__(
        self,
        capacity: int = settings.CAPACITY,
        group_window_min: int = settings.GROUP_WINDOW_MIN,
        min_gap_between_trips_min: int = settings.MIN_GAP_BETWEEN_TRIPS_MIN,
        pickup_lead_min: int = settings.PICKUP_LEAD_MIN,
        stop_dwell_min: int = settings.STOP_DWELL_MIN,
        max_wait_min: int = settings.MAX_WAIT_MIN,
        route_buffer_min: int = settings.ROUTE_BUFFER_MIN,
    ):
        self.capacity = capacity
        self.group_window_min = group_window_min
        self.min_gap_between_trips_min = min_gap_between_trips_min
        self.pickup_lead_min = pickup_lead_min
        self.stop_dwell_min = stop_dwell_min
        self.max_wait_min = max_wait_min
        self.route_buffer_min = route_buffer_min

    def generate_trips_for_day(
        self,
        schedules: List[Dict[str, Any]],
        absences: Optional[List[int]] = None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Zonalar bo'yicha reyslarni shakllantirish algoritmi:
        1. Absences (kelmaydigan o'quvchilar) filtrlanadi.
        2. Dars jadvallari zonalar (zone_id) bo'yicha ajratiladi. Turli zonalar aralashmaydi.
        3. Har bir zona ichida:
           - Kelish (to_center): start_time bo'yicha GROUP_WINDOW_MIN va MAX_WAIT_MIN doirasida guruhlanadi.
             CAPACITY (9) oshsa reys bo'linadi.
             Reys ichida route_order ASC (1-uzoqlikdan markazga tomon) tartiblanadi.
             planned_time = eng_erta_start - max(travel_minutes) - dwell_total - route_buffer_min.
           - Qaytish (to_home): end_time bo'yicha GROUP_WINDOW_MIN doirasida guruhlanadi.
             CAPACITY (9) oshsa bo'linadi.
             Reys ichida route_order DESC (yaqindan uzoqqa tomon) tartiblanadi.
             planned_time = end_time.
        4. Reyslar orasi MIN_GAP_BETWEEN_TRIPS_MIN (40 daqiqa) dan kam bo'lsa ogohlantirish qaytariladi.
        """
        warnings: List[str] = []
        if not schedules:
            return [], warnings

        # 1. Absences filtrlash
        if absences:
            absent_set = set(absences)
            valid_schedules = [s for s in schedules if s["student_id"] not in absent_set]
        else:
            valid_schedules = list(schedules)

        if not valid_schedules:
            return [], warnings

        # 2. Zonalar bo'yicha guruhlash
        # Do not put children assigned to different drivers into one trip.
        zones_map: Dict[Tuple[int, Optional[int]], List[Dict[str, Any]]] = {}
        for sc in valid_schedules:
            z_id = sc.get("zone_id", 1)
            key = (z_id, sc.get("default_driver_id"))
            zones_map.setdefault(key, []).append(sc)

        all_trips: List[Dict[str, Any]] = []

        # Har bir zona uchun reyslarni alohida hisoblash
        for (zone_id, _driver_id), zone_schedules in sorted(
            zones_map.items(), key=lambda item: (item[0][0], item[0][1] or 0)
        ):
            # A) Kelish reyslari (to_center)
            center_trips = self._build_to_center_trips(zone_id, zone_schedules)
            all_trips.extend(center_trips)

            # B) Qaytish reyslari (to_home)
            home_trips = self._build_to_home_trips(zone_id, zone_schedules)
            all_trips.extend(home_trips)

        # 4. Barcha reyslarni vaqt bo'yicha saralash
        all_trips.sort(key=lambda t: parse_time(t["planned_time"]))

        # 5. Reyslar orasidagi oraliqni tekshirish
        for i in range(len(all_trips) - 1):
            t_curr = parse_time(all_trips[i]["planned_time"])
            t_next = parse_time(all_trips[i+1]["planned_time"])
            gap = int((t_next - t_curr).total_seconds() // 60)
            if gap < self.min_gap_between_trips_min:
                warnings.append(
                    f"⚠️ Diqqat! #{i+1} ({all_trips[i]['planned_time']} {all_trips[i]['direction']}) va "
                    f"#{i+2} ({all_trips[i+1]['planned_time']} {all_trips[i+1]['direction']}) reyslari "
                    f"orasidagi oraliq {gap} daqiqa (minimal norma: {self.min_gap_between_trips_min} daqiqa)!"
                )

        return all_trips, warnings

    def _build_to_center_trips(self, zone_id: int, schedules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Zona ichida markazga kelish (to_center) reyslarini tuzish."""
        # start_time bo'yicha tartiblash
        sorted_schedules = sorted(schedules, key=lambda s: parse_time(s["start_time"]))

        clusters: List[List[Dict[str, Any]]] = []
        current_cluster: List[Dict[str, Any]] = []
        earliest_start_dt: Optional[datetime] = None

        for sc in sorted_schedules:
            st_dt = parse_time(sc["start_time"])
            if not current_cluster:
                current_cluster = [sc]
                earliest_start_dt = st_dt
            else:
                diff_min = int((st_dt - earliest_start_dt).total_seconds() // 60)
                if (
                    diff_min <= self.group_window_min
                    and diff_min + self.route_buffer_min <= self.max_wait_min
                ):
                    current_cluster.append(sc)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [sc]
                    earliest_start_dt = st_dt

        if current_cluster:
            clusters.append(current_cluster)

        trips: List[Dict[str, Any]] = []

        for cluster in clusters:
            # CAPACITY bo'yicha bo'lish (masalan 10 ta bo'lsa -> 9 va 1)
            for i in range(0, len(cluster), self.capacity):
                chunk = list(cluster[i:i + self.capacity])

                # SATR: route_order ASC bo'yicha saralash (1-uzoqlikdan markazga tomon yaqinlashish)
                chunk.sort(key=lambda x: (x.get("route_order", 0), x.get("student_id", 0)))

                # planned_time = eng_erta_start - max(travel_minutes) - dwell_total - ROUTE_BUFFER_MIN
                earliest_min = min(time_to_minutes(x["start_time"]) for x in chunk)
                max_travel = max(x.get("travel_minutes", 0) for x in chunk)
                unique_stops = len(set(x["village_id"] for x in chunk))
                dwell_total = unique_stops * self.stop_dwell_min

                planned_minutes = earliest_min - max_travel - dwell_total - self.route_buffer_min
                planned_time_str = minutes_to_time(planned_minutes)

                items = []
                for it in chunk:
                    item_copy = dict(it)
                    item_copy["target_time_str"] = planned_time_str
                    item_copy["lesson_time"] = it["start_time"]
                    items.append(item_copy)

                trip_dict = {
                    "direction": TripDirection.TO_CENTER.value,
                    "type": TripDirection.TO_CENTER.value,
                    "zone_id": zone_id,
                    "zone_name": chunk[0].get("zone_name", f"{zone_id}-zona"),
                    "planned_time": planned_time_str,
                    "driver_id": chunk[0].get("default_driver_id"),
                    "status": TripStatus.PLANNED.value,
                    "items": items
                }
                trips.append(trip_dict)

        return trips

    def _build_to_home_trips(self, zone_id: int, schedules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Zona ichida uyga qaytish (to_home) reyslarini tuzish."""
        # end_time bo'yicha tartiblash
        sorted_schedules = sorted(schedules, key=lambda s: parse_time(s["end_time"]))

        clusters: List[List[Dict[str, Any]]] = []
        current_cluster: List[Dict[str, Any]] = []
        earliest_end_dt: Optional[datetime] = None

        for sc in sorted_schedules:
            end_dt = parse_time(sc["end_time"])
            if not current_cluster:
                current_cluster = [sc]
                earliest_end_dt = end_dt
            else:
                diff_min = int((end_dt - earliest_end_dt).total_seconds() // 60)
                if diff_min <= self.group_window_min:
                    current_cluster.append(sc)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [sc]
                    earliest_end_dt = end_dt

        if current_cluster:
            clusters.append(current_cluster)

        trips: List[Dict[str, Any]] = []

        for cluster in clusters:
            # CAPACITY bo'yicha bo'lish (masalan 10 ta bo'lsa -> 9 va 1)
            for i in range(0, len(cluster), self.capacity):
                chunk = list(cluster[i:i + self.capacity])

                # SATR: route_order DESC bo'yicha saralash (yaqindan uzoqqa tomon tarqatish)
                chunk.sort(key=lambda x: (-x.get("route_order", 0), x.get("student_id", 0)))

                # planned_time = end_time (hamma darsini tugatgan vaqt)
                max_end_min = max(time_to_minutes(x["end_time"]) for x in chunk)
                planned_time_str = minutes_to_time(max_end_min)

                items = []
                for it in chunk:
                    item_copy = dict(it)
                    item_copy["target_time_str"] = planned_time_str
                    item_copy["lesson_time"] = it["end_time"]
                    items.append(item_copy)

                trip_dict = {
                    "direction": TripDirection.TO_HOME.value,
                    "type": TripDirection.TO_HOME.value,
                    "zone_id": zone_id,
                    "zone_name": chunk[0].get("zone_name", f"{zone_id}-zona"),
                    "planned_time": planned_time_str,
                    "driver_id": chunk[0].get("default_driver_id"),
                    "status": TripStatus.PLANNED.value,
                    "items": items
                }
                trips.append(trip_dict)

        return trips
