"""Работа без systemd: OpenRC/SysV, фолбэк журнала, деградация метрик.

Бот должен оставаться управляемым там, где systemd нет (Alpine/Gentoo, старые
SysVinit), поэтому проверяем не «на словах», а запуском обёртки с подменённым
менеджером служб и урезанным PATH.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from xui_monitor_bot.settings import BASE_DIR

WRAPPER = BASE_DIR / "install" / "xui-ctl"
INIT_TEMPLATE = BASE_DIR / "install" / "xui-monitor-bot.init"
INSTALL_SH = BASE_DIR / "install.sh"

pytestmark = pytest.mark.skipif(
    os.path.exists("/usr/bin/sh") is False, reason="нужен POSIX shell"
)


def _run_wrapper(
    *args: str, path: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    full_env["PATH"] = path
    if env:
        full_env.update(env)
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=full_env,
    )


def _shim(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# ----------------------------------------------------------- OpenRC backend


def test_restart_uses_rc_service_when_forced(tmp_path: Path) -> None:
    """Без systemd рестарт идёт через rc-service, а не падает."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    log = tmp_path / "calls.log"
    _shim(
        shim_dir,
        "rc-service",
        f'printf "%s\\n" "$*" >> "{log}"\nexit 0\n',
    )

    result = _run_wrapper(
        "restart_service",
        "x-ui",
        path=f"{shim_dir}:{os.environ['PATH']}",
        env={"SVC_BACKEND": "openrc"},
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "x-ui restart" in calls
    assert "x-ui status" in calls  # проверка, что служба поднялась
    assert "перезапущен" in result.stdout


def test_is_active_reports_inactive_from_rc_service(tmp_path: Path) -> None:
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    _shim(shim_dir, "rc-service", "exit 1\n")

    result = _run_wrapper(
        "is_active",
        "x-ui",
        path=f"{shim_dir}:{os.environ['PATH']}",
        env={"SVC_BACKEND": "openrc"},
    )

    assert result.returncode != 0
    assert "inactive" in result.stdout


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    result = _run_wrapper(
        "is_active",
        "x-ui",
        path=os.environ["PATH"],
        env={"SVC_BACKEND": "upstart"},
    )
    assert result.returncode != 0
    assert "не поддерживается" in result.stderr


# ------------------------------------------------- журнал без journalctl


def test_journal_falls_back_to_bot_log_file(tmp_path: Path) -> None:
    """На системах без systemd журнала нет — читаем файл лога бота."""
    minimal = tmp_path / "bin"
    minimal.mkdir()
    for name in ("sh", "grep", "tail", "cat"):
        source = (
            f"/usr/bin/{name}" if Path(f"/usr/bin/{name}").exists() else f"/bin/{name}"
        )
        (minimal / name).symlink_to(source)

    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "2026-10-19 03:12:00 INFO старт\n2026-10-19 03:12:01 ERROR панель недоступна\n",
        encoding="utf-8",
    )

    result = _run_wrapper(
        "journal",
        "x-ui",
        "10",
        path=str(minimal),
        env={"LOG_PATH": str(log_file)},
    )

    assert result.returncode == 0, result.stderr
    assert "панель недоступна" in result.stdout


def test_journal_errors_filters_bot_log(tmp_path: Path) -> None:
    minimal = tmp_path / "bin"
    minimal.mkdir()
    for name in ("sh", "grep", "tail", "cat"):
        source = (
            f"/usr/bin/{name}" if Path(f"/usr/bin/{name}").exists() else f"/bin/{name}"
        )
        (minimal / name).symlink_to(source)

    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "INFO всё хорошо\nERROR что-то сломалось\nINFO снова хорошо\n",
        encoding="utf-8",
    )

    result = _run_wrapper(
        "journal_errors",
        "x-ui",
        "10",
        path=str(minimal),
        env={"LOG_PATH": str(log_file)},
    )

    assert result.returncode == 0, result.stderr
    assert "сломалось" in result.stdout
    assert "всё хорошо" not in result.stdout


# ------------------------------------------------------------- метрики


async def test_service_metrics_degrade_without_systemd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без systemd бот не падает: он честно сообщает, что метрик служб нет."""
    from xui_monitor_bot.collectors import services as services_module

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    collector = services_module.ServiceCollector(("x-ui",))

    with pytest.raises(RuntimeError, match="нет systemd"):
        await collector.collect()


# ------------------------------------------------------- init-скрипт


def test_init_template_is_valid_posix_shell() -> None:
    subprocess.run(["sh", "-n", str(INIT_TEMPLATE)], check=True, timeout=30)


def test_init_template_has_placeholders_and_actions() -> None:
    text = INIT_TEMPLATE.read_text(encoding="utf-8")
    assert "@SERVICE_USER@" in text
    assert "@STATE_DIR@" in text
    for action in ("start)", "stop)", "restart)", "status)"):
        assert action in text, action


def test_installer_supports_init_systems() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    for marker in (
        "INIT=openrc",
        "INIT=sysv",
        "rc-update add",
        "update-rc.d",
        "/etc/init.d/",
        "--init TYPE",
    ):
        assert marker in text, marker
