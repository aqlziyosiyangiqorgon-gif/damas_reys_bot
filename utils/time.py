from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings


def now_tashkent() -> datetime:
    """Return the current time in the configured application timezone."""
    return datetime.now(ZoneInfo(settings.TIMEZONE))
