# AQL ZIYOSI transport boti

O'quvchilarni Damas transportida markazga olib kelish va uyiga qaytarishni rejalashtiradi. Ota-onalarga yo'l holati va kelmaslik xabarlari yuboriladi; adminlar jadval, o'quvchi, haydovchi va xodimlarni boshqaradi.

## Texnologiyalar

- Python 3.11+
- aiogram 3.15, aiosqlite va SQLite (WAL)
- APScheduler 3.10; rejalashtirilgan vaqtlar `Asia/Tashkent` bo'yicha
- openpyxl (Excel import va eksport)
- pytest va pytest-asyncio

## Mahalliy ishga tushirish

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` ichida kamida `BOT_TOKEN` va `ADMIN_IDS` qiymatlarini sozlang. `ADMIN_IDS` — vergul bilan ajratilgan Telegram ID’lar. Boshqa sozlamalar uchun `.env.example`ga qarang.

```powershell
python -m pytest -q
python main.py
```

SQLite fayli `DB_PATH`da saqlanadi. FSM jarayonlari ham shu bazada saqlanadi, shuning uchun bot qayta ishga tushganda admin va ota-ona formalaridagi holat saqlanadi. Excel import va broadcast tasdiqlari ham qisqa muddatga bazaga yoziladi.

## Jadval va zaxira

- Kunlik reja `DAILY_PLAN_TIME`da tuziladi.
- Tasdiqlanmagan reyslar `AUTO_APPROVE_TIME`da avtomatik tasdiqlanadi.
- Haydovchi eslatmalari har daqiqada ko'rib chiqiladi.
- SQLite zaxirasi har kuni soat 23:00 da `BACKUP_DIR` ichiga onlayn SQLite backup API orqali olinadi. Standart saqlash muddati 7 kun (`BACKUP_RETENTION_DAYS`).

Zaxiralar bot serveridagi lokal fayllardir; Railway'da saqlanib qolishi uchun `BACKUP_DIR` ham Volume ichida bo'lishi kerak. Alohida tashqi zaxira xizmatiga nusxa yuborish hozircha sozlanmagan.

## Railway deploy

1. GitHub repozitoriyadan Railway service yarating.
2. Service Variables bo'limida `BOT_TOKEN`, `ADMIN_IDS`, `DB_PATH`, `BACKUP_DIR` va kerakli ish sozlamalarini kiriting.
3. Railway Volume'ni `/app/data` manziliga ulang. `DB_PATH=/app/data/damas_reys.db` va `BACKUP_DIR=/app/data/backups`dan foydalaning.
4. Service'ni **bitta replica** bilan ishlating. `Procfile` worker jarayonini `python main.py` orqali ishga tushiradi.

Deploydan keyin loglarda token yoki maxfiy qiymatlarni ulashmang. Volume yo'li noto'g'ri bo'lsa SQLite va backup fayllari deploylar orasida saqlanmasligi mumkin.

## Asosiy admin buyruqlari

- `/qoshish` — o'quvchi va dars jadvalini qo'shish
- `/shablon` va `.xlsx` yuklash — ommaviy import; bot oldindan ko'rish va tasdiqlash so'raydi
- `/haydovchilar`, `/xodimlar` — transport jamoasini boshqarish
- `/qishloqlar` — marshrut va mahalla ma'lumotlari
- `/jadval`, `/ozgartirish`, `/ochirish` — o'quvchi va jadvalni boshqarish
- `/bugun`, `/ertaga` — reyslarni ko'rish va kunlik rejani boshqarish
- `/xabar` — auditoriyaga xabar yuborish

Ota-onalar `/start` orqali telefonlarini tasdiqlaydi, so'ng farzand bog'lanishini tasdiqlaydi. Haydovchi va markaz xodimlari faqat o'z vazifasiga tegishli reys amallarini bajaradi.
