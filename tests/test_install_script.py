"""Установщик: разбор опций, справка, статическая проверка обоих скриптов.

`install.sh` запускается из интернета под root, поэтому даже разбор опций
стоит на тестах: ошибка здесь означает, что одна команда установки упадёт
у всех сразу.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from xui_monitor_bot.settings import BASE_DIR

INSTALL_SH = BASE_DIR / "install.sh"
XUI_CTL = BASE_DIR / "install" / "xui-ctl"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="нужен bash")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_help_lists_install_options_and_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    for fragment in (
        "--tarball",
        "--prefix",
        "--yes",
        "--no-start",
        "--menu",
        "--uninstall",
        "--purge",
        "TARBALL_URL",
    ):
        assert fragment in result.stdout, fragment


@pytest.mark.skipif(
    os.geteuid() == 0, reason="под root удаление реально сработает — недопустимо"
)
@pytest.mark.parametrize("flag", ["--uninstall", "--purge"])
def test_uninstall_requires_root(flag: str) -> None:
    """Удаление — операция с root-правами: без них только понятный отказ."""
    result = _run(flag)
    assert result.returncode != 0
    assert "Запускать от root" in result.stderr


def test_unknown_option_fails_before_any_system_change() -> None:
    result = _run("--boom")
    assert result.returncode != 0
    assert "Неизвестная опция" in result.stderr
    # Отказ происходит до шагов установки: ни один системный вызов не звучит.
    assert "Проверяем систему" not in result.stdout


def test_tarball_option_requires_a_url() -> None:
    result = _run("--tarball")
    assert result.returncode != 0
    assert "--tarball требует URL" in result.stderr


def test_install_sh_is_syntactically_valid() -> None:
    subprocess.run(["bash", "-n", str(INSTALL_SH)], check=True, timeout=30)


def test_prompts_read_from_tty_when_stdin_is_a_pipe() -> None:
    """Регрессия: при установке `curl … | sudo bash` stdin занят скриптом.

    Без запасного чтения с /dev/tty вопросы молча пропускались и установка
    шла на значениях по умолчанию — без токенов и адреса панели.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "exec 3</dev/tty" in text
    assert "read -r __answer <&$READ_FD" in text
    assert "[ -t 0 ]" in text


def test_print_source_shows_default_repository() -> None:
    """По умолчанию источником должен быть реальный репозиторий, не заглушка."""
    result = _run("--print-source")
    assert result.returncode == 0, result.stderr
    assert "REPO_URL=https://github.com/Hijackery/xui-monitor-bot" in result.stdout
    assert "placeholder=no" in result.stdout


def test_print_source_marks_placeholder_repository() -> None:
    """Страховка: явная заглушка OWNER/REPO должна быть видна сразу."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--print-source"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "REPO_OWNER": "OWNER", "REPO_NAME": "REPO"},
    )
    assert result.returncode == 0, result.stderr
    assert "placeholder=yes" in result.stdout


def test_print_source_resolves_repo_and_ref() -> None:
    """--repo/--ref дают ровно тот адрес, с которого пойдёт установка."""
    result = _run("--repo", "mylogin/xui-monitor-bot", "--ref", "dev", "--print-source")
    assert result.returncode == 0, result.stderr
    assert "REPO_URL=https://github.com/mylogin/xui-monitor-bot" in result.stdout
    assert (
        "TARBALL_URL=https://codeload.github.com/mylogin/xui-monitor-bot"
        "/tar.gz/refs/heads/dev" in result.stdout
    )
    assert "placeholder=no" in result.stdout


def test_print_source_respects_repo_url_env() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--print-source"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "REPO_URL": "https://github.com/someone/bot.git"},
    )
    assert result.returncode == 0, result.stderr
    assert "codeload.github.com/someone/bot/tar.gz" in result.stdout


def test_repo_option_requires_owner_and_name() -> None:
    result = _run("--repo", "no-slash", "--print-source")
    assert result.returncode != 0
    assert "owner/name" in result.stderr


def test_candidates_fall_back_to_main_and_master() -> None:
    """Ветка репозитория может называться иначе — пробуем частые варианты."""
    result = _run("--repo", "me/bot", "--ref", "dev", "--print-source")
    assert result.returncode == 0, result.stderr
    candidates = next(
        line for line in result.stdout.splitlines() if line.startswith("CANDIDATES=")
    )
    assert "/refs/heads/dev" in candidates
    assert "/refs/heads/main" in candidates
    assert "/refs/heads/master" in candidates


def test_github_tarball_download_is_cache_busted() -> None:
    """Регрессия: codeload кэширует архив ветки и отдаёт устаревший снимок.

    Без метки времени установка сразу после обновления репозитория ставила бы
    прошлую версию кода.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert '_fetch_url="${_fetch_url}?t=$(date +%s)"' in text
    assert "*codeload.github.com/*)" in text


def test_installer_supports_archive_checksum() -> None:
    """Установщик исполняется от root: должна быть возможность привязки к версии."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "SOURCE_SHA256" in text
    assert "--sha256" in text
    assert "verify_archive" in text


def _verify_archive_probe(
    tmp_path: Path, expected: str
) -> subprocess.CompletedProcess[str]:
    """Вытаскиваем функцию verify_archive из установщика и запускаем её отдельно."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    start = script.index("verify_archive() {")
    end = script.index("\n}\n", start) + 3
    payload = tmp_path / "src.tar.gz"
    payload.write_bytes(b"payload")
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'fail() { echo "FAIL: $1" >&2; exit 9; }\n'
        'warn() { echo "WARN: $1" >&2; }\n'
        'ok() { echo "OK: $1"; }\n'
        f"{script[start:end]}\n"
        f"SOURCE_SHA256={expected}\n"
        f'verify_archive "{payload}"\n',
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(probe)], capture_output=True, text=True, timeout=30, check=False
    )


def test_checksum_accepts_matching_archive(tmp_path: Path) -> None:
    import hashlib

    digest = hashlib.sha256(b"payload").hexdigest()
    result = _verify_archive_probe(tmp_path, digest)
    assert result.returncode == 0, result.stderr
    assert "совпала" in result.stdout


def test_checksum_rejects_tampered_archive(tmp_path: Path) -> None:
    result = _verify_archive_probe(tmp_path, "0" * 64)
    assert result.returncode == 1
    assert "не совпала" in result.stderr


@pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck не установлен"
)
@pytest.mark.parametrize("script", [INSTALL_SH, XUI_CTL], ids=lambda p: p.name)
def test_scripts_pass_shellcheck(script: Path) -> None:
    """shellcheck — в обязательной обходной карте, не только вручную."""
    result = subprocess.run(
        ["shellcheck", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_keeps_local_copy_for_xui_menu_update() -> None:
    """`xui-menu update` берёт установщик рядом с ботом, а не только из сети."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert '"$SOURCE_DIR/install.sh" "$PREFIX/install.sh"' in text


def test_installer_never_asks_to_dump_env_to_terminal() -> None:
    """Подсказка не должна предлагать вывести .env (в нём токены) на экран."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "grep -v '^#'" not in text
    assert "не пересылайте" in text


def test_prefix_exists_before_env_is_written() -> None:
    """Регрессия: мастер пишет .env до копирования кода.

    На чистой машине каталога /opt/xui-monitor-bot ещё нет, поэтому установщик
    обязан создать его до первого касания .env — иначе установка падает
    на `touch: cannot touch .../.env: No such file or directory`.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    first_mkdir = text.index('mkdir -p "$PREFIX"')
    # Инвариант — про запись, а не про присваивание переменной: каталог обязан
    # существовать до первого касания файла.
    first_write = min(
        text.index('cp "$SOURCE_DIR/.env.example" "$ENV_FILE"'),
        text.index('touch "$ENV_FILE"'),
    )
    assert first_mkdir < first_write, "каталог установки создаётся после записи .env"


def test_incomplete_env_does_not_block_install() -> None:
    """Регрессия: .env после прерванного запуска не должен требовать правки руками.

    На реальной машине установка падала с «В .env пусто PANEL_API_TOKEN» сразу
    после неудачного прогона — вместо этого мастер обязан дозаполнить значения.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "CONFIG_COMPLETE=0" in text
    assert "дозаполняю через мастер" in text
    assert "for _key in TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_IDS PANEL_URL PANEL_API_TOKEN" in text
