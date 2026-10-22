"""Mini App: cookie, initData, read-only API, CSP, конфиг."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from stacksentry.models import MonitorSnapshot, PanelHealth, SystemMetrics
from stacksentry.settings import ConfigError, Settings, load_settings
from stacksentry.storage import Storage
from stacksentry.webapp import (
    WebAppConfig,
    create_app,
    issue_cookie,
    verify_cookie,
)

ADMIN_ID = 42
SECRET = "w" * 40


def _config(
    settings: Settings, *, origin: str = "", terminal_origin: str = ""
) -> WebAppConfig:
    return WebAppConfig(
        settings=settings,
        secret=SECRET,
        host="127.0.0.1",
        port=8787,
        public_origin=origin,
        session_ttl=3600,
        terminal_origin=terminal_origin,
    )


def _init_data(token: str, user_id: int = ADMIN_ID) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "webapp-test",
        "user": json.dumps(
            {"id": user_id, "first_name": "Admin"}, separators=(",", ":")
        ),
    }
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**data, "hash": digest})


class FakeMonitor:
    async def snapshot(self) -> MonitorSnapshot:
        return MonitorSnapshot(
            system=SystemMetrics(
                cpu_percent=12.0,
                mem_percent=40.0,
                mem_total_bytes=1024**3,
                swap_percent=0.0,
                disk_percent=30.0,
                disk_free_bytes=10**9,
                load1=0.1,
                uptime_seconds=100.0,
                tcp_connections=1,
                net_sent_bytes=0,
                net_recv_bytes=0,
            ),
            services=[],
            panel=PanelHealth(
                reachable=False,
                status_code=None,
                latency_ms=None,
                error=None,
                disabled=True,
            ),
            inbounds=[],
            subscriptions=[],
        )


def test_cookie_roundtrip_and_tamper() -> None:
    cookie = issue_cookie(SECRET, ADMIN_ID, 3600)
    assert verify_cookie(SECRET, cookie) == ADMIN_ID
    assert verify_cookie(SECRET, cookie + "0") is None
    assert verify_cookie(SECRET, issue_cookie(SECRET, ADMIN_ID, -1)) is None
    assert verify_cookie("x" * 40, cookie) is None


def test_session_and_read_only_api(
    make_settings: Callable[..., Settings], storage: Storage
) -> None:
    settings = make_settings(
        webapp_enabled=True, webapp_secret=SECRET, webapp_public_origin=""
    )
    app = create_app(
        _config(settings),
        monitor=FakeMonitor(),  # type: ignore[arg-type]
        storage=storage,
    )
    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    assert client.post("/api/session", json={"initData": "bad"}).status_code == 401

    response = client.post(
        "/api/session", json={"initData": _init_data(settings.telegram_token)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == ADMIN_ID

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["panel"]["disabled"] is True
    assert "Content-Security-Policy" in status.headers
    assert client.get("/api/history").status_code == 200
    assert client.get("/api/audit").status_code == 200


def test_terminal_tab_origin_and_csp(
    make_settings: Callable[..., Settings], storage: Storage
) -> None:
    settings = make_settings(webapp_enabled=True, webapp_secret=SECRET)
    app = create_app(
        _config(settings, terminal_origin="https://term.example.com"),
        monitor=FakeMonitor(),  # type: ignore[arg-type]
        storage=storage,
    )
    client = TestClient(app)
    client.post("/api/session", json={"initData": _init_data(settings.telegram_token)})
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert meta.json()["terminal_origin"] == "https://term.example.com"
    policy = meta.headers["Content-Security-Policy"]
    assert "frame-src 'self' https://term.example.com" in policy


def test_non_admin_is_rejected(
    make_settings: Callable[..., Settings], storage: Storage
) -> None:
    settings = make_settings(webapp_enabled=True, webapp_secret=SECRET)
    app = create_app(
        _config(settings),
        monitor=FakeMonitor(),  # type: ignore[arg-type]
        storage=storage,
    )
    client = TestClient(app)
    response = client.post(
        "/api/session", json={"initData": _init_data(settings.telegram_token, 999)}
    )
    assert response.status_code == 403


def test_config_requires_secret(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    base = (
        "TELEGRAM_BOT_TOKEN=123456:ABC\n"
        "TELEGRAM_ADMIN_IDS=42\n"
        "PANEL_URL=https://panel.example.com\n"
        "PANEL_API_TOKEN=t\n"
        "SERVICES=x-ui\n"
        "WEBAPP_ENABLED=1\n"
    )
    env.write_text(base + "WEBAPP_SECRET=short\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="WEBAPP_SECRET"):
        load_settings(env)
    env.write_text(base + f"WEBAPP_SECRET={SECRET}\n", encoding="utf-8")
    config = WebAppConfig.load(env)
    assert config.secret == SECRET


def test_installer_webapp_flag() -> None:
    from stacksentry.settings import BASE_DIR

    text = (BASE_DIR / "install.sh").read_text(encoding="utf-8")
    assert "--webapp" in text
    assert "stacksentry-webapp.service" in text
    assert "create_app_from_env" in text
    assert "WEBAPP_TERMINAL_ORIGIN" in text
    assert "terminal-ssh" in text
    assert 'chmod 600 "$TERM_SSH_KEY"' in text


def test_cookie_flags(make_settings: Callable[..., Settings], storage: Storage) -> None:
    settings = make_settings(webapp_enabled=True, webapp_secret=SECRET)
    app = create_app(
        _config(settings),
        monitor=FakeMonitor(),  # type: ignore[arg-type]
        storage=storage,
    )
    client = TestClient(app)
    response = client.post(
        "/api/session", json={"initData": _init_data(settings.telegram_token)}
    )
    cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" not in cookie

    secure_app = create_app(
        _config(settings, origin="https://app.example.com"),
        monitor=FakeMonitor(),  # type: ignore[arg-type]
        storage=storage,
    )
    secure_response = TestClient(secure_app).post(
        "/api/session", json={"initData": _init_data(settings.telegram_token)}
    )
    secure_cookie = secure_response.headers.get("set-cookie", "").lower()
    assert "secure" in secure_cookie
    assert "samesite=none" in secure_cookie
