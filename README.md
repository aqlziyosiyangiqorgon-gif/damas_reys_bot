# Damas Reys Boti 🚐

O'quv markaz atrofidagi 7 ta qishloqdan o'quvchilarni Damas (sig'imi 8 kishi) bilan olib kelish va uyiga eltib qo'yish jadvalini avtomatik tuzish hamda haydovchiga yetkazish boti.

## Texnologiyalar
- **Python 3.11+**
- **aiogram 3.x**
- **aiosqlite** (Asinxron SQLite)
- **APScheduler**
- **pydantic-settings**
- **pytest** (Unit testlar uchun)

---

## O'rnatish va Ishga Tushirish

1. **Repozitoriyga o'ting va virtual muhit yarating:**
   ```bash
   cd damas_reys_bot
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/macOS:
   source venv/bin/activate
   ```

2. **Kutubxonalarni o'rnating:**
   ```bash
   pip install -r requirements.txt
   ```

3. **.env faylini sozlang:**
   `.env.example` dan `.env` nusxasini yarating va ma'lumotlarni to'ldiring:
   ```env
   BOT_TOKEN=telegram_bot_tokeningiz
   ADMIN_IDS=123456789,987654321
   DRIVER_ID=123456789
   CAPACITY=8
   GROUP_WINDOW_MIN=20
   MIN_GAP_BETWEEN_TRIPS_MIN=40
   PICKUP_LEAD_MIN=30
   DAILY_PLAN_TIME=06:30
   DB_PATH=damas_reys.db
   ```

4. **Testlarni ishga tushirish:**
   ```bash
   pytest tests
   ```

5. **Botni ishga tushirish:**
   ```bash
   python main.py
   ```

---

## Railway.app platformasiga deploy qilish

1. [railway.app](https://railway.app) ga kiring va GitHub repozitoriyangizni ulang (**New Project** -> **Deploy from GitHub repo**).
2. **Variables** bo'limida `.env` dagi barcha o'zgaruvchilarni kiriting:
   - `BOT_TOKEN`
   - `ADMIN_IDS`
   - `DRIVER_ID`
   - `CAPACITY`, `GROUP_WINDOW_MIN`, `MIN_GAP_BETWEEN_TRIPS_MIN`, `PICKUP_LEAD_MIN`, `DAILY_PLAN_TIME`
   - `DB_PATH=/app/data/damas_reys.db` (doimiy saqlash uchun Volume ulang).
3. **Volume qo'shish (Majburiy - SQLite uchun):**
   - Service sozlamalaridan **Volumes** bo'limiga kiring va `/app/data` manziliga volume biriktiring.
4. Deploy avtomatik tarzda `Procfile` yoki `python main.py` orqali ishga tushadi.

---

## Buyruqlar va Imkoniyatlar
- `/qoshish` — FSM orqali o'quvchi qo'shish (F.I.SH, qishloq, ota-ona telefoni, kunlar, vaqt).
- `/shablon` — O'quvchilarni ommaviy yuklash uchun Excel shablonini yuklab olish.
- `Excel yuklash` — To'ldirilgan `.xlsx` faylni botga yuborish orqali barcha o'quvchilarni bir zumda bazaga kiritish.
- `/ozgartirish` — Dars jadvalini o'zgartirish.
- `/ochirish` — O'quvchini ro'yxatdan chiqarish.
- `/bugun` va `/ertaga` — Avtomatik reys jadvalini hisoblash va haydovchiga yuborish tugmasi.
- `/qishloqlar` — 7 ta qishloq va yo'l vaqtlari ro'yxati.
- `/ota_ona` — Ota-onalar o'z telefon raqamlarini yuborib, botga bog'lanishadi.
- **Haydovchi oqimi** — 30 daqiqa oldin eslatma, "Yo'lga chiqdim", har bola uchun "Oldim ✅" va "Yetkazdim ✅" tugmalari.
- **Ota-ona bildirishnomalari** — Haydovchi tugmani bosishi bilan ota-onaga Telegram orqali farzandi mashinaga o'tirgani yoki uyga yetkazilgani haqida tezkor xabarnoma boradi.
