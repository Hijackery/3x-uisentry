(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const main = $("main"), sub = $("sub"), toast = $("toast");
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  const initData = hash.get("tgWebAppData") || "";
  const theme = hash.get("tgWebAppThemeParams");
  if (theme) {
    try {
      const data = JSON.parse(decodeURIComponent(theme));
      const map = { bg_color: "--tg-theme-bg-color", text_color: "--tg-theme-text-color",
        hint_color: "--tg-theme-hint-color", button_color: "--tg-theme-button-color",
        secondary_bg_color: "--tg-theme-secondary-bg-color" };
      for (const [key, value] of Object.entries(map)) {
        if (data[key]) document.documentElement.style.setProperty(value, data[key]);
      }
    } catch (_) { /* тема не критична */ }
  }
  const BASE_TABS = [
    ["overview", "Обзор"], ["services", "Службы"], ["panel", "Панель"],
    ["traffic", "Трафик"], ["subs", "Подписки"], ["history", "История"],
  ];
  let TABS = BASE_TABS.slice();
  let active = "overview";
  let authAttempted = false;
  let snapshot = null;
  let timer = null;
  let terminalOrigin = "";
  let metaLoaded = false;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
  const fmtBytes = (value) => {
    let n = Number(value || 0), unit = 0;
    const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit += 1; }
    return `${n.toFixed(n >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
  };
  const tone = (percent) => percent >= 90 ? "bad" : percent >= 75 ? "warn" : "ok";
  function showToast(text) {
    toast.textContent = text;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
  }
  function renderTabs() {
    $("tabs").innerHTML = TABS.map(([key, label]) =>
      `<button class="tab ${key === active ? "active" : ""}" data-tab="${key}">${label}</button>`
    ).join("");
    $("tabs").querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => { active = button.dataset.tab; renderTabs(); render(); });
    });
  }
  function metricCard(label, value, percent) {
    return `<div class="card"><h3>${label}</h3>
      <div class="metric">${value}</div>
      <div class="bar"><i style="width:${Math.min(100, percent)}%;background:var(--${tone(percent)})"></i></div>
    </div>`;
  }
  function statusTone() {
    if (!snapshot) return ["warn", "нет данных"];
    const down = snapshot.services.filter((item) => !item.active);
    if (down.length) return ["bad", `не работают: ${down.map((s) => s.name).join(", ")}`];
    if (snapshot.warnings.length) return ["warn", `замечаний: ${snapshot.warnings.length}`];
    return ["ok", "всё в норме"];
  }
  function renderOverview() {
    const [toneName, verdict] = statusTone();
    const s = snapshot.system;
    const parts = [`<div class="card"><div class="verdict ${toneName}">${esc(verdict)}</div>
      <div class="muted">${snapshot.age.toFixed(0)} с назад · ${snapshot.services.length} служб · ${snapshot.inbounds.length} inbound</div></div>`];
    if (s) {
      parts.push(`<div class="grid">
        ${metricCard("CPU", `${s.cpu_percent.toFixed(0)}%`, s.cpu_percent)}
        ${metricCard("RAM", `${s.mem_percent.toFixed(0)}%`, s.mem_percent)}
        ${metricCard("Диск", `${s.disk_percent.toFixed(0)}%`, s.disk_percent)}
      </div>`);
    }
    parts.push(`<div class="card"><h3>Службы</h3>${servicesRows(6)}</div>`);
    if (snapshot.warnings.length) {
      parts.push(`<div class="card"><h3>Требует внимания</h3>${
        snapshot.warnings.slice(0, 8).map((w) => `<div class="row"><span>${esc(w)}</span></div>`).join("")
      }</div>`);
    }
    return parts.join("");
  }
  function servicesRows(limit = 50) {
    if (!snapshot.services.length) return `<div class="empty">службы не найдены</div>`;
    return snapshot.services.slice(0, limit).map((item) =>
      `<div class="row"><span class="name"><i class="dot ${item.active ? "ok" : "bad"}"></i> ${esc(item.name)}</span>
       <span class="chip">${esc(item.active_state || item.sub_state || "—")}</span></div>`
    ).join("");
  }
  function renderServices() {
    return `<div class="card"><h3>Службы (${snapshot.services.length})</h3>${servicesRows()}</div>`;
  }
  function renderPanel() {
    const p = snapshot.panel;
    if (p.disabled) {
      return `<div class="card"><h3>Панель</h3><div class="empty">API не настроен (monitor-only).<br>Службы и VPN наблюдаются.</div></div>`;
    }
    return `<div class="card"><h3>Панель</h3>
      <div class="row"><span>Доступность</span><span class="chip">${p.reachable ? "отвечает" : "недоступна"}</span></div>
      <div class="row"><span>Версия</span><span>${esc(p.panel_version || "—")}</span></div>
      <div class="row"><span>xray</span><span>${esc(p.xray_version || "—")} · ${esc(p.xray_state || "—")}</span></div>
      <div class="row"><span>API-токен</span><span class="chip">${p.api_authorized ? "принят" : "не принят"}</span></div>
      ${p.error ? `<div class="row"><span>${esc(p.error)}</span></div>` : ""}
    </div>`;
  }
  function renderTraffic() {
    const cards = snapshot.inbounds.map((item) =>
      `<div class="card"><h3>${esc(item.remark)} · ${esc(item.protocol)}:${item.port}</h3>
        <div class="metric">${fmtBytes(item.up_bytes + item.down_bytes)}</div>
        <div class="muted">клиентов ${item.clients}${item.total_bytes ? ` · лимит ${fmtBytes(item.total_bytes)}` : ""}</div>
      </div>`).join("");
    const top = [...snapshot.subscriptions].sort((a, b) => b.used_bytes - a.used_bytes).slice(0, 10);
    return `${cards || `<div class="empty">inbound'ы недоступны</div>`}
      <div class="card"><h3>Топ клиентов</h3>${top.length ? top.map((item) =>
        `<div class="row"><span class="name">${esc(item.email || item.sub_id || "—")}</span>
         <span class="chip">${fmtBytes(item.used_bytes)}${item.total_bytes ? ` / ${fmtBytes(item.total_bytes)}` : ""}</span></div>`
      ).join("") : `<div class="empty">нет данных</div>`}</div>`;
  }
  function renderSubs() {
    if (!snapshot.subscriptions.length) return `<div class="card"><div class="empty">подписок нет или API не настроен</div></div>`;
    return `<div class="card"><h3>Подписки (${snapshot.subscriptions.length})</h3>${
      snapshot.subscriptions.map((item) => {
        const percent = item.total_bytes > 0 ? Math.min(100, item.used_bytes / item.total_bytes * 100) : 0;
        const days = item.expiry_time_ms > 0 ? Math.ceil((item.expiry_time_ms - Date.now()) / 86400000) : null;
        return `<div class="row"><span class="name">${esc(item.email || item.sub_id || "—")}
          <span class="muted">${days === null ? "без срока" : days < 0 ? "истекла" : `${days} дн.`}</span></span>
          <span class="chip">${fmtBytes(item.used_bytes)}${item.total_bytes ? ` / ${fmtBytes(item.total_bytes)}` : ""}</span></div>
          <div class="bar"><i style="width:${percent}%;background:var(--${percent >= 90 ? "bad" : "accent"})"></i></div>`;
      }).join("")
    }</div>`;
  }
  async function renderHistory() {
    const rows = await api("/api/history?hours=24");
    if (!rows.length) return `<div class="card"><div class="empty">истории пока нет</div></div>`;
    const series = (key) => rows.map((row) => Number(row[key] || 0));
    const bars = (values) => `<div class="spark">${values.map((v) =>
      `<i style="height:${Math.min(100, Math.max(2, v))}%"></i>`).join("")}</div>`;
    return `<div class="card"><h3>CPU</h3>${bars(series("cpu"))}</div>
      <div class="card"><h3>RAM</h3>${bars(series("mem"))}</div>
      <div class="card"><h3>Диск</h3>${bars(series("disk"))}</div>`;
  }
  function renderTerminal() {
    if (!terminalOrigin) {
      return `<div class="card"><div class="empty">Терминал не настроен.<br>
        Установите его: <code>sudo bash install.sh --terminal --root-terminal</code></div></div>`;
    }
    if (main.querySelector("iframe.terminal-frame")) return null;
    const url = new URL(terminalOrigin);
    if (initData) url.hash = `tgWebAppData=${encodeURIComponent(initData)}`;
    return `<iframe class="terminal-frame" src="${esc(url.href)}"
      title="Терминал сервера" allow="clipboard-read; clipboard-write"></iframe>`;
  }
  async function render() {
    if (!snapshot) return;
    const [toneName, verdict] = statusTone();
    sub.textContent = verdict;
    sub.className = `muted ${toneName}`;
    if (active === "overview") main.innerHTML = renderOverview();
    else if (active === "services") main.innerHTML = renderServices();
    else if (active === "panel") main.innerHTML = renderPanel();
    else if (active === "traffic") main.innerHTML = renderTraffic();
    else if (active === "subs") main.innerHTML = renderSubs();
    else if (active === "terminal") {
      const frame = renderTerminal();
      if (frame !== null) main.innerHTML = frame;
    } else main.innerHTML = `<div class="skeleton">Загружаю историю…</div>`;
    if (active === "history") {
      try { main.innerHTML = await renderHistory(); }
      catch (error) { main.innerHTML = `<div class="card"><div class="empty">${esc(error.message)}</div></div>`; }
    }
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    if (response.status === 401) throw new Error("Откройте приложение из Telegram.");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function loadMeta() {
    if (metaLoaded) return;
    try {
      const meta = await api("/api/meta");
      metaLoaded = true;
      terminalOrigin = meta.terminal_origin || "";
      TABS = terminalOrigin
        ? [...BASE_TABS, ["terminal", "Терминал"]]
        : BASE_TABS.slice();
      if (active === "terminal" && !terminalOrigin) active = "overview";
      renderTabs();
    } catch (_) { /* без терминала Mini App продолжает работать */ }
  }
  async function load() {
    try {
      snapshot = await api("/api/status");
      await loadMeta();
      await render();
    } catch (error) {
      if (String(error.message).includes("Telegram")) {
        main.innerHTML = `<div class="card"><h3>Нужен вход</h3>
          <div class="empty">Откройте это приложение кнопкой в Telegram — 
          вход по подписи Telegram, без паролей.</div></div>`;
        if (initData && !authAttempted) {
          authAttempted = true;
          try {
            await api("/api/session", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ initData }),
            });
            await load();
          } catch (retryError) { showToast(retryError.message); }
        }
      } else {
        main.innerHTML = `<div class="card"><div class="empty">${esc(error.message)}</div></div>`;
      }
    }
  }
  $("refresh").addEventListener("click", () => { load(); showToast("Обновлено"); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
  renderTabs();
  load();
  timer = setInterval(() => { if (!document.hidden) load(); }, 20000);
  window.addEventListener("beforeunload", () => clearInterval(timer));
})();
