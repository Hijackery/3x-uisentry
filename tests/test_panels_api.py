"""API-адаптеры Marzban и Remnawave: маппинг пользователей в подписки."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from stacksentry.collectors.panels import MarzbanClient, RemnawaveClient
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
