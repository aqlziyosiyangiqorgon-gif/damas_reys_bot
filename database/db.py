from utils.clock import now_tz
import os
import json
import aiosqlite
import calendar
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from models.enums import UserRole
from services.billing import LESSON_RATE
from utils.time_validation import validate_schedule_times

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
    Mahalliy 9 xonali raqamlarni +998 formatiga, xalqaro raqamlarni esa
    '+' va 7–15 ta raqam ko'rinishiga keltiradi.
    """
    if not phone:
        return None

    value = str(phone).strip()
    if value.startswith("+"):
        formatted_part = value[1:]
        if any(not (char.isdigit() or char.isspace() or char in "()-") for char in formatted_part):
            return None
        international_digits = "".join(char for char in formatted_part if char.isdigit())
        if 7 <= len(international_digits) <= 15:
            return f"+{international_digits}"
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
        try:
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA busy_timeout = 5000;")
        except BaseException:
            await conn.close()
            raise
        return conn

    async def init_db(self):
        conn = await self.get_connection()
        try:
            # WAL is a database-level setting; set it once at startup instead of
            # asking every short-lived connection to change/check journal mode.
            await conn.execute("PRAGMA journal_mode = WAL;")
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
                driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
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
                reason TEXT,
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
                driver_advance_reminded_at TEXT,
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

            CREATE TABLE IF NOT EXISTS fsm_storage (
                storage_key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS workflow_payloads (
                batch_id TEXT PRIMARY KEY,
                admin_tg_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Oylik to'lovlar va tarix: billing yozuvlari o'quvchi o'chirilsa ham saqlanadi.
            CREATE TABLE IF NOT EXISTS billing_plans (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                days_per_week INTEGER NOT NULL CHECK (days_per_week BETWEEN 1 AND 7),
                monthly_amount INTEGER NOT NULL CHECK (monthly_amount >= 0),
                effective_from TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                period TEXT NOT NULL CHECK (length(period) = 7),
                amount INTEGER NOT NULL CHECK (amount >= 0),
                discount INTEGER NOT NULL DEFAULT 0 CHECK (discount >= 0),
                status TEXT NOT NULL CHECK (status IN ('unpaid', 'paid', 'waived')),
                billing_mode TEXT NOT NULL DEFAULT 'manual' CHECK (billing_mode IN ('manual', 'attendance')),
                created_at TEXT NOT NULL,
                UNIQUE (student_id, period)
            );

            CREATE TABLE IF NOT EXISTS invoice_lesson_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                lesson_date TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                created_at TEXT NOT NULL,
                UNIQUE (student_id, lesson_date)
            );

            CREATE INDEX IF NOT EXISTS idx_invoice_lesson_days_invoice_id
            ON invoice_lesson_days(invoice_id);

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
                amount INTEGER NOT NULL CHECK (amount > 0),
                method TEXT NOT NULL CHECK (method IN ('cash', 'card')),
                recorded_by INTEGER NOT NULL,
                paid_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS discount_log (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
                amount INTEGER NOT NULL CHECK (amount > 0),
                reason TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS billing_reminder_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES parents(id) ON DELETE RESTRICT,
                period TEXT NOT NULL CHECK (length(period) = 7),
                reminder_day INTEGER NOT NULL CHECK (reminder_day BETWEEN 1 AND 31),
                attempted_at TEXT NOT NULL,
                status TEXT NOT NULL,
                UNIQUE (parent_id, period, reminder_day)
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

            # Migratsiya: absences jadvalida reason ustunini tekshirish
            cur_col = await conn.execute("PRAGMA table_info(absences)")
            columns = [row[1] for row in await cur_col.fetchall()]
            if "reason" not in columns:
                await conn.execute("ALTER TABLE absences ADD COLUMN reason TEXT")

            # Existing DBs keep working; old students simply inherit their village driver.
            cur_col = await conn.execute("PRAGMA table_info(students)")
            student_columns = [row[1] for row in await cur_col.fetchall()]
            if "driver_id" not in student_columns:
                await conn.execute(
                    "ALTER TABLE students ADD COLUMN driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL"
                )

            cur_col = await conn.execute("PRAGMA table_info(invoices)")
            invoice_columns = [row[1] for row in await cur_col.fetchall()]
            if "billing_mode" not in invoice_columns:
                await conn.execute(
                    "ALTER TABLE invoices ADD COLUMN billing_mode TEXT NOT NULL DEFAULT 'manual'"
                )

            cur_col = await conn.execute("PRAGMA table_info(trips)")
            trip_columns = [row[1] for row in await cur_col.fetchall()]
            if "driver_advance_reminded_at" not in trip_columns:
                await conn.execute(
                    "ALTER TABLE trips ADD COLUMN driver_advance_reminded_at TEXT"
                )

            await conn.commit()
        finally:
            await conn.close()

    @staticmethod
    def _billing_period_dates(period: str) -> Tuple[date, date]:
        try:
            first_day = datetime.strptime(period, "%Y-%m").date().replace(day=1)
        except ValueError as exc:
            raise ValueError("period must use YYYY-MM format") from exc
        if first_day.strftime("%Y-%m") != period:
            raise ValueError("period must use YYYY-MM format")
        last_day = first_day.replace(day=calendar.monthrange(first_day.year, first_day.month)[1])
        return first_day, last_day

    async def _ensure_attendance_invoice(
        self,
        conn: aiosqlite.Connection,
        student_id: int,
        period: str,
        effective_from: date,
        weekdays: set[int],
    ) -> bool:
        """Create a scheduled-lesson invoice, charging 5,000 so'm per planned class."""
        if not weekdays:
            return False
        period_start, period_end = self._billing_period_dates(period)
        if effective_from > period_end:
            return False

        cur = await conn.execute(
            "SELECT id, billing_mode FROM invoices WHERE student_id = ? AND period = ?",
            (student_id, period),
        )
        existing_invoice = await cur.fetchone()
        if existing_invoice and existing_invoice["billing_mode"] != "attendance":
            # Old/manual invoices are preserved without layering a second charge on them.
            return False

        if existing_invoice:
            invoice_id = int(existing_invoice["id"])
        else:
            cur = await conn.execute(
                "INSERT INTO invoices (student_id, period, amount, status, billing_mode, created_at) "
                "VALUES (?, ?, 0, 'paid', 'attendance', ?)",
                (student_id, period, now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            invoice_id = int(cur.lastrowid)

        charge_from = max(period_start, effective_from)
        max_lesson_days = len(weekdays) * 4
        cur = await conn.execute(
            "SELECT lesson_date FROM invoice_lesson_days WHERE invoice_id = ?", (invoice_id,)
        )
        billed_dates = {row[0] for row in await cur.fetchall()}
        existing_lesson_count = len(billed_dates)
        remaining_capacity = max(0, max_lesson_days - existing_lesson_count)

        lesson_dates: List[date] = []
        current_day = charge_from
        while current_day <= period_end and len(lesson_dates) < remaining_capacity:
            if current_day.weekday() in weekdays and current_day.isoformat() not in billed_dates:
                lesson_dates.append(current_day)
            current_day += timedelta(days=1)

        created_at = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        for lesson_date in lesson_dates:
            await conn.execute(
                "INSERT OR IGNORE INTO invoice_lesson_days "
                "(invoice_id, student_id, lesson_date, amount, created_at) VALUES (?, ?, ?, ?, ?)",
                (invoice_id, student_id, lesson_date.isoformat(), LESSON_RATE, created_at),
            )

        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM invoice_lesson_days WHERE invoice_id = ?",
            (invoice_id,),
        )
        amount = int((await cur.fetchone())[0])
        cur = await conn.execute(
            "SELECT discount FROM invoices WHERE id = ?", (invoice_id,)
        )
        discount = int((await cur.fetchone())[0])
        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?", (invoice_id,)
        )
        paid_total = int((await cur.fetchone())[0])
        status = "paid" if paid_total >= amount - discount else "unpaid"
        await conn.execute(
            "UPDATE invoices SET amount = ?, status = ? WHERE id = ?",
            (amount, status, invoice_id),
        )
        return not bool(existing_invoice)

    async def _ensure_student_billing(
        self,
        conn: aiosqlite.Connection,
        student_id: int,
        effective_from: date,
        created_by: int = 0,
    ) -> bool:
        cur = await conn.execute(
            "SELECT weekday FROM schedules WHERE student_id = ? ORDER BY weekday",
            (student_id,),
        )
        weekdays = {int(row[0]) for row in await cur.fetchall()}
        if not weekdays:
            return False

        cur = await conn.execute(
            "SELECT id, effective_from FROM billing_plans WHERE student_id = ? "
            "AND effective_from <= ? ORDER BY effective_from DESC, id DESC LIMIT 1",
            (student_id, effective_from.isoformat()),
        )
        plan = await cur.fetchone()
        days_per_week = len(weekdays)
        monthly_amount = days_per_week * 4 * LESSON_RATE
        if plan is None:
            await conn.execute(
                "INSERT INTO billing_plans "
                "(student_id, days_per_week, monthly_amount, effective_from, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (student_id, days_per_week, monthly_amount, effective_from.isoformat(), created_by,
                 now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            plan_start = effective_from
        else:
            plan_start = date.fromisoformat(plan["effective_from"])

        period = effective_from.strftime("%Y-%m")
        return await self._ensure_attendance_invoice(conn, student_id, period, plan_start, weekdays)

    async def ensure_monthly_billing(self, period: Optional[str] = None) -> Dict[str, int]:
        """Ensure active students have the current period's automatic lesson invoice."""
        today = now_tz().date()
        period = period or today.strftime("%Y-%m")
        period_start, period_end = self._billing_period_dates(period)
        created = 0
        skipped = 0
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT s.id FROM students s WHERE s.is_active = 1 ORDER BY s.id"
            )
            for row in await cur.fetchall():
                student_id = int(row[0])
                cur_schedule = await conn.execute(
                    "SELECT weekday FROM schedules WHERE student_id = ? ORDER BY weekday",
                    (student_id,),
                )
                weekdays = {int(item[0]) for item in await cur_schedule.fetchall()}
                if not weekdays:
                    skipped += 1
                    continue

                cur_plan = await conn.execute(
                    "SELECT effective_from FROM billing_plans WHERE student_id = ? "
                    "AND effective_from <= ? ORDER BY effective_from DESC, id DESC LIMIT 1",
                    (student_id, period_end.isoformat()),
                )
                plan = await cur_plan.fetchone()
                if plan is None:
                    # Older records have no enrollment timestamp; start fairly from today.
                    effective_from = max(today, period_start)
                    days_per_week = len(weekdays)
                    await conn.execute(
                        "INSERT INTO billing_plans "
                        "(student_id, days_per_week, monthly_amount, effective_from, created_by, created_at) "
                        "VALUES (?, ?, ?, ?, 0, ?)",
                        (student_id, days_per_week, days_per_week * 4 * LESSON_RATE,
                         effective_from.isoformat(), now_tz().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                else:
                    effective_from = date.fromisoformat(plan["effective_from"])

                if await self._ensure_attendance_invoice(
                    conn, student_id, period, effective_from, weekdays
                ):
                    created += 1
            await conn.commit()
            return {"created": created, "skipped": skipped}
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def save_workflow_payload(self, batch_id: str, admin_tg_id: int, kind: str, payload: Dict[str, Any]) -> None:
        conn = await self.get_connection()
        try:
            await conn.execute(
                "DELETE FROM workflow_payloads WHERE created_at < ?",
                ((now_tz() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO workflow_payloads (batch_id, admin_tg_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (batch_id, admin_tg_id, kind, json.dumps(payload, ensure_ascii=False),
                 now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def take_workflow_payload(self, batch_id: str, admin_tg_id: int, kind: str) -> Optional[Dict[str, Any]]:
        """Atomically consume a persisted admin confirmation payload once."""
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT payload FROM workflow_payloads WHERE batch_id = ? AND admin_tg_id = ? AND kind = ?",
                (batch_id, admin_tg_id, kind),
            )
            row = await cur.fetchone()
            if not row:
                await conn.rollback()
                return None
            await conn.execute("DELETE FROM workflow_payloads WHERE batch_id = ?", (batch_id,))
            await conn.commit()
            return json.loads(row["payload"])
        finally:
            await conn.close()

    async def delete_workflow_payload(self, batch_id: str, admin_tg_id: int) -> None:
        conn = await self.get_connection()
        try:
            await conn.execute(
                "DELETE FROM workflow_payloads WHERE batch_id = ? AND admin_tg_id = ?",
                (batch_id, admin_tg_id),
            )
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

    async def add_student_with_schedules(
        self,
        student_fields: Dict[str, Any],
        schedules: List[Dict[str, Any]],
        created_by: int = 0,
    ) -> int:
        """Insert a student and all schedule rows atomically."""
        full_name = student_fields["full_name"]
        village_id = student_fields["village_id"]
        phone_1 = normalize_phone(student_fields["parent_phone_1"])
        phone_2 = normalize_phone(student_fields.get("parent_phone_2"))
        if not phone_1:
            raise ValueError("Asosiy ota-ona telefon raqami noto'g'ri")

        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("""
                INSERT INTO students (full_name, village_id, parent_phone_1, parent_phone_2, is_active)
                VALUES (?, ?, ?, ?, 1)
            """, (full_name, village_id, phone_1, phone_2))
            student_id = cur.lastrowid

            for schedule in schedules:
                weekday = schedule["weekday"]
                start_time = schedule["start_time"]
                end_time = schedule["end_time"]
                validate_schedule_times(start_time, end_time)
                await conn.execute("""
                    INSERT INTO schedules (student_id, weekday, start_time, end_time)
                    VALUES (?, ?, ?, ?)
                """, (student_id, weekday, start_time, end_time))

            await self._ensure_student_billing(conn, student_id, now_tz().date(), created_by=created_by)

            await conn.commit()
            return student_id
        except BaseException:
            await conn.rollback()
            raise
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
            registered_at = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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

    async def get_admin_students(self) -> List[Dict[str, Any]]:
        """Return active students with their schedules and effective driver for admin panels."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.id, s.full_name, s.parent_phone_1, s.parent_phone_2, s.village_id,
                       s.driver_id AS explicit_driver_id,
                       COALESCE(explicit_driver.id, village_driver.id) AS assigned_driver_id,
                       COALESCE(explicit_driver.full_name, village_driver.full_name) AS assigned_driver_name,
                       v.name AS village_name, z.name AS zone_name,
                       sch.weekday, sch.start_time, sch.end_time
                FROM students s
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                LEFT JOIN drivers explicit_driver ON s.driver_id = explicit_driver.id AND explicit_driver.is_active = 1
                LEFT JOIN drivers village_driver ON v.default_driver_id = village_driver.id AND village_driver.is_active = 1
                LEFT JOIN schedules sch ON sch.student_id = s.id
                WHERE s.is_active = 1
                ORDER BY s.full_name COLLATE NOCASE, sch.weekday
            """)
            grouped: Dict[int, Dict[str, Any]] = {}
            for row in await cur.fetchall():
                item = dict(row)
                student_id = item["id"]
                if student_id not in grouped:
                    grouped[student_id] = {
                        "id": student_id, "full_name": item["full_name"],
                        "parent_phone_1": item["parent_phone_1"], "parent_phone_2": item["parent_phone_2"],
                        "village_id": item["village_id"], "village_name": item["village_name"],
                        "zone_name": item["zone_name"], "explicit_driver_id": item["explicit_driver_id"],
                        "assigned_driver_id": item["assigned_driver_id"],
                        "assigned_driver_name": item["assigned_driver_name"], "schedules": [],
                    }
                if item["weekday"] is not None:
                    grouped[student_id]["schedules"].append({
                        "weekday": item["weekday"], "start_time": item["start_time"], "end_time": item["end_time"],
                    })
            return list(grouped.values())
        finally:
            await conn.close()

    async def get_student_by_id(self, student_id: int) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT s.*, v.name as village_name, v.school_name, v.zone_id, z.name as zone_name,
                       COALESCE(explicit_driver.id, village_driver.id) AS assigned_driver_id,
                       COALESCE(explicit_driver.full_name, village_driver.full_name) AS assigned_driver_name
                FROM students s
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                LEFT JOIN drivers explicit_driver ON s.driver_id = explicit_driver.id AND explicit_driver.is_active = 1
                LEFT JOIN drivers village_driver ON v.default_driver_id = village_driver.id AND village_driver.is_active = 1
                WHERE s.id = ?
            """, (student_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def set_student_driver(self, student_id: int, driver_id: Optional[int]) -> bool:
        """Set an explicit student driver; NULL restores village/automatic assignment."""
        conn = await self.get_connection()
        try:
            if driver_id is not None:
                cur = await conn.execute("SELECT id FROM drivers WHERE id = ? AND is_active = 1", (driver_id,))
                if not await cur.fetchone():
                    return False
            cur = await conn.execute(
                "UPDATE students SET driver_id = ? WHERE id = ? AND is_active = 1",
                (driver_id, student_id),
            )
            await conn.commit()
            return cur.rowcount > 0
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
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("UPDATE students SET is_active = 0 WHERE id = ?", (student_id,))
            if cur.rowcount == 0:
                await conn.rollback()
                return False
            today = now_tz().strftime("%Y-%m-%d")
            await conn.execute(
                "DELETE FROM trip_items WHERE student_id=? AND trip_id IN "
                "(SELECT id FROM trips WHERE date=? AND status='planned')",
                (student_id, today),
            )
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def add_schedule(self, student_id: int, weekday: int, start_time: str, end_time: str) -> int:
        validate_schedule_times(start_time, end_time)
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
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
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("""
                SELECT * FROM schedules WHERE student_id = ? ORDER BY weekday ASC
            """, (student_id,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def update_student_schedule_time(self, student_id: int, weekday: int, start_time: str, end_time: str) -> bool:
        validate_schedule_times(start_time, end_time)
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

    async def delete_student_schedule(self, student_id: int, weekday: int) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                "DELETE FROM schedules WHERE student_id = ? AND weekday = ?",
                (student_id, weekday),
            )
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
                       v.school_name,
                       COALESCE(explicit_driver.id, village_driver.id) AS default_driver_id,
                       z.name as zone_name,
                       sch.weekday, sch.start_time, sch.end_time
                FROM schedules sch
                JOIN students s ON sch.student_id = s.id
                JOIN villages v ON s.village_id = v.id
                JOIN zones z ON v.zone_id = z.id
                LEFT JOIN drivers explicit_driver ON s.driver_id = explicit_driver.id AND explicit_driver.is_active = 1
                LEFT JOIN drivers village_driver ON v.default_driver_id = village_driver.id AND village_driver.is_active = 1
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

    async def get_trips_by_date(self, date: str) -> List[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT t.*, d.full_name as driver_name, d.phone as driver_phone
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE t.date = ?
                ORDER BY t.planned_time ASC
            """, (date,))
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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
            day_of_week = datetime.strptime(date, "%Y-%m-%d").weekday()
            for t in trips:
                cur_items = await conn.execute("""
                    SELECT ti.*, s.full_name, s.parent_phone_1 as parent_phone,
                           v.name as village_name, v.travel_minutes, v.route_order, v.pickup_point,
                           sch.start_time AS lesson_start_time, sch.end_time AS lesson_end_time
                    FROM trip_items ti
                    JOIN students s ON ti.student_id = s.id
                    JOIN villages v ON ti.village_id = v.id
                    LEFT JOIN schedules sch ON sch.student_id = s.id AND sch.weekday = ?
                    WHERE ti.trip_id = ?
                    ORDER BY CASE WHEN ? = 'to_center' THEN v.route_order ELSE -v.route_order END ASC
                """, (day_of_week, t["id"], t["direction"]))
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

    async def assign_driver_to_trip(self, trip_id: int, driver_id: int) -> bool:
        """Assign an active driver to a trip that has not started yet."""
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("SELECT id FROM drivers WHERE id = ? AND is_active = 1", (driver_id,))
            if not await cur.fetchone():
                await conn.rollback()
                return False
            cur = await conn.execute(
                "UPDATE trips SET driver_id = ? WHERE id = ? AND status IN ('planned', 'approved')",
                (driver_id, trip_id),
            )
            if cur.rowcount != 1:
                await conn.rollback()
                return False
            await conn.commit()
            return True
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

    async def claim_item(
        self,
        item_id: int,
        trip_id: int,
        from_statuses: List[str],
        to_status: str,
        ts_col: str,
        ts: str,
        actor_id: int,
        *,
        trip_statuses: Optional[List[str]] = None,
        direction: Optional[str] = None,
        actor_tg_id: Optional[int] = None,
        event_action: Optional[str] = None
    ) -> bool:
        """K5: bola holati faqat kutilgan oldingi holatdan o'zgaradi (double-tap va eskirgan tugma himoyasi)."""
        if ts_col not in ("picked_at", "arrived_at", "accepted_at", "handed_at", "dropped_at"):
            raise ValueError(f"Noto'g'ri ustun: {ts_col}")
        if not from_statuses:
            return False
        placeholders = ",".join("?" * len(from_statuses))
        trip_conditions = ["t.id = ti.trip_id", "t.id = ?"]
        params: List[Any] = [to_status, ts, actor_id, item_id, trip_id, *from_statuses, trip_id]
        if trip_statuses is not None:
            if not trip_statuses:
                return False
            trip_conditions.append(f"t.status IN ({','.join('?' * len(trip_statuses))})")
            params.extend(trip_statuses)
        if direction is not None:
            trip_conditions.append("t.direction = ?")
            params.append(direction)
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                f"UPDATE trip_items AS ti SET status = ?, {ts_col} = ?, actor_id = ? "
                f"WHERE ti.id = ? AND ti.trip_id = ? AND ti.status IN ({placeholders}) "
                f"AND EXISTS (SELECT 1 FROM trips t WHERE {' AND '.join(trip_conditions)})",
                params
            )
            if cur.rowcount == 1 and event_action:
                await conn.execute(
                    "INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id, student_id) "
                    "SELECT ?, 'driver', ?, ?, ti.trip_id, ti.student_id FROM trip_items ti WHERE ti.id = ?",
                    (ts, actor_tg_id if actor_tg_id is not None else actor_id, event_action, item_id)
                )
            await conn.commit()
            return cur.rowcount == 1
        finally:
            await conn.close()

    async def transition_trip(
        self,
        trip_id: int,
        from_statuses: List[str],
        to_status: str,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        *,
        direction: Optional[str] = None,
        actor_tg_id: Optional[int] = None,
        event_action: Optional[str] = None,
        require_all_items_complete: bool = False
    ) -> bool:
        """K5: reys holati faqat ruxsat etilgan oldingi holatdan o'zgaradi."""
        if not from_statuses:
            return False
        placeholders = ",".join("?" * len(from_statuses))
        conditions = [f"id = ?", f"status IN ({placeholders})"]
        params: List[Any] = [to_status, started_at, finished_at, trip_id, *from_statuses]
        if direction is not None:
            conditions.append("direction = ?")
            params.append(direction)
        if require_all_items_complete:
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM trip_items ti WHERE ti.trip_id = trips.id "
                "AND ti.status NOT IN ('dropped_off', 'absent'))"
            )
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                f"UPDATE trips SET status = ?, "
                f"started_at = coalesce(?, started_at), "
                f"finished_at = coalesce(?, finished_at) "
                f"WHERE {' AND '.join(conditions)}",
                params
            )
            if cur.rowcount == 1 and event_action:
                await conn.execute(
                    "INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id) "
                    "VALUES (?, 'driver', ?, ?, ?)",
                    (started_at or finished_at or now_tz().strftime("%Y-%m-%d %H:%M:%S"),
                     actor_tg_id if actor_tg_id is not None else 0, event_action, trip_id)
                )
            await conn.commit()
            return cur.rowcount == 1
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

    async def pickup_village_students(
        self, trip_id: int, village_id: int, driver_id: int
    ) -> List[Dict[str, Any]]:
        """
        Bir mahalladagi barcha kutayotgan (pending) bolalarni birdaniga mashinaga olish.
        """
        from datetime import datetime
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            # Haydovchini aniqlash (id yoki telegram_id)
            cur_driver = await conn.execute(
                "SELECT id, telegram_id FROM drivers WHERE id = ? OR telegram_id = ?",
                (driver_id, driver_id)
            )
            d_row = await cur_driver.fetchone()
            if not d_row:
                return []
            real_driver_id = d_row["id"]
            actor_tg_id = d_row["telegram_id"] or real_driver_id

            # Reysni tekshirish
            cur_trip = await conn.execute("SELECT id, driver_id, status, direction FROM trips WHERE id = ?", (trip_id,))
            trip_row = await cur_trip.fetchone()
            if (not trip_row or trip_row["driver_id"] != real_driver_id
                    or trip_row["status"] != "on_way" or trip_row["direction"] != "to_center"):
                return []

            # Shu mahalladagi barcha pending bolalar
            cur = await conn.execute("""
                SELECT ti.id, ti.student_id, ti.trip_id, ti.village_id, ti.status,
                       s.full_name, s.parent_phone_1 as parent_phone,
                       v.name as village_name, v.travel_minutes
                FROM trip_items ti
                JOIN students s ON ti.student_id = s.id
                JOIN villages v ON ti.village_id = v.id
                WHERE ti.trip_id = ? AND ti.village_id = ? AND ti.status = 'pending'
            """, (trip_id, village_id))
            items = [dict(r) for r in await cur.fetchall()]
            if not items:
                return []

            for it in items:
                await conn.execute("""
                    UPDATE trip_items
                    SET status = 'picked_up', picked_at = ?, actor_id = ?
                    WHERE id = ?
                """, (now_str, real_driver_id, it["id"]))
                await conn.execute("""
                    INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id, student_id)
                    VALUES (?, 'driver', ?, 'pickup_student', ?, ?)
                """, (now_str, actor_tg_id, trip_id, it["student_id"]))

            await conn.commit()
            return items
        finally:
            await conn.close()

    async def dropoff_village_students(
        self, trip_id: int, village_id: int, driver_id: int
    ) -> List[Dict[str, Any]]:
        """
        Bir mahalladagi barcha yetkazilmagan bolalarni birdaniga uyiga yetkazish.
        """
        from datetime import datetime
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            # Haydovchini aniqlash (id yoki telegram_id)
            cur_driver = await conn.execute(
                "SELECT id, telegram_id FROM drivers WHERE id = ? OR telegram_id = ?",
                (driver_id, driver_id)
            )
            d_row = await cur_driver.fetchone()
            if not d_row:
                return []
            real_driver_id = d_row["id"]
            actor_tg_id = d_row["telegram_id"] or real_driver_id

            # Reysni tekshirish
            cur_trip = await conn.execute("SELECT id, driver_id, status, direction FROM trips WHERE id = ?", (trip_id,))
            trip_row = await cur_trip.fetchone()
            if (not trip_row or trip_row["driver_id"] != real_driver_id
                    or trip_row["status"] != "on_way" or trip_row["direction"] != "to_home"):
                return []

            # Shu mahalladagi barcha hali yetkazilmagan va kelmaydi bo'lmagan bolalar
            cur = await conn.execute("""
                SELECT ti.id, ti.student_id, ti.trip_id, ti.village_id, ti.status,
                       s.full_name, s.parent_phone_1 as parent_phone,
                       v.name as village_name, v.travel_minutes
                FROM trip_items ti
                JOIN students s ON ti.student_id = s.id
                JOIN villages v ON ti.village_id = v.id
                WHERE ti.trip_id = ? AND ti.village_id = ?
                  AND ti.status IN ('pending', 'handed_over', 'arrived', 'accepted')
            """, (trip_id, village_id))
            items = [dict(r) for r in await cur.fetchall()]
            if not items:
                return []

            for it in items:
                await conn.execute("""
                    UPDATE trip_items
                    SET status = 'dropped_off', dropped_at = ?, actor_id = ?
                    WHERE id = ?
                """, (now_str, real_driver_id, it["id"]))
                await conn.execute("""
                    INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id, student_id)
                    VALUES (?, 'driver', ?, 'dropoff_student', ?, ?)
                """, (now_str, actor_tg_id, trip_id, it["student_id"]))

            await conn.commit()
            return items
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
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
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
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

            if item["direction"] != "to_home":
                return False, "wrong_direction", item

            if item["status"] in ("picked_up", "dropped_off"):
                return False, "invalid_state", item

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
                WHERE id = ? AND status IN ('planned', 'approved', 'handed_over')
            """, (item["trip_id"],))

            await conn.commit()
            item["status"] = "handed_over"
            item["handed_at"] = now_str
            return True, "success", item
        finally:
            await conn.close()

    async def handover_all_trip_students(self, trip_id: int, staff_id: int) -> Tuple[bool, str, List[Dict[str, Any]]]:
        from datetime import datetime
        now_str = now_tz().strftime("%Y-%m-%d %H:%M:%S")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur_t = await conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
            trip = await cur_t.fetchone()
            if not trip:
                return False, "trip_not_found", []

            if trip["direction"] != "to_home":
                return False, "wrong_direction", []

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
                WHERE id = ? AND status IN ('planned', 'approved', 'handed_over')
            """, (trip_id,))

            await conn.commit()
            return True, "success", items_to_handover
        finally:
            await conn.close()

    # --- 7-faza: Scheduler, Eslatmalar va Tasdiqlash metodlari ---
    async def check_existing_active_trips(self, date_str: str) -> bool:
        """
        Qayta hisoblashdan himoya: tasdiqlangan, boshlangan yoki yakunlangan reys bo'lsa True.
        """
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT COUNT(*) FROM trips
                WHERE date = ? AND (approved = 1 OR status IN ('on_way', 'at_center', 'handed_over', 'done'))
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
        conn = await self.get_connection()
        try:
            # Serialize plan replacement so concurrent invocations cannot interleave writes.
            await conn.execute("BEGIN IMMEDIATE")
            cur_active = await conn.execute(
                "SELECT 1 FROM trips WHERE date = ? AND (approved = 1 OR status IN ('on_way', 'at_center', 'handed_over', 'done')) LIMIT 1",
                (date_str,),
            )
            if await cur_active.fetchone():
                await conn.rollback()
                return False

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
        except aiosqlite.IntegrityError:
            await conn.rollback()
            return False
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
                WHERE t.date = ? AND t.approved = 0 AND t.status = 'planned'
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
                WHERE date = ? AND approved = 0 AND status = 'planned'
            """, (date_str,))
            count = cur.rowcount
            await conn.commit()

            if count > 0:
                now_ts = now_tz().strftime("%Y-%m-%d %H:%M:%S")
                await conn.execute("""
                    INSERT INTO event_log (ts, actor_role, actor_id, action)
                    VALUES (?, ?, ?, ?)
                """, (now_ts, "admin" if admin_id > 0 else "system", admin_id, "approve_trips" if admin_id > 0 else "auto_approve_trips"))
            await conn.commit()
            return count
        finally:
            await conn.close()

    async def approve_trip(self, trip_id: int, admin_id: int) -> bool:
        """Approve one planned trip without changing other trips for that date."""
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "UPDATE trips SET approved=1, status='approved' "
                "WHERE id=? AND approved=0 AND status='planned'",
                (trip_id,),
            )
            if cur.rowcount != 1:
                await conn.rollback()
                return False
            await conn.execute(
                "INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id) "
                "VALUES (?, 'admin', ?, 'approve_trip', ?)",
                (now_tz().strftime("%Y-%m-%d %H:%M:%S"), admin_id, trip_id),
            )
            await conn.commit()
            return True
        finally:
            await conn.close()

    async def cancel_trip(self, trip_id: int, admin_id: int) -> bool:
        """Cancel a trip unless a driver is already on the road or it is done."""
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "UPDATE trips SET status='cancelled', approved=0 "
                "WHERE id=? AND status NOT IN ('on_way', 'done', 'cancelled')",
                (trip_id,),
            )
            if cur.rowcount != 1:
                await conn.rollback()
                return False
            await conn.execute(
                "INSERT INTO event_log (ts, actor_role, actor_id, action, trip_id) "
                "VALUES (?, 'admin', ?, 'cancel_trip', ?)",
                (now_tz().strftime("%Y-%m-%d %H:%M:%S"), admin_id, trip_id),
            )
            await conn.commit()
            return True
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
                       , (SELECT group_concat(p.telegram_id)
                          FROM student_parents sp
                          JOIN parents p ON p.id = sp.parent_id
                          WHERE sp.student_id = ti.student_id AND p.telegram_id IS NOT NULL
                       ) as parent_telegram_ids
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

    async def get_driver_advance_reminder_trips(self, now_dt: datetime) -> List[Dict[str, Any]]:
        """Return approved morning trips whose earliest lesson starts in <=50 minutes."""
        date_str = now_dt.strftime("%Y-%m-%d")
        weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                SELECT t.*, d.full_name AS driver_name, d.telegram_id AS driver_tg_id
                FROM trips t
                JOIN drivers d ON d.id = t.driver_id AND d.is_active = 1
                WHERE t.date = ? AND t.direction = 'to_center' AND t.status = 'approved'
                  AND t.driver_advance_reminded_at IS NULL
                ORDER BY t.planned_time ASC, t.id ASC
                """,
                (date_str,),
            )
            trips = [dict(row) for row in await cur.fetchall()]
            due: List[Dict[str, Any]] = []
            for trip in trips:
                cur_items = await conn.execute(
                    """
                    SELECT ti.*, s.full_name, sch.start_time AS lesson_start_time,
                           sch.end_time AS lesson_end_time, v.name AS village_name,
                           v.travel_minutes, v.route_order, v.pickup_point
                    FROM trip_items ti
                    JOIN students s ON s.id = ti.student_id
                    JOIN villages v ON v.id = ti.village_id
                    JOIN schedules sch ON sch.student_id = s.id AND sch.weekday = ?
                    WHERE ti.trip_id = ? AND ti.status != 'absent'
                    ORDER BY v.route_order ASC, ti.id ASC
                    """,
                    (weekday, trip["id"]),
                )
                items = [dict(row) for row in await cur_items.fetchall()]
                starts = [item["lesson_start_time"] for item in items if item.get("lesson_start_time")]
                if not starts:
                    continue
                earliest_start = min(starts)
                try:
                    lesson_dt = datetime.strptime(f"{date_str} {earliest_start}", "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                reminder_dt = lesson_dt - timedelta(minutes=50)
                # If the scheduler was briefly unavailable, send the reminder late
                # while the lesson is still ahead; never send an advance alert after class.
                if reminder_dt <= now_dt < lesson_dt:
                    trip["items"] = items
                    trip["first_lesson_time"] = earliest_start
                    due.append(trip)
            return due
        finally:
            await conn.close()

    async def mark_driver_advance_reminder_sent(self, trip_id: int, sent_at: str) -> bool:
        """Mark a route reminder after Telegram confirms the PDF was delivered."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                UPDATE trips SET driver_advance_reminded_at = ?
                WHERE id = ? AND driver_advance_reminded_at IS NULL
                """,
                (sent_at, trip_id),
            )
            await conn.commit()
            return cur.rowcount == 1
        finally:
            await conn.close()

    # --- 8-faza: Kelmaydi (Absence) va Excel import metodlari ---
    async def match_village_by_name_or_school(self, village_query: str) -> Optional[int]:
        """
        Mahalla yoki maktab nomini apostrof variantlarini normallashtirib aniq moslaydi.
        """
        if not village_query:
            return None
        import re

        q = re.sub(r"[ʻʼ‘’`´]", "'", str(village_query)).strip().casefold()
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT id, name, school_name FROM villages")
            rows = [dict(r) for r in await cur.fetchall()]

            for r in rows:
                name = re.sub(r"[ʻʼ‘’`´]", "'", str(r["name"] or "")).strip().casefold()
                school = re.sub(r"[ʻʼ‘’`´]", "'", str(r["school_name"] or "")).strip().casefold()
                if name == q or (school and school == q):
                    return r["id"]
            return None
        finally:
            await conn.close()

    async def suggest_villages(self, village_query: str, limit: int = 5) -> List[str]:
        """Return likely village/school labels for an admin to review manually."""
        from difflib import SequenceMatcher
        import re

        normalize = lambda value: re.sub(r"[ʻʼ‘’`´]", "'", str(value or "")).strip().casefold()
        query = normalize(village_query)
        conn = await self.get_connection()
        try:
            cur = await conn.execute("SELECT name, school_name FROM villages")
            labels = []
            for row in await cur.fetchall():
                for label in (row["name"], row["school_name"]):
                    if label and label not in labels:
                        labels.append(label)
            ranked = sorted(
                labels,
                key=lambda label: SequenceMatcher(None, query, normalize(label)).ratio(),
                reverse=True,
            )
            return [label for label in ranked[:limit] if SequenceMatcher(None, query, normalize(label)).ratio() >= 0.3]
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
                if it["item_status"] in ["picked_up", "arrived", "accepted", "dropped_off", "handed_over"]:
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
            now_ts = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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

    async def mark_student_absent_with_reason(
        self,
        student_id: int,
        date_str: str,
        role: str,
        actor_id: int,
        reason: str
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        O'quvchini izoh (sabab) bilan kelmaydi deb belgilash.
        Agar bola allaqachon mashinada (picked_up yoki keyin) bo'lsa xatolik qaytaradi.
        """
        conn = await self.get_connection()
        try:
            cur_st = await conn.execute("""
                SELECT s.id, s.full_name, s.parent_phone_1, v.name as village_name
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

            # ALLAQACHON MASHINADA BO'LSA HIMOYA
            for it in trip_items:
                if it["item_status"] in ["picked_up", "arrived", "accepted", "dropped_off", "handed_over"]:
                    return False, "already_picked_up", None

            # Absences jadvalida bor-yo'qligini tekshirish
            cur_abs = await conn.execute(
                "SELECT id FROM absences WHERE student_id = ? AND date = ?",
                (student_id, date_str)
            )
            if await cur_abs.fetchone():
                return False, "already_marked", None

            await conn.execute("""
                INSERT INTO absences (student_id, date, reported_by_role, reported_by_id, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (student_id, date_str, role, actor_id, reason))

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
            now_ts = now_tz().strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute("""
                INSERT INTO event_log (ts, actor_role, actor_id, action, student_id)
                VALUES (?, ?, ?, 'mark_absent', ?)
            """, (now_ts, role, actor_id, student_id))

            await conn.commit()

            result_info = {
                "student_name": student_info["full_name"],
                "village_name": student_info["village_name"],
                "parent_phone": student_info.get("parent_phone_1") or "Kiritilmagan",
                "driver_tg_id": driver_info["driver_tg_id"] if driver_info else None,
                "driver_name": driver_info["driver_name"] if driver_info else None,
                "trip_id": driver_info["trip_id"] if driver_info else None,
                "reason": reason
            }
            return True, "success", result_info
        finally:
            await conn.close()

    async def get_student_current_status(self, student_id: int, date_str: str) -> Dict[str, Any]:
        """
        O'quvchining bugungi kungi reysdagi holatini aniqlash.
        """
        conn = await self.get_connection()
        try:
            # 1. Avval absences jadvalini tekshiramiz
            cur_abs = await conn.execute(
                "SELECT reason FROM absences WHERE student_id = ? AND date = ?",
                (student_id, date_str)
            )
            abs_row = await cur_abs.fetchone()
            if abs_row:
                reason = abs_row[0] or ""
                return {
                    "status_code": "absent",
                    "status_text": "Bugun kelmaydi",
                    "reason": reason,
                    "trip": None
                }

            # 2. Bugungi eng muhim holatdagi reys elementini olish.
            cur = await conn.execute("""
                SELECT ti.id, ti.status as item_status, ti.picked_at, ti.arrived_at, ti.accepted_at, ti.handed_at, ti.dropped_at,
                       t.id as trip_id, t.direction, t.planned_time, t.status as trip_status,
                       d.full_name as driver_name, d.phone as driver_phone
                FROM trip_items ti
                JOIN trips t ON ti.trip_id = t.id
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE ti.student_id = ? AND t.date = ?
                ORDER BY CASE
                    WHEN t.status IN ('on_way', 'at_center') THEN 0
                    WHEN ti.status IN ('pending', 'handed_over') AND t.status != 'cancelled' THEN 1
                    ELSE 2
                END, ti.id DESC
            """, (student_id, date_str))
            rows = [dict(r) for r in await cur.fetchall()]

            if not rows:
                return {
                    "status_code": "no_trip",
                    "status_text": "Bugun reys rejalashtirilmagan",
                    "reason": None,
                    "trip": None
                }

            latest = rows[0]
            item_status = latest["item_status"]

            status_map = {
                "pending": "Kutmoqda",
                "picked_up": "Mashinada",
                "at_center": "Markazga yetib kelgan",
                "accepted": "Darsda",
                "handed_over": "Haydovchiga topshirilgan",
                "dropped_off": "Uyga yetkazilgan",
                "absent": "Bugun kelmaydi"
            }
            status_text = status_map.get(item_status, "Kutmoqda")
            if latest["trip_status"] == "cancelled":
                status_text = "Reys bekor qilingan"
            elif item_status == "picked_up" and latest["trip_status"] == "at_center":
                status_text = "Markazga yetib kelgan"

            return {
                "status_code": item_status,
                "status_text": status_text,
                "reason": None,
                "trip": latest
            }
        finally:
            await conn.close()

    async def bulk_upsert_students_and_schedules(
        self, parsed_data: List[Dict[str, Any]], created_by: int = 0
    ) -> Dict[str, int]:
        """Excel orqali o'quvchilar va dars jadvallarini ommaviy saqlash/yangilash."""
        created = 0
        updated = 0
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
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
                    validate_schedule_times(st_time, end_time)
                    await conn.execute("""
                        INSERT INTO schedules (student_id, weekday, start_time, end_time)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(student_id, weekday)
                        DO UPDATE SET start_time = excluded.start_time, end_time = excluded.end_time
                    """, (student_id, weekday, st_time, end_time))

                await self._ensure_student_billing(
                    conn, student_id, now_tz().date(), created_by=created_by
                )

            await conn.commit()
            return {"created": created, "updated": updated}
        except BaseException:
            await conn.rollback()
            raise
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

            # Eslatma zanjirini faqat BIRINCHI kechiktirishda qayta boshlaymiz.
            # Keyingi kechiktirishlar hisoblagichni nolga tushirmaydi: haydovchi bir reysga
            # cheksiz eslatma olmaydi (reys uchun jami eslatma chegaralangan).
            reset_chain = 1 if (trip.get("delay_min") or 0) == 0 else 0
            await conn.execute("""
                UPDATE trips
                SET delay_min = ?, planned_time = ?,
                    reminder_count = CASE WHEN ? = 1 THEN 0 ELSE reminder_count END,
                    last_reminder_at = CASE WHEN ? = 1 THEN NULL ELSE last_reminder_at END
                WHERE id = ?
            """, (new_delay_min, new_planned_time, reset_chain, reset_chain, trip_id))

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

            now_ts = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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
                # Single-student broadcasts are keyed only by an exact database ID.
                try:
                    student_id = int(audience_value)
                except (TypeError, ValueError):
                    return []
                cur = await conn.execute("""
                    SELECT DISTINCT p.telegram_id, p.phone as contact_info, 'Ota-ona' as role
                    FROM parents p
                    JOIN student_parents sp ON p.id = sp.parent_id
                    JOIN students s ON sp.student_id = s.id
                    WHERE s.id = ?
                      AND s.is_active = 1 AND p.telegram_id IS NOT NULL
                """, (student_id,))
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
        now_ts = now_tz().strftime("%Y-%m-%d %H:%M:%S")
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

    async def get_daily_transport_stats(self, date_str: str) -> Dict[str, Any]:
        """
        Bugungi transport tizimining umumiy ko'rsatkichlari:
        - Jami reyslar (kelish va qaytish)
        - Markazga yetib kelganlar
        - Hozir darsda o'tirganlar
        - Bugun darsga kelmaganlar
        - Faol haydovchilar
        """
        conn = await self.get_connection()
        try:
            # 1. Jami reyslar va yo'nalishlar
            cur_trips = await conn.execute("""
                SELECT 
                    COUNT(*) as total_trips,
                    COALESCE(SUM(CASE WHEN direction = 'to_center' THEN 1 ELSE 0 END), 0) as to_center_trips,
                    COALESCE(SUM(CASE WHEN direction = 'to_home' THEN 1 ELSE 0 END), 0) as to_home_trips
                FROM trips WHERE date = ?
            """, (date_str,))
            row_trips = dict(await cur_trips.fetchone() or {})
            total_trips = row_trips.get("total_trips") or 0
            to_center_trips = row_trips.get("to_center_trips") or 0
            to_home_trips = row_trips.get("to_home_trips") or 0

            # 2. O'quvchilar statuslari
            cur_items = await conn.execute("""
                SELECT 
                    COUNT(DISTINCT CASE WHEN ti.status IN ('arrived', 'accepted', 'handed_over', 'dropped_off') THEN ti.student_id END) as arrived_count,
                    COUNT(DISTINCT CASE WHEN ti.status = 'accepted' THEN ti.student_id END) as in_class_count
                FROM trip_items ti
                JOIN trips t ON ti.trip_id = t.id
                WHERE t.date = ?
            """, (date_str,))
            row_items = dict(await cur_items.fetchone() or {})
            arrived_count = row_items.get("arrived_count") or 0
            in_class_count = row_items.get("in_class_count") or 0

            # 3. Bugun kelmaganlar soni
            cur_abs = await conn.execute("SELECT COUNT(DISTINCT student_id) FROM absences WHERE date = ?", (date_str,))
            absent_count = (await cur_abs.fetchone())[0]

            # 4. Faol haydovchilar soni
            cur_drivers = await conn.execute("SELECT COUNT(*) FROM drivers WHERE is_active = 1")
            active_drivers = (await cur_drivers.fetchone())[0]

            return {
                "date": date_str,
                "total_trips": total_trips,
                "to_center_trips": to_center_trips,
                "to_home_trips": to_home_trips,
                "arrived_count": arrived_count,
                "in_class_count": in_class_count,
                "absent_count": absent_count,
                "active_drivers": active_drivers
            }
        finally:
            await conn.close()

    async def get_today_absences_detailed(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Bugun absences jadvaliga tushgan barcha bolalar ro'yxati (sabablari va mahallasibilan).
        """
        conn = await self.get_connection()
        try:
            cur = await conn.execute("""
                SELECT a.id, a.student_id, a.date, a.reported_by_role, a.reported_by_id, a.reason,
                       s.full_name as student_name, s.parent_phone_1 as parent_phone,
                       v.name as village_name
                FROM absences a
                JOIN students s ON a.student_id = s.id
                LEFT JOIN villages v ON s.village_id = v.id
                WHERE a.date = ?
                ORDER BY a.id DESC
            """, (date_str,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def get_parent_billing_summary(self, parent_id: int, period: str) -> Dict[str, Any]:
        """Return this month's invoice and every outstanding older invoice per linked child."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                SELECT s.id AS student_id, s.full_name, s.is_active,
                       i.id AS invoice_id, i.period, i.amount, i.discount, i.status, i.billing_mode,
                       COALESCE((SELECT COUNT(*) FROM invoice_lesson_days ild WHERE ild.invoice_id = i.id), 0) AS lesson_count,
                       COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid_total
                FROM student_parents sp
                JOIN students s ON s.id = sp.student_id
                LEFT JOIN invoices i ON i.student_id = s.id
                  AND (
                    i.period = ?
                    OR (i.period < ? AND i.status <> 'waived' AND
                        i.amount - i.discount - COALESCE((
                            SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = i.id
                        ), 0) > 0)
                  )
                WHERE sp.parent_id = ?
                ORDER BY s.full_name COLLATE NOCASE, i.period DESC
                """,
                (period, period, parent_id),
            )
            children_by_id: Dict[int, Dict[str, Any]] = {}
            for row in await cur.fetchall():
                student_id = row["student_id"]
                child = children_by_id.setdefault(student_id, {
                    "student_id": student_id,
                    "name": row["full_name"],
                    "active": bool(row["is_active"]),
                    "current_invoice": None,
                    "overdue_invoices": [],
                    "total_due": 0,
                })
                if row["invoice_id"] is None:
                    continue
                due = 0 if row["status"] == "waived" else max(
                    0, row["amount"] - row["discount"] - row["paid_total"]
                )
                invoice = {
                    "id": row["invoice_id"],
                    "period": row["period"],
                    "amount": row["amount"],
                    "discount": row["discount"],
                    "paid_total": row["paid_total"],
                    "billing_mode": row["billing_mode"],
                    "lesson_count": row["lesson_count"],
                    "lesson_rate": LESSON_RATE if row["billing_mode"] == "attendance" else None,
                    "due": due,
                    "status": row["status"],
                }
                if row["period"] == period:
                    child["current_invoice"] = invoice
                elif due > 0:
                    child["overdue_invoices"].append(invoice)
                child["total_due"] += due

            children = list(children_by_id.values())
            return {
                "period": period,
                "due_day": 10,
                "total_due": sum(child["total_due"] for child in children),
                "children": children,
            }
        finally:
            await conn.close()

    async def get_billing_reminder_candidates(self, period: str) -> List[Dict[str, Any]]:
        """Return linked parents and student invoices with a positive outstanding balance."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                SELECT p.id AS parent_id, p.telegram_id,
                       s.id AS student_id, s.full_name AS student_name,
                       i.id AS invoice_id, i.period, i.amount, i.discount, i.status,
                       COALESCE(SUM(pay.amount), 0) AS paid_total
                FROM parents p
                JOIN student_parents sp ON sp.parent_id = p.id
                JOIN students s ON s.id = sp.student_id
                JOIN invoices i ON i.student_id = s.id
                LEFT JOIN payments pay ON pay.invoice_id = i.id
                WHERE i.period <= ? AND i.status <> 'waived'
                GROUP BY p.id, p.telegram_id, s.id, s.full_name,
                         i.id, i.period, i.amount, i.discount, i.status
                HAVING i.amount - i.discount - COALESCE(SUM(pay.amount), 0) > 0
                ORDER BY p.id, s.full_name COLLATE NOCASE, i.period DESC
                """,
                (period,),
            )
            return [dict(row) for row in await cur.fetchall()]
        finally:
            await conn.close()

    async def claim_billing_reminder(self, parent_id: int, period: str, reminder_day: int) -> bool:
        """Claim one reminder slot so a scheduler restart cannot send it twice."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO billing_reminder_log
                    (parent_id, period, reminder_day, attempted_at, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (parent_id, period, reminder_day, now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            await conn.commit()
            return cur.rowcount == 1
        finally:
            await conn.close()

    async def update_billing_reminder_status(
        self, parent_id: int, period: str, reminder_day: int, status: str
    ) -> bool:
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                UPDATE billing_reminder_log SET status = ?
                WHERE parent_id = ? AND period = ? AND reminder_day = ?
                """,
                (status, parent_id, period, reminder_day),
            )
            await conn.commit()
            return cur.rowcount == 1
        finally:
            await conn.close()

    async def create_invoice_if_missing(self, student_id: int, period: str, amount: int) -> bool:
        """Create one monthly invoice; return False when that student/month exists."""
        if type(amount) is not int or amount < 0:
            raise ValueError("amount must be a non-negative integer")
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "INSERT OR IGNORE INTO invoices (student_id, period, amount, status, created_at) "
                "VALUES (?, ?, ?, CASE WHEN ? = 0 THEN 'paid' ELSE 'unpaid' END, ?)",
                (student_id, period, amount, amount, now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            await conn.commit()
            return cur.rowcount == 1
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def record_payment(
        self, invoice_id: int, amount: int, method: str, recorded_by: int, paid_at: str
    ) -> Tuple[bool, str]:
        ok, reason, _payment_id = await self.record_payment_with_id(
            invoice_id, amount, method, recorded_by, paid_at
        )
        return ok, reason

    async def record_payment_with_id(
        self, invoice_id: int, amount: int, method: str, recorded_by: int, paid_at: str
    ) -> Tuple[bool, str, Optional[int]]:
        """Record a payment without allowing overpayment and return its receipt ID."""
        if type(amount) is not int or amount <= 0:
            return False, "invalid_amount", None
        if method not in ("cash", "card"):
            return False, "invalid_method", None

        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT amount, discount, status FROM invoices WHERE id = ?", (invoice_id,)
            )
            invoice = await cur.fetchone()
            if invoice is None:
                await conn.rollback()
                return False, "not_found", None
            if invoice["status"] == "waived":
                await conn.rollback()
                return False, "waived", None

            cur = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?", (invoice_id,)
            )
            paid_total = (await cur.fetchone())[0]
            due = invoice["amount"] - invoice["discount"]
            if paid_total + amount > due:
                await conn.rollback()
                return False, "overpayment", None

            cur = await conn.execute(
                "INSERT INTO payments (invoice_id, amount, method, recorded_by, paid_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (invoice_id, amount, method, recorded_by, paid_at),
            )
            new_total = paid_total + amount
            status = "paid" if new_total == due else "unpaid"
            await conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
            await conn.commit()
            return True, "ok", int(cur.lastrowid)
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def get_payment_receipt_details(self, payment_id: int) -> Optional[Dict[str, Any]]:
        """Get a payment and the invoice/student snapshot needed for its receipt."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                SELECT p.id AS payment_id, p.amount AS payment_amount, p.method, p.paid_at,
                       p.recorded_by, i.id AS invoice_id, i.period, i.amount AS invoice_amount,
                       i.discount, i.billing_mode, s.id AS student_id, s.full_name AS student_name,
                       v.name AS village_name,
                       COALESCE((SELECT COUNT(*) FROM invoice_lesson_days ild WHERE ild.invoice_id = i.id), 0) AS lesson_count,
                       COALESCE((SELECT SUM(prior.amount) FROM payments prior
                                 WHERE prior.invoice_id = p.invoice_id AND prior.id <= p.id), 0) AS paid_total
                FROM payments p
                JOIN invoices i ON i.id = p.invoice_id
                JOIN students s ON s.id = i.student_id
                LEFT JOIN villages v ON v.id = s.village_id
                WHERE p.id = ?
                """,
                (payment_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["remaining_due"] = max(
                0, result["invoice_amount"] - result["discount"] - result["paid_total"]
            )
            result["paid_before"] = result["paid_total"] - result["payment_amount"]
            return result
        finally:
            await conn.close()

    async def apply_discount(
        self, invoice_id: int, amount: int, reason: str, admin_id: int
    ) -> Tuple[bool, str]:
        """Apply a discount and preserve its reason in the same transaction."""
        if type(amount) is not int or amount <= 0:
            return False, "invalid_amount"
        if not reason or not reason.strip():
            return False, "invalid_reason"

        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT amount, discount, status FROM invoices WHERE id = ?", (invoice_id,)
            )
            invoice = await cur.fetchone()
            if invoice is None:
                await conn.rollback()
                return False, "not_found"
            if invoice["status"] == "waived":
                await conn.rollback()
                return False, "waived"
            new_discount = invoice["discount"] + amount
            if new_discount > invoice["amount"]:
                await conn.rollback()
                return False, "discount_exceeds_amount"
            cur = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?", (invoice_id,)
            )
            paid_total = (await cur.fetchone())[0]
            if paid_total > invoice["amount"] - new_discount:
                await conn.rollback()
                return False, "discount_exceeds_due"

            await conn.execute("UPDATE invoices SET discount = ? WHERE id = ?", (new_discount, invoice_id))
            await conn.execute(
                "INSERT INTO discount_log (invoice_id, amount, reason, admin_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (invoice_id, amount, reason.strip(), admin_id, now_tz().strftime("%Y-%m-%d %H:%M:%S")),
            )
            if paid_total == invoice["amount"] - new_discount:
                await conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
            await conn.commit()
            return True, "ok"
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def get_invoice_summary(self, student_id: int, period: str) -> Optional[Dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                "SELECT id, amount, discount, status, billing_mode FROM invoices WHERE student_id = ? AND period = ?",
                (student_id, period),
            )
            invoice = await cur.fetchone()
            if invoice is None:
                return None
            cur = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?", (invoice["id"],)
            )
            paid_total = (await cur.fetchone())[0]
            cur = await conn.execute(
                "SELECT COUNT(*) FROM invoice_lesson_days WHERE invoice_id = ?", (invoice["id"],)
            )
            lesson_count = int((await cur.fetchone())[0])
            return {
                "id": invoice["id"],
                "amount": invoice["amount"],
                "discount": invoice["discount"],
                "paid_total": paid_total,
                "billing_mode": invoice["billing_mode"],
                "lesson_count": lesson_count,
                "lesson_rate": LESSON_RATE if invoice["billing_mode"] == "attendance" else None,
                "due": invoice["amount"] - invoice["discount"] - paid_total,
                "status": invoice["status"],
            }
        finally:
            await conn.close()

    async def get_billing_students(self, period: str) -> List[Dict[str, Any]]:
        """List active students with this month's invoice status and schedule count."""
        conn = await self.get_connection()
        try:
            cur = await conn.execute(
                """
                SELECT s.id, s.full_name, COUNT(DISTINCT sch.weekday) AS days_per_week,
                       i.id AS invoice_id, i.amount, i.discount, i.status, i.billing_mode,
                       COALESCE((SELECT COUNT(*) FROM invoice_lesson_days ild WHERE ild.invoice_id = i.id), 0) AS lesson_count,
                       COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid_total
                FROM students s
                LEFT JOIN schedules sch ON sch.student_id = s.id
                LEFT JOIN invoices i ON i.student_id = s.id AND i.period = ?
                WHERE s.is_active = 1
                GROUP BY s.id, s.full_name, i.id, i.amount, i.discount, i.status
                ORDER BY s.full_name COLLATE NOCASE
                """,
                (period,),
            )
            rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                if row["invoice_id"] is None:
                    row.update({"paid_total": 0, "due": None, "lesson_rate": None})
                else:
                    row["due"] = row["amount"] - row["discount"] - row["paid_total"]
                    row["lesson_rate"] = LESSON_RATE if row["billing_mode"] == "attendance" else None
            return rows
        finally:
            await conn.close()




