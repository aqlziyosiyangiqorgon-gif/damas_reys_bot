import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#718096"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(2 * cm, 28.5 * cm, "Damas Reys Boti — Texnik Ishlab Chiqish Rejasi")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(2 * cm, 28.3 * cm, 19 * cm, 28.3 * cm)
            
        # Footer
        text = f"Sahifa {self._pageNumber} / {page_count}"
        self.drawRightString(19 * cm, 1.2 * cm, text)
        self.drawString(2 * cm, 1.2 * cm, "Maxfiy / Ichki foydalanish uchun")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(2 * cm, 1.6 * cm, 19 * cm, 1.6 * cm)
        self.restoreState()

def build_pdf(filename):
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2.2 * cm
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=15
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=8,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#2D3748"),
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3
    )

    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#C53030")
    )

    badge_style = ParagraphStyle(
        'Badge',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#2C5282")
    )

    story = []

    # Title & Metadata
    story.append(Paragraph("DAMAS REYS BOTI", title_style))
    story.append(Paragraph("Bosqichma-bosqich Arxitektura va Mukammal Ishlab Chiqish Yo'riqnomasi", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=15))

    # Meta Table
    meta_data = [
        [Paragraph("<b>Texnologiyalar:</b> Python >=3.11, aiogram 3, aiosqlite, APScheduler, openpyxl", body_style),
         Paragraph("<b>Holati:</b> Tasdiqlangan / Bosqichli ijro", body_style)],
        [Paragraph("<b>Vaqt zonasi:</b> Asia/Tashkent (UTC+5)", body_style),
         Paragraph("<b>Deploy:</b> Railway (Worker service)", body_style)]
    ]
    meta_table = Table(meta_data, colWidths=[9.5 * cm, 7.5 * cm])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EDF2F7")),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    # Architecture Overview Table
    story.append(Paragraph("Loyihaning 8 ta Asosiy Bo'limi Xaritasi", h1_style))
    
    sections_summary = [
        ["#", "Bo'lim nomi", "Fayllar", "Asosiy vazifasi"],
        ["1", "Konfiguratsiya & Muhit", "config.py, .env", "Pydantic validatsiya, Asia/Tashkent zonasi"],
        ["2", "Ma'lumotlar Bazasi", "database/db.py", "aiosqlite, 5 ta jadval, xavfsiz context manager"],
        ["3", "Reys Tuzish Algoritmi", "trip_builder.py", "Vaqt klasteri, sig'im (8), DESC/ASC tartiblash"],
        ["4", "Avtomatik Scheduler", "scheduler_service.py", "06:30 kunlik reja, 30 min oldingi eslatmalar"],
        ["5", "Admin Boshqaruv Paneli", "handlers/admin.py", "FSM o'quvchi qo'shish, qishloqlar, paginatsiya"],
        ["6", "Haydovchi Oqimi", "handlers/driver.py", "Yo'lga chiqdim, Oldim, Yetkazdim tugmalari"],
        ["7", "Ota-onalar & Excel", "parent.py, excel_service.py", "Telefon bog'lash, shablon va ommaviy yuklash"],
        ["8", "Asosiy Dastur & Deploy", "main.py, Procfile", "Routerlarni ulash, polling, Railway sozlamasi"]
    ]
    
    t_summary = Table(sections_summary, colWidths=[0.8*cm, 4.5*cm, 4.5*cm, 7.2*cm])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_summary)
    story.append(Spacer(1, 15))

    # Detailed Sections
    sections = [
        {
            "num": "1-BO'LIM",
            "title": "Konfiguratsiya va Muhit Sozlamalari (Config & Environment)",
            "files": "config.py, .env, .env.example",
            "desc": "Tizimning poydevori. Muhit o'zgaruvchilari qat'iy tip tekshiruvi bilan yuklanadi.",
            "points": [
                "<b>Kutubxona:</b> pydantic-settings orqali BaseSettings klassi shakllantiriladi.",
                "<b>Parametrlar:</b> BOT_TOKEN, ADMIN_IDS (stringdan List[int] ga), DRIVER_ID, CAPACITY=8, GROUP_WINDOW_MIN=20, MIN_GAP_BETWEEN_TRIPS_MIN=40, PICKUP_LEAD_MIN=30, DAILY_PLAN_TIME='06:30', TIMEZONE='Asia/Tashkent'.",
                "<b>Qat'iy qoida:</b> TIMEZONE='Asia/Tashkent' (UTC+5) bo'lishi shart, aks holda Railway (UTC) serverida vaqt 5 soatga kechikadi."
            ],
            "result": "settings obyekti import qilinadi va butun loyihada barcha konstantalar markazlashgan holda ishlatiladi."
        },
        {
            "num": "2-BO'LIM",
            "title": "Ma'lumotlar Bazasi Qatlami (Database Layer)",
            "files": "database/db.py",
            "desc": "aiosqlite yordamida to'liq asinxron va xotiradan xavfsiz boshqaruv tizimi.",
            "points": [
                "<b>Jadvallar:</b> villages (7 ta qishloq), students, schedules, trips, trip_items.",
                "<b>Ulanish usuli:</b> Har bir operatsiya uchun mustaqil async with aiosqlite.connect(...) as conn context manageri qo'llanadi (Windowsda fayl bloklanishining oldini oladi).",
                "<b>Xavfsiz qayta hisoblash:</b> save_generated_trips tranzaksiyada faol reyslarni tekshiradi; agar sanada tasdiqlangan, yo'lda yoki yakunlangan reyslar bo'lsa, ma'lumotlar o'chirilmaydi va qayta hisoblash rad etiladi.",
                "<b>Dinamik tartiblash:</b> get_trips_by_date da pickup reyslariga route_order DESC (uzoqdan markazga), dropoff reyslariga route_order ASC (markazdan uzoqqa) saralash beriladi."
            ],
            "result": "Baza avtomatik ochiladi, migratsiyalar bajariladi va 7 ta qishloq standart kiritiladi."
        },
        {
            "num": "3-BO'LIM",
            "title": "Reys Tuzish Matematik Algoritmi (Trip Builder Algorithm)",
            "files": "services/trip_builder.py, tests/test_trip_builder.py",
            "desc": "Botning intellektual yadrosi — dars jadvallarini avtomatik reyslarga ajratadi.",
            "points": [
                "<b>Pickup vaqti:</b> start_time - PICKUP_LEAD_MIN (30m) - village.travel_minutes.",
                "<b>Dropoff vaqti:</b> end_time (dars tugash vaqti).",
                "<b>Vaqt klasteri:</b> GROUP_WINDOW_MIN (20 daqiqa) oraliqdagi barcha o'quvchilar bitta reys klasteriga birlashadi.",
                "<b>Sig'im nazorati:</b> Klaster CAPACITY (8 kishi) dan oshsa, alohida reyslarga bo'linadi.",
                "<b>Tanaffus chegarasi:</b> Reyslar orasidagi oraliq 40 daqiqadan kam bo'lsa, tizim ogohlantirish qaytaradi."
            ],
            "result": "Jadvaldan reyslar ro'yxati va ogohlantirishlar shakllanadi. pytest testlari bilan 100% himoyalangan."
        },
        {
            "num": "4-BO'LIM",
            "title": "Avtomatik Rejalashtiruvchi (Scheduler Service)",
            "files": "services/scheduler_service.py",
            "desc": "APScheduler orqali avtomatlashtirilgan cron va intervalli eslatmalar.",
            "points": [
                "<b>Vaqt zonasi:</b> scheduler = AsyncIOScheduler(timezone=ZoneInfo('Asia/Tashkent')).",
                "<b>06:30 Kunlik Reja:</b> Bugungi haftaning kuniga mos darslarni olib, reyslarni hisoblaydi va adminga tasdiqlash tugmasi bilan yuboradi.",
                "<b>30 daqiqa oldin eslatma:</b> Har 3 daqiqada tekshiruv; faqat approved=1 va reminded=0 bo'lgan reyslarga haydovchiga eslatma jo'natilib, bazada reminded=1 qilinadi (takrorlanish yo'qotiladi)."
            ],
            "result": "Tizim inson omilisiz o'z vaqtida rejalashtirish va haydovchini ogohlantirishni bajaradi."
        },
        {
            "num": "5-BO'LIM",
            "title": "Admin Boshqaruv Paneli (Admin Handlers)",
            "files": "handlers/admin.py",
            "desc": "Admin uchun to'liq interaktiv, FSM va tugmali boshqaruv interfeysi.",
            "points": [
                "<b>Ruxsat nazorati:</b> Barcha buyruqlarda is_admin(user_id) tekshiruvi.",
                "<b>/qishloqlar:</b> Har bir qishloq nomi tagida ✏️ tahrirlash tugmasi, FSM orqali nom va yo'l vaqti yangilanadi.",
                "<b>/qoshish:</b> Bosqichma-bosqich FSM (Ism -> Qishloq tugmasi -> Telefon -> Kunlar tugmasi -> Vaqt).",
                "<b>/ozgartirish va /ochirish:</b> 10 tadan sahifalash (Paginatsiya ⬅️ / ➡️) bilan barcha o'quvchilarni boshqarish.",
                "<b>/bugun va /ertaga:</b> Reyslarni ko'rish, 'Haydovchiga yuborish' va 'Qayta hisoblash' tugmalari."
            ],
            "result": "Admin telegramdan chiqmasdan butun logistikani qulay boshqaradi."
        },
        {
            "num": "6-BO'LIM",
            "title": "Haydovchi Interaktiv Oqimi (Driver Handlers)",
            "files": "handlers/driver.py",
            "desc": "Damas haydovchisining jonli ish marshruti va statuslar paneli.",
            "points": [
                "<b>Reysni boshlash:</b> Eslatmadagi [🚗 Yo'lga chiqdim] tugmasi bosiladi (status -> on_way).",
                "<b>Har bir bola nazorati:</b> Pickupda [⏳ Bola — Oldim] -> [✅ Bola (Olindi)], Dropoffda [⏳ Bola — Yetkazdim] -> [✅ Bola (Yetkazildi)].",
                "<b>Tugallangan tugmalar:</b> callback_data='none' uchun cb_none handleri ulangan (soat aylanib qolmaydi).",
                "<b>Yakunlash:</b> Barcha bolalar bajarilgach, [🏁 Reysni yakunlash] tugmasi chiqadi va adminga hisobot boradi."
            ],
            "result": "Haydovchi faqat tugmalarni bosadi, adminga va ota-onaga jonli statuslar boradi."
        },
        {
            "num": "7-BO'LIM",
            "title": "Ota-onalar va Excel Tizimi (Parent & Excel Services)",
            "files": "handlers/parent.py, services/excel_service.py",
            "desc": "Ota-onalarni xabardor qilish va yuzlab o'quvchilarni 1 soniyada yuklash.",
            "points": [
                "<b>/ota_ona:</b> Kontakt yuborish tugmasi orqali telefon raqam bazadagi o'quvchiga avtomatik bog'lanadi.",
                "<b>Tezkor bildirishnoma:</b> Haydovchi tugmani bosishi bilan ota-onaga farzandining mashinaga o'tirgani yoki yetkazilgani haqida xabar keladi.",
                "<b>/shablon:</b> Bo'sh Excel shablonini generatsiya qilib beradi.",
                "<b>Excel yuklash:</b> .xlsx fayl openpyxl orqali o'qilib, o'quvchilar va ularning jadvallari bazaga kiritiladi."
            ],
            "result": "Ota-onalarda xotirjamlik, markaz ma'muriyatida esa qog'ozbozlikdan to'liq qutilish."
        },
        {
            "num": "8-BO'LIM",
            "title": "Asosiy Dastur, Yaxlitlash va Deploy (Main & Production)",
            "files": "main.py, Procfile, requirements.txt, README.md",
            "desc": "Barcha modullarni birlashtirish va Railway platformasiga joylash.",
            "points": [
                "<b>main.py:</b> Dependency injection (db, scheduler_service) barcha routerlarga uzatiladi.",
                "<b>Procfile:</b> worker: python main.py (bot port tinglamaydi, worker rejimida ishlaydi).",
                "<b>Railway deploy:</b> SQLite xotirasi saqlanib qolishi uchun Railway Volume (/app/data) ulanishi ko'rsatilgan.",
                "<b>Bitta nusxa nazorati:</b> Polling ziddiyatlarini oldini oluvchi toza arxitektura."
            ],
            "result": "Tizim 24/7 rejimida serverda ishonchli ishlaydi."
        }
    ]

    for sec in sections:
        sec_flow = []
        sec_flow.append(Paragraph(f"{sec['num']}: {sec['title']}", h1_style))
        sec_flow.append(Paragraph(f"<b>Tegishli fayllar:</b> <code>{sec['files']}</code>", body_style))
        sec_flow.append(Paragraph(sec['desc'], body_style))
        sec_flow.append(Spacer(1, 4))
        
        for p in sec['points']:
            sec_flow.append(Paragraph(f"• {p}", bullet_style))
            
        sec_flow.append(Spacer(1, 4))
        
        # Result box
        res_data = [[Paragraph(f"<b>Kutilayotgan yakuniy natija:</b> {sec['result']}", body_style)]]
        res_table = Table(res_data, colWidths=[17 * cm])
        res_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EBF8FF")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#90CDF4")),
            ('PADDING', (0,0), (-1,-1), 6),
        ]))
        sec_flow.append(res_table)
        sec_flow.append(Spacer(1, 12))
        
        story.append(KeepTogether(sec_flow))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF generated: {filename}")

if __name__ == "__main__":
    out_path = r"C:\Users\HP\Desktop\Damas_Reys_Boti_Reja.pdf"
    build_pdf(out_path)
