(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const login = $("login");
  const wrap = $("terminal-wrap");
  const errorEl = $("error");
  const connectBtn = $("connect");
  const totpEl = $("totp");
  const tokenEl = $("token");
  const rootEl = $("root");
  const rootRow = $("root-row");
  const tokenRow = $("token-row");
  const modeEl = $("mode");
  const connEl = $("conn");
  const setupEl = $("setup");
  const setupQr = $("setup-qr");
  const setupSecret = $("setup-secret");

  // initData приходит из Telegram (hash) или от родительского Mini App через
  // postMessage. В URL его не оставляем — это одноразовый админ-креденшел.
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  let initData = hash.get("tgWebAppData") || "";
  if (initData) {
    tokenRow.hidden = true;
    history.replaceState(null, "", location.pathname + location.search);
  }
  const parentOrigin = (() => {
    try {
      return new URL(document.referrer).origin;
    } catch (_) {
      return "";
    }
  })();
  window.addEventListener("message", (event) => {
    if (!event.data || event.data.type !== "stacksentry-init") return;
    if (parentOrigin && event.origin !== parentOrigin) return;
    initData = String(event.data.initData || "").slice(0, 8192);
    if (initData) {
      tokenRow.hidden = true;
      loadSetup();
    }
  });

  let socket = null;
  let term = null;
  let fit = null;
  let rootSession = false;

  function setError(text) {
    errorEl.textContent = text || "";
  }

  function setConnected(ok, text) {
    connEl.textContent = text;
    connEl.classList.toggle("down", !ok);
  }

  async function loadSetup() {
    if (!initData || !setupEl) return;
    try {
      const response = await fetch("/api/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData }),
      });
      if (!response.ok) return;
      const data = await response.json();
      setupQr.innerHTML = String(data.qr_svg || "");
      setupSecret.textContent = String(data.secret || "");
      setupEl.hidden = !data.qr_svg;
    } catch (_) {
      /* Настройка не критична: обычный вход с уже добавленным кодом работает. */
    }
  }

  async function loadHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      if (data.root) rootRow.hidden = false;
      if (data.setup) await loadSetup();
    } catch (_) {
      /* health не критичен */
    }
  }

  function sendResize() {
    if (!socket || socket.readyState !== WebSocket.OPEN || !term) return;
    socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }

  function startTerminal() {
    wrap.hidden = false;
    try {
      if (!window.Terminal || !window.FitAddon) {
        throw new Error("Не загрузился xterm.js. Обновите страницу.");
      }
      if (!term) {
        term = new window.Terminal({
          cursorBlink: true,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
          fontSize: 14,
          theme: {
            background: "#0b0f14",
            foreground: "#e6edf3",
            cursor: "#4cc2ff",
            selectionBackground: "#223041",
          },
          scrollback: 5000,
        });
        fit = new window.FitAddon.FitAddon();
        term.loadAddon(fit);
        term.open($("terminal"));
        term.onData((data) => {
          if (socket && socket.readyState === WebSocket.OPEN) socket.send(data);
        });
        term.onResize(sendResize);
      }
      login.hidden = true;
      fit.fit();
      sendResize();
      term.focus();
    } catch (error) {
      wrap.hidden = true;
      login.hidden = false;
      setError(error.message || "Не удалось запустить терминал.");
      connectBtn.disabled = false;
    }
  }

  function stopTerminal(text) {
    if (socket) {
      socket.onclose = null;
      socket.close();
      socket = null;
    }
    wrap.hidden = true;
    login.hidden = false;
    setConnected(false, text || "отключено");
    connectBtn.disabled = false;
  }

  async function connect() {
    setError("");
    const totp = (totpEl.value || "").replace(/\D/g, "");
    if (totp.length !== 6) {
      setError("Введите 6-значный код.");
      return;
    }
    connectBtn.disabled = true;
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          initData,
          token: initData ? "" : tokenEl.value.trim(),
          totp,
          root: !rootRow.hidden && rootEl.checked,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Не удалось войти.");
      }
      rootSession = Boolean(data.root);
      if (setupEl) setupEl.hidden = true;
      modeEl.textContent = rootSession ? "root" : "shell";
      modeEl.classList.toggle("root", rootSession);
      startTerminal();
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/ws?ticket=${encodeURIComponent(data.ticket)}`);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        setConnected(true, "подключено");
        sendResize();
        term.focus();
      };
      socket.onmessage = (event) => {
        term.write(new Uint8Array(event.data));
      };
      socket.onclose = () => {
        setConnected(false, "сессия завершена");
        stopTerminal("сессия завершена");
      };
      socket.onerror = () => {
        setError("Ошибка соединения с терминалом.");
      };
    } catch (error) {
      setError(error.message || "Ошибка входа.");
      connectBtn.disabled = false;
    }
  }

  connectBtn.addEventListener("click", connect);
  totpEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect();
  });
  $("disconnect").addEventListener("click", () => stopTerminal("вы вышли"));
  window.addEventListener("resize", () => {
    if (fit && term) {
      fit.fit();
      sendResize();
    }
  });

  loadHealth();
})();
