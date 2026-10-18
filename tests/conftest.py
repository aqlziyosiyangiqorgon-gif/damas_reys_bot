"""Pytest sozlamasi.

Testlar lokal .env faylidagi ADMIN_IDS ga bog'liq bo'lmasligi kerak.
config.settings va handlers.admin router filtri ADMIN_IDS ni modul import
qilinayotganda o'qiydi, shuning uchun qiymat testlar import qilinishidan
OLDIN (shu conftest orqali) o'rnatiladi. Muhit o'zgaruvchisi .env fayldan
ustun turadi, shuning uchun bu faqat test jarayoniga ta'sir qiladi.
"""

import os

os.environ["ADMIN_IDS"] = "6273865667,7446734373"
