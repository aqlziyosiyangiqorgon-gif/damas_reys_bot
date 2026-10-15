from utils.clock import now_tz
import asyncio
import os
import re
import tempfile
import uuid
import openpyxl
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from database.db import Database, normalize_phone
from utils.time_validation import validate_schedule_times

WEEKDAY_COLUMNS = {
    "dushanba": 0,
    "seshanba": 1,
    "chorshanba": 2,
    "payshanba": 3,
    "juma": 4,
    "shanba": 5
}

class ExcelService:
    @staticmethod
    def generate_template(output_path: Optional[str] = None) -> str:
        """
        Bo'sh namuna .xlsx faylini generatsiya qilish.
        Ustunlar: F.I.Sh | Mahalla | Ota telefoni | Ona telefoni | Dushanba | Seshanba | Chorshanba | Payshanba | Juma | Shanba
        """
        if not output_path:
            stamp = now_tz().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"shablon_oquvchilar_{stamp}_{uuid.uuid4().hex[:8]}.xlsx"
            output_path = os.path.join(tempfile.gettempdir(), filename)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "O'quvchilar"

        headers = [
            "F.I.Sh",
            "Mahalla",
            "Ota telefoni",
            "Ona telefoni",
            "Dushanba",
            "Seshanba",
            "Chorshanba",
            "Payshanba",
            "Juma",
            "Shanba"
        ]
        ws.append(headers)

        # Namunaviy qator
        sample_row = [
            "Ali Valiyev",
            "Arpaariq",
            "+998901234567",
            "+998907654321",
            "09:00-11:00",
            "",
            "09:00-11:00",
            "",
            "09:00-11:00",
            ""
        ]
        ws.append(sample_row)

        # Ustunlar kengligini chiroyli qilish
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

        wb.save(output_path)
        return output_path

    @staticmethod
    async def parse_and_preview_excel(file_path: str, db: Database) -> Dict[str, Any]:
        """
        Excel faylni o'qish, parslash, validatsiya qilish va oldindan ko'rish hisobotini tayyorlash.
        SATR: EXCELNI OLDINDAN KO'RISH VA XATOLARNI AJRATISH MANTIQI
        """
        headers, sheet_rows = await asyncio.to_thread(_read_excel_rows, file_path)

        # Sarlavha qatorini aniqlash (1-qator)
        header_map = {}
        for col_idx, header in enumerate(headers, 1):
            val = _normalize_apostrophes(str(header or "")).strip().lower()
            if any(k in val for k in ["f.i.sh", "fish", "fio", "ism"]):
                header_map["full_name"] = col_idx
            elif any(k in val for k in ["mahalla", "qishloq", "maktab"]):
                header_map["village"] = col_idx
            elif val in {"ona telefoni", "ona telefon", "telefon 2", "qo'shimcha telefon", "qo'shimcha raqam"}:
                header_map["phone_2"] = col_idx
            elif any(k in val for k in ["ota telefoni", "ota telefon", "asosiy telefon", "telefon 1"]):
                if "phone_1" not in header_map:
                    header_map["phone_1"] = col_idx
            elif "telefon" in val and "phone_1" not in header_map:
                header_map["phone_1"] = col_idx
            elif any(k in val for k in ["ona", "qo'shimcha"]):
                header_map["phone_2"] = col_idx

            for day_key, day_num in WEEKDAY_COLUMNS.items():
                if day_key in val:
                    header_map[f"day_{day_num}"] = col_idx

        # Standart indekslar (agar nomlar mos kelmasa tartib bo'yicha)
        idx_name = header_map.get("full_name", 1)
        idx_village = header_map.get("village", 2)
        idx_phone1 = header_map.get("phone_1", 3)
        idx_phone2 = header_map.get("phone_2", 4)

        valid_records = []
        errors = []

        conn = await db.get_connection()
        try:
            duplicate_keys = set()
            for row_idx, row_cells in enumerate(sheet_rows, start=2):
                # Bo'sh qator bo'lsa o'tkazib yuborish
                if not any(row_cells):
                    continue

                cell_value = lambda index: row_cells[index - 1] if index <= len(row_cells) else None
                full_name_val = _normalize_apostrophes(str(cell_value(idx_name) or "").strip())
                village_val = _normalize_apostrophes(str(cell_value(idx_village) or "").strip())
                phone1_val = str(cell_value(idx_phone1) or "").strip()
                phone2_val = str(cell_value(idx_phone2) or "").strip()

                if not full_name_val:
                    errors.append({"row": row_idx, "full_name": "-", "error": "F.I.Sh ko'rsatilmagan"})
                    continue

                if not village_val:
                    errors.append({"row": row_idx, "full_name": full_name_val, "error": "Mahalla ko'rsatilmagan"})
                    continue

                norm_p1 = normalize_phone(phone1_val)
                if not norm_p1:
                    errors.append({"row": row_idx, "full_name": full_name_val, "error": f"Asosiy telefon noto'g'ri: {phone1_val}"})
                    continue

                norm_p2 = normalize_phone(phone2_val) if phone2_val else None

                village_id = await db.match_village_by_name_or_school(village_val)

                # Dars vaqtlari tekshiruvi
                schedules = []
                sched_error = None
                for day_name, day_num in WEEKDAY_COLUMNS.items():
                    col_idx = header_map.get(f"day_{day_num}", 5 + day_num)
                    time_val = str(cell_value(col_idx) or "").strip()
                    if time_val:
                        if "-" not in time_val:
                            sched_error = f"{day_name.capitalize()} dars vaqti noto'g'ri formatda: {time_val}"
                            break
                        parts = time_val.split("-")
                        if len(parts) != 2:
                            sched_error = f"{day_name.capitalize()} dars vaqti noto'g'ri formatda: {time_val}"
                            break
                        st = parts[0].strip()
                        et = parts[1].strip()
                        try:
                            validate_schedule_times(st, et)
                            schedules.append({"weekday": day_num, "start_time": st, "end_time": et})
                        except ValueError:
                            sched_error = f"{day_name.capitalize()} soati noto'g'ri: tugash vaqti boshlanishidan keyin bo'lishi va HH:MM formatida yozilishi kerak: {time_val}"
                            break

                if sched_error:
                    errors.append({"row": row_idx, "full_name": full_name_val, "error": sched_error})
                    continue

                if not village_id:
                    candidates = await db.suggest_villages(village_val)
                    errors.append({
                        "row": row_idx,
                        "full_name": full_name_val,
                        "error": f"Mahalla aniq topilmadi: {village_val}",
                        "candidates": candidates,
                    })
                    continue

                duplicate_key = (full_name_val.casefold(), norm_p1)
                if duplicate_key in duplicate_keys:
                    errors.append({
                        "row": row_idx,
                        "full_name": full_name_val,
                        "error": "Takroriy o'quvchi qatori; birinchi yozuv saqlanadi",
                    })
                    continue
                duplicate_keys.add(duplicate_key)

                valid_records.append({
                    "full_name": full_name_val,
                    "village_id": village_id,
                    "parent_phone_1": norm_p1,
                    "parent_phone_2": norm_p2,
                    "schedules": schedules
                })

            # Oldindan ko'rish hisoboti: Yangi va yangilanadiganlar sonini hisoblash
            new_count = 0
            update_count = 0
            for rec in valid_records:
                cur_ex = await conn.execute(
                    "SELECT id FROM students WHERE full_name = ? AND parent_phone_1 = ?",
                    (rec["full_name"], rec["parent_phone_1"])
                )
                if await cur_ex.fetchone():
                    update_count += 1
                else:
                    new_count += 1

            return {
                "new_count": new_count,
                "update_count": update_count,
                "error_count": len(errors),
                "errors": errors,
                "valid_records": valid_records
            }
        finally:
            await conn.close()

    @staticmethod
    async def export_students_to_excel(db: Database, output_path: Optional[str] = None) -> str:
        """
        Bazadagi barcha faol o'quvchilar, ularning mahallasi, telefonlari va dars jadvallarini
        yagona chiroyli formatlangan .xlsx faylga eksport qilish.
        """
        if not output_path:
            output_path = f"baza_oquvchilar_{now_tz().strftime('%Y%m%d_%H%M%S')}.xlsx"

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "O'quvchilar Bazasi"

        headers = [
            "F.I.Sh",
            "Mahalla",
            "Ota telefoni",
            "Ona telefoni",
            "Dushanba",
            "Seshanba",
            "Chorshanba",
            "Payshanba",
            "Juma",
            "Shanba"
        ]
        ws.append(headers)

        conn = await db.get_connection()
        try:
            cur_students = await conn.execute("""
                SELECT s.id, s.full_name, s.parent_phone_1, s.parent_phone_2, v.name as village_name
                FROM students s
                LEFT JOIN villages v ON s.village_id = v.id
                WHERE s.is_active = 1
                ORDER BY s.id ASC
            """)
            students = [dict(r) for r in await cur_students.fetchall()]

            for s in students:
                cur_sched = await conn.execute("""
                    SELECT weekday, start_time, end_time
                    FROM schedules
                    WHERE student_id = ?
                    ORDER BY weekday ASC
                """, (s["id"],))
                sched_rows = await cur_sched.fetchall()
                sched_map = {row["weekday"]: f"{row['start_time']}-{row['end_time']}" for row in sched_rows}

                row = [
                    _excel_safe_text(s["full_name"]),
                    _excel_safe_text(s.get("village_name") or ""),
                    _excel_safe_text(s.get("parent_phone_1") or ""),
                    _excel_safe_text(s.get("parent_phone_2") or ""),
                    sched_map.get(0, ""),
                    sched_map.get(1, ""),
                    sched_map.get(2, ""),
                    sched_map.get(3, ""),
                    sched_map.get(4, ""),
                    sched_map.get(5, "")
                ]
                ws.append(row)

            # Ustunlar kengligini chiroyli qilish
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

            wb.save(output_path)
            return output_path
        finally:
            await conn.close()


def _excel_safe_text(value: Any) -> str:
    """Prevent user-controlled strings from being interpreted as spreadsheet formulas."""
    text = str(value if value is not None else "")
    if text.lstrip().startswith("="):
        return "'" + text
    return text


def _normalize_apostrophes(text: str) -> str:
    return re.sub(r"[ʻʼ‘’`´]", "'", text)


def _read_excel_rows(file_path: str) -> Tuple[List[Any], List[Tuple[Any, ...]]]:
    """Load cell values off the event loop and return plain Python data."""
    workbook = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        headers = list(next(rows, ()))
        return headers, list(rows)
    finally:
        workbook.close()

