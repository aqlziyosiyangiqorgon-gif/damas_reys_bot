import os
import openpyxl
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from database.db import Database, normalize_phone

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
            output_path = "shablon_oquvchilar.xlsx"

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
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active

        # Sarlavha qatorini aniqlash (1-qator)
        header_map = {}
        for col_idx, cell in enumerate(ws[1], 1):
            val = str(cell.value or "").strip().lower()
            if any(k in val for k in ["f.i.sh", "fish", "fio", "ism"]):
                header_map["full_name"] = col_idx
            elif any(k in val for k in ["mahalla", "qishloq", "maktab"]):
                header_map["village"] = col_idx
            elif any(k in val for k in ["ota", "asosiy", "telefon 1", "telefon"]):
                if "phone_1" not in header_map:
                    header_map["phone_1"] = col_idx
            elif any(k in val for k in ["ona", "qo'shimcha", "telefon 2"]):
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
            for row_idx in range(2, ws.max_row + 1):
                row_cells = [ws.cell(row=row_idx, column=c).value for c in range(1, ws.max_column + 1)]
                # Bo'sh qator bo'lsa o'tkazib yuborish
                if not any(row_cells):
                    continue

                full_name_val = str(ws.cell(row=row_idx, column=idx_name).value or "").strip()
                village_val = str(ws.cell(row=row_idx, column=idx_village).value or "").strip()
                phone1_val = str(ws.cell(row=row_idx, column=idx_phone1).value or "").strip()
                phone2_val = str(ws.cell(row=row_idx, column=idx_phone2).value or "").strip()

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
                if not village_id:
                    errors.append({"row": row_idx, "full_name": full_name_val, "error": f"Mahalla topilmadi: {village_val}"})
                    continue

                # Dars vaqtlari tekshiruvi
                schedules = []
                sched_error = None
                for day_name, day_num in WEEKDAY_COLUMNS.items():
                    col_idx = header_map.get(f"day_{day_num}", 5 + day_num)
                    time_val = str(ws.cell(row=row_idx, column=col_idx).value or "").strip()
                    if time_val:
                        if "-" not in time_val:
                            sched_error = f"{day_name.capitalize()} dars vaqti noto'g'ri formatda: {time_val}"
                            break
                        parts = time_val.split("-")
                        st = parts[0].strip()
                        et = parts[1].strip()
                        try:
                            datetime.strptime(st, "%H:%M")
                            datetime.strptime(et, "%H:%M")
                            schedules.append({"weekday": day_num, "start_time": st, "end_time": et})
                        except ValueError:
                            sched_error = f"{day_name.capitalize()} soati noto'g'ri: {time_val}"
                            break

                if sched_error:
                    errors.append({"row": row_idx, "full_name": full_name_val, "error": sched_error})
                    continue

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
