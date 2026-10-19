"""Проверки действий: подтверждения, белый список, кулдаун, argv для sudo."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

import pytest

from xui_monitor_bot.actions import (
    ACTION_REBOOT,
    ACTION_RESTART_SERVICE,
    ActionError,
    ActionManager,
)
from xui_monitor_bot.collectors.panel import PanelClient
from xui_monitor_bot.models import CommandResult
from xui_monitor_bot.settings import Settings


def ok_result(action: str = "test") -> CommandResult:
    return CommandResult(
        action=action,
        command="test",
        returncode=0,
        stdout="ok",
        stderr="",
        duration=0.1,
    )


def manager(
    make_settings: Callable[..., Settings], **overrides: object
) -> ActionManager:
    return ActionManager(make_settings(**overrides))


def fake_runner(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Подменяет запуск команд и записывает argv."""
    calls: list[list[str]] = []

    async def _run(argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        return ok_result(argv[1] if len(argv) > 1 else "test")

    monkeypatch.setattr("xui_monitor_bot.actions.run_command", _run)
    return calls


def test_confirmation_token_is_single_use(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    pending = actions.request(ACTION_RESTART_SERVICE, "рестарт", "x-ui")

    assert actions.consume(pending.token) is not None
    assert actions.consume(pending.token) is None


def test_expired_confirmation_is_rejected(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings, confirm_ttl=10)
    pending = actions.request(ACTION_REBOOT, "перезагрузка")
    actions._pending[pending.token] = dataclasses.replace(
        pending, created_at=time.time() - 30
    )

    with pytest.raises(ActionError, match="Время подтверждения истекло"):
        actions.consume(pending.token)


def test_cancelled_action_is_forgotten(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    pending = actions.request(ACTION_REBOOT, "перезагрузка")
    actions.cancel(pending.token)
    assert actions.consume(pending.token) is None


async def test_unknown_service_is_refused(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    with pytest.raises(ActionError, match="не входит в список"):
        await actions.restart_service("nginx")


async def test_command_outside_allowlist_is_refused(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings, command_allowlist=("free -h",))
    with pytest.raises(ActionError, match="не разрешена"):
        await actions.run_allowed_command("rm -rf /")


async def test_empty_allowlist_disables_custom_commands(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings, command_allowlist=())
    with pytest.raises(ActionError, match="запрещены"):
        await actions.run_allowed_command("free -h")


async def test_cooldown_blocks_repeated_action(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    actions = manager(make_settings, action_cooldown=30)
    calls = fake_runner(monkeypatch)

    await actions.restart_service("x-ui")
    with pytest.raises(ActionError, match="недавно"):
        await actions.restart_service("x-ui")

    assert calls == [
        ["sudo", "-n", "--", "/usr/local/bin/xui-ctl", "restart_service", "x-ui"]
    ]


async def test_wrapper_argv_and_custom_prefix(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    actions = manager(make_settings, sudo_wrapper="/opt/xui-ctl")
    calls = fake_runner(monkeypatch)
    result = await actions.restart_service("x-ui")

    assert calls == [["sudo", "-n", "--", "/opt/xui-ctl", "restart_service", "x-ui"]]
    assert result.ok


async def test_allowlisted_command_is_one_argv_element(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """sudo-правило разрешает ``xui-ctl run_command *``, поэтому команда с
    пробелами должна приходить одним аргументом, а не распыляться по argv."""
    actions = manager(make_settings, command_allowlist=("journalctl --disk-usage",))
    calls = fake_runner(monkeypatch)

    await actions.run_allowed_command("journalctl --disk-usage")
    assert calls == [
        [
            "sudo",
            "-n",
            "--",
            "/usr/local/bin/xui-ctl",
            "run_command",
            "journalctl_--disk-usage",
        ]
    ]


async def test_execute_dispatches_by_action(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    actions = manager(make_settings)
    calls = fake_runner(monkeypatch)
    pending = actions.request(ACTION_RESTART_SERVICE, "рестарт", "x-ui")

    result = await actions.execute(pending)
    assert result.ok
    assert calls[0][4] == "restart_service"


async def test_execute_rejects_unknown_action(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    pending = actions.request("что-то", "неизвестно")
    with pytest.raises(ActionError, match="Неизвестное действие"):
        await actions.execute(pending)


async def test_journal_goes_through_privileged_wrapper(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Журнал читается через sudo-обёртку.

    Сервисный пользователь не входит в systemd-journal/adm, поэтому прямой
    `journalctl` вернул бы пустоту или отказ прав — журнал был бы не виден.
    """
    actions = manager(make_settings)
    calls = fake_runner(monkeypatch)
    output = await actions.journal("x-ui", 20)

    assert calls[0][:5] == [
        "sudo",
        "-n",
        "--",
        "/usr/local/bin/xui-ctl",
        "journal",
    ]
    assert calls[0] == [
        "sudo",
        "-n",
        "--",
        "/usr/local/bin/xui-ctl",
        "journal",
        "x-ui",
    ]  # без аргумента-числа
    assert output == "ok"


async def test_journal_does_not_trip_action_cooldown(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Просмотр журнала — не опасное действие: кулдаун на него не распространяется."""
    actions = manager(make_settings, action_cooldown=3600)
    fake_runner(monkeypatch)
    await actions.journal("x-ui", 10)
    await actions.journal("x-ui", 10)  # не должно бросить ActionError


async def test_journal_refuses_foreign_unit(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    with pytest.raises(ActionError, match="не входит в список"):
        await actions.journal("sshd", 10)


def test_cooldown_remaining_time_is_reported(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings, action_cooldown=60)
    actions._last_run[ACTION_REBOOT] = time.time() - 10
    with pytest.raises(ActionError, match="подождите"):
        actions._check_cooldown(ACTION_REBOOT)


async def test_restart_xray_goes_through_panel_api(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Рестарт xray не требует sudo: панель умеет сама по API-токену."""
    actions = manager(make_settings)
    called = False

    async def fake_restart(_self: PanelClient) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    async def fake_close(_self: PanelClient) -> None:
        return None

    monkeypatch.setattr(
        "xui_monitor_bot.collectors.panel.PanelClient.restart_xray", fake_restart
    )
    monkeypatch.setattr(
        "xui_monitor_bot.collectors.panel.PanelClient.close", fake_close
    )

    result = await actions.restart_xray()
    assert called
    assert result.ok
    assert "restartXrayService" in result.command


async def test_apt_status_uses_exact_kind(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Статус запрашивается точной подкомандой: в sudoers нет шаблонов."""
    from xui_monitor_bot.actions import ACTION_UPGRADE

    actions = manager(make_settings)
    calls = fake_runner(monkeypatch)
    await actions.apt_status(ACTION_UPGRADE)

    assert calls == [
        [
            "sudo",
            "-n",
            "--",
            "/usr/local/bin/xui-ctl",
            "apt_status",
            "upgrade",
        ]
    ]


async def test_apt_status_rejects_unknown_action(
    make_settings: Callable[..., Settings],
) -> None:
    actions = manager(make_settings)
    with pytest.raises(ActionError):
        await actions.apt_status("restart_service")
