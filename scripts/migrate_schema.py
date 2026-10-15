"""Safely back up and apply missing SQLite schema constraints.

Run from the project root with ``python scripts/migrate_schema.py``.
The migration never deletes duplicate or orphan rows: it creates a backup and
stops with an actionable error if existing data cannot satisfy the constraints.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from config import settings


def create_online_backup(db_path: str, backup_dir: str) -> str:
    """Create a consistent SQLite backup using the online backup API."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = os.path.join(backup_dir, f"pre_migration_{stamp}.db")
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return backup_path


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _require_no_rows(conn: sqlite3.Connection, sql: str, description: str) -> None:
    rows = conn.execute(sql).fetchmany(10)
    if rows:
        sample = ", ".join(str(tuple(row)) for row in rows)
        raise RuntimeError(f"{description}; misol qatorlar: {sample}")


def _rebuild_with_driver_fk(conn: sqlite3.Connection, table: str, column: str) -> None:
    create_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not create_sql_row or not create_sql_row[0]:
        return
    create_sql = create_sql_row[0]
    fk_rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    if any(row[3] == column and row[2] == "drivers" for row in fk_rows):
        return

    temporary = f"{table}__schema_migration"
    if _table_exists(conn, temporary):
        raise RuntimeError(f"Vaqtinchalik jadval allaqachon mavjud: {temporary}")
    rewritten = re.sub(
        rf"(?i)(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\"{table}\"|`{table}`|\[{table}\]|{table})\b)",
        lambda match: match.group(1).rsplit(table, 1)[0] + temporary,
        create_sql,
        count=1,
    )
    column_pattern = rf"(?i)(\b{re.escape(column)}\s+INTEGER)(\s+NULL)?"
    rewritten, changed = re.subn(
        column_pattern,
        lambda match: f"{match.group(1)}{match.group(2) or ''} REFERENCES drivers(id) ON DELETE SET NULL",
        rewritten,
        count=1,
    )
    if not changed:
        raise RuntimeError(f"{table}.{column} ustuniga FK qo'shish uchun sxema tanilmadi")

    conn.execute(rewritten)
    cols = _columns(conn, table)
    quoted = ", ".join(f'"{name}"' for name in cols)
    conn.execute(f'INSERT INTO "{temporary}" ({quoted}) SELECT {quoted} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')


def migrate(db_path: str, backup_dir: str) -> str:
    """Back up first, validate existing rows, then add/rebuild constraints."""
    db_path = str(Path(db_path).resolve())
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Ma'lumotlar bazasi topilmadi: {db_path}")
    backup_path = create_online_backup(db_path, str(Path(backup_dir).resolve()))
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        _require_no_rows(
            conn,
            "SELECT ti.trip_id, ti.student_id, COUNT(*) FROM trip_items ti "
            "GROUP BY ti.trip_id, ti.student_id HAVING COUNT(*) > 1",
            "trip_items ichida bir reysdagi takroriy o'quvchilar bor; dublikatlarni qo'lda hal qiling",
        )
        for table, column in (("trips", "driver_id"), ("villages", "default_driver_id")):
            if _table_exists(conn, table) and "id" in _columns(conn, "drivers"):
                _require_no_rows(
                    conn,
                    f'SELECT t.rowid, t."{column}" FROM "{table}" t '
                    f'LEFT JOIN drivers d ON d.id=t."{column}" '
                    f'WHERE t."{column}" IS NOT NULL AND d.id IS NULL',
                    f"{table}.{column} da mavjud bo'lmagan haydovchi IDlari bor",
                )
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drivers'").fetchone():
            _require_no_rows(conn, "PRAGMA foreign_key_check", "Mavjud FK bog'lanishlarida yetim qatorlar bor")

        # Validate all unique-index inputs before making any schema change.
        if _table_exists(conn, "trip_items"):
            _require_no_rows(
                conn,
                "SELECT trip_id, student_id, COUNT(*) FROM trip_items "
                "GROUP BY trip_id, student_id HAVING COUNT(*) > 1",
                "trip_items uchun UNIQUE(trip_id, student_id) yaratilmaydi: dublikatlar topildi",
            )
        for table in ("protected_groups", "required_channels"):
            if _table_exists(conn, table) and "chat_id" in _columns(conn, table):
                _require_no_rows(
                    conn,
                    f'SELECT chat_id, COUNT(*) FROM "{table}" GROUP BY chat_id HAVING COUNT(*) > 1',
                    f"{table}.chat_id dublikatlari bor",
                )

        conn.execute("BEGIN IMMEDIATE")
        if _table_exists(conn, "trips") and _table_exists(conn, "drivers"):
            _rebuild_with_driver_fk(conn, "trips", "driver_id")
        if _table_exists(conn, "villages") and _table_exists(conn, "drivers"):
            _rebuild_with_driver_fk(conn, "villages", "default_driver_id")

        if _table_exists(conn, "trip_items"):
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_trip_items_trip_student "
                "ON trip_items(trip_id, student_id)"
            )
        for table in ("protected_groups", "required_channels"):
            if _table_exists(conn, table) and "chat_id" in _columns(conn, table):
                conn.execute(
                    f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_{table}_chat_id" ON "{table}"(chat_id)'
                )
        violations = conn.execute("PRAGMA foreign_key_check").fetchmany(10)
        if violations:
            raise RuntimeError(f"Migratsiyadan keyin FK xatolari: {violations}")
        conn.commit()
        return backup_path
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


if __name__ == "__main__":
    result = migrate(settings.get_db_path(), settings.get_backup_dir())
    print(f"Migratsiya muvaffaqiyatli. Oldindan olingan zaxira: {result}")
