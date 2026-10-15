import pytest

from database.db import normalize_phone


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+998901234567", "+998901234567"),
        ("90 123 45 67", "+998901234567"),
        ("+79161234567", "+79161234567"),
        ("+82101234567", "+82101234567"),
        ("12345", None),
    ],
)
def test_normalize_phone_supported_and_invalid_numbers(value, expected):
    assert normalize_phone(value) == expected
