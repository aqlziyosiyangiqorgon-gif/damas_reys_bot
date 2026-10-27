(() => {
  "use strict";

  function findHomeCard() {
    return [...document.querySelectorAll(".card")].find(card => {
      const title = card.querySelector(":scope > .card-head h3")?.textContent?.trim();
      return title === "Bugungi holat";
    });
  }

  function makeStep(number, title, text) {
    const item = document.createElement("div");
    item.className = "plan-step";
    const badge = document.createElement("span");
    badge.className = "plan-step-number";
    badge.textContent = String(number);
    const copy = document.createElement("div");
    copy.className = "plan-step-copy";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const body = document.createElement("span");
    body.textContent = text;
    copy.append(heading, body);
    item.append(badge, copy);
    return item;
  }

  function simplifyHome() {
    const card = findHomeCard();
    if (!card || card.dataset.planUiReady === "1") return;

    const grid = card.querySelector(":scope > .metric-grid");
    if (grid) {
      const tiles = [...grid.children];
      if (tiles[2]) tiles[2].remove();
      if (tiles[1]) {
        const label = tiles[1].querySelector("span");
        if (label) label.textContent = "Markazga kelgan";
      }
    }

    const quick = card.querySelector(":scope > .button-row");
    if (quick) {
      const buttons = [...quick.querySelectorAll("button")];
      if (buttons[0]) {
        buttons[0].textContent = "🧩 Bugungi reja tuzish";
        buttons[0].classList.add("plan-primary");
      }
      if (buttons[1]) {
        buttons[1].textContent = buttons[1].textContent.replace("Tasdiqlash", "Reyslarni tasdiqlash");
        buttons[1].classList.add("plan-approve");
      }
    }

    const guide = document.createElement("section");
    guide.className = "plan-guide";
    const heading = document.createElement("div");
    heading.className = "plan-guide-head";
    const title = document.createElement("h3");
    title.textContent = "Bugungi reys rejasini qanday tuzaman?";
    const hint = document.createElement("p");
    hint.textContent = "Asosan 3 ta ish qilasiz. Qolganini tizim o‘zi hisoblaydi.";
    heading.append(title, hint);
    guide.append(heading);
    guide.append(makeStep(1, "Reja tuzish", "Tizim bugungi dars jadvali bo‘yicha reyslarni avtomatik yaratadi."));
    guide.append(makeStep(2, "Haydovchi biriktirish", "Reyslar bo‘limiga kirib, har bir reysga faol haydovchini tanlaysiz."));
    guide.append(makeStep(3, "Tasdiqlash", "Hammasi to‘g‘ri bo‘lsa, reyslarni tasdiqlaysiz. Shundan keyin haydovchi ishlaydi."));

    card.insertAdjacentElement("afterend", guide);
    card.dataset.planUiReady = "1";
  }

  const observer = new MutationObserver(() => window.requestAnimationFrame(simplifyHome));

  function init() {
    const content = document.getElementById("content");
    if (!content) return;
    observer.observe(content, { childList: true, subtree: true });
    simplifyHome();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
