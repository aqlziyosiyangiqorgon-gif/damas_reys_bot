from datetime import date

import pytest

from services.billing import (
    count_service_days,
    prorated_amount,
    refund_amount,
    round_to_thousand,
    tariff_for_days,
)


def test_tariff_for_days_known_and_manual_values():
    assert tariff_for_days(3) == 60_000
    assert tariff_for_days(6) == 120_000
    assert tariff_for_days(1) is None
    assert tariff_for_days(4) is None
    assert tariff_for_days(5) is None


def test_count_service_days_inclusive_october_range():
    assert count_service_days(
        {0, 2, 4}, date(2026, 10, 1), date(2026, 10, 31)
    ) == 13


def test_count_service_days_reversed_range_is_zero():
    assert count_service_days({0}, date(2026, 10, 10), date(2026, 10, 9)) == 0


def test_round_to_thousand_uses_half_up_integer_rounding():
    assert round_to_thousand(30_499) == 30_000
    assert round_to_thousand(30_500) == 31_000
    assert round_to_thousand(0) == 0
    with pytest.raises(ValueError):
        round_to_thousand(-1)


@pytest.mark.parametrize(
    ("full_price", "service_days", "period_days", "expected"),
    [
        (60_000, 6, 12, 30_000),
        (120_000, 1, 3, 40_000),
        (60_000, 13, 31, 25_000),
    ],
)
def test_prorated_amount_exact_values(full_price, service_days, period_days, expected):
    assert prorated_amount(full_price, service_days, period_days) == expected


def test_prorated_amount_rejects_service_days_over_period():
    with pytest.raises(ValueError):
        prorated_amount(60_000, 13, 12)


@pytest.mark.parametrize(
    ("paid", "full_price", "used_days", "period_days", "expected"),
    [
        (60_000, 60_000, 6, 12, 30_000),
        (30_000, 60_000, 6, 12, 0),
        (60_000, 60_000, 12, 12, 0),
    ],
)
def test_refund_amount_exact_values(paid, full_price, used_days, period_days, expected):
    assert refund_amount(paid, full_price, used_days, period_days) == expected


def test_round_to_thousand_30499():
    assert round_to_thousand(30_499) == 30_000


def test_round_to_thousand_30500():
    assert round_to_thousand(30_500) == 31_000


def test_round_to_thousand_zero():
    assert round_to_thousand(0) == 0


def test_round_to_thousand_negative_raises_value_error():
    with pytest.raises(ValueError):
        round_to_thousand(-1)


def test_prorated_amount_zero_service_days():
    assert prorated_amount(60_000, 0, 12) == 0


def test_prorated_amount_full_period():
    assert prorated_amount(60_000, 12, 12) == 60_000


def test_prorated_amount_negative_full_price_raises_value_error():
    with pytest.raises(ValueError):
        prorated_amount(-1, 1, 12)


def test_prorated_amount_zero_period_raises_value_error():
    with pytest.raises(ValueError):
        prorated_amount(60_000, 1, 0)


def test_prorated_amount_negative_service_days_raises_value_error():
    with pytest.raises(ValueError):
        prorated_amount(60_000, -1, 12)


def test_refund_amount_negative_paid_raises_value_error():
    with pytest.raises(ValueError):
        refund_amount(paid=-1, full_price=60_000, used_days=6, period_days=12)


def test_count_service_days_empty_weekdays():
    assert count_service_days(set(), date(2026, 10, 1), date(2026, 10, 31)) == 0


def test_count_service_days_single_monday():
    assert count_service_days({0}, date(2026, 10, 5), date(2026, 10, 5)) == 1


def test_count_service_days_saturday_not_in_weekdays():
    assert count_service_days({0, 2, 4}, date(2026, 10, 10), date(2026, 10, 10)) == 0
