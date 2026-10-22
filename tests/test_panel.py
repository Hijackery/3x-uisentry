"""Проверки клиента панели 3X-UI на поддельном транспорте httpx."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from stacksentry.collectors.panel import (
    PanelClient,
    PanelError,
    PanelForbidden,
    parse_inbound,
)
from stacksentry.settings import Settings


def client_with(
    make_settings: Callable[..., Settings],
    handler: httpx.MockTransport,
    **overrides: object,
) -> PanelClient:
    """Клиент с подменённым транспортом: сеть не трогаем, ответы задаём сами."""
    settings: Settings = make_settings(**overrides)
    client = PanelClient(settings)
    client._client = httpx.AsyncClient(
        transport=handler,
        headers={
            "Authorization": f"Bearer {settings.panel_api_token}",
            "Accept": "application/json",
        },
        follow_redirects=True,
    )
    return client


async def test_request_builds_api_url_with_base_path(
    make_settings: Callable[..., Settings],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "obj": {"uptime": 5}})

    client = client_with(
        make_settings, httpx.MockTransport(handler), panel_base_path="secret"
    )
    status = await client.server_status()
    await client.close()

    assert status == {"uptime": 5}
    assert str(seen[0].url) == (
        "https://panel.example.com/secret/panel/api/server/status"
    )
    assert seen[0].headers["authorization"] == "Bearer secret-token"


async def test_unwraps_obj_payload(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": True, "obj": [{"id": 1, "remark": "in"}]}
        )

    client = client_with(make_settings, httpx.MockTransport(handler))
    inbounds = await client.inbounds()
    await client.close()

    assert inbounds == [{"id": 1, "remark": "in"}]


async def test_403_is_reported_as_scope_problem(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False, "msg": "forbidden"})

    client = client_with(make_settings, httpx.MockTransport(handler))
    with pytest.raises(PanelForbidden, match="область действия токена"):
        await client.inbounds()
    await client.close()


async def test_401_is_reported_as_wrong_token(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client = client_with(make_settings, httpx.MockTransport(handler))
    with pytest.raises(PanelForbidden, match="токен отвергнут"):
        await client.server_status()
    await client.close()


async def test_panel_error_message_is_surfaced(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "msg": "not found"})

    client = client_with(make_settings, httpx.MockTransport(handler))
    with pytest.raises(PanelError, match="not found"):
        await client.server_status()
    await client.close()


async def test_health_reports_versions_and_authorization(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server/status"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "obj": {
                        "panelVersion": "2.6.0",
                        "xray": {"version": "25.1.0", "state": "running"},
                    },
                },
            )
        return httpx.Response(200, text="ok")

    client = client_with(make_settings, httpx.MockTransport(handler))
    health = await client.health()
    await client.close()

    assert health.reachable
    assert health.api_authorized
    assert health.panel_version == "2.6.0"
    assert health.xray_state == "running"
    assert health.latency_ms is not None


async def test_health_flags_wrong_base_path(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(404, text="404 page not found")
        return httpx.Response(200, json={"success": True, "obj": {}})

    client = client_with(make_settings, httpx.MockTransport(handler))
    health = await client.health()
    await client.close()

    assert not health.reachable
    assert health.status_code == 404
    assert "PANEL_BASE_PATH" in (health.error or "")


async def test_health_keeps_panel_reachable_when_token_rejected(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server/status"):
            return httpx.Response(403, json={"success": False})
        return httpx.Response(200, text="login page")

    client = client_with(make_settings, httpx.MockTransport(handler))
    health = await client.health()
    await client.close()

    assert health.reachable  # веб-панель жива
    assert not health.api_authorized
    assert "не пускает" in (health.error or "")


async def test_health_marks_panel_down_on_network_error(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = client_with(make_settings, httpx.MockTransport(handler))
    health = await client.health()
    await client.close()

    assert not health.reachable
    assert "connection refused" in (health.error or "")


async def test_restart_xray_posts(
    make_settings: Callable[..., Settings],
) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"success": True, "obj": {}})

    client = client_with(make_settings, httpx.MockTransport(handler))
    await client.restart_xray()
    await client.close()

    assert calls == [("POST", "/panel/api/server/restartXrayService")]


def test_parse_inbound_reads_json_settings() -> None:
    raw = {
        "remark": "vless-in",
        "port": "443",
        "protocol": "vless",
        "enable": True,
        "up": 100,
        "down": 200,
        "total": 0,
        "settings": json.dumps(
            {"clients": [{"email": "a@b.c", "totalGB": 10, "up": 1, "down": 2}]}
        ),
    }
    summary, clients = parse_inbound(raw)

    assert summary["port"] == 443
    assert summary["remark"] == "vless-in"
    assert clients[0]["email"] == "a@b.c"


def test_parse_inbound_survives_broken_settings() -> None:
    summary, clients = parse_inbound({"remark": "x", "settings": "{не json"})
    assert clients == []
    assert summary["port"] == 0


async def test_session_marks_requests_as_xhr(
    make_settings: Callable[..., Settings],
) -> None:
    """3X-UI иначе маскирует неверный токен под 404."""
    client = PanelClient(make_settings())
    session = client._build_session()
    try:
        assert session.headers["x-requested-with"] == "XMLHttpRequest"
    finally:
        await session.aclose()
