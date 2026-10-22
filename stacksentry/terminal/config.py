"""Настройки отдельного сервиса терминала.

Терминал живёт в своём процессе и под своим пользователем (stacksentry-term),
чтобы веб-слой не имел доступа к sudo-правилам бота. Конфиг лежит отдельно от
.env бота: /etc/stacksentry/terminal.env (640 root:stacksentry-term).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


class TerminalConfigError(RuntimeError):
    """Конфигурация терминала неполная или небезопасная."""


DEFAULT_ENV_FILE = Path(os.environ.get("TERMINAL_ENV", "/etc/stacksentry/terminal.env"))


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists() and os.access(path, os.R_OK):
        try:
            values = {
                str(key): str(value)
                for key, value in dotenv_values(path).items()
                if value is not None
            }
        except OSError:
            # 600 root-файл: сервисный пользователь читает значения из
            # EnvironmentFile, а не с диска.
            values = {}
    # systemd EnvironmentFile и ручной запуск имеют приоритет над файлом.
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ADMIN_IDS",
        "TERMINAL_SECRET",
        "TERMINAL_TOTP_SECRET",
        "TERMINAL_ACCESS_TOKEN",
        "TERMINAL_ACCESS_USER",
        "TERMINAL_ALLOW_ROOT",
        "TERMINAL_HOST",
        "TERMINAL_PORT",
        "TERMINAL_PUBLIC_ORIGIN",
        "TERMINAL_SESSION_TTL",
        "TERMINAL_IDLE_TIMEOUT",
        "TERMINAL_MAX_SESSIONS",
        "TERMINAL_FRAME_ANCESTORS",
        "TERMINAL_SSH_KEY",
        "TERMINAL_SSH_USER",
        "TERMINAL_SSH_HOST",
        "TERMINAL_SSH_PORT",
        "TERMINAL_SSH_KNOWN_HOSTS",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def _int(values: dict[str, str], key: str, default: int, minimum: int) -> int:
    raw = values.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise TerminalConfigError(f"{key}={raw!r}: ожидается число") from exc
    if value < minimum:
        raise TerminalConfigError(f"{key}={value}: минимум {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class TerminalSettings:
    bot_token: str
    admin_ids: tuple[int, ...]
    secret: str
    totp_secret: str
    access_token: str
    access_user: int
    allow_root: bool
    shell_user: str
    shell_home: str
    host: str
    port: int
    public_origin: str
    session_ttl: int
    idle_timeout: int
    max_sessions: int
    frame_ancestors: str
    ssh_key: str
    ssh_user: str
    ssh_host: str
    ssh_port: int
    ssh_known_hosts: str

    @classmethod
    def load(cls, path: Path | None = None) -> TerminalSettings:
        values = _read_env(path or DEFAULT_ENV_FILE)
        bot_token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        if ":" not in bot_token:
            raise TerminalConfigError("TELEGRAM_BOT_TOKEN не задан или неверен")
        raw_ids = values.get("TELEGRAM_ADMIN_IDS", "").replace(";", ",")
        try:
            admin_ids = tuple(
                int(item.strip()) for item in raw_ids.split(",") if item.strip()
            )
        except ValueError as exc:
            raise TerminalConfigError("TELEGRAM_ADMIN_IDS: нужны числовые ID") from exc
        if not admin_ids:
            raise TerminalConfigError(
                "TELEGRAM_ADMIN_IDS пуст — терминал не для кого открывать"
            )
        secret = values.get("TERMINAL_SECRET", "").strip()
        if len(secret) < 32:
            raise TerminalConfigError(
                "TERMINAL_SECRET: нужен случайный секрет ≥ 32 символов"
            )
        totp_secret = (
            values.get("TERMINAL_TOTP_SECRET", "").strip().upper().replace(" ", "")
        )
        if not totp_secret:
            raise TerminalConfigError("TERMINAL_TOTP_SECRET не задан")
        try:
            base64.b32decode(totp_secret + "=" * ((8 - len(totp_secret) % 8) % 8))
        except Exception as exc:  # noqa: BLE001 — любая ошибка декодирования
            raise TerminalConfigError("TERMINAL_TOTP_SECRET: неверный base32") from exc
        access_token = values.get("TERMINAL_ACCESS_TOKEN", "").strip()
        if access_token and len(access_token) < 32:
            raise TerminalConfigError("TERMINAL_ACCESS_TOKEN: минимум 32 символа")
        access_user = _int(values, "TERMINAL_ACCESS_USER", admin_ids[0], 1)
        if access_user not in admin_ids:
            raise TerminalConfigError(
                "TERMINAL_ACCESS_USER должен быть в TELEGRAM_ADMIN_IDS"
            )
        public_origin = values.get("TERMINAL_PUBLIC_ORIGIN", "").strip().rstrip("/")
        if public_origin and not public_origin.startswith("https://"):
            raise TerminalConfigError(
                "TERMINAL_PUBLIC_ORIGIN должен начинаться с https://"
            )
        ssh_key = values.get("TERMINAL_SSH_KEY", "").strip()
        if ssh_key and not Path(ssh_key).is_file():
            raise TerminalConfigError(f"TERMINAL_SSH_KEY: файл не найден ({ssh_key})")
        ssh_known_hosts = values.get(
            "TERMINAL_SSH_KNOWN_HOSTS", "/etc/stacksentry/terminal-ssh/known_hosts"
        ).strip()
        if ssh_key and not Path(ssh_known_hosts).is_file():
            raise TerminalConfigError(
                f"TERMINAL_SSH_KNOWN_HOSTS: файл не найден ({ssh_known_hosts})"
            )
        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
            secret=secret,
            totp_secret=totp_secret,
            access_token=access_token,
            access_user=access_user,
            allow_root=values.get("TERMINAL_ALLOW_ROOT", "").strip()
            in {"1", "true", "yes"},
            shell_user=values.get("TERMINAL_SHELL_USER", "stacksentry-shell").strip()
            or "stacksentry-shell",
            shell_home=values.get(
                "TERMINAL_SHELL_HOME", "/var/lib/stacksentry-shell"
            ).strip()
            or "/var/lib/stacksentry-shell",
            host=values.get("TERMINAL_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int(values, "TERMINAL_PORT", 8788, 1),
            public_origin=public_origin,
            session_ttl=_int(values, "TERMINAL_SESSION_TTL", 30, 5),
            idle_timeout=_int(values, "TERMINAL_IDLE_TIMEOUT", 900, 60),
            max_sessions=_int(values, "TERMINAL_MAX_SESSIONS", 1, 1),
            frame_ancestors=values.get(
                "TERMINAL_FRAME_ANCESTORS",
                "'self' https://web.telegram.org https://*.telegram.org",
            ).strip()
            or "'self' https://web.telegram.org https://*.telegram.org",
            ssh_key=ssh_key,
            ssh_user=values.get("TERMINAL_SSH_USER", "stacksentry-term").strip()
            or "stacksentry-term",
            ssh_host=values.get("TERMINAL_SSH_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            ssh_port=_int(values, "TERMINAL_SSH_PORT", 22, 1),
            ssh_known_hosts=ssh_known_hosts,
        )
