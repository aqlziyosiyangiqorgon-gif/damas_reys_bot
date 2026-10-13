import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

def get_default_db_path() -> str:
    env_db = os.getenv("DB_PATH")
    if env_db:
        return env_db
    if os.path.isdir("/app/data"):
        return "/app/data/damas_reys.db"
    return "damas_reys.db"

def get_default_backup_dir() -> str:
    env_bdir = os.getenv("BACKUP_DIR")
    if env_bdir:
        return env_bdir
    if os.path.isdir("/app/data"):
        return "/app/data/backups"
    return "backups"

class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"
    ADMIN_IDS: str = "6273865667,7446734373"
    
    # Standart sozlamalar
    CAPACITY: int = 9
    GROUP_WINDOW_MIN: int = 20
    MIN_GAP_BETWEEN_TRIPS_MIN: int = 40
    PICKUP_LEAD_MIN: int = 30
    STOP_DWELL_MIN: int = 2          # Har bir to'xtashga qo'shiladigan vaqt (daqiqa)
    MAX_WAIT_MIN: int = 30           # Markazda dars boshlanishini kutish maksimal vaqti
    
    DAILY_PLAN_TIME: str = "06:30"
    AUTO_APPROVE_TIME: str = "07:00"
    TIMEZONE: str = "Asia/Tashkent"
    
    # Railway Volume va Zaxiralash sozlamalari
    DB_PATH: str = get_default_db_path()
    BACKUP_DIR: str = get_default_backup_dir()
    BACKUP_RETENTION_DAYS: int = 7

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_ids_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]

    def get_db_path(self) -> str:
        """Railway muhitida (/app/data) avtomatik Volume yo'lini qaytaradi."""
        if os.path.isdir("/app/data") and self.DB_PATH == "damas_reys.db":
            return "/app/data/damas_reys.db"
        return self.DB_PATH

    def get_backup_dir(self) -> str:
        """Railway Volume doimiy zaxira papkasi."""
        if os.path.isdir("/app/data") and self.BACKUP_DIR == "backups":
            return "/app/data/backups"
        return self.BACKUP_DIR

settings = Settings()
