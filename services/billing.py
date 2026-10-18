"""Pure integer-so'm billing calculations."""

from datetime import date, timedelta

LESSON_RATE = 5_000


def tariff_for_days(days_per_week: int) -> int | None:
    """Return the known weekly tariff, or None when it must be entered manually."""
    return {3: 60_000, 6: 120_000}.get(days_per_week)


def count_service_days(weekdays: set[int], start: date, end: date) -> int:
    """Count service dates in the inclusive range (Monday is weekday 0)."""
    if end < start:
        return 0

    total = 0
    current = start
    while current <= end:
        if current.weekday() in weekdays:
            total += 1
        current += timedelta(days=1)
    return total


def round_to_thousand(value: int) -> int:
    """Round to the nearest thousand so'm, with halves rounded upward."""
    if value < 0:
        raise ValueError("value must be non-negative")
    return ((value + 500) // 1_000) * 1_000


def _validate_period(service_days: int, period_days: int) -> None:
    if period_days <= 0:
        raise ValueError("period_days must be positive")
    if service_days < 0 or service_days > period_days:
        raise ValueError("service_days must be between 0 and period_days")


def prorated_amount(full_price: int, service_days: int, period_days: int) -> int:
    """Calculate a prorated amount and round it to the nearest thousand so'm."""
    if full_price < 0:
        raise ValueError("full_price must be non-negative")
    _validate_period(service_days, period_days)

    numerator = full_price * service_days
    # Round the exact rational amount to the nearest 1,000 without using floats.
    return ((numerator + period_days * 500) // (period_days * 1_000)) * 1_000


def refund_amount(paid: int, full_price: int, used_days: int, period_days: int) -> int:
    """Return the non-negative remainder after charging for used service days."""
    if paid < 0:
        raise ValueError("paid must be non-negative")
    earned = prorated_amount(full_price, used_days, period_days)
    return max(0, paid - earned)
