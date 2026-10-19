"""Регрессии по итогам ревью: подтверждения, таймауты, отмена, база, рендер.

Каждый тест здесь соответствует конкретной найденной ошибке — так при будущем
рефакторинге видно, что именно было сломано и зачем это защищено.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from telegram.error import RetryAfter

from xui_monitor_bot import ui
from xui_monitor_bot.actions import ActionError, ActionManager
from xui_monitor_bot.app import _send_with_retry
from xui_monitor_bot.collectors.panel import PanelClient
from xui_monitor_bot.command import run_command
from xui_monitor_bot.models import CommandResult
from xui_monitor_bot.settings import ConfigError, Settings, load_settings
from xui_monitor_bot.storage import Storage, StorageError

# ------------------------------------------------------ подтверждения


def test_foreign_attempt_does_not_destroy_confirmation(
    make_settings: Callable[..., Settings],
) -> None:
    """Раньше чужое нажатие удаляло токен и ломало подтверждение админа."""
    actions = ActionManager(make_settings())
    pending = actions.request("reboot", "перезагрузка", owner_id=111)

    with pytest.raises(ActionError, match="другой администратор"):
        actions.consume(pending.token, user_id=222)

    assert actions.consume(pending.token, user_id=111) is not None


def test_cancel_reports_whether_it_had_anything_to_cancel(
    make_settings: Callable[..., Settings],
) -> None:
    actions = ActionManager(make_settings())
    pending = actions.request("reboot", "перезагрузка", owner_id=111)

    assert actions.cancel(pending.token) is True
    assert actions.cancel(pending.token) is False


async def test_restart_xray_respects_cooldown(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Раньше рестарт xray шёл мимо кулдауна и его можно было спамить."""

    class FakePanel:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def restart_xray(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("xui_monitor_bot.actions.PanelClient", FakePanel)
    actions = ActionManager(make_settings(action_cooldown=3600))

    await actions.restart_xray()
    with pytest.raises(ActionError, match="недавно"):
        await actions.restart_xray()


async def test_command_uses_configured_timeout(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """COMMAND_TIMEOUT из .env раньше игнорировался для произвольных команд."""
    seen: dict[str, float] = {}

    async def fake_run(
        argv: list[str], *, timeout: float, action: str
    ) -> CommandResult:
        seen["timeout"] = timeout
        return CommandResult(
            action=action,
            command=" ".join(argv),
            returncode=0,
            stdout="ok",
            stderr="",
            duration=0.0,
        )

    monkeypatch.setattr("xui_monitor_bot.actions.run_command", fake_run)
    actions = ActionManager(
        make_settings(command_allowlist=("free -h",), command_timeout=42)
    )

    await actions.run_allowed_command("free -h")
    assert seen["timeout"] == 42.0


# ------------------------------------------------ процессы: таймаут и отмена


class _FakeProcess:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.killed = False
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            return b"", b""
        if self.mode == "cancel":
            raise asyncio.CancelledError
        if self.mode == "asyncio-timeout":
            # На Python < 3.11 это отдельный класс, не встроенный TimeoutError.
            raise asyncio.TimeoutError
        await asyncio.sleep(30)
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _patch_process(monkeypatch: pytest.MonkeyPatch, process: _FakeProcess) -> None:
    async def factory(*argv: object, **kwargs: object) -> _FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)


async def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess("hang")
    _patch_process(monkeypatch, process)

    result = await run_command(["dummy"], timeout=0.05)

    assert result.returncode == 124
    assert process.killed is True


async def test_asyncio_timeout_error_is_also_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """На Python 3.10 asyncio бросал собственный TimeoutError.

    Ловить только встроенный было недостаточно: на 3.10 таймаут улетал наружу,
    команда не прерывалась, а дочерний процесс продолжал работать.
    """
    process = _FakeProcess("asyncio-timeout")
    _patch_process(monkeypatch, process)

    result = await run_command(["dummy"], timeout=30)

    assert result.returncode == 124
    assert process.killed is True


async def test_cancellation_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остановка бота не должна оставлять работать дочерние процессы (apt и др.)."""
    process = _FakeProcess("cancel")
    _patch_process(monkeypatch, process)

    task = asyncio.create_task(run_command(["dummy"], timeout=30))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True


# --------------------------------------------------------------- flood control


async def test_flood_control_is_waited_out() -> None:
    """RetryAfter раньше просто терял сообщение; теперь ждём и повторяем."""
    calls = {"n": 0}

    async def sender(**kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryAfter(0)

    await _send_with_retry(sender, text="x")
    assert calls["n"] == 2


async def test_too_long_flood_control_does_not_hang() -> None:
    """Если Telegram просит ждать дольше MAX_FLOOD_WAIT — не подвисаем."""

    async def sender(**kwargs: object) -> None:
        raise RetryAfter(timedelta(seconds=120))

    with pytest.raises(RetryAfter):
        await _send_with_retry(sender, text="x")


# ------------------------------------------------------------ база данных


def test_busy_timeout_is_configured(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.db")
    try:
        value = storage.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert value == 5000
    finally:
        storage.close()


def test_corrupted_database_reports_clearly(tmp_path: Path) -> None:
    path = tmp_path / "broken.db"
    path.write_bytes(b"this is definitely not a sqlite database")

    with pytest.raises(StorageError, match="повреждена"):
        Storage(path)


# ------------------------------------------------------------------ рендер


def test_broken_metric_rows_do_not_crash_render() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT NULL AS bucket, NULL AS cpu, 'мусор' AS mem, 30 AS disk"
    ).fetchall()

    text = ui.render_history(rows, title="Срез")

    assert "—" in text
    assert "30%" in text
    conn.close()


def test_broken_audit_rows_do_not_crash_render() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT NULL AS ts, NULL AS telegram_id, 'reboot' AS action, 1 AS ok"
    ).fetchall()

    text = ui.render_audit(rows)

    assert "reboot" in text
    assert "—" in text
    conn.close()


# --------------------------------------------- пиннинг сертификата панели


def _capture_httpx_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Подменяет httpx.AsyncClient, чтобы увидеть аргументы подключения.

    Патчим именно модуль httpx: panel.py обращается к нему по имени, поэтому
    подмена видна и там (и снимается автоматически после теста).
    """
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return captured


def test_panel_uses_custom_ca_bundle(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PANEL_CA_BUNDLE должен доходить до httpx как путь к CA-файлу."""
    bundle = tmp_path / "panel-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    captured = _capture_httpx_kwargs(monkeypatch)

    PanelClient(make_settings(panel_ca_bundle=str(bundle)))._session()  # noqa: SLF001

    assert captured["verify"] == str(bundle)


def test_panel_verifies_tls_by_default(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_httpx_kwargs(monkeypatch)

    PanelClient(make_settings())._session()  # noqa: SLF001

    assert captured["verify"] is True


def test_missing_ca_bundle_is_rejected(tmp_path: Path) -> None:
    """Опечатка в пути должна падать при старте, а не при первом запросе."""
    path = tmp_path / ".env"
    path.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:AArealish\n"
        "TELEGRAM_ADMIN_IDS=42\n"
        "PANEL_URL=https://panel.example.com\n"
        "PANEL_API_TOKEN=token\n"
        "PANEL_CA_BUNDLE=/nowhere/panel-ca.pem\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="PANEL_CA_BUNDLE"):
        load_settings(path)


def test_nonstandard_bot_api_is_warned(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    """Подменённый TELEGRAM_API_URL уводит токен бота — это должно быть видно."""
    from xui_monitor_bot.app import _warn_on_unusual_api

    settings = make_settings(telegram_api_url="http://127.0.0.1:8081")
    with caplog.at_level(logging.WARNING, logger="xui_monitor_bot.app"):
        _warn_on_unusual_api(settings)

    assert "неофициальный Bot API" in caplog.text


def test_official_bot_api_is_not_warned(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    from xui_monitor_bot.app import _warn_on_unusual_api

    with caplog.at_level(logging.WARNING, logger="xui_monitor_bot.app"):
        _warn_on_unusual_api(make_settings())

    assert "неофициальный" not in caplog.text


# ------------------------------------------------- обновление с phased


async def test_phased_upgrade_goes_through_privileged_wrapper(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка phased-обновления обязана идти через ту же обёртку и allowlist."""
    seen: list[list[str]] = []

    async def fake_run(
        argv: list[str], *, timeout: float, action: str
    ) -> CommandResult:
        seen.append(list(argv))
        return CommandResult(
            action=action,
            command=" ".join(argv),
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
        )

    monkeypatch.setattr("xui_monitor_bot.actions.run_command", fake_run)
    actions = ActionManager(make_settings())

    await actions.apt_upgrade_phased()

    assert seen[0][0] == "/usr/local/bin/xui-ctl"
    assert seen[0][1] == "apt_upgrade_phased"
