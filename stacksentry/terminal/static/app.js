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

  // Telegram Mini App передаёт initData в hash фрагменте — без внешних скриптов.
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  const initData = hash.get("tgWebAppData") || "";
  if (initData) {
    tokenRow.hidden = true;
  }

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

  async function loadHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      if (data.root) rootRow.hidden = false;
    } catch (_) {
      /* health не критичен */
    }
  }

  function sendResize() {
    if (!socket || socket.readyState !== WebSocket.OPEN || !term) return;
    socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }

  function startTerminal() {
    login.hidden = true;
    wrap.hidden = false;
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
      fit.fit();
      term.focus();
      term.onData((data) => {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send(data);
      });
      term.onResize(sendResize);
    }
    fit.fit();
    sendResize();
    term.focus();
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
