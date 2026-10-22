"""Общие константы и форматирование экранов StackSentry."""

from __future__ import annotations

import html
from typing import Final

from .models import Severity

BAR_WIDTH: Final = 10

MAX_INLINE_ROWS: Final = 12

SUBSCRIPTIONS_PAGE: Final = 10

PANEL_TITLES: Final = {
    "3x-ui": "3X-UI",
    "marzban": "Marzban",
    "marzneshin": "Marzneshin",
    "pasarguard": "PasarGuard",
    "remnawave": "Remnawave",
    "s-ui": "S-UI",
    "hiddify": "Hiddify",
    "outline": "Outline",
}

PANEL_RESTART: Final = frozenset(
    {"3x-ui", "marzban", "marzneshin", "pasarguard", "s-ui"}
)

PANEL_INBOUNDS: Final = frozenset({"3x-ui"})


def panel_title(panel_type: str) -> str:
    return PANEL_TITLES.get(panel_type, panel_type or "панель")


def esc(value: object) -> str:
    return html.escape(str(value))


def fmt_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024 or unit == "PB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"


def fmt_uptime(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"
    return f"{hours // 24} д {(hours % 24)} ч"


def progress_bar(percent: float, width: int = BAR_WIDTH) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = round(percent / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def severity_icon(severity: Severity) -> str:
    return {
        Severity.ok: "✅",
        Severity.warning: "⚠️",
        Severity.critical: "🔴",
        Severity.unknown: "⚪",
    }[severity]


def level(percent: float, warn: float, crit: float) -> Severity:
    if percent >= crit:
        return Severity.critical
    if percent >= warn:
        return Severity.warning
    return Severity.ok


_STACK_TITLES = {
    "panel": "Панели",
    "proxy": "Прокси и ядра",
    "vpn": "VPN",
    "mesh": "Mesh/VPN",
}

LOG_WITH_MORE = frozenset({"os_logs", "xui_logs", "kernel_logs"})

LOG_WITH_ERRORS = frozenset({"os_logs", "xui_logs", "kernel_logs", "dmesg"})

PACKAGE_MANAGER_TITLES: Final = {
    "apt": "APT (Debian/Ubuntu)",
    "dnf": "DNF (Fedora/RHEL)",
    "yum": "YUM (CentOS/RHEL)",
    "zypper": "Zypper (openSUSE)",
    "pacman": "Pacman (Arch)",
    "apk": "APK (Alpine)",
    "xbps": "XBPS (Void)",
    "emerge": "Portage (Gentoo)",
    "eopkg": "eopkg (Solus)",
    "pkg": "pkg (FreeBSD)",
}

PKG_UPDATE_LABELS: Final = {
    "apt": "🔄 Обновить список пакетов (apt update)",
    "dnf": "🔄 Обновить индексы (dnf makecache)",
    "yum": "🔄 Обновить индексы (yum makecache)",
    "zypper": "🔄 Обновить индексы (zypper refresh)",
    "pacman": "🔄 Обновить индексы (pacman -Sy)",
    "apk": "🔄 Обновить индексы (apk update)",
    "xbps": "🔄 Обновить индексы (xbps-install -S)",
    "emerge": "🔄 Обновить индексы (emerge --sync)",
    "eopkg": "🔄 Обновить индексы (eopkg update-repo)",
    "pkg": "🔄 Обновить индексы (pkg update)",
}

PKG_UPGRADE_LABELS: Final = {
    "apt": "📦 Обновить систему (full-upgrade)",
    "dnf": "📦 Обновить систему (dnf upgrade)",
    "yum": "📦 Обновить систему (yum update)",
    "zypper": "📦 Обновить систему (zypper update)",
    "pacman": "📦 Обновить систему (pacman -Syu)",
    "apk": "📦 Обновить систему (apk upgrade)",
    "xbps": "📦 Обновить систему (xbps-install -Su)",
    "emerge": "📦 Обновить систему (emerge -uDN @world)",
    "eopkg": "📦 Обновить систему (eopkg upgrade)",
    "pkg": "📦 Обновить систему (pkg upgrade)",
}

COMMAND_HELP: dict[str, tuple[str, str]] = {
    "os_logs": ("Логи", "журнал ОС: предупреждения (100 строк)"),
    "os_errors": ("Логи", "только ошибки ОС"),
    "kernel_logs": ("Логи", "сообщения ядра"),
    "xui_logs": ("Логи", "журнал службы x-ui"),
    "dmesg": ("Логи", "кольцевой буфер ядра с временем"),
    "journalctl --disk-usage": ("Логи", "сколько места занимает журнал"),
    "sockets": ("Сеть", "слушающие TCP/UDP-порты"),
    "net_summary": ("Сеть", "сводка сокетов"),
    "addresses": ("Сеть", "IP-адреса интерфейсов"),
    "routes": ("Сеть", "таблица маршрутов"),
    "failed_units": ("Службы", "упавшие systemd-юниты"),
    "xui_service": ("Службы", "статус службы x-ui"),
    "x-ui status": ("Службы", "статус панели через x-ui"),
    "systemctl list-timers --all": ("Службы", "таймеры systemd"),
    "uname": ("Система", "версия ядра и ОС"),
    "cpu_info": ("Система", "информация о процессоре"),
    "block_devices": ("Система", "диски и разделы"),
    "time_status": ("Система", "время и часовой пояс"),
    "logged_in": ("Система", "кто сейчас вошёл"),
    "boot_time": ("Система", "время последней загрузки"),
    "upgradable": ("Обновления", "пакеты, доступные к обновлению"),
    "upgrade_sim": ("Обновления", "симуляция apt upgrade"),
    "top_cpu": ("Ресурсы", "процессы по CPU и памяти"),
    "vmstat": ("Ресурсы", "нагрузка, память, swap"),
    "log_size": ("Ресурсы", "размер /var/log"),
    "inodes": ("Ресурсы", "использование inode"),
    "df -h": ("Ресурсы", "место на дисках"),
    "free -h": ("Ресурсы", "память и swap"),
    "uptime": ("Ресурсы", "аптайм и load average"),
}

_COMMAND_GROUPS = (
    "Логи",
    "Сеть",
    "Службы",
    "Система",
    "Обновления",
    "Ресурсы",
    "Прочее",
)
