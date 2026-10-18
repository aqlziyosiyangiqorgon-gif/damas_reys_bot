import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _project_path(path: str) -> str:
    """Resolve relative local paths against the code directory, not process CWD."""
    expanded = os.path.expanduser(path)
    return expanded if os.path.isabs(expanded) else os.path.join(BASE_DIR, expanded)


def get_default_db_path() -> str:
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_mount:
        return os.path.join(volume_mount, "damas_reys.db")
    env_db = os.getenv("DB_PATH")
    if env_db:
        return _project_path(env_db)
    if is_railway_runtime():
        return "/app/data/damas_reys.db"
    if os.path.isdir("/app/data"):
        return "/app/data/damas_reys.db"
    return os.path.join(BASE_DIR, "damas_reys.db")

def get_default_backup_dir() -> str:
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_mount:
        return os.path.join(volume_mount, "backups")
    env_bdir = os.getenv("BACKUP_DIR")
    if env_bdir:
        return _project_path(env_bdir)
    if is_railway_runtime():
        return "/app/data/backups"
    if os.path.isdir("/app/data"):
        return "/app/data/backups"
    return os.path.join(BASE_DIR, "backups")


def is_railway_runtime() -> bool:
    """Detect Railway using its runtime-provided service variables."""
    return any(os.getenv(name) for name in (
        "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"
    ))


def _path_is_within(path: str, directory: str) -> bool:
    path = os.path.realpath(os.path.abspath(path))
    directory = os.path.realpath(os.path.abspath(directory))
    try:
        return os.path.normcase(os.path.commonpath((path, directory))) == os.path.normcase(directory)
    except ValueError:
        return False

class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"
    ADMIN_IDS: str = ""
    # Markaz administratoriga ota-onalarning kelmaslik xabarlari yuboriladi.
    # Bu bot administratorlari (ADMIN_IDS) ro'yxatidan alohida.
    CENTER_ADMIN_TG_ID: int = 8067625824
    
    # Standart sozlamalar
    CAPACITY: int = 9
    GROUP_WINDOW_MIN: int = 20
    MIN_GAP_BETWEEN_TRIPS_MIN: int = 40
    PICKUP_LEAD_MIN: int = 30
    ROUTE_BUFFER_MIN: int = 5
    STOP_DWELL_MIN: int = 2          # Har bir to'xtashga qo'shiladigan vaqt (daqiqa)
    MAX_WAIT_MIN: int = 30           # Markazda dars boshlanishini kutish maksimal vaqti
    
    DAILY_PLAN_TIME: str = "06:30"
    AUTO_APPROVE_TIME: str = "07:00"
    TIMEZONE: str = "Asia/Tashkent"
    
    # Railway Volume va Zaxiralash sozlamalari
    DB_PATH: str = get_default_db_path()
    BACKUP_DIR: str = get_default_backup_dir()
    BACKUP_RETENTION_DAYS: int = 7
    BACKUP_CHAT_ID: int | None = None
    MINIAPP_URL: str = ""

    @field_validator("BACKUP_CHAT_ID", mode="before")
    @classmethod
    def parse_optional_backup_chat_id(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return int(value)

    @field_validator("MINIAPP_URL", mode="before")
    @classmethod
    def validate_miniapp_url(cls, value):
        value = (value or "").strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("MINIAPP_URL must be a public HTTPS site root")
        return value.rstrip("/")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def admin_ids_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]

    def get_db_path(self) -> str:
        """Return a database path on persistent storage when running on Railway."""
        if is_railway_runtime():
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if not mount or not os.path.isdir(mount):
                raise RuntimeError(
                    "Railway Volume topilmadi. Xizmatga doimiy diskni ulang; "
                    "bazasiz/vaqtinchalik joyda bot ishga tushmaydi."
                )
            configured = self.DB_PATH.strip()
            if not configured:
                raise RuntimeError("Railway muhitida DB_PATH bo'sh bo'lishi mumkin emas.")
            if os.path.isabs(configured):
                db_path = os.path.abspath(configured)
            else:
                db_path = os.path.abspath(os.path.join(mount, configured))
            if not _path_is_within(db_path, mount):
                raise RuntimeError(
                    "DB_PATH Railway Volume ichida bo'lishi kerak. "
                    "DB_PATH ni RAILWAY_VOLUME_MOUNT_PATH ichidagi faylga sozlang."
                )
            return db_path
        return _project_path(self.DB_PATH)

    def get_backup_dir(self) -> str:
        """Railway Volume doimiy zaxira papkasi."""
        if is_railway_runtime():
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if not mount or not os.path.isdir(mount):
                raise RuntimeError("Railway Volume ulanmagan; zaxira papkasini aniqlab bo'lmadi.")
            configured = self.BACKUP_DIR.strip()
            if not configured:
                configured = "backups"
            if os.path.isabs(configured):
                backup_dir = os.path.abspath(configured)
            else:
                backup_dir = os.path.abspath(os.path.join(mount, configured))
            if not _path_is_within(backup_dir, mount):
                raise RuntimeError("BACKUP_DIR Railway Volume ichida bo'lishi kerak.")
            return backup_dir
        return _project_path(self.BACKUP_DIR)

    def validate_existing_database(self, db_path: str) -> None:
        """Refuse to initialize a fresh, empty database on a Railway deployment."""
        if not is_railway_runtime():
            return
        path = Path(db_path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(
                f"Doimiy diskda mavjud baza topilmadi: {db_path}. "
                "Bo'sh baza yaratib foydalanuvchilar ro'yxatini yo'qotmaslik uchun bot to'xtatildi."
            )
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                check = connection.execute("PRAGMA quick_check").fetchone()
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise RuntimeError("Railway Volume'dagi ma'lumotlar bazasini tekshirib bo'lmadi.") from exc
        if not check or check[0] != "ok" or "students" not in tables:
            raise RuntimeError(
                "Railway Volume'dagi baza yaroqsiz yoki bot bazasi emas. "
                "Ma'lumotni saqlash uchun bot ishga tushirilmadi."
            )

settings = Settings()
