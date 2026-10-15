import os
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _project_path(path: str) -> str:
    """Resolve relative local paths against the code directory, not process CWD."""
    expanded = os.path.expanduser(path)
    return expanded if os.path.isabs(expanded) else os.path.join(BASE_DIR, expanded)


def get_default_db_path() -> str:
    env_db = os.getenv("DB_PATH")
    if env_db:
        return _project_path(env_db)
    if os.path.isdir("/app/data"):
        return "/app/data/damas_reys.db"
    return os.path.join(BASE_DIR, "damas_reys.db")

def get_default_backup_dir() -> str:
    env_bdir = os.getenv("BACKUP_DIR")
    if env_bdir:
        return _project_path(env_bdir)
    if os.path.isdir("/app/data"):
        return "/app/data/backups"
    return os.path.join(BASE_DIR, "backups")

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

    @field_validator("BACKUP_CHAT_ID", mode="before")
    @classmethod
    def parse_optional_backup_chat_id(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return int(value)

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def admin_ids_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]

    def get_db_path(self) -> str:
        """Railway muhitida (/app/data) avtomatik Volume yo'lini qaytaradi."""
        if os.path.isdir("/app/data") and self.DB_PATH == "damas_reys.db":
            return "/app/data/damas_reys.db"
        return _project_path(self.DB_PATH)

    def get_backup_dir(self) -> str:
        """Railway Volume doimiy zaxira papkasi."""
        if os.path.isdir("/app/data") and self.BACKUP_DIR == "backups":
            return "/app/data/backups"
        return _project_path(self.BACKUP_DIR)

settings = Settings()
