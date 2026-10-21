"""Инвентарь панелей/VPN и экран /stack."""

from __future__ import annotations

from stacksentry import inventory, ui
from stacksentry.models import ServiceStatus


def test_detects_3xui_and_extra_units(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: {"x-ui", "fail2ban"})
    monkeypatch.setattr(inventory, "_listening_ports", lambda: set())

    items = inventory.detect_stack()
    found = next(item for item in items if item.item.key == "3x-ui")
    assert found.unit == "x-ui"
    assert "unit" in found.evidence

    services = inventory.watched_services()
    assert "x-ui" in services
    assert "fail2ban" in services


def test_detects_wildcard_units(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: {"wg-quick@wg0"})
    monkeypatch.setattr(inventory, "_listening_ports", lambda: set())

    items = inventory.detect_stack()
    found = next(item for item in items if item.item.key == "wireguard")
    assert found.unit == "wg-quick@wg0"


def test_stack_marks_watched_and_unwatched() -> None:
    detected = [
        inventory.Detected(
            inventory.STACK[0],
            "x-ui",
            ("unit",),  # 3X-UI
        ),
        inventory.Detected(
            inventory.STACK[1],
            "s-ui",
            ("unit",),  # S-UI
        ),
    ]
    services = [
        ServiceStatus(
            name="x-ui",
            active=True,
            sub_state="running",
            active_state="active",
            main_pid=1,
            since="",
            restarts=0,
            memory_bytes=0,
            cpu_percent=0.0,
        )
    ]
    text = ui.render_stack(detected, ("x-ui",), services)
    assert "3X-UI" in text and "наблюдается" in text
    assert "S-UI" in text and "не наблюдается" in text
    assert "🟢" in text and "⚪" in text


def test_services_menu_has_stack_button() -> None:
    data = {
        button.callback_data
        for row in ui.services_menu(("x-ui",), True).inline_keyboard
        for button in row
    }
    assert "stack" in data


def test_installer_uses_inventory() -> None:
    from stacksentry.settings import BASE_DIR

    text = (BASE_DIR / "install.sh").read_text(encoding="utf-8")
    assert "-m stacksentry.inventory --services" in text
    assert "apk add --no-cache" in text


def test_no_fake_x_ui_when_nothing_found(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: set())
    assert inventory.watched_services() == []


def test_panel_detection_cli(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: set())
    assert inventory.main(["--panel"]) == 0
    assert capsys.readouterr().out.strip() == "0"
    monkeypatch.setattr(inventory, "_units", lambda: {"x-ui"})
    assert inventory.main(["--panel"]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_expanded_registry_detects_vpn_and_panels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        inventory,
        "_units",
        lambda: {"hysteria-server", "wg-quick@wg0", "remnawave", "ssserver"},
    )
    monkeypatch.setattr(inventory, "_process_names", lambda: set())
    keys = {item.item.key for item in inventory.detect_stack()}
    assert {"hysteria", "wireguard", "remnawave", "shadowsocks"} <= keys


def test_process_evidence_without_unit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: set())
    monkeypatch.setattr(inventory, "_process_names", lambda: {"xray"})
    found = next(item for item in inventory.detect_stack() if item.item.key == "xray")
    assert "process" in found.evidence
    assert found.unit == ""


def test_extra_units_include_security_and_web(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: {"fail2ban", "docker", "nginx"})
    services = inventory.watched_services()
    assert {"fail2ban", "docker", "nginx"} <= set(services)


def test_platform_info_has_sane_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "detect_package_manager", lambda: "apt")
    monkeypatch.setattr(inventory, "detect_init", lambda: "systemd")
    info = inventory.platform_info()
    assert info.package_manager == "apt"
    assert info.init == "systemd"
    assert isinstance(info.os_id, str)


def test_panel_suggestion_prefers_supported_panel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "_units", lambda: {"marzneshin"})
    monkeypatch.setattr(inventory, "_listening_ports", lambda: set())
    monkeypatch.setattr(inventory, "_process_names", lambda: set())
    assert inventory.panel_suggestion() == ("marzneshin", 8000)
