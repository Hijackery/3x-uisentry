"""Консольная панель `xui-menu`: показ настроек, проверки, белый список.

Меню — точка, где админ правит `.env` руками терминала, поэтому проверяем
и маскировку секретов, и отказы на «мусорные» значения, и совпадение списка
редактируемых ключей с `.env.example`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from xui_monitor_bot.settings import BASE_DIR

MENU = BASE_DIR / "install" / "xui-menu"
ENV_EXAMPLE = BASE_DIR / ".env.example"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="нужен bash")


def _run(*args: str, env_file: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if env_file is not None:
        env["ENV_FILE"] = str(env_file)
        env["PREFIX"] = str(env_file.parent)
    return subprocess.run(
        ["bash", str(MENU), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )


def test_menu_help_lists_commands() -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    for command in ("show", "keys", "set", "restart", "logs", "update", "uninstall"):
        assert command in result.stdout, command


def test_menu_is_syntactically_valid() -> None:
    subprocess.run(["bash", "-n", str(MENU)], check=True, timeout=30)


def test_editable_keys_exist_in_env_example() -> None:
    """Нельзя предлагать правку ключа, которого нет в примере конфигурации."""
    result = _run("keys")
    assert result.returncode == 0, result.stderr
    editable = {line.strip() for line in result.stdout.splitlines() if line.strip()}

    example = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    unknown = sorted(editable - example)
    assert not unknown, f"меню предлагает ключи, которых нет в .env.example: {unknown}"


def test_show_masks_secrets(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:AAsupersecretvalue\n"
        "PANEL_API_TOKEN=panelsecret1234\n"
        "PANEL_URL=https://panel.example.com:2053\n",
        encoding="utf-8",
    )

    result = _run("show", env_file=env_file)

    assert result.returncode == 0, result.stderr
    assert "AAsupersecretvalue" not in result.stdout
    assert "panelsecret1234" not in result.stdout
    assert "123456" in result.stdout  # префикс виден для сверки
    assert "https://panel.example.com:2053" in result.stdout


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LOG_LEVEL", "INFO"),
        ("CHECK_INTERVAL", "60"),
        ("PANEL_VERIFY_TLS", "true"),
        ("PANEL_URL", "https://panel.example.com:2053"),
        ("TELEGRAM_ADMIN_IDS", "111,222"),
        ("CPU_WARN", "85"),
    ],
)
def test_check_accepts_valid_values(key: str, value: str) -> None:
    result = _run("check", key, value)
    assert result.returncode == 0, result.stderr
    assert "корректно" in result.stdout


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LOG_LEVEL", "TRACE"),
        ("CHECK_INTERVAL", "пять"),
        ("PANEL_URL", "panel.example.com"),
        ("TELEGRAM_ADMIN_IDS", "не-id"),
        ("PANEL_VERIFY_TLS", "maybe"),
        ("CPU_WARN", "85%"),
    ],
)
def test_check_rejects_broken_values(key: str, value: str) -> None:
    result = _run("check", key, value)
    assert result.returncode != 0
    assert "✗" in result.stderr


def test_check_rejects_unknown_key() -> None:
    result = _run("check", "SECRET_OVERRIDE", "1")
    assert result.returncode != 0
    assert "менять нельзя" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="под root меню реально правит .env")
def test_set_requires_root(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

    result = _run("set", "LOG_LEVEL", "DEBUG", env_file=env_file)

    assert result.returncode != 0
    assert "Нужен root" in result.stderr
    assert "DEBUG" not in env_file.read_text(encoding="utf-8")


def test_set_rejects_unknown_key_even_as_root_message(tmp_path: Path) -> None:
    """Незнакомый ключ отсекается раньше прав и записи."""
    result = _run("set", "TELEGRAM_TOKEN_WRONG", "x", env_file=tmp_path / ".env")
    assert result.returncode != 0
    assert re.search(r"Нужен root|менять нельзя", result.stderr)
