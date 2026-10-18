from pathlib import Path
from datetime import datetime, time

import openpyxl
import pytest
import pytest_asyncio

from database.db import Database
from services.excel_service import ExcelService


BASE_HEADERS = ["F.I.Sh", "Mahalla", "Ota telefoni", "Ona telefoni"]
ALL_DAY_HEADERS = BASE_HEADERS + [
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
]


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "excel_parse.db"))
    await database.init_db()
    return database


def write_xlsx(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()


async def parse_file(tmp_path, db, headers, rows):
    path = tmp_path / "students.xlsx"
    write_xlsx(path, headers, rows)
    return await ExcelService.parse_and_preview_excel(str(path), db)


@pytest.mark.asyncio
async def test_template_day_headers_map_to_exact_weekdays(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        ALL_DAY_HEADERS,
        [["Ali Valiyev", "Arpaariq", "901234567", "", "09:00-10:00", "10:00-11:00", "11:00-12:00", "12:00-13:00", "13:00-14:00", "14:00-15:00", "15:00-16:00"]],
    )

    assert len(result["valid_records"]) == 1
    assert [
        (schedule["weekday"], schedule["start_time"])
        for schedule in result["valid_records"][0]["schedules"]
    ] == [(0, "09:00"), (1, "10:00"), (2, "11:00"), (3, "12:00"), (4, "13:00"), (5, "14:00"), (6, "15:00")]


@pytest.mark.asyncio
async def test_missing_saturday_header_does_not_use_fixed_column_fallback(tmp_path, db):
    headers = BASE_HEADERS + ["Chorshanba"]
    result = await parse_file(
        tmp_path,
        db,
        headers,
        [["Ali Valiyev", "Arpaariq", "901234567", "", "09:00-10:00"]],
    )

    schedules = result["valid_records"][0]["schedules"]
    assert [schedule["weekday"] for schedule in schedules] == [2]
    assert all(schedule["weekday"] != 5 for schedule in schedules)


@pytest.mark.asyncio
async def test_reordered_saturday_and_monday_headers_keep_their_weekdays(tmp_path, db):
    headers = BASE_HEADERS + ["Shanba", "Dushanba"]
    result = await parse_file(
        tmp_path,
        db,
        headers,
        [["Ali Valiyev", "Arpaariq", "901234567", "", "10:00-11:00", "09:00-10:00"]],
    )

    schedules = {schedule["weekday"]: schedule for schedule in result["valid_records"][0]["schedules"]}
    assert schedules[5]["start_time"] == "10:00"
    assert schedules[0]["start_time"] == "09:00"


@pytest.mark.asyncio
async def test_single_digit_hour_is_zero_padded(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba"],
        [["Ali Valiyev", "Arpaariq", "901234567", "", "9:00-11:00"]],
    )

    schedule = result["valid_records"][0]["schedules"][0]
    assert schedule["start_time"] == "09:00"
    assert schedule["end_time"] == "11:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["–", "—"])
async def test_unicode_dash_time_ranges_are_accepted(tmp_path, db, separator):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba"],
        [["Ali Valiyev", "Arpaariq", "901234567", "", f"09:00{separator}11:00"]],
    )

    assert len(result["valid_records"]) == 1
    assert result["valid_records"][0]["schedules"][0]["end_time"] == "11:00"


@pytest.mark.asyncio
async def test_single_time_cell_is_rejected_with_range_format_error(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba"],
        [["Ali Valiyev", "Arpaariq", "901234567", "", "09:00"]],
    )

    assert result["valid_records"] == []
    assert result["errors"][0]["error"] == "Vaqt oralig'i HH:MM-HH:MM ko'rinishida bo'lishi kerak"


@pytest.mark.asyncio
@pytest.mark.parametrize("single_time", [time(9, 0), datetime(2026, 10, 15, 9, 0)])
async def test_excel_time_and_datetime_cells_are_not_guessed_as_ranges(tmp_path, db, single_time):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba"],
        [["Ali Valiyev", "Arpaariq", "901234567", "", single_time]],
    )

    assert result["valid_records"] == []
    assert result["errors"][0]["error"] == "Vaqt oralig'i HH:MM-HH:MM ko'rinishida bo'lishi kerak"


@pytest.mark.asyncio
async def test_start_after_end_uses_existing_schedule_validation_message(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba"],
        [["Ali Valiyev", "Arpaariq", "901234567", "", "11:00-09:00"]],
    )

    assert result["valid_records"] == []
    assert "tugash vaqti boshlanishidan keyin bo'lishi" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_twelve_valid_rows_are_all_returned(tmp_path, db):
    rows = [
        [f"O'quvchi {index}", "Arpaariq", str(901234560 + index), ""]
        for index in range(12)
    ]
    result = await parse_file(tmp_path, db, BASE_HEADERS, rows)

    assert len(result["valid_records"]) == 12


@pytest.mark.asyncio
async def test_whitespace_only_rows_are_skipped(tmp_path, db):
    result = await parse_file(tmp_path, db, BASE_HEADERS, [["  ", "\t", "", "   "]])

    assert result["valid_records"] == []
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_duplicate_name_and_phone_keep_first_and_report_one_error(tmp_path, db):
    row = ["Ali Valiyev", "Arpaariq", "901234567", ""]
    result = await parse_file(tmp_path, db, BASE_HEADERS, [row, row.copy()])

    assert len(result["valid_records"]) == 1
    assert len(result["errors"]) == 1
    assert "Takroriy" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_integer_phone_cell_is_normalized_as_uzbek_number(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS,
        [["Ali Valiyev", "Arpaariq", 901234567, ""]],
    )

    assert result["valid_records"][0]["parent_phone_1"] == "+998901234567"


@pytest.mark.asyncio
async def test_all_eight_bad_rows_are_returned_as_errors(tmp_path, db):
    rows = [["", "Arpaariq", f"90123456{index}", ""] for index in range(8)]
    result = await parse_file(tmp_path, db, BASE_HEADERS, rows)

    assert len(result["errors"]) == 8
    assert result["error_count"] == 8


@pytest.mark.asyncio
async def test_duplicate_weekday_headers_reject_the_whole_file(tmp_path, db):
    result = await parse_file(
        tmp_path,
        db,
        BASE_HEADERS + ["Dushanba", " dushanba "],
        [["Ali Valiyev", "Arpaariq", "901234567", "", "09:00-10:00", "11:00-12:00"]],
    )

    assert result["valid_records"] == []
    assert result["error_count"] == 1
    assert "Dushanba" in result["errors"][0]["error"]
