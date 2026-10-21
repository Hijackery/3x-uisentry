"""Проверки загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest

from stacksentry.settings import ConfigError, load_settings

BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:ABC",
    "TELEGRAM_ADMIN_IDS": "42,43",
    "PANEL_URL": "https://panel.example.com",
    "PANEL_API_TOKEN": "tok",
    "SERVICES": "x-ui",
}


def write_env(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / "test.env"
    lines = [f"{key}={value}" for key, value in {**BASE_ENV, **values}.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_loads_values_from_env_file(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path))
    assert settings.telegram_token == "123:ABC"
    assert settings.admin_ids == (42, 43)
    assert settings.services == ("x-ui",)
    assert settings.is_admin(42)
    assert not settings.is_admin(7)


def test_builds_api_base_with_base_path(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, PANEL_BASE_PATH="mysecret/"))
    assert settings.api_base == "https://panel.example.com/mysecret/panel/api"


def test_api_base_without_base_path(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path))
    assert settings.api_base == "https://panel.example.com/panel/api"


def test_missing_token_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.env"
    path.write_text("TELEGRAM_ADMIN_IDS=1\nPANEL_URL=https://x\nPANEL_API_TOKEN=t\n")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_settings(path)


def test_missing_admin_ids_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.env"
    path.write_text(
        "TELEGRAM_BOT_TOKEN=t\nTELEGRAM_ADMIN_IDS=\nPANEL_URL=https://x\nPANEL_API_TOKEN=t\n"
    )
    with pytest.raises(ConfigError, match="TELEGRAM_ADMIN_IDS"):
        load_settings(path)


def test_panel_url_scheme_is_checked(tmp_path: Path) -> None:
    path = write_env(tmp_path, PANEL_URL="panel.example.com")
    with pytest.raises(ConfigError, match="http"):
        load_settings(path)


def test_warn_threshold_above_crit_is_rejected(tmp_path: Path) -> None:
    path = write_env(tmp_path, CPU_WARN="99", CPU_CRIT="90")
    with pytest.raises(ConfigError, match="CPU_WARN"):
        load_settings(path)


def test_non_numeric_threshold_is_rejected(tmp_path: Path) -> None:
    path = write_env(tmp_path, MEM_WARN="много")
    with pytest.raises(ConfigError, match="MEM_WARN"):
        load_settings(path)


def test_services_and_allowlist_are_splittable(tmp_path: Path) -> None:
    settings = load_settings(
        write_env(
            tmp_path,
            SERVICES="x-ui;fail2ban",
            COMMAND_ALLOWLIST="free -h, df -h",
        )
    )
    assert settings.services == ("x-ui", "fail2ban")
    assert settings.command_allowlist == ("free -h", "df -h")


def test_environment_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAfromenv")
    settings = load_settings(write_env(tmp_path))
    assert settings.telegram_token == "123456789:AAfromenv"


def test_monitor_only_mode_without_panel(tmp_path: Path) -> None:
    """Без 3X-UI бот работает как монитор служб: оба поля пусты."""
    settings = load_settings(write_env(tmp_path, PANEL_URL="", PANEL_API_TOKEN=""))
    assert settings.panel_enabled is False
    assert settings.api_base == ""
    assert settings.panel_web_base == ""


def test_panel_url_and_token_must_be_set_together(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="задаются вместе"):
        load_settings(write_env(tmp_path, PANEL_API_TOKEN=""))


def test_empty_services_is_allowed(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, SERVICES=""))
    assert settings.services == ()


@pytest.mark.parametrize("panel_type", ["s-ui", "hiddify", "remnawave"])
def test_token_panel_types_are_accepted(tmp_path: Path, panel_type: str) -> None:
    settings = load_settings(write_env(tmp_path, PANEL_TYPE=panel_type))
    assert settings.panel_type == panel_type
    assert settings.panel_enabled is True


def test_panel_type_is_normalized(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, PANEL_TYPE="S-UI"))
    assert settings.panel_type == "s-ui"


def test_unknown_panel_type_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="PANEL_TYPE"):
        load_settings(write_env(tmp_path, PANEL_TYPE="unknown"))


def test_marzban_requires_login_and_password(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="PANEL_USERNAME"):
        load_settings(
            write_env(
                tmp_path,
                PANEL_TYPE="marzban",
                PANEL_URL="https://marzban.example.com",
                PANEL_API_TOKEN="",
            )
        )


def test_marzban_credentials_without_url_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="пуст PANEL_URL"):
        load_settings(
            write_env(
                tmp_path,
                PANEL_TYPE="marzban",
                PANEL_URL="",
                PANEL_API_TOKEN="",
                PANEL_USERNAME="admin",
                PANEL_PASSWORD="secret",
            )
        )


@pytest.mark.parametrize("panel_type", ["marzneshin", "pasarguard"])
def test_login_panel_types_are_accepted(tmp_path: Path, panel_type: str) -> None:
    settings = load_settings(
        write_env(
            tmp_path,
            PANEL_TYPE=panel_type,
            PANEL_API_TOKEN="",
            PANEL_USERNAME="admin",
            PANEL_PASSWORD="secret",
        )
    )
    assert settings.panel_type == panel_type
    assert settings.panel_enabled is True


def test_outline_needs_only_url(tmp_path: Path) -> None:
    settings = load_settings(
        write_env(
            tmp_path,
            PANEL_TYPE="outline",
            PANEL_URL="https://outline.example.com/SecretPath",
            PANEL_API_TOKEN="",
        )
    )
    assert settings.panel_enabled is True


def test_outline_without_url_is_monitor_only(tmp_path: Path) -> None:
    settings = load_settings(
        write_env(tmp_path, PANEL_TYPE="outline", PANEL_URL="", PANEL_API_TOKEN="")
    )
    assert settings.panel_enabled is False


def test_webapp_origin_requires_enabled_service(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="WEBAPP_ENABLED"):
        load_settings(
            write_env(
                tmp_path,
                WEBAPP_PUBLIC_ORIGIN="https://sentry.example.com",
                WEBAPP_ENABLED="0",
            )
        )


def test_enabled_webapp_with_public_origin_is_valid(tmp_path: Path) -> None:
    settings = load_settings(
        write_env(
            tmp_path,
            WEBAPP_ENABLED="1",
            WEBAPP_SECRET="x" * 32,
            WEBAPP_PUBLIC_ORIGIN="https://sentry.example.com",
        )
    )
    assert settings.webapp_enabled is True
    assert settings.webapp_public_origin == "https://sentry.example.com"
