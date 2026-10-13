import os
import aiosqlite
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from models.enums import UserRole

# Boshlang'ich zonalar va ularga tegishli 10 ta mahalla
INITIAL_ZONES = [
    (1, "A-zona", 20),
    (2, "B-zona", 20),
    (3, "C-zona", 20),
    (4, "D-zona", 20)
]

# (name, school_name, zone_id, travel_minutes, route_order)
# route_order: zona ichida markazdan UZOQDAN YAQINGA
INITIAL_VILLAGES = [
    # A-zona
    ("Arpaariq", "4-maktab", 1, 35, 1),
    ("Galaravat 2", "5-maktab", 1, 25, 2),
    ("Galaravat", "1-maktab", 1, 15, 3),
    # B-zona
    ("Pirimkent", "10-maktab", 2, 30, 1),
    ("Mavlum", "9-maktab", 2, 20, 2),
    # C-zona
    ("Oytamg'ali", "8-maktab", 3, 30, 1),
    ("Pilol", "7-maktab", 3, 20, 2),
    # D-zona
    ("Sug'onchi", "6-maktab", 4, 35, 1),
    ("Yangiqo'rg'on", "2-maktab", 4, 25, 2),
    ("Yangihayot Domlar", "54-maktab", 4, 15, 3)
]

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Telefon raqamni +998XXXXXXXXX formatiga keltiradi.
    Faqat:
    - 9 xonali (masalan '901234567')
    - 998 bilan 12 xonali (masalan '998901234567' yoki '+998901234567')
    qabul qilinadi. Boshqa barcha holatlarda None qaytariladi.
    """
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) == 9:
        return f"+998{digits}"
    elif len(digits) == 12 and digits.startswith("998"):
        return f"+{digits}"
    return None

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def get_connection(self) -> aiosqlite.Connection:
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    async def init_db(self):
        conn = await self.get_connection()
        try:
            await conn.executescript("""
            -- 1. Zonalar
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                merge_window_min INTEGER DEFAULT 20
            );

            -- 2. Qishloqlar (Mahallalar)
            CREATE TABLE IF NOT EXISTS villages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                school_name TEXT,
                zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE RESTRICT,
                pickup_point TEXT,
                pickup_lat REAL,
                pickup_lon REAL,
                travel_minutes INTEGER NOT NULL,
                route_order INTEGER NOT NULL,
                default_driver_id INTEGER NULL
            );

            -- 3. Haydovchilar
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            );

            -- 4. Xodimlar (Staff)
            CREATE TABLE IF NOT EXISTS staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                phone TEXT,
                is_active INTEGER DEFAULT 1
            );

            -- 5. O'quvchilar
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                village_id INTEGER NOT NULL REFERENCES villages(id) ON DELETE RESTRICT,
                parent_phone_1 TEXT NOT NULL,
                parent_phone_2 TEXT,
                is_active INTEGER DEFAULT 1
            );

            -- 6. Dars jadvallari
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                UNIQUE(student_id, weekday)
            );

            -- 7. Ota-onalar
            CREATE TABLE IF NOT EXISTS parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                registered_at TEXT NOT NULL
            );

            -- 8. O'quvchi va ota-ona bog'lanishi (tasdiqlangandan keyin)
            CREATE TABLE IF NOT EXISTS student_parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                parent_id INTEGER NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                confirmed_at TEXT NOT NULL,
                UNIQUE(student_id, parent_id)
            );

            -- 9. Kelmaydiganlar (Absences)
            CREATE TABLE IF NOT EXISTS absences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                reported_by_role TEXT NOT NULL,
                reported_by_id INTEGER NOT NULL,
                UNIQUE(student_id, date)
            );

            -- 10. Reyslar
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                direction TEXT NOT NULL,
                driver_id INTEGER,
                planned_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                approved INTEGER NOT NULL DEFAULT 0,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                last_reminder_at TEXT,
                delay_min INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT
            );

            -- 11. Reys elementlari
            CREATE TABLE IF NOT EXISTS trip_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                village_id INTEGER NOT NULL REFERENCES villages(id) ON DELETE RESTRICT,
                status TEXT NOT NULL DEFAULT 'pending',
                picked_at TEXT,
                arrived_at TEXT,
                accepted_at TEXT,
                handed_at TEXT,
                dropped_at TEXT,
                actor_id INTEGER
            );

            -- 12. Audit log
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                trip_id INTEGER,
                student_id INTEGER
            );

            -- 13. Bildirishnomalar logi
            CREATE TABLE IF NOT EXISTS notify_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                parent_id INTEGER,
                student_id INTEGER,
                text TEXT NOT NULL,
                status TEXT NOT NULL
            );

            -- 14. E'lonlar / Xabarlar
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                audience_type TEXT NOT NULL,
                audience_value TEXT,
                text TEXT NOT NULL,
                media TEXT,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0
            );
            """)

            # Boshlang'ich zonalar
            cur = await conn.execute("SELECT COUNT(*) FROM zones")
            if (await cur.fetchone())[0] == 0:
                await conn.executemany(
                    "INSERT INTO zones (id, name, merge_window_min) VALUES (?, ?, ?)",
                    INITIAL_ZONES
                )

            # Boshlang'ich qishloqlar
            cur = await conn.execute("SELECT COUNT(*) FROM villages")
            if (await cur.fetchone())[0] == 0:
                await conn.executemany("""
                    INSERT INTO villages (name, school_name, zone_id, travel_minutes, route_order)
                    VALUES (?, ?, ?, ?, ?)
                """, INITIAL_VILLAGES)

            await conn.commit()
        finally:
            await conn.close()

    # --- Rolni aniqlash ---
    async def get_user_role(self, telegram_id: int, admin_ids: List[int]) -> UserRole:
        if telegram_id in admin_ids:
            return UserRole.ADMIN

        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT id FROM staff WHERE telegram_id = ? AND is_active = 1", (telegram_id,))
            if await cur.fetchone():
                return UserRole.STAFF

            cur = await conn.execute("SELECT id FROM drivers WHERE telegram_id = ? AND is_active = 1", (telegram_id,))
            if await cur.fetchone():
                return UserRole.DRIVER

            cur = await conn.execute("SELECT id FROM parents WHERE telegram_id = ?", (telegram_id,))
            if await cur.fetchone():
                return UserRole.PARENT
        finally:
            await conn.close()

        return UserRole.GUEST

    # --- Zonalar va Qishloqlar ---
    async def get_zones(self) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM zones ORDER BY id ASC")
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_villages(self) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT v.*, z.name as zone_name, z.merge_window_min
                FROM villages v
                JOIN zones z ON v.zone_id = z.id
                ORDER BY v.zone_id ASC, v.route_order ASC
            """)
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_village_by_id(self, village_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT v.*, z.name as zone_name
                FROM villages v
                JOIN zones z ON v.zone_id = z.id
                WHERE v.id = ?
            """, (village_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def update_village(
        self,
        village_id: int,
        name: str,
        travel_minutes: int,
        school_name: Optional[str] = None,
        route_order: Optional[int] = None,
        pickup_point: Optional[str] = None
    ) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE villages
                SET name = ?,
                    travel_minutes = ?,
                    school_name = coalesce(?, school_name),
                    route_order = coalesce(?, route_order),
                    pickup_point = coalesce(?, pickup_point)
                WHERE id = ?
            """, (name, travel_minutes, school_name, route_order, pickup_point, village_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    # --- O'quvchilar ---
    async def add_student(self, full_name: str, village_id: int, parent_phone_1: str, parent_phone_2: Optional[str] = None) -> int:
        norm_phone_1 = normalize_phone(parent_phone_1)
        if not norm_phone_1:
            raise ValueError(f"Asosiy ota-ona telefon raqami noto'g'ri: {parent_phone_1}")
        norm_phone_2 = normalize_phone(parent_phone_2)
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO students (full_name, village_id, parent_phone_1, parent_phone_2, is_active)
                VALUES (?, ?, ?, ?, 1)
            """, (full_name, village_id, norm_phone_1, norm_phone_2))
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()

    # --- Foydalanuvchilar (Testlar va rollar uchun) ---
    async def add_driver(self, telegram_id: int, full_name: str, phone: str) -> int:
        norm_phone = normalize_phone(phone)
        if not norm_phone:
            raise ValueError(f"Haydovchi telefon raqami noto'g'ri: {phone}")
        conn = await self.get_connection()
        try:
            await conn.execute("""
                INSERT INTO drivers (telegram_id, full_name, phone, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name, phone=excluded.phone, is_active=1
            """, (telegram_id, full_name, norm_phone))
            await conn.commit()
            cur = await conn.execute("SELECT id FROM drivers WHERE telegram_id = ?", (telegram_id,))
            row = await cur.fetchone()
            return row[0]
        finally:
            await conn.close()

    async def add_staff(self, telegram_id: int, full_name: str, phone: Optional[str] = None) -> int:
        norm_phone = None
        if phone is not None:
            norm_phone = normalize_phone(phone)
            if not norm_phone:
                raise ValueError(f"Xodim telefon raqami noto'g'ri: {phone}")
        conn = await self.get_connection()
        try:
            await conn.execute("""
                INSERT INTO staff (telegram_id, full_name, phone, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name, phone=coalesce(excluded.phone, staff.phone), is_active=1
            """, (telegram_id, full_name, norm_phone))
            await conn.commit()
            cur = await conn.execute("SELECT id FROM staff WHERE telegram_id = ?", (telegram_id,))
            row = await cur.fetchone()
            return row[0]
        finally:
            await conn.close()

    async def add_parent(self, telegram_id: int, phone: str, registered_at: Optional[str] = None) -> int:
        if not registered_at:
            from datetime import datetime
            registered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        norm_phone = normalize_phone(phone)
        if not norm_phone:
            raise ValueError(f"Ota-ona telefon raqami noto'g'ri: {phone}")
        conn = await self.get_connection()
        try:
            await conn.execute("""
                INSERT INTO parents (telegram_id, phone, registered_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET phone=excluded.phone
            """, (telegram_id, norm_phone, registered_at))
            await conn.commit()
            cur = await conn.execute("SELECT id FROM parents WHERE telegram_id = ?", (telegram_id,))
            row = await cur.fetchone()
            return row[0]
        finally:
            await conn.close()

    # --- 2-faza: Admin boshqaruvi metodlari ---
    async def get_drivers(self) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM drivers ORDER BY id ASC")
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def toggle_driver_status(self, driver_id: int, is_active: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("UPDATE drivers SET is_active = ? WHERE id = ?", (is_active, driver_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def get_all_staff(self) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM staff ORDER BY id ASC")
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_staff_by_id(self, staff_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def toggle_staff_status(self, staff_id: int, is_active: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("UPDATE staff SET is_active = ? WHERE id = ?", (is_active, staff_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def get_active_students(self) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.*, v.name as village_name, v.school_name, v.zone_id, z.name as zone_name
                FROM students s
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                WHERE s.is_active = 1
                ORDER BY s.id ASC
            """)
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_student_by_id(self, student_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.*, v.name as village_name, v.school_name, v.zone_id, z.name as zone_name
                FROM students s
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                WHERE s.id = ?
            """, (student_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def update_student(self, student_id: int, full_name: str, village_id: int, parent_phone_1: str, parent_phone_2: Optional[str] = None) -> bool:
        norm_phone_1 = normalize_phone(parent_phone_1)
        if not norm_phone_1:
            raise ValueError(f"Asosiy ota-ona telefon raqami noto'g'ri: {parent_phone_1}")
        norm_phone_2 = normalize_phone(parent_phone_2)
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE students
                SET full_name = ?, village_id = ?, parent_phone_1 = ?, parent_phone_2 = ?
                WHERE id = ?
            """, (full_name, village_id, norm_phone_1, norm_phone_2, student_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def deactivate_student(self, student_id: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("UPDATE students SET is_active = 0 WHERE id = ?", (student_id,))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def add_schedule(self, student_id: int, weekday: int, start_time: str, end_time: str) -> int:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO schedules (student_id, weekday, start_time, end_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id, weekday) DO UPDATE SET start_time=excluded.start_time, end_time=excluded.end_time
            """, (student_id, weekday, start_time, end_time))
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()

    async def get_student_schedules(self, student_id: int) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT * FROM schedules WHERE student_id = ? ORDER BY weekday ASC
            """, (student_id,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def update_student_schedule_time(self, student_id: int, weekday: int, start_time: str, end_time: str) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO schedules (student_id, weekday, start_time, end_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id, weekday) DO UPDATE SET start_time=excluded.start_time, end_time=excluded.end_time
            """, (student_id, weekday, start_time, end_time))
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def delete_student_schedules(self, student_id: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("DELETE FROM schedules WHERE student_id = ?", (student_id,))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    # --- 3-faza: Reyslar bazasi metodlari ---
    async def get_day_schedules(self, weekday: int) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.id as student_id, s.full_name, s.village_id, s.parent_phone_1, s.parent_phone_2,
                       v.name as village_name, v.zone_id, v.travel_minutes, v.route_order,
                       v.school_name, v.default_driver_id, z.name as zone_name,
                       sch.weekday, sch.start_time, sch.end_time
                FROM schedules sch
                JOIN students s ON sch.student_id = s.id
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                WHERE sch.weekday = ? AND s.is_active = 1
                ORDER BY sch.start_time ASC, v.route_order ASC
            """, (weekday,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_absent_student_ids(self, date: str) -> List[int]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT student_id FROM absences WHERE date = ?", (date,))
            rows = await cur.fetchall()
            return [r[0] for r in rows]
        finally:
            await conn.close()

    async def has_active_trips(self, date: str) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT COUNT(*) FROM trips
                WHERE date = ? AND status IN ('on_way', 'at_center', 'handed_over', 'done')
            """, (date,))
            row = await cur.fetchone()
            return row[0] > 0
        finally:
            await conn.close()

    async def save_planned_trips(self, date: str, trips: List[Dict[str, Any]], force: bool = False) -> bool:
        if not force and await self.has_active_trips(date):
            return False
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT id FROM trips WHERE date = ? AND status IN ('planned', 'approved')", (date,))
            old_trip_ids = [r[0] for r in await cur.fetchall()]
            for t_id in old_trip_ids:
                await conn.execute("DELETE FROM trip_items WHERE trip_id = ?", (t_id,))
                await conn.execute("DELETE FROM trips WHERE id = ?", (t_id,))

            for trip in trips:
                direction = trip.get("direction") or trip.get("type", "to_center")
                cur_t = await conn.execute("""
                    INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
                    VALUES (?, ?, ?, ?, 'planned', 0)
                """, (date, direction, trip.get("driver_id"), trip["planned_time"]))
                trip_db_id = cur_t.lastrowid
                for item in trip.get("items", []):
                    await conn.execute("""
                        INSERT INTO trip_items (trip_id, student_id, village_id, status)
                        VALUES (?, ?, ?, 'pending')
                    """, (trip_db_id, item["student_id"], item["village_id"]))
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def get_trips_by_date(self, date: str) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM trips WHERE date = ? ORDER BY planned_time ASC", (date,))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, s.parent_phone_1 as parent_phone, v.name as village_name, v.route_order
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY v.route_order ASC
                """, (t["id"],))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
                t["type"] = t["direction"]
            return trips
        finally:
            await conn.close()

    # --- 4-faza: Ota-onalar oqimi metodlari ---
    async def find_students_by_phone(self, phone: str) -> List[Dict[str, Any]]:
        norm = normalize_phone(phone)
        if not norm:
            return []
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.*, v.name as village_name, v.school_name, v.zone_id, z.name as zone_name
                FROM students s
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                WHERE (s.parent_phone_1 = ? OR s.parent_phone_2 = ?) AND s.is_active = 1
                ORDER BY s.id ASC
            """, (norm, norm))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_parent_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM parents WHERE telegram_id = ?", (telegram_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def link_student_parent(self, student_id: int, parent_id: int) -> bool:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("""
                INSERT INTO student_parents (student_id, parent_id, confirmed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(student_id, parent_id) DO NOTHING
            """, (student_id, parent_id, now_str))
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def is_student_linked_to_parent(self, student_id: int, parent_id: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT COUNT(*) FROM student_parents
                WHERE student_id = ? AND parent_id = ?
            """, (student_id, parent_id))
            row = await cur.fetchone()
            return row[0] > 0
        finally:
            await conn.close()

    async def get_parent_students(self, parent_id: int) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.*, v.name as village_name, v.school_name, v.zone_id, z.name as zone_name,
                       sp.confirmed_at
                FROM student_parents sp
                JOIN students s ON sp.student_id = s.id
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                WHERE sp.parent_id = ? AND s.is_active = 1
                ORDER BY s.id ASC
            """, (parent_id,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def log_event(
        self,
        actor_role: str,
        actor_id: int,
        action: str,
        trip_id: Optional[int] = None,
        student_id: Optional[int] = None
    ) -> int:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id, student_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, actor_role, actor_id, action, trip_id, student_id))
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()

    # --- 5-faza: Haydovchilar va Bildirishnomalar metodlari ---
    async def get_driver_by_tg_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM drivers WHERE telegram_id = ? AND is_active = 1", (telegram_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def get_driver_today_trips(self, driver_id: int, date: str) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM trips WHERE driver_id = ? AND date = ? ORDER BY planned_time ASC", (driver_id, date))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, s.parent_phone_1 as parent_phone,
                           v.name as village_name, v.travel_minutes, v.route_order, v.pickup_point
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY CASE WHEN ? = 'to_center' THEN v.route_order ELSE -v.route_order END ASC
                """, (t["id"], t["direction"]))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
                t["type"] = t["direction"]
            return trips
        finally:
            await conn.close()

    async def get_trip_details(self, trip_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
            row = await cur.fetchone()
            if not row:
                return None
            trip = dict(row)
            trip["type"] = trip["direction"]
            cur_items = await conn.execute("""
                SELECT ti.*, s.full_name, s.parent_phone_1 as parent_phone,
                       v.name as village_name, v.travel_minutes, v.route_order, v.pickup_point
                FROM trip_items ti
                JOIN students s ON ti.student_id = s.id
                JOIN villages v ON ti.village_id = v.id
                WHERE ti.trip_id = ?
                ORDER BY CASE WHEN ? = 'to_center' THEN v.route_order ELSE -v.route_order END ASC
            """, (trip_id, trip["direction"]))
            trip["items"] = [dict(r) for r in await cur_items.fetchall()]
            return trip
        finally:
            await conn.close()

    async def get_trip_item_by_id(self, item_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT ti.*, s.full_name, v.name as village_name, v.travel_minutes, v.route_order
                FROM trip_items ti
                JOIN students s ON ti.student_id = s.id
                JOIN villages v ON ti.village_id = v.id
                WHERE ti.id = ?
            """, (item_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def update_trip_status(
        self,
        trip_id: int,
        status: str,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None
    ) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE trips
                SET status = ?,
                    started_at = coalesce(?, started_at),
                    finished_at = coalesce(?, finished_at)
                WHERE id = ?
            """, (status, started_at, finished_at, trip_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def update_trip_item_status(
        self,
        item_id: int,
        status: str,
        picked_at: Optional[str] = None,
        arrived_at: Optional[str] = None,
        accepted_at: Optional[str] = None,
        handed_at: Optional[str] = None,
        dropped_at: Optional[str] = None,
        actor_id: Optional[int] = None
    ) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE trip_items
                SET status = ?,
                    picked_at = coalesce(?, picked_at),
                    arrived_at = coalesce(?, arrived_at),
                    accepted_at = coalesce(?, accepted_at),
                    handed_at = coalesce(?, handed_at),
                    dropped_at = coalesce(?, dropped_at),
                    actor_id = coalesce(?, actor_id)
                WHERE id = ?
            """, (status, picked_at, arrived_at, accepted_at, handed_at, dropped_at, actor_id, item_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    async def get_student_parent_telegram_ids(self, student_id: int) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT p.id as parent_id, p.telegram_id, p.phone
                FROM student_parents sp
                JOIN parents p ON sp.parent_id = p.id
                WHERE sp.student_id = ?
            """, (student_id,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def log_notification(
        self,
        parent_id: Optional[int],
        student_id: int,
        text: str,
        status: str = "sent"
    ) -> int:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO notify_log (ts, parent_id, student_id, text, status)
                VALUES (?, ?, ?, ?, ?)
            """, (now_str, parent_id, student_id, text, status))
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()

    # --- 6-faza: Xodimlar boshqaruvi metodlari ---
    async def get_staff_by_tg_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT * FROM staff WHERE telegram_id = ? AND is_active = 1", (telegram_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def get_active_to_center_trips_for_staff(self, date: str) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT t.*, d.full_name as driver_name, d.phone as driver_phone
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.date = ? AND t.direction = 'to_center' AND t.status = 'at_center'
                ORDER BY t.planned_time ASC
            """, (date,))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, v.name as village_name, v.route_order
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY v.route_order ASC
                """, (t["id"],))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
            return trips
        finally:
            await conn.close()

    async def get_active_to_home_trips_for_staff(self, date: str) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT t.*, d.full_name as driver_name, d.phone as driver_phone
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.date = ? AND t.direction = 'to_home' AND t.status IN ('planned', 'approved', 'handed_over')
                ORDER BY t.planned_time ASC
            """, (date,))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, v.name as village_name, v.route_order
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY v.route_order DESC
                """, (t["id"],))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
            return trips
        finally:
            await conn.close()

    async def accept_student(self, item_id: int, staff_id: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT ti.*, t.status as trip_status, t.direction
                FROM trip_items ti
                JOIN trips t ON ti.trip_id = t.id
                WHERE ti.id = ?
            """, (item_id,))
            row = await cur.fetchone()
            if not row:
                return False, "item_not_found", None
            item = dict(row)

            if item["status"] == "accepted" or item["trip_status"] == "done":
                return False, "already_accepted", item

            if item["trip_status"] != "at_center":
                return False, "trip_not_at_center", item

            if item["status"] == "absent":
                return False, "student_absent", item

            await conn.execute("""
                UPDATE trip_items
                SET status = 'accepted', accepted_at = ?, actor_id = ?
                WHERE id = ?
            """, (now_str, staff_id, item_id))

            # Agar reysdagi barcha o'quvchilar qabul qilingan (yoki absent) bo'lsa reysni done qilish
            cur_remain = await conn.execute("""
                SELECT COUNT(*) FROM trip_items
                WHERE trip_id = ? AND status NOT IN ('accepted', 'absent')
            """, (item["trip_id"],))
            remaining = (await cur_remain.fetchone())[0]
            if remaining == 0:
                await conn.execute("""
                    UPDATE trips
                    SET status = 'done', finished_at = ?
                    WHERE id = ?
                """, (now_str, item["trip_id"]))

            await conn.commit()
            item["status"] = "accepted"
            item["accepted_at"] = now_str
            return True, "success", item
        finally:
            await conn.close()

    async def accept_all_trip_students(self, trip_id: int, staff_id: int) -> Tuple[bool, str, List[Dict[str, Any]]]:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur_t = await conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
            trip = await cur_t.fetchone()
            if not trip:
                return False, "trip_not_found", []
            if trip["status"] == "done":
                return False, "already_all_accepted", []
            if trip["status"] != "at_center":
                return False, "trip_not_at_center", []

            cur_items = await conn.execute("""
                SELECT * FROM trip_items
                WHERE trip_id = ? AND status NOT IN ('accepted', 'absent')
            """, (trip_id,))
            items_to_accept = [dict(r) for r in await cur_items.fetchall()]
            if not items_to_accept:
                return False, "already_all_accepted", []

            for it in items_to_accept:
                await conn.execute("""
                    UPDATE trip_items
                    SET status = 'accepted', accepted_at = ?, actor_id = ?
                    WHERE id = ?
                """, (now_str, staff_id, it["id"]))
                it["status"] = "accepted"
                it["accepted_at"] = now_str

            await conn.execute("""
                UPDATE trips
                SET status = 'done', finished_at = ?
                WHERE id = ?
            """, (now_str, trip_id))

            await conn.commit()
            return True, "success", items_to_accept
        finally:
            await conn.close()

    async def handover_student(self, item_id: int, staff_id: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT ti.*, t.status as trip_status, t.direction
                FROM trip_items ti
                JOIN trips t ON ti.trip_id = t.id
                WHERE ti.id = ?
            """, (item_id,))
            row = await cur.fetchone()
            if not row:
                return False, "item_not_found", None
            item = dict(row)

            if item["status"] == "handed_over":
                return False, "already_handed_over", item

            if item["status"] == "absent":
                return False, "student_absent", item

            await conn.execute("""
                UPDATE trip_items
                SET status = 'handed_over', handed_at = ?, actor_id = ?
                WHERE id = ?
            """, (now_str, staff_id, item_id))

            # Reys holatini 'handed_over' ga o'tkazish
            await conn.execute("""
                UPDATE trips
                SET status = 'handed_over'
                WHERE id = ?
            """, (item["trip_id"],))

            await conn.commit()
            item["status"] = "handed_over"
            item["handed_at"] = now_str
            return True, "success", item
        finally:
            await conn.close()

    async def handover_all_trip_students(self, trip_id: int, staff_id: int) -> Tuple[bool, str, List[Dict[str, Any]]]:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur_t = await conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
            trip = await cur_t.fetchone()
            if not trip:
                return False, "trip_not_found", []

            cur_items = await conn.execute("""
                SELECT * FROM trip_items
                WHERE trip_id = ? AND status NOT IN ('handed_over', 'absent')
            """, (trip_id,))
            items_to_handover = [dict(r) for r in await cur_items.fetchall()]
            if not items_to_handover:
                return False, "already_all_handed_over", []

            for it in items_to_handover:
                await conn.execute("""
                    UPDATE trip_items
                    SET status = 'handed_over', handed_at = ?, actor_id = ?
                    WHERE id = ?
                """, (now_str, staff_id, it["id"]))
                it["status"] = "handed_over"
                it["handed_at"] = now_str

            await conn.execute("""
                UPDATE trips
                SET status = 'handed_over'
                WHERE id = ?
            """, (trip_id,))

            await conn.commit()
            return True, "success", items_to_handover
        finally:
            await conn.close()

    # --- 7-faza: Scheduler, Eslatmalar va Tasdiqlash metodlari ---
    async def check_existing_active_trips(self, date_str: str) -> bool:
        """
        Qayta hisoblashdan himoya: Agar sanada 'on_way', 'at_center', 'handed_over' yoki 'done'
        holatidagi reyslar mavjud bo'lsa True qaytaradi.
        """
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT COUNT(*) FROM trips
                WHERE date = ? AND status IN ('on_way', 'at_center', 'handed_over', 'done')
            """, (date_str,))
            row = await cur.fetchone()
            return row[0] > 0
        finally:
            await conn.close()

    async def save_generated_trips(self, trips: List[Dict[str, Any]], date_str: str) -> bool:
        """
        Kunlik generatsiya qilingan reyslarni bazaga yozish.
        Faol/yakunlangan reyslar bo'lsa rad etiladi.
        """
        if await self.check_existing_active_trips(date_str):
            return False

        conn = await self.get_connection()
        try:
            # Ushbu sanadagi hali tasdiqlanmagan eski rejalarni tozalash
            cur_old = await conn.execute(
                "SELECT id FROM trips WHERE date = ? AND approved = 0",
                (date_str,)
            )
            old_ids = [r[0] for r in await cur_old.fetchall()]
            for o_id in old_ids:
                await conn.execute("DELETE FROM trip_items WHERE trip_id = ?", (o_id,))
                await conn.execute("DELETE FROM trips WHERE id = ?", (o_id,))

            for trip in trips:
                direction = trip.get("direction") or trip.get("type", "to_center")
                cur_t = await conn.execute("""
                    INSERT INTO trips (date, direction, driver_id, planned_time, status, approved, reminder_count)
                    VALUES (?, ?, ?, ?, 'planned', 0, 0)
                """, (date_str, direction, trip.get("driver_id"), trip["planned_time"]))
                trip_db_id = cur_t.lastrowid

                for item in trip.get("items", []):
                    await conn.execute("""
                        INSERT INTO trip_items (trip_id, student_id, village_id, status)
                        VALUES (?, ?, ?, 'pending')
                    """, (trip_db_id, item["student_id"], item["village_id"]))

            await conn.commit()
            return True
        finally:
            await conn.close()

    async def get_unapproved_trips(self, date_str: str) -> List[Dict[str, Any]]:
        """Tasdiqlanmagan (approved = 0) kunlik reyslarni olish."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT t.*, d.full_name as driver_name, d.phone as driver_phone, d.telegram_id as driver_tg_id
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.date = ? AND t.approved = 0
                ORDER BY t.planned_time ASC
            """, (date_str,))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, v.name as village_name, v.pickup_point, v.route_order
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY v.route_order ASC
                """, (t["id"],))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
            return trips
        finally:
            await conn.close()

    async def approve_all_daily_trips(self, date_str: str, admin_id: int) -> int:
        """Kunlik barcha tasdiqlanmagan reyslarni approved holatiga o'tkazish."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE trips
                SET approved = 1, status = 'approved'
                WHERE date = ? AND approved = 0
            """, (date_str,))
            count = cur.rowcount
            await conn.commit()

            if count > 0:
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await conn.execute("""
                    INSERT INTO event_log (ts, actor_role, actor_id, action)
                    VALUES (?, ?, ?, ?)
                """, (now_ts, "admin" if admin_id > 0 else "system", admin_id, "approve_trips" if admin_id > 0 else "auto_approve_trips"))
            await conn.commit()
            return count
        finally:
            await conn.close()

    async def get_trips_needing_reminder(self, now_dt: datetime) -> List[Dict[str, Any]]:
        """
        Bugungi 'approved' holatidagi va eslatma talab qiladigan reyslar:
        reminder_count < 3 bo'lgan reyslar ro'yxati.
        """
        date_str = now_dt.strftime("%Y-%m-%d")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT t.*, d.full_name as driver_name, d.phone as driver_phone, d.telegram_id as driver_tg_id
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.date = ? AND t.status = 'approved' AND t.reminder_count < 3
                ORDER BY t.planned_time ASC
            """, (date_str,))
            trips = [dict(r) for r in await cur.fetchall()]
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, v.name as village_name, v.pickup_point, v.travel_minutes, v.route_order
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    WHERE ti.trip_id = ?
                    ORDER BY v.route_order ASC
                """, (t["id"],))
                t["items"] = [dict(r) for r in await cur_items.fetchall()]
            return trips
        finally:
            await conn.close()

    async def update_trip_reminder(self, trip_id: int, reminder_count: int, last_reminder_at: str) -> bool:
        """Reysning eslatma hisoblagichi va oxirgi eslatma vaqtini yangilash."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                UPDATE trips
                SET reminder_count = ?, last_reminder_at = ?
                WHERE id = ?
            """, (reminder_count, last_reminder_at, trip_id))
            await conn.commit()
            return cur.rowcount > 0
        finally:
            await conn.close()

    # --- 8-faza: Kelmaydi (Absence) va Excel import metodlari ---
    async def match_village_by_name_or_school(self, village_query: str) -> Optional[int]:
        """
        Kiritilgan nom yoki maktab raqami bo'yicha mahallani aniqlash.
        Masalan: 'Arpaariq', '4-maktab', '4 maktab', '4'.
        """
        if not village_query:
            return None
        q = str(village_query).strip().lower()
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT id, name, school_name FROM villages")
            rows = [dict(r) for r in await cur.fetchall()]

            # 1. Aniq tenglik (nomi yoki maktab nomi)
            for r in rows:
                if r["name"].lower() == q or r["school_name"].lower() == q:
                    return r["id"]

            # 2. Maktab raqami bo'yicha solishtirish (masalan '4' -> '4-maktab')
            digits_q = "".join(ch for ch in q if ch.isdigit())
            if digits_q:
                for r in rows:
                    digits_school = "".join(ch for ch in r["school_name"] if ch.isdigit())
                    if digits_school and digits_q == digits_school:
                        return r["id"]

            # 3. Substring (qisman moslik)
            for r in rows:
                name_l = r["name"].lower()
                school_l = r["school_name"].lower()
                if q in name_l or name_l in q or q in school_l:
                    return r["id"]

            return None
        finally:
            await conn.close()

    async def mark_student_absent(
        self,
        student_id: int,
        date_str: str,
        role: str,
        actor_id: int
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        O'quvchini kelmaydi deb belgilash.
        Agar bola allaqachon mashinada (picked_up yoki keyin) bo'lsa xatolik qaytaradi.
        """
        conn = await self.get_connection()
        try:
            cur_st = await conn.execute("""
                SELECT s.id, s.full_name, v.name as village_name
                FROM students s
                JOIN villages v ON s.village_id = v.id
                WHERE s.id = ?
            """, (student_id,))
            st_row = await cur_st.fetchone()
            if not st_row:
                return False, "student_not_found", None
            student_info = dict(st_row)

            # Ushbu sanadagi reys elementlarini tekshirish
            cur_items = await conn.execute("""
                SELECT ti.id, ti.trip_id, ti.status as item_status,
                       t.direction, t.status as trip_status, t.driver_id,
                       d.telegram_id as driver_tg_id, d.full_name as driver_name
                FROM trip_items ti
                JOIN trips t ON ti.trip_id = t.id
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE ti.student_id = ? AND t.date = ?
            """, (student_id, date_str))
            trip_items = [dict(r) for r in await cur_items.fetchall()]

            # SATR: ALLAQACHON MASHINADA BO'LSA HIMOYA
            for it in trip_items:
                if it["item_status"] in ["picked_up", "arrived", "accepted", "dropped_off"]:
                    return False, "already_picked_up", None

            # Absences jadvalida bor-yo'qligini tekshirish
            cur_abs = await conn.execute(
                "SELECT id FROM absences WHERE student_id = ? AND date = ?",
                (student_id, date_str)
            )
            if await cur_abs.fetchone():
                return False, "already_marked", None

            await conn.execute("""
                INSERT INTO absences (student_id, date, reported_by_role, reported_by_id)
                VALUES (?, ?, ?, ?)
            """, (student_id, date_str, role, actor_id))

            # Kunlik trip_items larni absent qilish
            driver_info = None
            for it in trip_items:
                await conn.execute(
                    "UPDATE trip_items SET status = 'absent' WHERE id = ?",
                    (it["id"],)
                )
                if it.get("driver_tg_id"):
                    driver_info = {
                        "driver_tg_id": it["driver_tg_id"],
                        "driver_name": it["driver_name"],
                        "trip_id": it["trip_id"]
                    }

            # Audit log
            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute("""
                INSERT INTO event_log (ts, actor_role, actor_id, action, student_id)
                VALUES (?, ?, ?, 'mark_absent', ?)
            """, (now_ts, role, actor_id, student_id))

            await conn.commit()

            result_info = {
                "student_name": student_info["full_name"],
                "village_name": student_info["village_name"],
                "driver_tg_id": driver_info["driver_tg_id"] if driver_info else None,
                "driver_name": driver_info["driver_name"] if driver_info else None,
                "trip_id": driver_info["trip_id"] if driver_info else None
            }
            return True, "success", result_info
        finally:
            await conn.close()

    async def bulk_upsert_students_and_schedules(self, parsed_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Excel orqali o'quvchilar va dars jadvallarini ommaviy saqlash/yangilash."""
        created = 0
        updated = 0
        conn = await self.get_connection()
        try:
            for item in parsed_data:
                full_name = item["full_name"].strip()
                norm_p1 = normalize_phone(item.get("parent_phone_1"))
                norm_p2 = normalize_phone(item.get("parent_phone_2"))
                village_id = item["village_id"]

                cur_exist = await conn.execute(
                    "SELECT id FROM students WHERE full_name = ? AND parent_phone_1 = ?",
                    (full_name, norm_p1)
                )
                existing = await cur_exist.fetchone()

                if existing:
                    student_id = existing[0]
                    await conn.execute("""
                        UPDATE students
                        SET village_id = ?, parent_phone_2 = ?, is_active = 1
                        WHERE id = ?
                    """, (village_id, norm_p2, student_id))
                    updated += 1
                else:
                    cur_ins = await conn.execute("""
                        INSERT INTO students (full_name, village_id, parent_phone_1, parent_phone_2, is_active)
                        VALUES (?, ?, ?, ?, 1)
                    """, (full_name, village_id, norm_p1, norm_p2))
                    student_id = cur_ins.lastrowid
                    created += 1

                # Dars jadvallari (schedules) UPSERT
                schedules = item.get("schedules", [])
                for sch in schedules:
                    weekday = sch["weekday"]
                    st_time = sch["start_time"]
                    end_time = sch["end_time"]
                    await conn.execute("""
                        INSERT INTO schedules (student_id, weekday, start_time, end_time)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(student_id, weekday)
                        DO UPDATE SET start_time = excluded.start_time, end_time = excluded.end_time
                    """, (student_id, weekday, st_time, end_time))

            await conn.commit()
            return {"created": created, "updated": updated}
        finally:
            await conn.close()

    # --- 9-faza: Kechikish xabari va Broadcast metodlari ---
    async def apply_trip_delay(
        self,
        trip_id: int,
        delay_min: int,
        admin_id: int
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Reysni kechiktirish:
        1. trips.delay_min += delay_min, planned_time N daqiqaga suriladi.
        2. Ota-onalar va haydovchi ma'lumotlari to'planadi.
        3. event_log ga yoziladi.
        """
        conn = await self.get_connection()
        try:
            cur_t = await conn.execute("""
                SELECT t.*, d.telegram_id as driver_tg_id, d.full_name as driver_name
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.id = ?
            """, (trip_id,))
            trip_row = await cur_t.fetchone()
            if not trip_row:
                return False, "trip_not_found", None
            trip = dict(trip_row)

            # Yangi planned_time hisoblash
            from datetime import datetime, timedelta
            try:
                pt_dt = datetime.strptime(trip["planned_time"], "%H:%M")
                new_pt_dt = pt_dt + timedelta(minutes=delay_min)
                new_planned_time = new_pt_dt.strftime("%H:%M")
            except Exception:
                new_planned_time = trip["planned_time"]

            new_delay_min = (trip.get("delay_min") or 0) + delay_min

            await conn.execute("""
                UPDATE trips
                SET delay_min = ?, planned_time = ?
                WHERE id = ?
            """, (new_delay_min, new_planned_time, trip_id))

            # Reysdagi barcha o'quvchilar va ularning ota-onalarini olish
            cur_items = await conn.execute("""
                SELECT ti.id, ti.student_id, ti.status as item_status, s.full_name as student_name, v.name as village_name
                FROM trip_items ti
                JOIN students s ON ti.student_id = s.id
                JOIN villages v ON ti.village_id = v.id
                WHERE ti.trip_id = ?
            """, (trip_id,))
            items = [dict(r) for r in await cur_items.fetchall()]

            for it in items:
                cur_p = await conn.execute("""
                    SELECT p.id as parent_id, p.telegram_id
                    FROM student_parents sp
                    JOIN parents p ON sp.parent_id = p.id
                    WHERE sp.student_id = ?
                """, (it["student_id"],))
                it["parents"] = [dict(r) for r in await cur_p.fetchall()]
                it["is_picked_up"] = (it["item_status"] in ["picked_up", "arrived", "accepted"])

            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute("""
                INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id)
                VALUES (?, 'admin', ?, 'trip_delay', ?)
            """, (now_ts, admin_id, trip_id))

            await conn.commit()

            delay_data = {
                "trip_id": trip_id,
                "delay_min": delay_min,
                "new_planned_time": new_planned_time,
                "driver_tg_id": trip.get("driver_tg_id"),
                "driver_name": trip.get("driver_name"),
                "items": items
            }
            return True, "success", delay_data
        finally:
            await conn.close()

    async def get_broadcast_audience(
        self,
        audience_type: str,
        audience_value: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Auditoriya tanlash:
        - all: barcha tasdiqlangan ota-onalar
        - village: mahalla bo'yicha
        - zone: zona bo'yicha (A, B, C, D)
        - student: bitta o'quvchi ota-onalari
        - drivers: haydovchilar
        - staff: xodimlar
        """
        conn = await self.get_connection()
        try:
            if audience_type == "all":
                cur = await conn.execute("""
                    SELECT DISTINCT p.telegram_id, p.phone as contact_info, 'Ota-ona' as role
                    FROM parents p
                    JOIN student_parents sp ON p.id = sp.parent_id
                    WHERE p.telegram_id IS NOT NULL
                """)
            elif audience_type == "village":
                cur = await conn.execute("""
                    SELECT DISTINCT p.telegram_id, p.phone as contact_info, 'Ota-ona' as role
                    FROM parents p
                    JOIN student_parents sp ON p.id = sp.parent_id
                    JOIN students s ON sp.student_id = s.id
                    WHERE (s.village_id = ? OR s.village_id IN (SELECT id FROM villages WHERE name LIKE ?))
                      AND s.is_active = 1 AND p.telegram_id IS NOT NULL
                """, (audience_value, f"%{audience_value}%"))
            elif audience_type == "zone":
                # audience_value zona id (1, 2, 3, 4) yoki nomi (A, B, C, D)
                cur = await conn.execute("""
                    SELECT DISTINCT p.telegram_id, p.phone as contact_info, 'Ota-ona' as role
                    FROM parents p
                    JOIN student_parents sp ON p.id = sp.parent_id
                    JOIN students s ON sp.student_id = s.id
                    JOIN villages v ON s.village_id = v.id
                    JOIN zones z ON v.zone_id = z.id
                    WHERE (z.id = ? OR z.name LIKE ?)
                      AND s.is_active = 1 AND p.telegram_id IS NOT NULL
                """, (audience_value, f"%{audience_value}%"))
            elif audience_type == "student":
                # audience_value: student_id yoki qidiruv so'zi
                cur = await conn.execute("""
                    SELECT DISTINCT p.telegram_id, p.phone as contact_info, 'Ota-ona' as role
                    FROM parents p
                    JOIN student_parents sp ON p.id = sp.parent_id
                    JOIN students s ON sp.student_id = s.id
                    WHERE (s.id = ? OR s.full_name LIKE ?)
                      AND s.is_active = 1 AND p.telegram_id IS NOT NULL
                """, (audience_value, f"%{audience_value}%"))
            elif audience_type == "drivers":
                cur = await conn.execute("""
                    SELECT DISTINCT telegram_id, phone as contact_info, full_name, 'Haydovchi' as role
                    FROM drivers
                    WHERE is_active = 1 AND telegram_id IS NOT NULL
                """)
            elif audience_type == "staff":
                cur = await conn.execute("""
                    SELECT DISTINCT telegram_id, phone as contact_info, full_name, 'Xodim' as role
                    FROM staff
                    WHERE is_active = 1 AND telegram_id IS NOT NULL
                """)
            else:
                return []

            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def log_broadcast(
        self,
        admin_id: int,
        audience_type: str,
        audience_value: Optional[str],
        text: str,
        media: Optional[str],
        total: int,
        sent: int,
        failed: int
    ) -> int:
        from datetime import datetime
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                INSERT INTO broadcasts (ts, admin_id, audience_type, audience_value, text, media, total, sent, failed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now_ts, admin_id, audience_type, audience_value, text, media, total, sent, failed))
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()



