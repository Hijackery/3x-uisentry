"""Форматирование сообщений и клавиатуры."""

from __future__ import annotations

import html
import sqlite3
import time
from datetime import datetime, timezone
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .alerts import Finding
from .models import (
    InboundSummary,
    MonitorSnapshot,
    Severity,
    Subscription,
    SystemMetrics,
)
from .settings import Settings

BAR_WIDTH: Final = 10
MAX_INLINE_ROWS: Final = 12
#: Подписок на странице: больше — уже нечитаемо с телефона.
SUBSCRIPTIONS_PAGE: Final = 10


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


# ------------------------------------------------------------------ отчёты


def render_overview(snapshot: MonitorSnapshot, settings: Settings) -> str:
    """Главный отчёт: сервер, сервисы, панель, трафик одной сводкой."""
    lines: list[str] = ["🖥 <b>Состояние сервера</b>"]

    metrics = snapshot.system
    if metrics is None:
        lines.append("Метрики системы недоступны")
    else:
        lines.append(render_system(metrics, settings))

    lines.append(render_services(snapshot))
    lines.append(render_panel(snapshot))
    lines.append(render_traffic(snapshot))

    if snapshot.warnings:
        lines.append("\n⚠️ <b>Замечания</b>")
        lines.extend(f"• {esc(item)}" for item in snapshot.warnings)

    lines.append(f"\n<i>обновлено {time.strftime('%H:%M:%S')}</i>")
    return "\n".join(lines)


def render_system(metrics: SystemMetrics, settings: Settings) -> str:
    """Строки метрик с полосами загрузки и цветом порога."""

    def row(label: str, percent: float, warn: float, crit: float) -> str:
        icon = severity_icon(level(percent, warn, crit))
        return f"{label} {progress_bar(percent)} {icon} {percent:.1f}%"

    used_mem = metrics.mem_total_bytes * metrics.mem_percent / 100
    tcp = metrics.tcp_connections if metrics.tcp_connections >= 0 else "н/д"
    cpu = row("CPU ", metrics.cpu_percent, settings.cpu_warn, settings.cpu_crit)
    mem = row("RAM ", metrics.mem_percent, settings.mem_warn, settings.mem_crit)
    disk = row("Диск", metrics.disk_percent, settings.disk_warn, settings.disk_crit)
    sent = fmt_bytes(metrics.net_sent_bytes)
    recv = fmt_bytes(metrics.net_recv_bytes)
    return "\n".join(
        (
            cpu,
            f"{mem} ({fmt_bytes(used_mem)} занято)",
            f"{disk} · свободно {fmt_bytes(metrics.disk_free_bytes)}",
            f"Swap {metrics.swap_percent:.1f}%",
            f"Load {metrics.load1:.2f} · соединений TCP: {tcp}",
            f"Сеть ↑ {sent} / ↓ {recv}",
            f"Аптайм {fmt_uptime(metrics.uptime_seconds)}",
        )
    )


def render_services(snapshot: MonitorSnapshot) -> str:
    if not snapshot.services:
        return "\n⚙️ <b>Сервисы</b>\nне удалось прочитать состояние"
    lines = ["", "⚙️ <b>Сервисы</b>"]
    for service in snapshot.services:
        icon = "🟢" if service.active else "🔴"
        detail = (
            f"MainPID {service.main_pid}" if service.active else service.active_state
        )
        restarts = f" · рестартов {service.restarts}" if service.restarts else ""
        lines.append(
            f"{icon} <code>{esc(service.name)}</code> — {esc(service.sub_state)}"
            f" · {esc(detail)} · RAM {fmt_bytes(service.memory_bytes)}"
            f" · CPU {service.cpu_percent:.1f}%{restarts}"
        )
    return "\n".join(lines)


def render_panel(snapshot: MonitorSnapshot) -> str:
    panel = snapshot.panel
    if not panel.reachable:
        reason = esc(panel.error or "нет ответа")
        return f"\n🌐 <b>Панель 3X-UI</b>\n🔴 недоступна: {reason}"
    latency = f"{panel.latency_ms:.0f} мс" if panel.latency_ms is not None else "—"
    api = "токен принят" if panel.api_authorized else "⚠️ токен не принят"
    return (
        "\n🌐 <b>Панель 3X-UI</b>\n"
        f"🟢 отвечает (HTTP {panel.status_code}, {latency})\n"
        f"Версия панели: {esc(panel.panel_version or '?')} · xray: "
        f"{esc(panel.xray_version or '?')} [{esc(panel.xray_state or '?')}]\n"
        f"API: {api}"
    )


def render_traffic(snapshot: MonitorSnapshot) -> str:
    if not snapshot.inbounds:
        return "\n📊 <b>Inbounds</b>\nнет данных (нужен токен со scope node-sync/admin)"
    lines = [
        "",
        "📊 <b>Inbounds</b>",
        f"Включено {sum(1 for item in snapshot.inbounds if item.enabled)} из "
        f"{len(snapshot.inbounds)} · клиентов {snapshot.active_clients} · "
        f"трафик {fmt_bytes(snapshot.total_used_bytes)}",
    ]
    for inbound in snapshot.inbounds[:MAX_INLINE_ROWS]:
        icon = "🟢" if inbound.enabled else "⚪"
        limit = f" из {fmt_bytes(inbound.total_bytes)}" if inbound.total_bytes else ""
        lines.append(
            f"{icon} {esc(inbound.remark)} — {esc(inbound.protocol)}:{inbound.port} · "
            f"клиентов {inbound.clients} · ↑{fmt_bytes(inbound.up_bytes)} "
            f"↓{fmt_bytes(inbound.down_bytes)}{limit}"
        )
    if len(snapshot.inbounds) > MAX_INLINE_ROWS:
        lines.append(f"… ещё {len(snapshot.inbounds) - MAX_INLINE_ROWS}")
    return "\n".join(lines)


def ordered_subscriptions(
    subscriptions: list[Subscription], soon_days: int
) -> list[Subscription]:
    """Порядок показа: проблемные (истёкшие, исчерпанные, скоро истекают) — вперёд."""
    expired = [item for item in subscriptions if item.is_expired]
    depleted = [item for item in subscriptions if item.is_depleted]
    soon = [
        item
        for item in subscriptions
        if not item.is_expired
        and item.expires_in_days is not None
        and item.expires_in_days <= soon_days
    ]
    interesting = expired + depleted + soon
    seen: set[str] = set()
    ordered: list[Subscription] = []
    for item in interesting:
        if item.email not in seen:
            seen.add(item.email)
            ordered.append(item)
    ordered.extend(item for item in subscriptions if item.email not in seen)
    return ordered


def render_subscriptions(
    subscriptions: list[Subscription],
    *,
    soon_days: int = 7,
    limit: int = SUBSCRIPTIONS_PAGE,
    offset: int = 0,
) -> str:
    if not subscriptions:
        return "Подписок нет или список недоступен для этого токена."

    expired = [item for item in subscriptions if item.is_expired]
    depleted = [item for item in subscriptions if item.is_depleted]
    soon = [
        item
        for item in subscriptions
        if not item.is_expired
        and item.expires_in_days is not None
        and item.expires_in_days <= soon_days
    ]
    ordered = ordered_subscriptions(subscriptions, soon_days)
    page = ordered[offset : offset + limit]

    lines = [
        "🪪 <b>Подписки</b>",
        f"всего {len(subscriptions)} · истёкших {len(expired)} · "
        f"трафик исчерпан {len(depleted)} · истекают ≤{soon_days} дн. {len(soon)}",
    ]
    if offset > 0 or offset + len(page) < len(ordered):
        lines.append(
            f"страница {offset // limit + 1}: показано {offset + 1}–"
            f"{offset + len(page)} из {len(ordered)}"
        )
    lines.append("")
    lines.extend(_render_subscription(item) for item in page)
    if offset + len(page) < len(ordered):
        lines.append(f"\n… ещё {len(ordered) - offset - len(page)} — кнопка «📄 Ещё»")
    return "\n".join(lines)


def subscriptions_menu(
    offset: int,
    *,
    has_more: bool,
) -> InlineKeyboardMarkup:
    """Постраничная навигация по подпискам плюс выход в главное меню."""
    rows: list[list[InlineKeyboardButton]] = []
    pager: list[InlineKeyboardButton] = []
    if offset > 0:
        pager.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"subs:{max(0, offset - SUBSCRIPTIONS_PAGE)}",
            )
        )
    if has_more:
        pager.append(
            InlineKeyboardButton(
                "📄 Ещё", callback_data=f"subs:{offset + SUBSCRIPTIONS_PAGE}"
            )
        )
    if pager:
        rows.append(pager)
    rows.append([home_button()])
    return InlineKeyboardMarkup(rows)


def _render_subscription(item: Subscription) -> str:
    flags = []
    if item.is_expired:
        flags.append("❌ истекла")
    if item.is_depleted:
        flags.append("🈳 трафик исчерпан")
    if not item.enabled:
        flags.append("⏸ выключена")
    if item.expires_in_days is not None and 0 < item.expires_in_days <= 7:
        flags.append(f"⌛ {item.expires_in_days:.1f} дн.")

    traffic = fmt_bytes(item.used_bytes)
    if item.total_gb:
        traffic += f" из {item.total_gb} ГБ ({item.used_percent:.0f}%)"

    suffix = f" · {' · '.join(flags)}" if flags else ""
    email = esc(item.email)
    remark = esc(item.inbound_remark)
    return f"• <code>{email}</code> [{remark}] — {traffic}{suffix}"


def render_inbound_detail(inbound: InboundSummary) -> str:
    return (
        f"🔌 <b>{esc(inbound.remark)}</b>\n"
        f"Протокол: {esc(inbound.protocol)} · порт {inbound.port}\n"
        f"Состояние: {'включён' if inbound.enabled else 'выключен'}\n"
        f"Клиентов: {inbound.clients}\n"
        f"Отправлено: {fmt_bytes(inbound.up_bytes)}\n"
        f"Принято: {fmt_bytes(inbound.down_bytes)}\n"
        f"Всего: {fmt_bytes(inbound.used_bytes)}\n"
        f"Лимит: {fmt_bytes(inbound.total_bytes) if inbound.total_bytes else '—'}"
    )


def render_finding(finding: Finding) -> str:
    text = f"{severity_icon(finding.severity)} <b>{esc(finding.title)}</b>"
    if finding.detail:
        text += f"\n{esc(finding.detail)}"
    if finding.resolve:
        text += f"\n→ {esc(finding.resolve)}"
    return text


def _cell(value: object, fmt: str = "{}") -> str:
    """Число из базы может оказаться NULL или мусором: не роняем весь отчёт."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return fmt.format(number)


def _stamp(value: object, fmt: str) -> str:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return "—"
    return moment.strftime(fmt)


def render_history(rows: list[sqlite3.Row], *, title: str) -> str:
    if not rows:
        return f"{title}\nПока нет накопленных измерений."
    lines = [f"📈 <b>{esc(title)}</b>", "почасовое среднее (UTC) · CPU / RAM / диск"]
    for row in rows:
        stamp = _stamp(row["bucket"], "%d.%m %H:00")
        lines.append(
            f"{stamp} — {_cell(row['cpu'], '{:.0f}')}% / "
            f"{_cell(row['mem'], '{:.0f}')}% / {_cell(row['disk'], '{:.0f}')}%"
        )
    return "\n".join(lines)


def render_audit(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "Журнал действий пуст."
    lines = ["🧾 <b>Журнал действий</b>"]
    for row in rows:
        stamp = _stamp(row["ts"], "%d.%m %H:%M:%S")
        mark = "✅" if row["ok"] else "❌"
        who = row["telegram_id"] or "—"
        lines.append(f"{mark} {stamp} · {esc(row['action'])} · id {who}")
    return "\n".join(lines)


# --------------------------------------------------------------- клавиатуры


def home_button() -> InlineKeyboardButton:
    """Кнопка выхода в главное меню — есть на каждом экране, где её легко потерять."""
    return InlineKeyboardButton("🏠 В меню", callback_data="home")


def main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
        [
            InlineKeyboardButton("🖥 Сервер", callback_data="server"),
            InlineKeyboardButton("⚙️ Сервисы", callback_data="services"),
        ],
        [
            InlineKeyboardButton("🌐 Панель", callback_data="panel"),
            InlineKeyboardButton("📊 Трафик", callback_data="traffic"),
        ],
        [
            InlineKeyboardButton("🪪 Подписки", callback_data="subs"),
            InlineKeyboardButton("📈 История", callback_data="history"),
        ],
    ]
    if is_admin:
        buttons.append(
            [InlineKeyboardButton("🛠 Обслуживание", callback_data="maintenance")]
        )
    return InlineKeyboardMarkup(buttons)


def services_menu(units: tuple[str, ...], is_admin: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if is_admin:
        for unit in units:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"♻️ Рестарт {unit}", callback_data=f"ask:restart_service:{unit}"
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(
                    "♻️ Рестарт xray", callback_data="ask:restart_xray:"
                ),
                InlineKeyboardButton("📜 Журнал", callback_data="logs_menu"),
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton("🔄 Обновить", callback_data="services"),
            home_button(),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def logs_menu(units: tuple[str, ...]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"📜 {unit}", callback_data=f"logs:{unit}")]
        for unit in units
    ]
    buttons.append([InlineKeyboardButton("🔙 Сервисы", callback_data="services")])
    buttons.append([home_button()])
    return InlineKeyboardMarkup(buttons)


def maintenance_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📦 Обновить систему (apt, в фоне)",
                    callback_data="ask:apt_upgrade:",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬆️ Проверить релиз-обновление", callback_data="ask:dist_upgrade:"
                )
            ],
            [
                InlineKeyboardButton(
                    "💻 Выполнить команду", callback_data="command_prompt"
                ),
                InlineKeyboardButton("📜 Журнал", callback_data="logs_menu"),
            ],
            [home_button()],
        ]
    )


def result_menu() -> InlineKeyboardMarkup:
    """Клавиатура после выполненного действия: не оставляем пользователя без выхода."""
    return InlineKeyboardMarkup([[home_button()]])


def confirm_menu(action: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Подтвердить", callback_data=f"do:{action}:{token}"
                ),
                InlineKeyboardButton("❌ Отмена", callback_data=f"no:{token}"),
            ]
        ]
    )


def update_commands_help(commands: list[tuple[str, str]]) -> str:
    lines = ["🤖 <b>Команды</b>", ""]
    lines.extend(f"<code>{name}</code> — {desc}" for name, desc in commands)
    return "\n".join(lines)
