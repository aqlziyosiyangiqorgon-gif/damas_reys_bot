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

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Authorization": `tma ${tg?.initData || ""}`,
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.ok) throw new Error(body.error || "Ma’lumot olinmadi. Qayta urinib ko‘ring.");
    return body;
  }

  function renderParent(data) {
    content.append(node("h2", "Farzandlarim", "section-title"));
    if (!data.children.length) {
      content.append(empty("Sizga tasdiqlangan o‘quvchi biriktirilmagan."));
      return;
    }
    for (const child of data.children) {
      const section = card(child.name, child.village || "Mahalla ko‘rsatilmagan");
      section.append(node("div", `📍 ${child.today_status}`, "status-line"));
      const schedules = [...child.schedules].sort((a, b) => a.weekday - b.weekday);
      if (schedules.length) {
        const list = node("div", null, "schedule-list");
        for (const schedule of schedules) {
          const row = node("div", null, "schedule-row");
          row.append(node("span", schedule.day || dayNames[schedule.weekday] || "Kun"));
          row.append(node("strong", `${schedule.start}–${schedule.end}`));
          list.append(row);
        }
        section.append(list);
      } else {
        section.append(node("p", "Dars jadvali kiritilmagan.", "muted"));
      }
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
      section.append(label, reason, report);
      content.append(section);
    }
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
    const summary = card("Bugungi holat", `${currentData.date} · reyslar: ${data.trip_count}`);
    summary.append(node("div", `👥 Faol o‘quvchilar: ${data.active_students}`, "status-line"));
    summary.append(node("p", `⏳ Tasdiqlanmagan reyslar: ${data.unapproved_count}`, "muted"));
    const controls = node("div", null, "button-row");
    controls.append(button("🧩 Bugungi rejani tuzish", "create_plan", {}));
    controls.append(button("✅ Reyslarni tasdiqlash", "approve_trips", {}, true));
    summary.append(controls);
    content.append(summary);
    content.append(node("h2", "Bugungi reyslar", "section-title"));
    if (!data.trips.length) content.append(empty("Bugungi reys rejasi hali tuzilmagan."));
    else for (const trip of data.trips) content.append(tripCard(trip, "admin"));
    content.append(node("h2", "Kelmaslik xabarlari", "section-title"));
    if (!data.absences.length) content.append(empty("Bugun kelmaslik xabari yo‘q."));
    else {
      for (const absence of data.absences) {
        const section = card(absence.student, absence.village);
        section.append(node("p", absence.reason, "muted"));
        content.append(section);
      }
    }
  }

  async function loadDashboard() {
    content.replaceChildren(node("div", null, "loading"));
    const spinner = content.firstElementChild;
    spinner.append(node("span", null, "spinner"), node("span", "Ma’lumot yuklanmoqda…"));
    try {
      currentData = await api("/api/miniapp/dashboard");
      document.getElementById("welcome").textContent = `Assalomu alaykum${currentData.name ? `, ${currentData.name}` : ""}`;
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

  document.getElementById("refresh").addEventListener("click", loadDashboard);
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
