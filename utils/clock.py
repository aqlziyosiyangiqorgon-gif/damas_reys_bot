from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings


def now_tz() -> datetime:
    """Return current configured-zone wall time as a naive datetime.

    Persisted application timestamps are local wall-clock strings, so keeping
    this value naive preserves the existing database format while deriving it
    from the configured timezone rather than the host timezone.
    """
    return datetime.now(ZoneInfo(settings.TIMEZONE)).replace(tzinfo=None)
