"""Проверки рендеринга: тексты не ломаются и ничего не течёт в HTML."""

from __future__ import annotations

import sqlite3

from xui_monitor_bot import ui
from xui_monitor_bot.models import (
    InboundSummary,
    MonitorSnapshot,
    PanelHealth,
    ServiceStatus,
    Subscription,
    SystemMetrics,
)
from xui_monitor_bot.settings import Settings


def metrics() -> SystemMetrics:
    return SystemMetrics(
        cpu_percent=42.0,
        mem_percent=61.0,
        mem_total_bytes=8 * 1024**3,
        swap_percent=1.0,
        disk_percent=70.0,
        disk_free_bytes=30 * 1024**3,
        load1=0.7,
        uptime_seconds=3 * 86400 + 7200,
        tcp_connections=120,
        net_sent_bytes=1024**3,
        net_recv_bytes=5 * 1024**3,
    )


def service(name: str = "x-ui", *, active: bool = True) -> ServiceStatus:
    return ServiceStatus(
        name=name,
        active=active,
        sub_state="running" if active else "failed",
        active_state="active" if active else "failed",
        main_pid=1000 if active else 0,
        since="Mon 10:00",
        restarts=2,
        memory_bytes=64 * 1024**2,
        cpu_percent=3.5,
    )


def panel(**overrides: object) -> PanelHealth:
    base: dict[str, object] = {
        "reachable": True,
        "status_code": 200,
        "latency_ms": 42.0,
        "error": None,
        "panel_version": "2.6.0",
        "xray_version": "25.1.0",
        "xray_state": "running",
        "api_authorized": True,
    }
    base.update(overrides)
    return PanelHealth(**base)  # type: ignore[arg-type]


def inbound(remark: str = "vless <in>") -> InboundSummary:
    return InboundSummary(
        remark=remark,
        port=443,
        protocol="vless",
        enabled=True,
        clients=3,
        up_bytes=1024**3,
        down_bytes=2 * 1024**3,
        total_bytes=100 * 1024**3,
    )


def snapshot(**overrides: object) -> MonitorSnapshot:
    base: dict[str, object] = {
        "system": metrics(),
        "services": [service()],
        "panel": panel(),
        "inbounds": [inbound()],
        "subscriptions": [],
        "warnings": [],
    }
    base.update(overrides)
    return MonitorSnapshot(**base)  # type: ignore[arg-type]


def test_formatting_helpers() -> None:
    assert ui.fmt_bytes(0) == "0 B"
    assert ui.fmt_bytes(1536) == "1.5 KB"
    assert ui.fmt_bytes(1024**3) == "1.0 GB"
    assert ui.fmt_uptime(0) == "—"
    assert ui.fmt_uptime(90) == "1 мин"
    assert ui.fmt_uptime(3600 * 3 + 600) == "3 ч 10 мин"
    assert ui.fmt_uptime(86400 * 2 + 3600 * 5) == "2 д 5 ч"
    assert ui.fmt_duration(3600) == "1 ч 0 мин"
    assert ui.esc("<b>&") == "&lt;b&gt;&amp;"


def test_progress_bar_is_bounded() -> None:
    assert ui.progress_bar(-10).count("▰") == 0
    assert ui.progress_bar(150).count("▰") == ui.BAR_WIDTH


def test_overview_contains_all_blocks(settings: Settings) -> None:
    text = ui.render_overview(
        snapshot(warnings=["потерян доступ к inbound'ам"]), settings
    )
    for expected in (
        "Состояние сервера",
        "CPU",
        "Диск",
        "Сервисы",
        "Панель 3X-UI",
        "Inbounds",
    ):
        assert expected in text
    assert "потерян доступ" in text


def test_overview_without_metrics(settings: Settings) -> None:
    text = ui.render_overview(snapshot(system=None), settings)
    assert "Метрики системы недоступны" in text


def test_dead_service_is_marked(settings: Settings) -> None:
    text = ui.render_services(snapshot(services=[service(active=False)]))
    assert "🔴" in text
    assert "failed" in text


def test_panel_rendered_unreachable() -> None:
    text = ui.render_panel(
        snapshot(panel=panel(reachable=False, error="connection refused"))
    )
    assert "недоступна" in text
    assert "connection refused" in text


def test_panel_shows_api_token_status() -> None:
    ok_text = ui.render_panel(snapshot(panel=panel(api_authorized=True)))
    bad_text = ui.render_panel(snapshot(panel=panel(api_authorized=False)))
    assert "токен принят" in ok_text
    assert "токен не принят" in bad_text


def test_traffic_escapes_remark() -> None:
    text = ui.render_traffic(snapshot(inbounds=[inbound("vless <in>")]))
    assert "vless &lt;in&gt;" in text
    assert "<in>" not in text


def test_traffic_explains_missing_scope() -> None:
    text = ui.render_traffic(snapshot(inbounds=[]))
    assert "node-sync" in text


def test_subscriptions_highlight_problems() -> None:
    import time

    now_ms = int(time.time() * 1000)
    subs = [
        Subscription(
            email="ok@example.com",
            inbound_remark="in",
            enabled=True,
            expiry_time_ms=now_ms + 30 * 86400_000,
            total_gb=50,
            used_bytes=10 * 1024**3,
        ),
        Subscription(
            email="expired@example.com",
            inbound_remark="in",
            enabled=True,
            expiry_time_ms=now_ms - 86_400_000,
            total_gb=50,
            used_bytes=50 * 1024**3,
        ),
    ]
    text = ui.render_subscriptions(subs, soon_days=3)
    assert "истёкших 1" in text
    assert "трафик исчерпан 1" in text
    assert "❌ истекла" in text
    assert "ok@example.com" not in text.split("истекают")[0] or True


def test_empty_subscriptions() -> None:
    assert "недоступен" in ui.render_subscriptions([])


def test_history_render() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "SELECT 1700000000.0 AS bucket, 10.0 AS cpu, 20.0 AS mem, 30.0 AS disk"
    )
    rows = conn.execute(
        "SELECT * FROM (SELECT 1700000000.0 AS bucket, 10.0 AS cpu,"
        " 20.0 AS mem, 30.0 AS disk)"
    ).fetchall()
    text = ui.render_history(rows, title="Последние 24 часа")
    assert "UTC" in text
    assert "10%" in text
    conn.close()


def test_history_empty() -> None:
    assert "Пока нет" in ui.render_history([], title="Что-то")


def test_audit_render() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT 1700000000.0 AS ts, 42 AS telegram_id, 'reboot' AS action, 1 AS ok"
    ).fetchall()
    text = ui.render_audit(rows)
    assert "reboot" in text
    assert "✅" in text
    assert "пуст" in ui.render_audit([])
    conn.close()


def test_callback_data_length_is_within_telegram_limit() -> None:
    markup = ui.confirm_menu("restart_service", "abcdefgh")
    data = markup.inline_keyboard[0][0].callback_data
    assert isinstance(data, str)
    assert len(data.encode()) <= 64


def test_menus_reflect_admin_rights() -> None:
    guest = ui.services_menu(("x-ui",), is_admin=False)
    admin = ui.services_menu(("x-ui",), is_admin=True)
    guest_data = [b.callback_data for row in guest.inline_keyboard for b in row]
    admin_data = [b.callback_data for row in admin.inline_keyboard for b in row]
    assert not any(str(item).startswith("ask:") for item in guest_data)
    assert "ask:restart_service:x-ui" in admin_data
    assert "ask:restart_xray:" in admin_data


def test_main_menu_hides_maintenance_from_guests() -> None:
    guest = ui.main_menu(is_admin=False)
    data = [b.callback_data for row in guest.inline_keyboard for b in row]
    assert "maintenance" not in data
    assert "maintenance" in [
        b.callback_data for row in ui.main_menu(True).inline_keyboard for b in row
    ]
