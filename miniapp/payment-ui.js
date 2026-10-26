(() => {
  "use strict";

  const STANDARD_TARIFFS = { 3: 60000, 6: 120000 };

  const money = value => `${Number(value || 0).toLocaleString("uz-UZ")} so‘m`;

  function parseAmount(text) {
    const match = String(text || "").match(/([\d\s]+)\s*so‘m?/i);
    if (!match) return null;
    const value = Number(match[1].replace(/\s/g, ""));
    return Number.isFinite(value) ? value : null;
  }

  function parseLabeledAmount(text, label) {
    const match = String(text || "").match(new RegExp(`${label}\\s*:\\s*([\\d\\s]+)`, "i"));
    if (!match) return null;
    const value = Number(match[1].replace(/\s/g, ""));
    return Number.isFinite(value) ? value : null;
  }

  function makeRow(label, value, modifier = "") {
    const row = document.createElement("div");
    row.className = `payment-summary-row${modifier ? ` ${modifier}` : ""}`;
    const left = document.createElement("span");
    left.textContent = label;
    const right = document.createElement("strong");
    right.textContent = value;
    row.append(left, right);
    return row;
  }

  function redesignBillingCard(card) {
    if (card.dataset.paymentRedesigned === "1") return;

    const head = card.querySelector(":scope > .card-head");
    if (!head) return;

    const subtitle = head.querySelector(":scope .muted")?.textContent || "";
    const daysMatch = subtitle.match(/(\d+)\s*kun\/hafta/i);
    const daysPerWeek = daysMatch ? Number(daysMatch[1]) : null;
    const standardTariff = daysPerWeek ? STANDARD_TARIFFS[daysPerWeek] : null;

    const directParagraphs = [...card.children].filter(element => element.tagName === "P");
    const summaryText = directParagraphs.find(p => p.textContent.includes("Hisob:"))?.textContent || "";
    const tariffText = directParagraphs.find(p => p.textContent.includes("Oylik tarif:"))?.textContent || "";
    const infoText = directParagraphs.find(p => p.textContent.includes("standart tarif yo‘q"))?.textContent || "";

    const invoiceAmount = parseLabeledAmount(summaryText, "Hisob");
    const discount = parseLabeledAmount(summaryText, "Chegirma");
    const paid = parseLabeledAmount(summaryText, "To‘langan");
    const activeTariff = parseAmount(tariffText);
    const remaining = invoiceAmount === null || discount === null || paid === null
      ? null
      : Math.max(0, invoiceAmount - discount - paid);

    const periodMatch = tariffText.match(/davr:\s*([^\n]+)/i);
    const periodText = periodMatch ? periodMatch[1].trim() : "";

    const table = document.createElement("section");
    table.className = "payment-summary-table";
    table.setAttribute("aria-label", "O‘quvchi to‘lov ma’lumotlari");

    table.append(makeRow("Dars jadvali", daysPerWeek ? `${daysPerWeek} kun/hafta` : "Belgilanmagan"));
    table.append(makeRow(
      "Standart tarif",
      standardTariff ? money(standardTariff) : "Mavjud emas",
      standardTariff ? "" : "is-muted"
    ));
    table.append(makeRow(
      "Amaldagi tarif",
      activeTariff !== null ? money(activeTariff) : "Belgilanmagan",
      activeTariff !== null ? "is-active" : "is-warning"
    ));

    if (invoiceAmount !== null) {
      table.append(makeRow("Hisoblangan summa", money(invoiceAmount)));
      table.append(makeRow("Chegirma", discount !== null ? money(discount) : "0 so‘m"));
      table.append(makeRow("To‘langan", paid !== null ? money(paid) : "0 so‘m"));
      table.append(makeRow(
        "Qoldiq",
        remaining !== null ? money(remaining) : "—",
        remaining > 0 ? "is-due" : "is-paid"
      ));
    }

    if (periodText) table.append(makeRow("Hisoblash davri", periodText));

    card.insertBefore(table, directParagraphs[0] || card.querySelector(":scope > .admin-form") || null);

    for (const paragraph of directParagraphs) {
      paragraph.remove();
    }

    const tariffForm = [...card.querySelectorAll(":scope > .admin-form")].find(form => {
      const title = form.querySelector("h3")?.textContent || "";
      return title.includes("To‘liq oylik tarif");
    });

    if (tariffForm) {
      const details = document.createElement("details");
      details.className = "payment-tariff-editor";
      const summary = document.createElement("summary");
      summary.textContent = "✏️ Amaldagi tarifni o‘zgartirish";
      details.append(summary);

      const helper = document.createElement("p");
      helper.className = "payment-tariff-helper";
      helper.textContent = standardTariff
        ? `Standart tarif: ${money(standardTariff)}. Zarur bo‘lsa, o‘quvchi uchun alohida tarif belgilang.`
        : "Bu jadval uchun standart tarif belgilanmagan. O‘quvchi uchun amaldagi tarifni qo‘lda belgilang.";
      details.append(helper);

      const oldTitle = tariffForm.querySelector("h3");
      if (oldTitle) oldTitle.textContent = "Yangi oylik tarif";
      const submit = tariffForm.querySelector('[type="submit"]');
      if (submit) submit.textContent = activeTariff !== null ? "Tarifni saqlash" : "Tarifni belgilash";
      details.append(tariffForm);
      card.append(details);
    } else if (infoText) {
      const note = document.createElement("div");
      note.className = "payment-tariff-note";
      note.textContent = "Bu jadval uchun standart tarif belgilanmagan. Amaldagi tarifni alohida tekshiring.";
      card.append(note);
    }

    const actionForms = [...card.querySelectorAll(":scope > .admin-form")];
    if (actionForms.length) {
      const actions = document.createElement("section");
      actions.className = "payment-actions";
      const title = document.createElement("h4");
      title.textContent = "To‘lov amallari";
      actions.append(title);
      actionForms.forEach(form => actions.append(form));
      card.append(actions);
    }

    card.dataset.paymentRedesigned = "1";
  }

  function redesignBillingCards() {
    document.querySelectorAll(".billing-list > .card").forEach(redesignBillingCard);
  }

  const observer = new MutationObserver(() => {
    window.requestAnimationFrame(redesignBillingCards);
  });

  function init() {
    const content = document.getElementById("content");
    if (!content) return;
    observer.observe(content, { childList: true, subtree: true });
    redesignBillingCards();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
