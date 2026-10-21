"""Форматирование сообщений и клавиатуры."""

from __future__ import annotations

import html
import sqlite3
import time
from datetime import datetime, timezone
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .alerts import Finding
from .hints import panel_token_hint
from .inventory import Detected
from .models import (
    InboundSummary,
    MonitorSnapshot,
    ServiceStatus,
    Severity,
    Subscription,
    SystemMetrics,
)
from .settings import Settings

BAR_WIDTH: Final = 10
MAX_INLINE_ROWS: Final = 12
#: Подписок на странице: больше — уже нечитаемо с телефона.
SUBSCRIPTIONS_PAGE: Final = 10

#: Человеческие названия панелей для экранов и кнопок.
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
#: Панели, у которых бот умеет перезапускать ядро через API.
PANEL_RESTART: Final = frozenset({"3x-ui", "marzban", "pasarguard", "s-ui"})
#: Панели, отдающие inbound'ы (для детального экрана трафика).
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


# ------------------------------------------------------------------ отчёты


def render_overview(
    snapshot: MonitorSnapshot,
    settings: Settings,
    *,
    age: float | None = None,
    compact: bool = False,
) -> str:
    """Главный отчёт: вердикт в первой строке, дальше детали.

    Уставшему админу нужен ответ «всё ок или нет» сразу, а не после чтения
    двадцати строк, поэтому состояние вынесено в заголовок, а замечания — выше
    подробностей.
    """
    metrics = snapshot.system
    # Без повторов: одна причина — одна строка.
    problems = list(dict.fromkeys(snapshot.warnings))
    if snapshot.panel.reachable is False and not snapshot.panel.disabled:
        problems.append("панель недоступна")
    if metrics is None:
        # Без метрик вердикт «всё в норме» был бы враньём.
        problems.append("метрики системы недоступны")
    down = [item.name for item in snapshot.services if not item.active]

    if down:
        verdict = "сервисы не работают: " + ", ".join(down)
    elif problems:
        verdict = f"есть замечания: {len(problems)}"
    else:
        verdict = "всё в норме"

    bits: list[str] = []
    if metrics is not None:
        bits.extend(
            (
                f"CPU {metrics.cpu_percent:.0f}%",
                f"RAM {metrics.mem_percent:.0f}%",
                f"Диск {metrics.disk_percent:.0f}%",
            )
        )
    services_bit = " · ".join(
        f"{item.name} {'🟢' if item.active else '🔴'}" for item in snapshot.services
    )
    if services_bit:
        bits.append(services_bit)
    if snapshot.panel.disabled:
        bits.append("панель ⚪")
    elif snapshot.panel.reachable:
        bits.append("панель 🟢")
    elif snapshot.panel.reachable is False:
        bits.append("панель 🔴")

    lines = [f"🖥 <b>Состояние сервера: {verdict}</b>"]
    if bits:
        lines.append(" · ".join(bits))

    if problems:
        lines.extend(("", "━━━ <b>Требует внимания</b>"))
        lines.extend(f"• {esc(item)}" for item in problems)

    if compact:
        # Ночью нужен один экран: вердикт и проблемы. Детали — по кнопке.
        lines.append("")
        lines.append(_stamp_line(age))
        return "\n".join(lines)

    lines.append("")
    lines.append(
        render_system(metrics, settings)
        if metrics is not None
        else "Метрики системы недоступны"
    )
    lines.append(render_services(snapshot))
    lines.append(render_panel(snapshot, panel_type=settings.panel_type))
    lines.append(render_traffic(snapshot, panel_type=settings.panel_type))

    lines.append(_stamp_line(age))
    return "\n".join(lines)


def _stamp_line(age: float | None) -> str:
    """«данные на 03:12 (12 с назад) · время сервера» — без догадок о поясе."""
    suffix = f" ({age:.0f} с назад)" if age is not None and age >= 1 else ""
    return f"\n<i>данные на {time.strftime('%H:%M:%S')}{suffix} · время сервера</i>"


def render_system(metrics: SystemMetrics, settings: Settings) -> str:
    """Метрики одним выровненным блоком.

    Моноширинный шрифт держит колонки ровными при любых значениях — иначе
    полосы и подписи «едут» и цифры приходится искать глазами.
    """
    tcp = metrics.tcp_connections if metrics.tcp_connections >= 0 else "н/д"
    rows = (
        ("CPU ", metrics.cpu_percent, settings.cpu_warn, settings.cpu_crit),
        ("RAM ", metrics.mem_percent, settings.mem_warn, settings.mem_crit),
        ("Диск", metrics.disk_percent, settings.disk_warn, settings.disk_crit),
    )
    lines: list[str] = []
    for label, percent, warn, crit in rows:
        icon = severity_icon(level(percent, warn, crit))
        lines.append(f"{label} {progress_bar(percent)} {percent:5.1f}% {icon}")

    used_mem = metrics.mem_total_bytes * metrics.mem_percent / 100
    lines.append(
        f"Память {fmt_bytes(used_mem)} занято из {fmt_bytes(metrics.mem_total_bytes)}"
    )
    lines.append(f"Свободно на диске: {fmt_bytes(metrics.disk_free_bytes)}")
    lines.append(
        f"Swap {metrics.swap_percent:.1f}% · Load {metrics.load1:.2f} · TCP {tcp}"
    )
    lines.append(
        f"Сеть ↑ {fmt_bytes(metrics.net_sent_bytes)} / "
        f"↓ {fmt_bytes(metrics.net_recv_bytes)}"
    )
    lines.append(f"Аптайм {fmt_uptime(metrics.uptime_seconds)}")
    return "<code>" + "\n".join(lines) + "</code>"


_STACK_TITLES = {
    "panel": "Панели",
    "proxy": "Прокси и ядра",
    "vpn": "VPN",
    "mesh": "Mesh/VPN",
}


def render_stack(
    detected: list[Detected],
    watched: tuple[str, ...],
    services: list[ServiceStatus],
) -> str:
    """Что найдено на сервере и что из этого реально наблюдается."""
    if not detected:
        return (
            "\n🧩 <b>Стек сервера</b>\n"
            "Панели и VPN-службы не найдены. Проверьте, что они установлены, "
            "или добавьте службы вручную: <code>sudo stacksentry-menu</code>."
        )
    active = {item.name: item.active for item in services}
    lines = [
        "",
        "🧩 <b>Стек сервера</b>",
        "Найдено на сервере; 🟢/🔴 — наблюдается и работает/лежит, "
        "⚪ — не наблюдается.",
    ]
    covered: set[str] = set()
    for kind, title in _STACK_TITLES.items():
        items = [item for item in detected if item.item.kind == kind]
        if not items:
            continue
        lines += ["", f"<b>{title}</b>"]
        for found in items:
            unit = found.unit
            if unit:
                covered.add(unit)
            if unit and unit in watched:
                icon = "🟢" if active.get(unit, False) else "🔴"
                status = "наблюдается"
            else:
                icon = "⚪"
                status = "не наблюдается"
            suffix = f" · <code>{esc(unit)}</code>" if unit else ""
            lines.append(f"{icon} {esc(found.item.title)}{suffix} — {status}")
            if found.item.note:
                lines.append(f"   <i>{esc(found.item.note)}</i>")
    extras = [unit for unit in watched if unit not in covered]
    if extras:
        lines += ["", "<b>Другие службы</b>"]
        for unit in extras:
            icon = "🟢" if active.get(unit, False) else "🔴"
            lines.append(f"{icon} <code>{esc(unit)}</code>")
    lines += [
        "",
        "Добавить в наблюдение: <code>sudo stacksentry-menu</code> → "
        "«Службы» (пункт 16).",
    ]
    return "\n".join(lines)


def render_services(snapshot: MonitorSnapshot) -> str:
    if not snapshot.services:
        return "\n⚙️ <b>Сервисы</b>\nне удалось прочитать состояние"
    lines = ["", "⚙️ <b>Сервисы</b>"]
    for service in snapshot.services:
        icon = "🟢" if service.active else "🔴"
        state = "работает" if service.active else esc(service.active_state)
        restarts = (
            f" · перезапускался {service.restarts} раз" if service.restarts else ""
        )
        lines.append(
            f"{icon} <code>{esc(service.name)}</code> — {state}"
            f" · RAM {fmt_bytes(service.memory_bytes)}"
            f" · CPU {service.cpu_percent:.1f}%{restarts}"
        )
    return "\n".join(lines)


def render_panel(snapshot: MonitorSnapshot, panel_type: str = "3x-ui") -> str:
    panel = snapshot.panel
    name = panel_title(panel_type)
    if panel.disabled:
        return (
            f"\n🌐 <b>Панель {esc(name)}</b>\n"
            "⚪ API панели не настроен — режим «только мониторинг служб».\n\n"
            "Службы и VPN-стек наблюдаются; пользователи и трафик недоступны.\n"
            "Настроить API: <code>sudo stacksentry-menu</code> → "
            "тип, адрес и токен панели."
        )
    if not panel.reachable:
        reason = esc(panel.error or "нет ответа")
        return f"\n🌐 <b>Панель {esc(name)}</b>\n🔴 недоступна: {reason}"
    latency = f"{panel.latency_ms:.0f} мс" if panel.latency_ms is not None else "—"
    api = "токен принят" if panel.api_authorized else "⚠️ токен не принят"
    core = ""
    if panel_type == "3x-ui":
        core = (
            f" · xray {esc(panel.xray_version or '?')} ({esc(panel.xray_state or '?')})"
        )
    hint = ""
    if not panel.api_authorized:
        if panel_type == "3x-ui":
            hint = (
                f"\n→ токен панели не принят. Где взять: "
                f"{panel_token_hint(panel.panel_version or '')}"
                "\n→ и проверьте PANEL_BASE_PATH (webBasePath панели): "
                "иначе API отвечает 404"
            )
        else:
            hint = (
                "\n→ проверьте PANEL_TYPE, PANEL_URL (включая путь панели) "
                "и PANEL_API_TOKEN в <code>sudo stacksentry-menu</code>"
            )
    return (
        f"\n🌐 <b>Панель {esc(name)}</b>\n"
        f"🟢 отвечает за {latency}\n"
        f"Панель {esc(panel.panel_version or '?')}{core}\n"
        f"API: {api}{hint}"
    )


def render_traffic(snapshot: MonitorSnapshot, panel_type: str = "3x-ui") -> str:
    """Трафик: итоги по inbound'ам и топ клиентов (в отличие от «Подписок»)."""
    name = panel_title(panel_type)
    if not snapshot.inbounds and not snapshot.subscriptions:
        if snapshot.panel.disabled:
            return (
                "\n📊 <b>Трафик</b>\n"
                "API панели не настроен — трафик недоступен.\n"
                "Службы и VPN-стек наблюдаются. Настроить: "
                "<code>sudo stacksentry-menu</code>."
            )
        hints = (
            "• у API-токена панели scope <code>node-sync</code> или "
            "<code>admin</code>;\n"
            "• <code>PANEL_BASE_PATH</code> — это «URL панели» из настроек "
            "(обычно пусто), а не путь <code>/panel</code>;\n"
            "• раздел «🌐 Панель» — отвечает ли API и принят ли токен.\n\n"
            "Токен: «Настройки панели → Учётная запись → API-токен»."
            if panel_type == "3x-ui"
            else (
                "• тип панели (<code>PANEL_TYPE</code>) и адрес с её путём;\n"
                "• токен/логин в <code>sudo stacksentry-menu</code>;\n"
                "• раздел «🌐 Панель» — отвечает ли API и принят ли токен."
            )
        )
        return (
            "\n📊 <b>Трафик</b>\n"
            f"Панель {esc(name)} не отдала пользователей — показывать нечего.\n\n"
            "<b>Что проверить</b>\n"
            f"{hints}"
        )

    if snapshot.inbounds:
        lines = [
            "",
            "📊 <b>Трафик</b>",
            f"Включено {sum(1 for item in snapshot.inbounds if item.enabled)} из "
            f"{len(snapshot.inbounds)} · клиентов {snapshot.active_clients} · "
            f"суммарно {fmt_bytes(snapshot.total_used_bytes)}",
        ]
        for inbound in snapshot.inbounds[:MAX_INLINE_ROWS]:
            icon = "🟢" if inbound.enabled else "⚪"
            limit = (
                f" из {fmt_bytes(inbound.total_bytes)}" if inbound.total_bytes else ""
            )
            lines.append(
                f"{icon} {esc(inbound.remark)} — "
                f"{esc(inbound.protocol)}:{inbound.port} · "
                f"клиентов {inbound.clients} · ↑{fmt_bytes(inbound.up_bytes)} "
                f"↓{fmt_bytes(inbound.down_bytes)}{limit}"
            )
        if len(snapshot.inbounds) > MAX_INLINE_ROWS:
            lines.append(f"… ещё {len(snapshot.inbounds) - MAX_INLINE_ROWS}")
    else:
        used = sum(item.used_bytes for item in snapshot.subscriptions)
        active = sum(1 for item in snapshot.subscriptions if item.enabled)
        lines = [
            "",
            "📊 <b>Трафик</b>",
            f"Панель {esc(name)} · пользователей {len(snapshot.subscriptions)} "
            f"(активных {active}) · суммарно {fmt_bytes(used)}",
        ]

    top = sorted(
        (item for item in snapshot.subscriptions if item.used_bytes > 0),
        key=lambda item: item.used_bytes,
        reverse=True,
    )[:8]
    if top:
        lines += ["", "👥 <b>Топ клиентов по трафику</b>"]
        for item in top:
            limit = (
                f" из {fmt_bytes(item.total_bytes)} ({item.used_percent:.0f}%)"
                if item.total_bytes
                else ""
            )
            lines.append(
                f"• <code>{esc(item.email or item.sub_id or '—')}</code> — "
                f"{fmt_bytes(item.used_bytes)}{limit}"
            )
        if len(snapshot.subscriptions) > len(top):
            lines.append(f"… ещё {len(snapshot.subscriptions) - len(top)} с трафиком")

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
        days = abs(int(item.expires_in_days)) if item.expires_in_days else None
        flags.append(f"❌ просрочена {days} дн. назад" if days else "❌ просрочена")
    if item.is_depleted:
        flags.append("🈳 трафик исчерпан")
    if not item.enabled:
        flags.append("⏸ выключена")
    if item.expires_in_days is not None and 0 < item.expires_in_days <= 7:
        flags.append(f"⌛ {item.expires_in_days:.1f} дн.")

    traffic = fmt_bytes(item.used_bytes)
    if item.total_bytes:
        traffic += f" из {fmt_bytes(item.total_bytes)} ({item.used_percent:.0f}%)"

    suffix = f" · {' · '.join(flags)}" if flags else ""
    return (
        f"• <code>{esc(item.email)}</code> [{esc(item.inbound_remark)}]\n"
        f"   <code>{traffic}</code>{suffix}"
    )


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


def main_menu(
    is_admin: bool, *, settings: Settings | None = None
) -> InlineKeyboardMarkup:
    """Главное меню подстраивается под настроенную панель.

    Без API панели нет смысла показывать «Трафик» и «Подписки»: они всё равно
    пустые. Кнопка «Панель» остаётся, чтобы объяснить, как включить API.
    """
    panel_enabled = True if settings is None else settings.panel_enabled
    buttons = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
        [
            InlineKeyboardButton("🖥 Сервер", callback_data="server"),
            InlineKeyboardButton("⚙️ Сервисы", callback_data="services"),
        ],
    ]
    if panel_enabled:
        buttons.append(
            [
                InlineKeyboardButton("🌐 Панель", callback_data="panel"),
                InlineKeyboardButton("📊 Трафик", callback_data="traffic"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton("🪪 Подписки", callback_data="subs"),
                InlineKeyboardButton("📈 История", callback_data="history"),
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton("🌐 Панель (не настроена)", callback_data="panel"),
                InlineKeyboardButton("📈 История", callback_data="history"),
            ]
        )
    if is_admin:
        buttons.append(
            [InlineKeyboardButton("🛠 Обслуживание", callback_data="maintenance")]
        )
        buttons.append(
            [
                InlineKeyboardButton("🔐 Безопасность", callback_data="security"),
                InlineKeyboardButton("❓ Помощь", callback_data="help"),
            ]
        )
    else:
        buttons.append([InlineKeyboardButton("❓ Помощь", callback_data="help")])
    return InlineKeyboardMarkup(buttons)


def render_security(settings: Settings, until: float) -> str:
    if not settings.require_2fa:
        return (
            "🔐 <b>Безопасность</b>\n\n"
            "2FA для опасных действий выключена. Включить: "
            "<code>sudo stacksentry-menu</code> или повторный установщик с "
            "<code>--2fa</code>.\n\n"
            "Даже без 2FA действия требуют подтверждения и пишутся в аудит."
        )
    if until > time.time():
        status = (
            "🔓 Разблокировано до <b>"
            + time.strftime("%H:%M", time.localtime(until))
            + "</b>"
        )
    else:
        status = "🔒 Заблокировано — нужен код TOTP"
    minutes = max(1, settings.elevation_ttl // 60)
    return (
        "🔐 <b>Безопасность</b>\n\n"
        f"2FA: <b>включена</b>\n{status}\n\n"
        f"Разблокировка действует {minutes} мин. Для обновлений ОС и "
        "перезагрузки нужен свежий код (не старше 2 минут).\n\n"
        "Просмотр состояния, журналов и подписок доступен без кода."
    )


def security_menu(elevated: bool) -> InlineKeyboardMarkup:
    if elevated:
        action = InlineKeyboardButton(
            "🔒 Заблокировать сейчас", callback_data="security:lock"
        )
    else:
        action = InlineKeyboardButton(
            "🔓 Разблокировать действия", callback_data="security:unlock"
        )
    return InlineKeyboardMarkup(
        [
            [action],
            [InlineKeyboardButton("🔄 Обновить", callback_data="security")],
            [home_button()],
        ]
    )


def twofa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❌ Отмена", callback_data="security:cancel"),
                InlineKeyboardButton("❓ Где взять код", callback_data="security:help"),
            ],
            [home_button()],
        ]
    )


def render_2fa_prompt(title: str, lock_left: float = 0.0) -> str:
    lines = [
        "🔐 <b>Нужен код 2FA</b>",
        f"Действие: {title}",
        "",
    ]
    if lock_left > 0:
        lines.append(f"⏳ Слишком много попыток. Подождите {lock_left:.0f} с.")
    else:
        lines.append(
            "Отправьте 6 цифр из приложения-аутентификатора. Сообщение с кодом я удалю."
        )
    return "\n".join(lines)


def services_menu(
    units: tuple[str, ...],
    is_admin: bool,
    *,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if is_admin:
        for unit in units:
            # Рестарт и журнал одного юнита — в одной строке: в 3 часа ночи
            # незачем ходить за журналом в отдельное меню.
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"♻️ {unit}", callback_data=f"ask:restart_service:{unit}"
                    ),
                    InlineKeyboardButton(
                        f"📜 Журнал {unit}", callback_data=f"logs:{unit}"
                    ),
                ]
            )
        # Кнопка рестарта ядра — только если панель это умеет.
        if settings is None or (
            settings.panel_enabled and settings.panel_type in PANEL_RESTART
        ):
            label = "♻️ Рестарт xray"
            if settings is not None and settings.panel_type == "s-ui":
                label = "♻️ Рестарт sing-box"
            elif settings is not None and settings.panel_type in {
                "marzban",
                "pasarguard",
            }:
                label = "♻️ Рестарт ядра"
            buttons.append(
                [InlineKeyboardButton(label, callback_data="ask:restart_xray:")]
            )
    buttons.append([InlineKeyboardButton("🧩 Стек сервера", callback_data="stack")])
    buttons.append(
        [
            InlineKeyboardButton("🔄 Обновить", callback_data="services"),
            home_button(),
        ]
    )
    return InlineKeyboardMarkup(buttons)


#: У каких логов есть кнопки «Ещё 200 строк» / «Только ошибки».
LOG_WITH_MORE = frozenset({"os_logs", "xui_logs", "kernel_logs"})
LOG_WITH_ERRORS = frozenset({"os_logs", "xui_logs", "kernel_logs", "dmesg"})


def log_variants_menu(base: str) -> InlineKeyboardMarkup:
    """Кнопки для лога, запущенного через /run."""
    row: list[InlineKeyboardButton] = []
    if base in LOG_WITH_ERRORS:
        row.append(
            InlineKeyboardButton("🔎 Только ошибки", callback_data=f"logerr:{base}")
        )
    if base in LOG_WITH_MORE:
        row.append(
            InlineKeyboardButton("📜 Ещё 200 строк", callback_data=f"logmore:{base}")
        )
    rows = [row] if row else []
    rows.append([home_button()])
    return InlineKeyboardMarkup(rows)


def logs_menu(units: tuple[str, ...]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"📜 {unit}", callback_data=f"logs:{unit}")]
        for unit in units
    ]
    buttons.append([InlineKeyboardButton("🔙 Сервисы", callback_data="services")])
    buttons.append([home_button()])
    return InlineKeyboardMarkup(buttons)


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


def maintenance_menu(*, package_manager: str | None = None) -> InlineKeyboardMarkup:
    """Пункты обслуживания под пакетный менеджер сервера.

    Обновление индексов и системы работает для всех поддерживаемых PM;
    phased- и релиз-обновления остаются только у APT.
    """
    pm = package_manager or ""
    # Без явного аргумента сохраняем прежнее поведение (APT-набор) для тестов
    # и старых вызовов; реальный неизвестный PM не получает apt-кнопки.
    apt = package_manager is None or package_manager == "apt"
    update_label = PKG_UPDATE_LABELS.get(pm, "🔄 Обновить список пакетов")
    upgrade_label = PKG_UPGRADE_LABELS.get(pm, "📦 Обновить систему")
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(update_label, callback_data="ask:apt_update:")],
        [InlineKeyboardButton(upgrade_label, callback_data="ask:apt_upgrade:")],
    ]
    if apt:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        "⬆️ Обновить с phased (обычный upgrade)",
                        callback_data="ask:apt_upgrade_phased:",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🚀 Запустить релиз-обновление ОС",
                        callback_data="ask:release_upgrade:",
                    )
                ],
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    "💻 Выполнить команду", callback_data="command_prompt"
                ),
                InlineKeyboardButton("📜 Журнал", callback_data="logs_menu"),
            ],
            [
                InlineKeyboardButton(
                    "🔻 Перезагрузить сервер", callback_data="ask:reboot:"
                )
            ],
            [home_button()],
        ]
    )
    return InlineKeyboardMarkup(rows)


def result_menu(
    extra: list[InlineKeyboardButton] | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после действия: контекст + выход, а не только «домой»."""
    rows = [extra] if extra else []
    rows.append([home_button()])
    return InlineKeyboardMarkup(rows)


def alert_menu(
    actions: list[InlineKeyboardButton] | None = None,
    *,
    mute_key: str = "",
) -> InlineKeyboardMarkup:
    """Кнопки под алертом: починить, посмотреть, «знаю, до утра»."""
    rows: list[list[InlineKeyboardButton]] = []
    if actions:
        rows.append(actions)
    rows.append(
        [
            InlineKeyboardButton("📊 Текущее состояние", callback_data="refresh"),
            InlineKeyboardButton("🏠 В меню", callback_data="home"),
        ]
    )
    if mute_key:
        rows.append(
            [InlineKeyboardButton("😴 Знаю, до утра", callback_data=f"mute:{mute_key}")]
        )
    return InlineKeyboardMarkup(rows)


def overview_menu(is_admin: bool, *, compact: bool) -> InlineKeyboardMarkup:
    """Главное меню плюс переключатель «подробнее/свернуть»."""
    toggle = InlineKeyboardButton(
        "📋 Подробнее" if compact else "🔼 Свернуть",
        callback_data="details" if compact else "refresh",
    )
    return InlineKeyboardMarkup([[toggle], *main_menu(is_admin).inline_keyboard])


def log_view_menu(unit: str) -> InlineKeyboardMarkup:
    """Журнал: фильтр ошибок и добор строк, а не простыня текста."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔎 Только ошибки", callback_data=f"logs_err:{unit}"
                ),
                InlineKeyboardButton(
                    "📜 Ещё 200 строк", callback_data=f"logs200:{unit}"
                ),
            ],
            [
                InlineKeyboardButton("🔙 Сервисы", callback_data="services"),
                home_button(),
            ],
        ]
    )


def audit_menu() -> InlineKeyboardMarkup:
    """Разбор инцидента: отказы отдельно от действий."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔎 Отказы", callback_data="audit:denied"),
                InlineKeyboardButton("🛠 Действия", callback_data="audit:actions"),
            ],
            [
                InlineKeyboardButton("📜 Всё подряд", callback_data="audit:all"),
                home_button(),
            ],
        ]
    )


def confirm_menu(action: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                # Отмена слева: опасное действие не должно стоять первым там,
                # куда попадает палец при быстром нажатии.
                InlineKeyboardButton("❌ Отмена", callback_data=f"no:{token}"),
                InlineKeyboardButton(
                    "✅ Подтвердить", callback_data=f"do:{action}:{token}"
                ),
            ]
        ]
    )


#: Человеческие описания безопасных команд: (группа, что показывает).
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


def render_command_help(allowlist: tuple[str, ...]) -> str:
    """Список безопасных команд с описаниями, сгруппированный по темам."""
    if not allowlist:
        return (
            "💻 <b>Команда на сервер</b>\n\n"
            "Список команд пуст — выполнение отключено "
            "(<code>COMMAND_ALLOWLIST</code>)."
        )
    grouped: dict[str, list[str]] = {group: [] for group in _COMMAND_GROUPS}
    for command in allowlist:
        group, description = COMMAND_HELP.get(command, ("Прочее", "без описания"))
        grouped.setdefault(group, []).append(
            f"• <code>/run {esc(command)}</code> — {esc(description)}"
        )
    lines = [
        "💻 <b>Команда на сервер</b>",
        "Пришлите команду сообщением в любом разделе или "
        "<code>/run &lt;команда&gt;</code>. Всё только читает, shell недоступен.",
        "У логов (os_logs, xui_logs, kernel_logs, dmesg) под выводом есть "
        "кнопки «Ещё 200 строк» и «Только ошибки».",
    ]
    for group in _COMMAND_GROUPS:
        items = grouped.get(group) or []
        if not items:
            continue
        lines += ["", f"<b>{group}</b>"]
        lines += items
    return "\n".join(lines)


def render_help(is_admin: bool) -> str:
    """Полная инструкция простыми словами: разделы, действия, настройки."""
    lines_out = [
        "🛰 <b>StackSentry</b>",
        "Следит за сервером и панелью и пишет, когда что-то сломалось. "
        "Всё управление — кнопками; команды можно не помнить.",
        "",
        "🚀 <b>С чего начать</b>",
        "• <code>/status</code> — сводка: вердикт, метрики, службы, панель.",
        "• <code>/help</code> — эта инструкция; кнопка «❓ Помощь» делает то же.",
        "• «🏠 В меню» есть на каждом экране — из любого места можно вернуться.",
        "• Переходы заменяют прошлый экран, а обновления идут одним сообщением: "
        "«в очереди» → «запущено» → отчёт.",
        "• Команды: <code>/status</code>, <code>/traffic</code>, <code>/subs</code>, "
        "<code>/history</code>, <code>/logs</code>, <code>/audit</code>, "
        "<code>/maint</code>, <code>/run</code>.",
        "",
        "📊 <b>Разделы</b>",
        "🖥 <b>Сервер</b> — CPU, RAM, swap, диск, сеть, load average.",
        "⚙️ <b>Сервисы</b> — работает ли x-ui; рядом «Рестарт» и «Журнал».",
        "🌐 <b>Панель</b> — доступность панели и xray, версии и обновления.",
        "📊 <b>Трафик</b> — итоги по inbound'ам и топ клиентов по расходу.",
        "🪪 <b>Подписки</b> — сроки, остаток трафика, истёкшие и исчерпанные.",
        "📈 <b>История</b> — средние CPU/RAM/диска за последние сутки.",
        "📜 <b>Журнал</b> — вывод службы, фильтр «только ошибки», ещё 200 строк.",
        "🗂 <b>Аудит</b> — кто что делал и какие попытки отклонены.",
        "",
        "🛠 <b>Обслуживание</b> (только администратор)",
        "• <b>Обновить список пакетов</b> — <code>apt update</code>: обновляет индексы "
        "и показывает, какие пакеты доступны к обновлению.",
        "• <b>Обновить систему</b> — <code>apt update + full-upgrade</code>: ставит "
        "все обновления.",
        "• <b>Обновить с phased</b> — обычный <code>upgrade</code>, но с "
        "разрешёнными phased-обновлениями Ubuntu.",
        "• <b>Запустить релиз-обновление</b> — <code>do-release-upgrade</code>: "
        "сначала проверит, есть ли новая версия ОС, и если нет — сообщит об этом. "
        "Если есть — обновление надолго, возможна перезагрузка.",
        "• <b>Перезагрузить сервер</b> — с подтверждением.",
        "Всё выполняется в фоне, отчёт придёт в этот чат сам — вводить ничего не "
        "нужно. Если обновление ставит новое ядро, бот напомнит про перезагрузку.",
        "Примеры диагностики: <code>/run os_logs</code>, "
        "<code>/run failed_units</code>, <code>/run sockets</code>.",
        "",
        "💻 <b>Команда на сервер из любого раздела</b>",
        "Просто пришлите команду сообщением (или <code>/run df -h</code>) — бот "
        "попросит подтверждение и вернёт вывод. Разрешены только безопасные "
        "диагностические команды из <code>COMMAND_ALLOWLIST</code>; произвольный "
        "shell недоступен. Посмотреть список: <code>/run</code>.",
        "",
        "📈 <b>Что значат цифры</b>",
        "CPU, RAM, Диск — загрузка в процентах: 🟢 норма, 🟡 близко к пределу, "
        "🔴 плохо. Load — очередь задач; если она больше числа ядер, сервер не "
        "успевает. xray 🟢 — процесс работает. «Время сервера» — часовой пояс "
        "самого сервера, а не телефона.",
        "",
        "🔐 <b>Безопасность</b>",
        "• Команды и кнопки работают только в личном чате администратора.",
        "• 2FA: просмотр без кода; рестарты/apt/команды — после TOTP на 15 мин; "
        "обновления ОС и перезагрузка — свежий код. Раздел «🔐 Безопасность».",
        "• Опасные действия — только после подтверждения; токен одноразовый.",
        "• Всё пишется в аудит; секреты в сообщениях и логах маскируются.",
        "• Бот работает от отдельного пользователя, привилегии — через узкий "
        "sudoers-список точных команд.",
        "",
        "⚙️ <b>Настройки</b> — в терминале сервера: <code>sudo stacksentry-menu</code>.",
        "Там токены, адрес панели и webBasePath, пороги, службы, уровень логов, "
        "рестарт, журнал, обновление и удаление. Меню показывает состояние службы "
        "и маскирует секреты.",
        "",
        "🩺 <b>Если что-то не работает</b>",
        "1. Прочитайте сообщение бота — там сказано, что именно сломалось.",
        "2. <code>/services</code> → «Рестарт» → «Журнал» нужной службы.",
        "3. «Панель» покажет, отвечает ли API; при ошибке токена проверьте scope "
        "<code>node-sync</code> или <code>admin</code>.",
        "4. Ошибка 404 на API — почти всегда лишний <code>/panel</code> в "
        "webBasePath: там должно быть значение из «URL панели» (обычно пусто).",
        "5. Журнал бота: <code>journalctl -u stacksentry-bot -n 50</code>.",
        "",
        "Подробная документация: github.com/Hijackery/stacksentry",
    ]
    if not is_admin:
        return (
            "\n".join(lines_out[:12])
            + "\n\n⛔ Управление доступно только администратору."
        )
    return "\n".join(lines_out)
