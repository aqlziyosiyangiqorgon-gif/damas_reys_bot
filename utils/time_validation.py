"""Shared validation for stored lesson schedule times."""

import re
from datetime import datetime


_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def validate_schedule_times(start_time: str, end_time: str) -> None:
    """Require zero-padded HH:MM values and a strictly increasing interval."""
    if not isinstance(start_time, str) or not isinstance(end_time, str):
        raise ValueError("Vaqt HH:MM formatida bo'lishi kerak")
    if not _TIME_RE.fullmatch(start_time) or not _TIME_RE.fullmatch(end_time):
        raise ValueError("Vaqt HH:MM formatida bo'lishi kerak")
    try:
        start = datetime.strptime(start_time, "%H:%M")
        end = datetime.strptime(end_time, "%H:%M")
    except ValueError as exc:
        raise ValueError("Vaqt HH:MM formatida bo'lishi kerak") from exc
    if start >= end:
        raise ValueError("Dars boshlanish vaqti tugash vaqtidan oldin bo'lishi kerak")
