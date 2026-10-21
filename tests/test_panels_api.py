"""API-адаптеры Marzban, Remnawave, S-UI и Hiddify: маппинг в подписки."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from stacksentry.collectors.panels import (
    HiddifyClient,
    MarzbanClient,
    RemnawaveClient,
    SUIClient,
    build_panel,
)
from stacksentry.settings import Settings


async def test_marzban_users_to_subscriptions(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        panel_type="marzban",
        panel_url="https://marzban.example.com",
        panel_username="admin",
        panel_password="secret",
    )
    client = MarzbanClient(settings)

    async def fake(path: str) -> dict[str, object]:
        return {
            "users": [
                {
                    "username": "alice",
                    "status": "active",
                    "expire": 1_700_000_000,
                    "data_limit": 1000,
                    "used_traffic": 400,
                    "subscription_url": "https://sub/alice",
                },
                {"username": "bob", "status": "expired"},
            ],
            "total": 2,
        }

    monkeypatch.setattr(client, "_auth_get", fake)
    try:
        subs = await client.subscriptions()
    finally:
        await client.close()

    alice = next(item for item in subs if item.email == "alice")
    assert alice.enabled is True
    assert alice.total_bytes == 1000
    assert alice.used_bytes == 400
    assert alice.expiry_time_ms == 1_700_000_000_000
    assert alice.sub_id == "https://sub/alice"
    bob = next(item for item in subs if item.email == "bob")
    assert bob.enabled is False


async def test_remnawave_users_to_subscriptions(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        panel_type="remnawave",
        panel_url="https://remna.example.com",
        panel_api_token="token",
    )
    client = RemnawaveClient(settings)

    async def fake(path: str, **_kwargs: object) -> dict[str, object]:
        return {
            "users": [
                {
                    "username": "carol",
                    "status": "ACTIVE",
                    "expireAt": "2030-01-01T00:00:00.000Z",
                    "trafficLimitBytes": 2000,
                    "trafficUsedBytes": 500,
                    "subscriptionUrl": "https://sub/carol",
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(client, "get", fake)
    try:
        subs = await client.subscriptions()
    finally:
        await client.close()

    carol = subs[0]
    assert carol.enabled is True
    assert carol.total_bytes == 2000
    assert carol.used_bytes == 500
    assert carol.expiry_time_ms > 0


async def test_sui_clients_to_subscriptions(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        panel_type="s-ui",
        panel_url="http://sui.example.com:2095/app",
        panel_api_token="sui-token",
    )
    client = SUIClient(settings)

    async def fake(path: str, **_kwargs: object) -> dict[str, object]:
        assert path == "/clients"
        return {
            "clients": [
                {
                    "name": "alice",
                    "enable": True,
                    "volume": 10 * 1024**3,
                    "expiry": 1_700_000_000,
                    "up": 300,
                    "down": 200,
                },
                {"name": "bob", "enable": False, "volume": 0, "expiry": 0},
            ]
        }

    monkeypatch.setattr(client, "get", fake)
    try:
        subs = await client.subscriptions()
    finally:
        await client.close()

    alice = next(item for item in subs if item.email == "alice")
    assert alice.enabled is True
    assert alice.total_bytes == 10 * 1024**3
    assert alice.used_bytes == 500
    assert alice.expiry_time_ms == 1_700_000_000_000
    bob = next(item for item in subs if item.email == "bob")
    assert bob.enabled is False
    assert bob.expiry_time_ms == 0


async def test_sui_envelope_invalid_token_is_forbidden(
    make_settings: Callable[..., Settings],
) -> None:
    from stacksentry.collectors.panel import PanelForbidden

    settings = make_settings(
        panel_type="s-ui",
        panel_url="http://sui.example.com/app",
        panel_api_token="bad",
    )
    client = SUIClient(settings)
    response = httpx.Response(
        200, json={"success": False, "msg": "invalid token", "obj": None}
    )
    try:
        with pytest.raises(PanelForbidden, match="invalid token"):
            client._parse(response, "/clients")
    finally:
        await client.close()


async def test_hiddify_users_to_subscriptions(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        panel_type="hiddify",
        panel_url="https://hiddify.example.com/secretpath",
        panel_api_token="admin-uuid",
    )
    client = HiddifyClient(settings)

    async def fake(path: str, **_kwargs: object) -> list[dict[str, object]]:
        assert path == "/admin/users/"
        return [
            {
                "uuid": "u-1",
                "name": "dave",
                "enable": True,
                "usage_limit_GB": 50,
                "current_usage_GB": 12.5,
                "package_days": 30,
                "start_date": "2030-01-01",
            },
            {"uuid": "u-2", "name": "erin", "enable": False},
        ]

    monkeypatch.setattr(client, "get", fake)
    try:
        subs = await client.subscriptions()
    finally:
        await client.close()

    dave = next(item for item in subs if item.email == "dave")
    assert dave.enabled is True
    assert dave.total_bytes == 50 * 1024**3
    assert dave.used_bytes == int(12.5 * 1024**3)
    assert dave.expiry_time_ms > 0
    assert dave.sub_id == "u-1"
    erin = next(item for item in subs if item.email == "erin")
    assert erin.enabled is False
    assert erin.total_bytes == 0


@pytest.mark.parametrize(
    ("panel_type", "expected"),
    [("s-ui", SUIClient), ("hiddify", HiddifyClient)],
)
async def test_facade_builds_new_panel_clients(
    make_settings: Callable[..., Settings], panel_type: str, expected: type
) -> None:
    settings = make_settings(panel_type=panel_type)
    facade = build_panel(settings)
    try:
        assert isinstance(facade._impl, expected)
    finally:
        await facade.close()
