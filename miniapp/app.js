(() => {
  const tg = window.Telegram?.WebApp;
  const content = document.getElementById("content");
  const notice = document.getElementById("notice");
  const roleNames = { parent: "Ota-ona kabineti", driver: "Haydovchi kabineti", staff: "Markaz xodimi kabineti", admin: "Admin kabineti" };
  const statusNames = {
    planned: "Rejalashtirilgan", approved: "Tasdiqlangan", on_way: "Yo‘lda", at_center: "Markazda",
    handed_over: "Topshirilgan", done: "Yakunlangan", cancelled: "Bekor qilingan", pending: "Kutilmoqda",
    picked_up: "Mashinada", arrived: "Yetib kelgan", accepted: "Qabul qilingan", dropped_off: "Yetkazilgan",
    absent: "Kelmaydi", no_trip: "Reys yo‘q"
  };
  const dayNames = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"];
  let currentData = null;
  let selectedParentChildId = null;
  let adminSection = "home";
  let adminPeriod = "";
  let excelPreview = null;
  let broadcastPreview = null;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function card(title, subtitle, badge) {
    const section = node("article", null, "card");
    const head = node("div", null, "card-head");
    const left = node("div");
    left.append(node("h3", title));
    if (subtitle) left.append(node("p", subtitle, "muted"));
    head.append(left);
    if (badge) head.append(node("span", badge, "badge"));
    section.append(head);
    return section;
  }

  function empty(text) { return node("div", text, "empty"); }

  function formatMoney(value) {
    return `${Number(value || 0).toLocaleString("uz-UZ")} so‘m`;
  }

  function formatBillingPeriod(period) {
    const [year, month] = String(period || "").split("-").map(Number);
    const names = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"];
    return names[month - 1] ? `${names[month - 1]} ${year}` : String(period || "Oy");
  }

  function getParentBillingDeadline(billing) {
    const dueDay = billing.due_day || 10;
    const todayPeriod = String(currentData?.date || "").slice(0, 7);
    const todayDay = Number(String(currentData?.date || "").slice(8, 10));
    const isLatePeriod = billing.period < todayPeriod || (billing.period === todayPeriod && todayDay > dueDay);
    return isLatePeriod ? `${dueDay}-sana muddati o‘tgan` : `har oyning ${dueDay}-sanasigacha`;
  }

  function renderParentBillingSummary(billing) {
    if (!billing) return;
    const hasInvoices = (billing.children || []).some(child => child.current_invoice || (child.overdue_invoices || []).length);
    const badge = billing.total_due > 0 ? "Qarz mavjud" : hasInvoices ? "Qarz yo‘q" : "Hisob kutilmoqda";
    const section = card("💰 Oila bo‘yicha to‘lov", `${formatBillingPeriod(billing.period)} · ${getParentBillingDeadline(billing)}`, badge);
    const total = node("div", null, `billing-total${billing.total_due > 0 ? " has-debt" : ""}`);
    total.append(node("span", "Jami qolgan qarz"), node("strong", formatMoney(billing.total_due)));
    section.append(total);
    section.append(node("p", "Farzandingizni tanlab, uning shaxsiy kabinetini oching.", "muted billing-hint"));
    content.append(section);
  }

  function renderChildBilling(section, childBilling, billing) {
    const block = node("section", null, "parent-detail-block");
    block.append(node("h3", "💰 To‘lovlar"));
    if (!childBilling) {
      block.append(node("p", "To‘lov ma’lumoti hozircha yo‘q.", "muted billing-line"));
    } else {
      const invoice = childBilling.current_invoice;
      if (invoice) {
        const todayPeriod = String(currentData?.date || "").slice(0, 7);
        const todayDay = Number(String(currentData?.date || "").slice(8, 10));
        const dueDay = billing?.due_day || 10;
        const isLate = invoice.period < todayPeriod || (invoice.period === todayPeriod && todayDay > dueDay);
        const status = invoice.status === "waived" ? "Bekor qilingan"
          : invoice.due === 0 ? (invoice.amount === 0 ? "Hozircha qarz yo‘q" : "To‘langan")
            : isLate ? "Muddati o‘tgan"
              : invoice.paid_total > 0 ? "Qisman to‘langan" : "To‘lanmagan";
        block.append(node("p", `${formatBillingPeriod(invoice.period)} · ${status}`, "billing-period"));
        if (invoice.billing_mode === "attendance") {
          block.append(node("p", `Jadval bo‘yicha ${invoice.lesson_count} dars × ${formatMoney(invoice.lesson_rate)} so‘m`, "billing-line"));
        }
        block.append(node("p", `Hisob: ${formatMoney(invoice.amount - invoice.discount)} · To‘langan: ${formatMoney(invoice.paid_total)}`, "billing-line"));
        block.append(node("p", `Qoldiq: ${formatMoney(invoice.due)}`, `billing-balance${invoice.due > 0 ? " due" : ""}`));
      } else {
        block.append(node("p", `${formatBillingPeriod(billing?.period)} uchun hisob dars jadvali asosida avtomatik ochiladi.`, "muted billing-line"));
      }
      for (const oldInvoice of childBilling.overdue_invoices || []) {
        block.append(node("p", `⏳ ${formatBillingPeriod(oldInvoice.period)} qarzi: ${formatMoney(oldInvoice.due)}`, "billing-balance due"));
      }
      if (!invoice && !(childBilling.overdue_invoices || []).length) {
        block.append(node("p", "Eski oylardan qarzdorlik yo‘q.", "muted billing-line"));
      }
      if (!childBilling.active) block.append(node("p", "O‘quvchi hozir faol ro‘yxatda emas.", "muted billing-line"));
    }
    section.append(block);
  }

  function button(label, action, payload, secondary = false) {
    const element = node("button", label, `action-button${secondary ? " secondary" : ""}`);
    element.type = "button";
    element.addEventListener("click", async () => {
      element.disabled = true;
      try {
        const result = await api("/api/miniapp/action", { method: "POST", body: JSON.stringify({ action, ...payload }) });
        showNotice(result.message || "Bajarildi.");
        await loadDashboard();
      } catch (error) {
        showNotice(error.message);
        element.disabled = false;
      }
    });
    return element;
  }

  function showNotice(text) {
    notice.textContent = text;
    notice.hidden = false;
    if (tg?.showPopup) tg.showPopup({ message: text, buttons: [{ type: "ok" }] });
  }

  function confirmMiniApp(text) {
    if (tg?.showConfirm) return new Promise(resolve => tg.showConfirm(text, resolve));
    return Promise.resolve(window.confirm(text));
  }

  async function api(url, options = {}) {
    const headers = { "Authorization": `tma ${tg?.initData || ""}`, ...(options.headers || {}) };
    if (!(options.body instanceof FormData) && options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      ...options,
      headers
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.ok) throw new Error(body.error || "Ma’lumot olinmadi. Qayta urinib ko‘ring.");
    return body;
  }

  function renderParent(data) {
    if (!data.children.length) {
      renderParentBillingSummary(data.billing);
      content.append(node("h2", "Farzandlarim", "section-title"));
      content.append(empty("Sizga tasdiqlangan o‘quvchi biriktirilmagan."));
      return;
    }
    const billingByStudent = new Map((data.billing?.children || []).map(item => [Number(item.student_id), item]));
    const selectedChild = data.children.find(child => Number(child.id) === Number(selectedParentChildId));
    if (!selectedChild) {
      selectedParentChildId = null;
      renderParentBillingSummary(data.billing);
      content.append(node("h2", `Farzandlarim · ${data.children.length}`, "section-title"));
      for (const child of data.children) {
        const childBilling = billingByStudent.get(Number(child.id));
        const due = Number(childBilling?.total_due || 0);
        const hasInvoice = Boolean(childBilling?.current_invoice || (childBilling?.overdue_invoices || []).length);
        const debtText = due > 0 ? `Qarz · ${formatMoney(due)}` : hasInvoice ? "Qarz yo‘q" : "Hisob kutilmoqda";
        const section = node("article", null, "parent-child-card parent-child-choice");
        const heading = node("div", null, "parent-child-summary");
        const nameBlock = node("span", null, "parent-child-name");
        nameBlock.append(node("strong", child.name), node("small", child.village || "Mahalla ko‘rsatilmagan"));
        heading.append(nameBlock, node("span", debtText, `parent-child-debt${due > 0 ? " due" : ""}`));
        section.append(heading, node("div", `📍 Bugungi holat: ${child.today_status}`, "status-line"));
        const open = node("button", "Farzand kabinetini ochish  →", "action-button");
        open.type = "button";
        open.addEventListener("click", () => {
          selectedParentChildId = Number(child.id);
          content.replaceChildren();
          renderParent(data);
        });
        section.append(open);
        content.append(section);
      }
      return;
    }

    const child = selectedChild;
    const childBilling = billingByStudent.get(Number(child.id));
    const switcher = node("div", null, "parent-child-switcher");
    const back = node("button", "← Barcha farzandlar", "action-button secondary");
    back.type = "button";
    back.addEventListener("click", () => {
      selectedParentChildId = null;
      content.replaceChildren();
      renderParent(data);
    });
    switcher.append(back);
    content.append(switcher);

    const choices = node("div", null, "parent-child-tabs");
    for (const candidate of data.children) {
      const tab = node("button", candidate.name, `parent-child-tab${Number(candidate.id) === Number(child.id) ? " active" : ""}`);
      tab.type = "button";
      tab.setAttribute("aria-pressed", String(Number(candidate.id) === Number(child.id)));
      tab.addEventListener("click", () => {
        selectedParentChildId = Number(candidate.id);
        content.replaceChildren();
        renderParent(data);
      });
      choices.append(tab);
    }
    content.append(choices);

    const section = card(`👤 ${child.name}`, child.village || "Mahalla ko‘rsatilmagan", "Farzand kabineti");
    section.append(node("div", `📍 Bugungi holat: ${child.today_status}`, "status-line"));
    renderChildBilling(section, childBilling, data.billing);

    const scheduleBlock = node("section", null, "parent-detail-block");
    scheduleBlock.append(node("h3", "📅 Dars jadvali"));
    const schedules = [...child.schedules].sort((a, b) => a.weekday - b.weekday);
    if (schedules.length) {
      const list = node("div", null, "schedule-list");
      for (const schedule of schedules) {
        const row = node("div", null, "schedule-row");
        row.append(node("span", schedule.day || dayNames[schedule.weekday] || "Kun"));
        row.append(node("strong", `${schedule.start}–${schedule.end}`));
        list.append(row);
      }
      scheduleBlock.append(list);
    } else {
      scheduleBlock.append(node("p", "Dars jadvali kiritilmagan.", "muted"));
    }
    section.append(scheduleBlock);

    const absenceBlock = node("section", null, "parent-detail-block absence-block");
    absenceBlock.append(node("h3", "🚫 Bugun kelmaslik xabari"));
    if (child.today_status_code === "absent") {
      absenceBlock.append(node("p", "Bugungi kelmaslik xabaringiz yuborilgan.", "absence-confirmation"));
    } else {
      const label = node("label", "Sababi (ixtiyoriy)", "input-label");
      const reason = node("textarea", null, "reason-input");
      reason.maxLength = 100;
      reason.placeholder = "Masalan: shamollab qoldi";
      const report = node("button", "🚫 Bugun kelmaydi", "action-button secondary");
      report.type = "button";
      report.addEventListener("click", async () => {
        const confirmed = tg?.showConfirm ? await new Promise(resolve => tg.showConfirm("Farzandingiz bugun kelmasligini bildirasizmi?", resolve)) : window.confirm("Farzandingiz bugun kelmasligini bildirasizmi?");
        if (!confirmed) return;
        report.disabled = true;
        try {
          const result = await api("/api/miniapp/action", {
            method: "POST", body: JSON.stringify({ action: "report_absence", student_id: child.id, reason: reason.value })
          });
          showNotice(result.message);
          await loadDashboard();
        } catch (error) {
          showNotice(error.message);
          report.disabled = false;
        }
      });
      absenceBlock.append(label, reason, report);
    }
    section.append(absenceBlock);
    content.append(section);
  }

  function tripCard(trip, mode) {
    const direction = trip.direction === "to_center" ? "Markazga kelish" : "Uyga qaytish";
    const section = card(`${direction} · ${trip.planned_time || "Vaqt belgilanmagan"}`, `Haydovchi: ${trip.driver}`, statusNames[trip.status] || trip.status);
    const list = node("div", null, "item-list");
    for (const item of trip.items) {
      const row = node("div", null, "item-row");
      const copy = node("div", null, "item-copy");
      copy.append(node("strong", item.name));
      const detail = [item.village, item.pickup_point, statusNames[item.status] || item.status].filter(Boolean).join(" · ");
      copy.append(node("span", detail));
      row.append(copy);
      if (mode === "driver" && trip.status === "on_way" && trip.direction === "to_center" && item.status === "pending") {
        row.append(button("✅ Oldim", "pickup", { trip_id: trip.id, item_id: item.id }));
      }
      if (mode === "driver" && trip.status === "on_way" && trip.direction === "to_home" && ["pending", "handed_over", "arrived", "accepted"].includes(item.status)) {
        row.append(button("🏠 Yetkazdim", "dropoff", { trip_id: trip.id, item_id: item.id }));
      }
      if (mode === "staff" && trip.direction === "to_center" && trip.status === "at_center" && item.status !== "accepted") {
        row.append(button("🏫 Qabul qildim", "accept", { item_id: item.id }));
      }
      if (mode === "staff" && trip.direction === "to_home" && ["planned", "approved", "handed_over"].includes(trip.status) && !["handed_over", "picked_up", "dropped_off", "absent"].includes(item.status)) {
        row.append(button("🤝 Topshirdim", "handover", { item_id: item.id }));
      }
      list.append(row);
    }
    if (trip.items.length) section.append(list);
    if (mode === "driver") {
      const controls = node("div", null, "button-row");
      if (["planned", "approved", "handed_over"].includes(trip.status)) controls.append(button("🚗 Reysni boshlash", "start", { trip_id: trip.id }));
      if (trip.direction === "to_center" && trip.status === "on_way") controls.append(button("🏁 Markazga yetdim", "arrive_center", { trip_id: trip.id }, true));
      if (trip.direction === "to_home" && trip.status === "on_way" && trip.items.every(item => ["dropped_off", "absent"].includes(item.status))) controls.append(button("✅ Reysni yakunlash", "finish", { trip_id: trip.id }));
      if (controls.childElementCount) section.append(controls);
    }
    return section;
  }

  function renderDriver(data) {
    content.append(node("h2", "Bugungi reyslar", "section-title"));
    if (!data.trips.length) { content.append(empty("Bugun sizga reys biriktirilmagan.")); return; }
    for (const trip of data.trips) content.append(tripCard(trip, "driver"));
  }

  function renderStaff(data) {
    content.append(node("h2", "Markazga kelish", "section-title"));
    if (!data.center_trips.length) content.append(empty("Markazga kelgan reys yo‘q."));
    else for (const trip of data.center_trips) content.append(tripCard(trip, "staff"));
    content.append(node("h2", "Uyga topshirish", "section-title"));
    if (!data.home_trips.length) content.append(empty("Uyga topshirish reysi yo‘q."));
    else for (const trip of data.home_trips) content.append(tripCard(trip, "staff"));
  }

  function renderAdmin(data) {
    content.replaceChildren();
    const sections = [
      ["home", "🏠 Bugun"], ["trips", "🚐 Reyslar"], ["students", "🎓 O‘quvchilar"],
      ["drivers", "🚗 Haydovchilar"], ["staff", "👥 Xodimlar"], ["villages", "🏘 Mahallalar"],
      ["billing", "💰 To‘lovlar"], ["broadcast", "📢 Xabar"], ["excel", "📊 Excel"],
      ["stats", "📈 Hisobot"], ["backup", "💾 Zaxira"]
    ];
    const tabs = node("nav", null, "admin-tabs");
    tabs.setAttribute("aria-label", "Administrator bo‘limlari");
    for (const [key, label] of sections) {
      const tab = node("button", label, `admin-tab${adminSection === key ? " active" : ""}`);
      tab.type = "button";
      tab.addEventListener("click", () => { adminSection = key; renderAdmin(data); });
      tabs.append(tab);
    }
    content.append(tabs);
    const titles = Object.fromEntries(sections);
    content.append(node("h2", titles[adminSection] || "Admin kabineti", "section-title"));
    const views = {
      home: () => renderAdminHome(data),
      trips: () => renderAdminTrips(data),
      students: () => renderAdminStudents(data),
      drivers: () => renderAdminDrivers(data),
      staff: () => renderAdminStaff(data),
      villages: () => renderAdminVillages(data),
      billing: () => renderAdminBilling(data),
      broadcast: () => renderAdminBroadcast(data),
      excel: () => renderAdminExcel(data),
      stats: () => renderAdminStats(data),
      backup: () => renderAdminBackup(data)
    };
    (views[adminSection] || views.home)();
  }

  function adminAction(label, action, payload = {}, secondary = false) {
    const element = node("button", label, `action-button${secondary ? " secondary" : ""}`);
    element.type = "button";
    element.addEventListener("click", () => {
      const prompt = action === "deactivate_student" ? "O‘quvchini nofaol qilasizmi? Bugungi boshlanmagan reysdan ham chiqariladi."
        : action === "delete_schedule" ? "Shu kunning dars jadvalini o‘chirasizmi?"
        : action === "send_broadcast" ? "Xabarni barcha tanlangan qabul qiluvchilarga yuborishni tasdiqlaysizmi?"
        : action === "save_excel_import" ? "Ko‘rib chiqilgan Excel ma’lumotlarini bazaga saqlaysizmi?" : null;
      if (!prompt) { runAdminAction(action, payload, element); return; }
      confirmMiniApp(prompt).then(confirmed => { if (confirmed) runAdminAction(action, payload, element); });
    });
    return element;
  }

  async function runAdminAction(action, payload, element) {
    if (element) element.disabled = true;
    try {
      const result = await api("/api/miniapp/admin/action", {
        method: "POST", body: JSON.stringify({ action, ...payload })
      });
      showNotice(result.message || "Bajarildi.");
      if (["send_broadcast", "cancel_broadcast"].includes(action)) broadcastPreview = null;
      if (["save_excel_import", "cancel_excel_import"].includes(action)) excelPreview = null;
      await loadDashboard(adminPeriod);
    } catch (error) {
      showNotice(error.message);
      if (element) element.disabled = false;
    }
  }

  function makeField(labelText, name, type = "text", value = "", required = false, options = {}) {
    const wrap = node("label", null, "form-field");
    wrap.append(node("span", labelText, "field-label"));
    const control = node(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") control.type = type;
    control.name = name;
    if (type === "textarea") control.value = value;
    else if (value !== undefined && value !== null) control.value = String(value);
    if (required) control.required = true;
    if (options.placeholder) control.placeholder = options.placeholder;
    if (options.min !== undefined) control.min = String(options.min);
    if (options.max !== undefined) control.max = String(options.max);
    if (options.maxLength) control.maxLength = options.maxLength;
    if (options.step) control.step = String(options.step);
    if (options.accept) control.accept = options.accept;
    control.className = type === "textarea" ? "form-textarea" : "form-input";
    wrap.append(control);
    return wrap;
  }

  function makeSelect(labelText, name, choices, value, required = true) {
    const wrap = node("label", null, "form-field");
    wrap.append(node("span", labelText, "field-label"));
    const select = node("select", null, "form-input");
    select.name = name;
    select.required = required;
    for (const option of choices) {
      const item = node("option", option.label);
      item.value = String(option.value);
      if (String(option.value) === String(value)) item.selected = true;
      select.append(item);
    }
    wrap.append(select);
    return wrap;
  }

  function makeForm(title, submitText, callback) {
    const form = node("form", null, "admin-form");
    if (title) form.append(node("h3", title));
    const submit = node("button", submitText, "action-button");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      submit.disabled = true;
      try { await callback(form); }
      catch (error) { showNotice(error.message); }
      finally { if (form.isConnected) submit.disabled = false; }
    });
    return form;
  }

  function formValue(formData, key) { return String(formData.get(key) || "").trim(); }

  function renderAdminHome(data) {
    const stats = data.stats || {};
    const overview = card("Bugungi holat", `${currentData.date} · ${data.trip_count} ta reys`, "AQL ZIYOSI");
    const grid = node("div", null, "metric-grid");
    for (const [label, value] of [["Faol o‘quvchi", data.active_students], ["Markazga kelgan", stats.arrived_count || 0],
      ["Darsda", stats.in_class_count || 0], ["Kelmagan", stats.absent_count || 0]]) {
      const metric = node("div", null, "metric-tile");
      metric.append(node("span", label), node("strong", value)); grid.append(metric);
    }
    overview.append(grid);
    const quick = node("div", null, "button-row");
    quick.append(adminAction("🧩 Reja tuzish", "create_plan"));
    quick.append(adminAction(`✅ Tasdiqlash (${data.unapproved_count})`, "approve_trips", {}, true));
    overview.append(quick);
    content.append(overview);
    const navCard = card("Boshqaruv markazi", "Barcha admin bo‘limlari shu yerda.");
    const shortcuts = node("div", null, "shortcut-grid");
    for (const [key, label, symbol] of [["students", "O‘quvchilar", "🎓"], ["trips", "Reyslar", "🚐"],
      ["billing", "Oylik to‘lov", "💰"], ["broadcast", "Xabar yuborish", "📢"],
      ["excel", "Excel boshqaruvi", "📊"], ["backup", "Zaxira", "💾"]]) {
      const shortcut = node("button", null, "shortcut-tile"); shortcut.type = "button";
      shortcut.append(node("span", symbol), node("strong", label));
      shortcut.addEventListener("click", () => { adminSection = key; renderAdmin(data); });
      shortcuts.append(shortcut);
    }
    navCard.append(shortcuts); content.append(navCard);
    content.append(node("h3", "Bugun kelmaydiganlar", "section-title"));
    renderAbsences(data.absences);
  }

  function renderAbsences(absences) {
    if (!absences.length) { content.append(empty("Bugun kelmaslik xabari yo‘q.")); return; }
    for (const absence of absences) {
      const section = card(absence.student, absence.village || "Mahalla ko‘rsatilmagan", "Bugun");
      section.append(node("p", absence.reason, "muted")); content.append(section);
    }
  }

  function renderAdminTrips(data) {
    const controls = card("Reyslarni boshqarish", `${data.unapproved_count} ta tasdiq kutmoqda`);
    const actions = node("div", null, "button-row");
    actions.append(adminAction("🧩 Bugungi reja tuzish", "create_plan"));
    actions.append(adminAction("✅ Barcha reyslarni tasdiqlash", "approve_trips", {}, true));
    controls.append(actions); content.append(controls);
    if (!data.trips.length) { content.append(empty("Bugungi reyslar topilmadi. Avval reja tuzing.")); return; }
    for (const trip of data.trips) {
      const section = card(`${trip.direction === "to_center" ? "🏫 Markazga" : "🏠 Uyga"} · ${trip.planned_time}`,
        `Haydovchi: ${trip.driver} · ${trip.items.length} nafar`, statusNames[trip.status] || trip.status);
      for (const item of trip.items) {
        const row = node("div", null, "item-row");
        row.append(node("div", `${item.name} · ${item.village} · ${statusNames[item.status] || item.status}`, "item-copy"));
        section.append(row);
      }
      const controlsRow = node("div", null, "admin-trip-actions");
      if (trip.status === "planned" && !trip.approved) controlsRow.append(adminAction("✅ Tasdiqlash", "approve_trip", { trip_id: trip.id }));
      if (["planned", "approved"].includes(trip.status)) {
        const assign = makeForm("Haydovchi biriktirish", "Biriktirish", form => {
          const fd = new FormData(form);
          return api("/api/miniapp/admin/action", { method: "POST", body: JSON.stringify({ action: "assign_trip_driver", trip_id: trip.id, driver_id: formValue(fd, "driver_id") }) })
            .then(result => { showNotice(result.message); return loadDashboard(adminPeriod); });
        });
        assign.insertBefore(makeSelect("Faol haydovchi", "driver_id", data.drivers.filter(d => d.active).map(d => ({ value: d.id, label: d.name })), ""), assign.querySelector('[type="submit"]'));
        if (data.drivers.some(d => d.active)) controlsRow.append(assign);
        const delay = makeForm("Kechikish e’lon qilish", "Kechiktirish", async form => {
          const minutes = Number(formValue(new FormData(form), "minutes"));
          if (!Number.isInteger(minutes) || minutes < 1 || minutes > 180) throw new Error("1–180 daqiqa oralig‘ida kiriting.");
          await runAdminAction("delay_trip", { trip_id: trip.id, minutes });
        });
        delay.insertBefore(makeField("Daqiqa", "minutes", "number", "10", true, { min: 1, max: 180 }), delay.querySelector('[type="submit"]'));
        controlsRow.append(delay);
      }
      if (!["on_way", "done", "cancelled"].includes(trip.status)) {
        const cancel = node("button", "❌ Reysni bekor qilish", "action-button secondary"); cancel.type = "button";
        cancel.addEventListener("click", () => {
          confirmMiniApp("Reysni bekor qilasizmi? Ota-onalarga xabar yuboriladi.").then(ok => {
            if (ok) runAdminAction("cancel_trip", { trip_id: trip.id }, cancel);
          });
        });
        controlsRow.append(cancel);
      }
      section.append(controlsRow); content.append(section);
    }
  }

  function renderAdminStudents(data) {
    const add = card("Yangi o‘quvchi", "Ism, mahalla, ota-ona telefoni va dars kunlarini kiriting.");
    const form = makeForm("", "O‘quvchini saqlash", async formEl => {
      const fd = new FormData(formEl);
      const weekdays = [...formEl.querySelectorAll('input[name="weekday"]:checked')].map(input => Number(input.value));
      const start = formValue(fd, "start_time"), end = formValue(fd, "end_time");
      if (!weekdays.length) throw new Error("Kamida bitta dars kunini tanlang.");
      await runAdminAction("add_student", {
        full_name: formValue(fd, "full_name"), village_id: Number(formValue(fd, "village_id")),
        parent_phone_1: formValue(fd, "parent_phone_1"), parent_phone_2: formValue(fd, "parent_phone_2"),
        schedules: weekdays.map(weekday => ({ weekday, start_time: start, end_time: end }))
      });
    });
    form.insertBefore(makeField("F.I.Sh", "full_name", "text", "", true, { maxLength: 150 }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeSelect("Mahalla", "village_id", data.villages.map(v => ({ value: v.id, label: `${v.name} · ${v.zone}` })), ""), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("Ota-onaning asosiy telefoni", "parent_phone_1", "tel", "", true, { placeholder: "+998901234567" }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("Qo‘shimcha telefon (ixtiyoriy)", "parent_phone_2", "tel", "", false), form.querySelector('[type="submit"]'));
    const dayWrap = node("fieldset", null, "weekday-picker"); dayWrap.append(node("legend", "Dars kunlari"));
    dayNames.forEach((day, index) => { const label = node("label", null, "check-day"); const input = node("input"); input.type = "checkbox"; input.name = "weekday"; input.value = String(index); label.append(input, node("span", day)); dayWrap.append(label); });
    form.insertBefore(dayWrap, form.querySelector('[type="submit"]'));
    const times = node("div", null, "form-grid");
    times.append(makeField("Boshlanish", "start_time", "time", "09:00", true), makeField("Tugash", "end_time", "time", "11:00", true));
    form.insertBefore(times, form.querySelector('[type="submit"]'));
    add.append(form); content.append(add);
    content.append(node("h3", `Faol o‘quvchilar · ${data.students.length}`, "section-title"));
    if (!data.students.length) { content.append(empty("Faol o‘quvchi yo‘q.")); return; }
    const searchField = makeField("Ism, telefon yoki mahalla bo‘yicha qidirish", "student_search", "search", "", false, { placeholder: "Masalan: Ali yoki Arpaariq" });
    const searchInput = searchField.querySelector("input");
    const studentList = node("div", null, "student-list");
    content.append(searchField, studentList);
    const drawStudents = query => {
      studentList.replaceChildren();
      const needle = String(query || "").trim().toLocaleLowerCase("uz");
      const matching = data.students.filter(student =>
        !needle || `${student.name} ${student.phone_1} ${student.phone_2 || ""} ${student.village}`.toLocaleLowerCase("uz").includes(needle));
      studentList.append(node("p", `${matching.length} ta mos o‘quvchi`, "muted"));
      if (!matching.length) { studentList.append(empty("Mos o‘quvchi topilmadi.")); return; }
      if (matching.length > 50) studentList.append(node("p", "Ko‘p natija topildi. Qidiruvni aniqroq kiriting; hozir dastlabki 50 tasi ko‘rsatilmoqda.", "muted"));
      for (const student of matching.slice(0, 50)) {
      const section = card(student.name, `${student.village} · ${student.zone}`, student.driver_name || "Haydovchi tanlanmagan");
      section.append(node("p", `☎ ${student.phone_1}${student.phone_2 ? ` · ${student.phone_2}` : ""}`, "muted"));
      const details = node("details", null, "manage-details"); details.append(node("summary", "Ma’lumot va jadvalni boshqarish"));
      const edit = makeForm("Ma’lumotni tahrirlash", "Saqlash", formEl => {
        const fd = new FormData(formEl);
        return runAdminAction("update_student", { student_id: student.id, full_name: formValue(fd, "full_name"),
          village_id: Number(formValue(fd, "village_id")), parent_phone_1: formValue(fd, "parent_phone_1"), parent_phone_2: formValue(fd, "parent_phone_2") });
      });
      edit.insertBefore(makeField("F.I.Sh", "full_name", "text", student.name, true, { maxLength: 150 }), edit.querySelector('[type="submit"]'));
      edit.insertBefore(makeSelect("Mahalla", "village_id", data.villages.map(v => ({ value: v.id, label: `${v.name} · ${v.zone}` })), student.village_id), edit.querySelector('[type="submit"]'));
      edit.insertBefore(makeField("Asosiy telefon", "parent_phone_1", "tel", student.phone_1, true), edit.querySelector('[type="submit"]'));
      edit.insertBefore(makeField("Qo‘shimcha telefon", "parent_phone_2", "tel", student.phone_2 || ""), edit.querySelector('[type="submit"]'));
      details.append(edit);
      const driverChoices = [{ value: "auto", label: "♻️ Avtomatik biriktirish" }, ...data.drivers.filter(d => d.active).map(d => ({ value: d.id, label: d.name }))];
      const driverForm = makeForm("O‘quvchiga haydovchi", "Biriktirish", formEl => runAdminAction("assign_student_driver", { student_id: student.id, driver_id: formValue(new FormData(formEl), "driver_id") }));
      driverForm.insertBefore(makeSelect("Haydovchi", "driver_id", driverChoices, student.driver_id || "auto"), driverForm.querySelector('[type="submit"]'));
      details.append(driverForm);
      const scheduleBox = node("div", null, "schedule-list");
      for (const schedule of student.schedules) {
        const rowForm = makeForm(`${dayNames[schedule.weekday]} · jadval`, "Saqlash", formEl => {
          const fd = new FormData(formEl); return runAdminAction("set_schedule", { student_id: student.id, weekday: schedule.weekday, start_time: formValue(fd, "start_time"), end_time: formValue(fd, "end_time") });
        });
        rowForm.classList.add("schedule-edit-form");
        rowForm.insertBefore(makeField("Boshlanish", "start_time", "time", schedule.start, true), rowForm.querySelector('[type="submit"]'));
        rowForm.insertBefore(makeField("Tugash", "end_time", "time", schedule.end, true), rowForm.querySelector('[type="submit"]'));
        rowForm.append(adminAction("🗑 O‘chirish", "delete_schedule", { student_id: student.id, weekday: schedule.weekday }, true));
        scheduleBox.append(rowForm);
      }
      const unusedDays = dayNames.map((name, weekday) => ({ name, weekday })).filter(d => !student.schedules.some(s => s.weekday === d.weekday));
      const addSchedule = makeForm("Yangi dars kunini qo‘shish", "Jadval qo‘shish", formEl => {
        const fd = new FormData(formEl); return runAdminAction("set_schedule", { student_id: student.id, weekday: Number(formValue(fd, "weekday")), start_time: formValue(fd, "start_time"), end_time: formValue(fd, "end_time") });
      });
      addSchedule.insertBefore(makeSelect("Hafta kuni", "weekday", unusedDays.map(d => ({ value: d.weekday, label: d.name })), unusedDays[0]?.weekday ?? ""), addSchedule.querySelector('[type="submit"]'));
      addSchedule.insertBefore(makeField("Boshlanish", "start_time", "time", "09:00", true), addSchedule.querySelector('[type="submit"]'));
      addSchedule.insertBefore(makeField("Tugash", "end_time", "time", "11:00", true), addSchedule.querySelector('[type="submit"]'));
      if (unusedDays.length) scheduleBox.append(addSchedule);
      details.append(scheduleBox);
      details.append(adminAction("🗑 O‘quvchini nofaol qilish", "deactivate_student", { student_id: student.id }, true));
      section.append(details); studentList.append(section);
      }
    };
    searchInput.addEventListener("input", () => drawStudents(searchInput.value));
    drawStudents("");
  }

  function renderAdminDrivers(data) {
    const add = card("Haydovchi qo‘shish", "Telegram ID orqali hisob yarating yoki ma’lumotni yangilang.");
    const form = makeForm("", "Haydovchini saqlash", formEl => {
      const fd = new FormData(formEl); return runAdminAction("add_driver", { telegram_id: Number(formValue(fd, "telegram_id")), full_name: formValue(fd, "full_name"), phone: formValue(fd, "phone") });
    });
    form.insertBefore(makeField("Telegram ID", "telegram_id", "number", "", true, { min: 1 }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("F.I.Sh", "full_name", "text", "", true, { maxLength: 150 }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("Telefon", "phone", "tel", "", true), form.querySelector('[type="submit"]'));
    add.append(form); content.append(add);
    for (const driver of data.drivers) {
      const section = card(driver.name, `${driver.phone} · ID ${driver.telegram_id}`, driver.active ? "Faol" : "Nofaol");
      section.append(adminAction(driver.active ? "⏸ Faolsizlantirish" : "▶️ Faollashtirish", "toggle_driver", { driver_id: driver.id, active: !driver.active }, true));
      content.append(section);
    }
  }

  function renderAdminStaff(data) {
    const add = card("Xodim qo‘shish", "Markazda o‘quvchilarni qabul qiluvchi xodimni kiriting.");
    const form = makeForm("", "Xodimni saqlash", formEl => {
      const fd = new FormData(formEl); return runAdminAction("add_staff", { telegram_id: Number(formValue(fd, "telegram_id")), full_name: formValue(fd, "full_name"), phone: formValue(fd, "phone") });
    });
    form.insertBefore(makeField("Telegram ID", "telegram_id", "number", "", true, { min: 1 }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("F.I.Sh", "full_name", "text", "", true, { maxLength: 150 }), form.querySelector('[type="submit"]'));
    form.insertBefore(makeField("Telefon (ixtiyoriy)", "phone", "tel"), form.querySelector('[type="submit"]'));
    add.append(form); content.append(add);
    for (const staff of data.staff) {
      const section = card(staff.name, `${staff.phone || "Telefon kiritilmagan"} · ID ${staff.telegram_id}`, staff.active ? "Faol" : "Nofaol");
      section.append(adminAction(staff.active ? "⏸ Faolsizlantirish" : "▶️ Faollashtirish", "toggle_staff", { staff_id: staff.id, active: !staff.active }, true));
      content.append(section);
    }
  }

  function renderAdminVillages(data) {
    content.append(node("p", "Mahalla va yo‘nalish ma’lumotlari reys hisobiga ta’sir qiladi. Saqlashdan oldin vaqt va tartibni tekshiring.", "muted"));
    for (const village of data.villages) {
      const section = card(village.name, `${village.zone} zona · ${village.school || "Maktab ko‘rsatilmagan"}`);
      const form = makeForm("Mahalla ma’lumotlari", "Saqlash", formEl => {
        const fd = new FormData(formEl); return runAdminAction("update_village", { village_id: village.id, name: formValue(fd, "name"), school: formValue(fd, "school"), travel_minutes: Number(formValue(fd, "travel_minutes")), route_order: Number(formValue(fd, "route_order")), pickup_point: formValue(fd, "pickup_point") });
      });
      form.insertBefore(makeField("Mahalla", "name", "text", village.name, true, { maxLength: 120 }), form.querySelector('[type="submit"]'));
      form.insertBefore(makeField("Maktab", "school", "text", village.school, false, { maxLength: 120 }), form.querySelector('[type="submit"]'));
      form.insertBefore(makeField("Yo‘l vaqti (daqiqa)", "travel_minutes", "number", village.travel_minutes, true, { min: 1, max: 300 }), form.querySelector('[type="submit"]'));
      form.insertBefore(makeField("Marshrutdagi tartib", "route_order", "number", village.route_order, true, { min: 1, max: 10000 }), form.querySelector('[type="submit"]'));
      form.insertBefore(makeField("Olish / tushirish manzili", "pickup_point", "text", village.pickup_point, false, { maxLength: 200 }), form.querySelector('[type="submit"]'));
      section.append(form); content.append(section);
    }
  }

  function renderAdminBilling(data) {
    const header = card("Oylik davr", "Kerakli oyni tanlang.");
    const periodForm = makeForm("", "Davrni ochish", async formEl => {
      const value = formValue(new FormData(formEl), "period");
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) throw new Error("Oy YYYY-MM shaklida bo‘lsin.");
      await loadDashboard(value);
    });
    periodForm.insertBefore(makeField("Oy", "period", "month", adminPeriod || data.billing_period, true), periodForm.querySelector('[type="submit"]'));
    header.append(periodForm); content.append(header);
    const billingSearch = makeField("O‘quvchi bo‘yicha qidirish", "billing_search", "search", "", false, { placeholder: "F.I.Sh kiriting" });
    const billingSearchInput = billingSearch.querySelector("input");
    const billingList = node("div", null, "billing-list");
    content.append(billingSearch, billingList);
    const drawBilling = query => {
      billingList.replaceChildren();
      const needle = String(query || "").trim().toLocaleLowerCase("uz");
      const matching = data.billing_students.filter(student => !needle || student.full_name.toLocaleLowerCase("uz").includes(needle));
      billingList.append(node("p", `${matching.length} ta o‘quvchi`, "muted"));
      if (!matching.length) { billingList.append(empty("Mos o‘quvchi topilmadi.")); return; }
      if (matching.length > 50) billingList.append(node("p", "Ko‘p natija topildi. Qidiruvni aniqroq kiriting; dastlabki 50 tasi ko‘rsatilmoqda.", "muted"));
      for (const student of matching.slice(0, 50)) {
      const status = student.invoice_id === null ? "Hisob ochilmagan" : student.status === "waived" ? "Bekor qilingan" : student.amount === 0 && student.due === 0 ? "Hozircha qarz yo‘q" : student.due === 0 ? "To‘langan" : `Qarz ${Number(student.due).toLocaleString("uz-UZ")} so‘m`;
      const section = card(student.full_name, `${student.days_per_week} kun/hafta`, status);
      if (student.invoice_id === null) {
        section.append(node("p", "Hisob o‘quvchi qo‘shilganda yoki oy boshida jadval bo‘yicha avtomatik ochiladi. Jadvalni tekshiring.", "muted"));
      } else {
        section.append(node("p", `Hisob: ${Number(student.amount).toLocaleString("uz-UZ")} · Chegirma: ${Number(student.discount).toLocaleString("uz-UZ")} · To‘langan: ${Number(student.paid_total).toLocaleString("uz-UZ")} so‘m`, "muted"));
        if (student.billing_mode === "attendance") section.append(node("p", `Jadval bo‘yicha ${student.lesson_count} dars × ${Number(student.lesson_rate).toLocaleString("uz-UZ")} so‘m`, "muted"));
        if (student.due > 0 && student.status !== "waived") {
          const payment = makeForm("To‘lov kiritish", "To‘lovni saqlash", formEl => {
            const fd = new FormData(formEl); const amount = Number(formValue(fd, "amount"));
            return confirmMiniApp(`${amount.toLocaleString("uz-UZ")} so‘m to‘lovni qayd etasizmi?`).then(confirmed => {
              if (confirmed) return runAdminAction("record_payment", { student_id: student.id, period: adminPeriod, amount, method: formValue(fd, "method") });
            });
          });
          payment.insertBefore(makeField(`Miqdor · qolgan ${Number(student.due).toLocaleString("uz-UZ")} so‘m`, "amount", "number", student.due, true, { min: 1, max: student.due }), payment.querySelector('[type="submit"]'));
          payment.insertBefore(makeSelect("To‘lov turi", "method", [{ value: "cash", label: "💵 Naqd" }, { value: "card", label: "💳 Karta" }], "cash"), payment.querySelector('[type="submit"]'));
          section.append(payment);
          const discount = makeForm("Chegirma qo‘shish", "Chegirmani saqlash", formEl => {
            const fd = new FormData(formEl); const amount = Number(formValue(fd, "amount"));
            return confirmMiniApp(`${amount.toLocaleString("uz-UZ")} so‘m chegirma berilsinmi?`).then(confirmed => {
              if (confirmed) return runAdminAction("apply_discount", { student_id: student.id, period: adminPeriod, amount, reason: formValue(fd, "reason") });
            });
          });
          discount.insertBefore(makeField("Chegirma summasi", "amount", "number", "", true, { min: 1 }), discount.querySelector('[type="submit"]'));
          discount.insertBefore(makeField("Sababi", "reason", "text", "", true, { maxLength: 500 }), discount.querySelector('[type="submit"]'));
          section.append(discount);
        }
      }
      billingList.append(section);
      }
    };
    billingSearchInput.addEventListener("input", () => drawBilling(billingSearchInput.value));
    drawBilling("");
  }

  function renderAdminBroadcast(data) {
    const section = card("Ota-onalar va xodimlarga xabar", "Avval auditoriyani tanlang. Yuborishdan oldin qabul qiluvchilar soni ko‘rsatiladi.");
    const form = node("form", null, "admin-form");
    const audience = makeSelect("Kimga yuboriladi", "audience_type", [
      { value: "all", label: "📢 Barcha ota-onalar" }, { value: "village", label: "🏘 Mahalla bo‘yicha" },
      { value: "zone", label: "📍 Zona bo‘yicha" }, { value: "student", label: "👤 Bitta o‘quvchi ota-onasiga" },
      { value: "drivers", label: "🚗 Haydovchilarga" }, { value: "staff", label: "👥 Xodimlarga" }
    ], "all");
    const target = node("div", null, "audience-target");
    const updateTarget = () => {
      target.replaceChildren();
      const kind = audience.querySelector("select").value;
      const choices = kind === "village" ? data.villages.map(v => ({ value: v.id, label: v.name }))
        : kind === "zone" ? data.zones.map(z => ({ value: z.id, label: `${z.name} zona` }))
        : kind === "student" ? data.students.map(s => ({ value: s.id, label: `${s.name} · ${s.village}` })) : [];
      if (choices.length) target.append(makeSelect("Guruhni tanlang", "audience_value", choices, ""));
    };
    audience.querySelector("select").addEventListener("change", updateTarget); updateTarget();
    form.append(audience, target, makeField("Xabar matni", "text", "textarea", "", false, { maxLength: 4096, placeholder: "Matn yozing (rasm ham qo‘shishingiz mumkin)" }));
    form.append(makeField("Rasm (ixtiyoriy)", "photo", "file", "", false, { accept: "image/jpeg,image/png,image/webp" }));
    const submit = node("button", "🔎 Qabul qiluvchilarni ko‘rish", "action-button"); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true;
      const formData = new FormData(form); formData.set("audience_type", audience.querySelector("select").value);
      try {
        broadcastPreview = await api("/api/miniapp/admin/broadcast-preview", { method: "POST", body: formData });
        showNotice(`${broadcastPreview.recipient_count} ta qabul qiluvchi topildi.`); renderAdmin(data);
      } catch (error) { showNotice(error.message); submit.disabled = false; }
    });
    section.append(form);
    if (broadcastPreview) {
      const preview = node("div", null, "preview-panel");
      preview.append(node("strong", `Yuborishga tayyor · ${broadcastPreview.recipient_count} kishi`));
      if (broadcastPreview.text_preview) preview.append(node("p", broadcastPreview.text_preview, "broadcast-copy"));
      if (broadcastPreview.has_photo) preview.append(node("p", "📷 Rasm ham birga yuboriladi.", "muted"));
      preview.append(node("p", broadcastPreview.recipient_count ? "Xabarni yuborsangiz, ro‘yxatdagi barcha faol qabul qiluvchilarga yetkaziladi." : "Bu auditoriyada qabul qiluvchi topilmadi.", "muted"));
      if (broadcastPreview.recipient_count) preview.append(adminAction("🚀 Xabarni yuborish", "send_broadcast", { batch_id: broadcastPreview.batch_id }));
      preview.append(adminAction("Bekor qilish", "cancel_broadcast", { batch_id: broadcastPreview.batch_id }, true));
      section.append(preview);
    }
    content.append(section);
  }

  async function downloadAdminFile(kind, filename) {
    try {
      const response = await fetch(`/api/miniapp/admin/excel/${kind}`, { headers: { "Authorization": `tma ${tg?.initData || ""}` } });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error || "Fayl olinmadi."); }
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const link = node("a"); link.href = url; link.download = filename; link.hidden = true;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      showNotice("Excel fayli yuklab olindi.");
    } catch (error) { showNotice(error.message); }
  }

  function renderAdminExcel(data) {
    const section = card("Excel bilan ishlash", "Shablonni yuklab oling, to‘ldiring va oldindan ko‘rib tasdiqlang.");
    const actions = node("div", null, "button-row");
    const template = node("button", "📄 Bo‘sh shablon", "action-button secondary"); template.type = "button"; template.addEventListener("click", () => downloadAdminFile("template", "oquvchilar_shabloni.xlsx"));
    const exportBtn = node("button", "📤 O‘quvchilar bazasini yuklab olish", "action-button"); exportBtn.type = "button"; exportBtn.addEventListener("click", () => downloadAdminFile("export", "oquvchilar_bazasi.xlsx"));
    actions.append(template, exportBtn); section.append(actions);
    const form = node("form", null, "admin-form"); form.append(makeField("To‘ldirilgan .xlsx fayl", "file", "file", "", true, { accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    const submit = node("button", "📥 Faylni tekshirish", "action-button"); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        excelPreview = await api("/api/miniapp/admin/excel/import-preview", { method: "POST", body: new FormData(form) });
        showNotice(`Yangi ${excelPreview.new_count} · yangilanadi ${excelPreview.update_count} · xato ${excelPreview.error_count}`); renderAdmin(data);
      } catch (error) { showNotice(error.message); submit.disabled = false; }
    });
    section.append(form);
    if (excelPreview) {
      const result = card("Fayl tahlili", `Yangi: ${excelPreview.new_count} · yangilanadi: ${excelPreview.update_count} · xatolar: ${excelPreview.error_count}`);
      if (excelPreview.students_preview?.length) {
        result.append(node("h3", "Saqlanadigan o‘quvchilar (dastlabki 20 ta)", "section-title"));
        for (const student of excelPreview.students_preview) result.append(node("p", `${student.name} · ${student.village}`, "muted"));
        const totalValid = excelPreview.new_count + excelPreview.update_count;
        if (totalValid > excelPreview.students_preview.length) result.append(node("p", `Yana ${totalValid - excelPreview.students_preview.length} ta o‘quvchi bor.`, "muted"));
      }
      if (excelPreview.errors?.length) {
        for (const item of excelPreview.errors) {
          result.append(node("p", `Qator ${item.row}: ${item.full_name} — ${item.error}`, "error-line"));
          if (item.candidates?.length) result.append(node("p", `O‘xshash mahallalar: ${item.candidates.map(v => v.name || v).join(", ")}`, "muted"));
        }
      } else result.append(node("p", "Faylda xato topilmadi.", "muted"));
      if (excelPreview.can_save) result.append(adminAction("✅ Bazaga saqlash", "save_excel_import", { batch_id: excelPreview.batch_id }));
      result.append(adminAction("❌ Bekor qilish", "cancel_excel_import", { batch_id: excelPreview.batch_id }, true));
      content.append(section, result); return;
    }
    content.append(section);
  }

  function renderAdminStats(data) {
    const stats = data.stats || {};
    const section = card(`Bugungi hisobot · ${currentData.date}`, "Transport holatining qisqa ko‘rinishi");
    const grid = node("div", null, "metric-grid");
    const metrics = [["Jami reys", stats.total_trips], ["Markazga", stats.to_center_trips], ["Uyga", stats.to_home_trips],
      ["Markazga yetgan", stats.arrived_count], ["Darsda", stats.in_class_count], ["Kelmagan", stats.absent_count], ["Faol haydovchi", stats.active_drivers]];
    for (const [label, value] of metrics) { const tile = node("div", null, "metric-tile"); tile.append(node("span", label), node("strong", value ?? 0)); grid.append(tile); }
    section.append(grid); content.append(section); renderAbsences(data.absences);
  }

  function renderAdminBackup(data) {
    const section = card("Ma’lumotlar zaxirasi", "Zaxira SQLite bazasidan olinadi va sozlangan manzilga yuboriladi.", "Qo‘lda");
    section.append(node("p", "Zaxiralash tugmasini bosganda nusxa tayyorlanadi. Botning kunlik avtomatik zaxirasi ham alohida ishlaydi.", "muted"));
    section.append(adminAction("💾 Hozir zaxira olish", "backup_now")); content.append(section);
  }

  async function loadDashboard(period = "") {
    if (typeof period !== "string" || !/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) period = "";
    if (!period && currentData?.role === "admin") period = adminPeriod;
    content.replaceChildren(node("div", null, "loading"));
    const spinner = content.firstElementChild;
    spinner.append(node("span", null, "spinner"), node("span", "Ma’lumot yuklanmoqda…"));
    try {
      const query = period ? `?period=${encodeURIComponent(period)}` : "";
      currentData = await api(`/api/miniapp/dashboard${query}`);
      if (currentData.role === "admin") adminPeriod = currentData.data.billing_period;
      const displayName = String(currentData.name || "").trim();
      document.getElementById("welcome").textContent = displayName ? `Assalomu alaykum, ${displayName}` : "Assalomu alaykum!";
      document.getElementById("role-label").textContent = roleNames[currentData.role] || "Foydalanuvchi";
      document.getElementById("date").textContent = `Bugun · ${currentData.date}`;
      content.replaceChildren();
      if (currentData.role === "parent") renderParent(currentData.data);
      else if (currentData.role === "driver") renderDriver(currentData.data);
      else if (currentData.role === "staff") renderStaff(currentData.data);
      else if (currentData.role === "admin") renderAdmin(currentData.data);
      else content.append(empty("Siz uchun bo‘lim topilmadi."));
      notice.hidden = true;
    } catch (error) {
      content.replaceChildren(empty(error.message));
    }
  }

  document.getElementById("refresh").addEventListener("click", () => loadDashboard());
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.themeParams) {
      for (const [key, value] of Object.entries(tg.themeParams)) {
        const color = value.startsWith("#") ? value : `#${value}`;
        if (/^#[0-9a-f]{6}$/i.test(color)) document.documentElement.style.setProperty(`--tg-theme-${key.replaceAll("_", "-")}`, color);
      }
    }
    loadDashboard();
  } else {
    content.replaceChildren(empty("Bu ilovani Telegram bot ichidan oching."));
  }
})();
