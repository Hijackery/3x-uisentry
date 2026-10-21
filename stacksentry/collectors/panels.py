"""Адаптеры API панелей: 3X-UI, Marzban, Remnawave.

Единый фасад для Monitor/ActionManager. 3X-UI остаётся на существующем
PanelClient; Marzban и Remnawave читают пользователей и отдают Subscription.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import httpx

from ..models import PanelHealth, Subscription
from ..settings import Settings
from .panel import PanelClient, PanelError, PanelForbidden


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _expiry_ms(value: Any) -> int:
    if value in (None, "", 0, "0"):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number * 1000) if number < 10_000_000_000 else int(number)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1000)


class _HttpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        verify: bool | str = settings.panel_verify_tls
        if settings.panel_ca_bundle:
            verify = settings.panel_ca_bundle
        self.base = settings.panel_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=settings.panel_timeout,
            verify=verify,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, *, headers: dict[str, str] | None = None) -> Any:
        response = await self._client.get(f"{self.base}{path}", headers=headers)
        return self._parse(response, path)

    async def post(
        self, path: str, *, headers: dict[str, str] | None = None, data: Any = None
    ) -> Any:
        response = await self._client.post(
            f"{self.base}{path}", headers=headers, data=data
        )
        return self._parse(response, path)

    def _parse(self, response: httpx.Response, path: str) -> Any:
        if response.status_code in {401, 403}:
            raise PanelForbidden(f"HTTP {response.status_code} на {path}")
        if 300 <= response.status_code < 400:
            raise PanelError(f"панель ответила редиректом HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PanelError(f"HTTP {response.status_code} на {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise PanelError(f"панель вернула не-JSON на {path}") from exc


class MarzbanClient(_HttpClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._token = ""

    async def _login(self) -> str:
        payload = await self.post(
            "/api/admin/token",
            data={
                "username": self.settings.panel_username,
                "password": self.settings.panel_password,
                "grant_type": "password",
            },
        )
        token = (
            str(payload.get("access_token", "")) if isinstance(payload, dict) else ""
        )
        if not token:
            raise PanelForbidden("Marzban не выдал access_token")
        self._token = token
        return token

    async def _auth_get(self, path: str) -> Any:
        token = self._token or await self._login()
        try:
            return await self.get(path, headers={"Authorization": f"Bearer {token}"})
        except PanelForbidden:
            token = await self._login()
            return await self.get(path, headers={"Authorization": f"Bearer {token}"})

    async def health(self) -> PanelHealth:
        try:
            stats = await self._auth_get("/api/system")
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"Marzban: {exc}",
                api_authorized=False,
            )
        except (PanelError, httpx.HTTPError) as exc:
            return PanelHealth(
                reachable=False, status_code=None, latency_ms=None, error=str(exc)
            )
        version = str(stats.get("version", "")) if isinstance(stats, dict) else ""
        return PanelHealth(
            reachable=True,
            status_code=200,
            latency_ms=None,
            error=None,
            panel_version=version,
            api_authorized=True,
        )

    async def subscriptions(self) -> list[Subscription]:
        result: list[Subscription] = []
        offset = 0
        while True:
            page = await self._auth_get(f"/api/users?offset={offset}&limit=1000")
            users = page.get("users", []) if isinstance(page, dict) else []
            total = _int(page.get("total", 0)) if isinstance(page, dict) else 0
            for user in users:
                if not isinstance(user, dict):
                    continue
                status = str(user.get("status", ""))
                result.append(
                    Subscription(
                        email=str(user.get("username", "")),
                        inbound_remark="marzban",
                        enabled=status == "active",
                        expiry_time_ms=_expiry_ms(user.get("expire")),
                        total_bytes=_int(user.get("data_limit")),
                        used_bytes=_int(user.get("used_traffic")),
                        sub_id=str(
                            user.get("subscription_url") or user.get("username", "")
                        ),
                    )
                )
            offset += len(users)
            if not users or offset >= total:
                return result

    async def restart_xray(self) -> dict[str, Any]:
        await self.post(
            "/api/core/restart",
            headers={"Authorization": f"Bearer {self._token or await self._login()}"},
        )
        return {"success": True}


class RemnawaveClient(_HttpClient):
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.panel_api_token}"}

    async def health(self) -> PanelHealth:
        try:
            await self.get("/api/users?size=1", headers=self._headers())
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"Remnawave: {exc}",
                api_authorized=False,
            )
        except (PanelError, httpx.HTTPError) as exc:
            return PanelHealth(
                reachable=False, status_code=None, latency_ms=None, error=str(exc)
            )
        return PanelHealth(
            reachable=True,
            status_code=200,
            latency_ms=None,
            error=None,
            api_authorized=True,
        )

    async def subscriptions(self) -> list[Subscription]:
        result: list[Subscription] = []
        start = 0
        while True:
            page = await self.get(
                f"/api/users?size=1000&start={start}", headers=self._headers()
            )
            users = page.get("users", []) if isinstance(page, dict) else []
            total = _int(page.get("total", 0)) if isinstance(page, dict) else 0
            for user in users:
                if not isinstance(user, dict):
                    continue
                status = str(user.get("status", ""))
                result.append(
                    Subscription(
                        email=str(user.get("username", "")),
                        inbound_remark="remnawave",
                        enabled=status.upper() == "ACTIVE",
                        expiry_time_ms=_expiry_ms(user.get("expireAt")),
                        total_bytes=_int(user.get("trafficLimitBytes")),
                        used_bytes=_int(
                            user.get("trafficUsedBytes") or user.get("usedTrafficBytes")
                        ),
                        sub_id=str(
                            user.get("subscriptionUrl")
                            or user.get("shortUuid")
                            or user.get("username", "")
                        ),
                    )
                )
            start += len(users)
            if not users or start >= total:
                return result

    async def restart_xray(self) -> dict[str, Any]:
        raise PanelError("Remnawave: рестарт xray из бота не поддерживается")


class PanelFacade:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._three = PanelClient(settings) if settings.panel_type == "3x-ui" else None
        if settings.panel_type == "marzban":
            self._impl: Any = MarzbanClient(settings)
        elif settings.panel_type == "remnawave":
            self._impl = RemnawaveClient(settings)
        else:
            self._impl = None

    async def close(self) -> None:
        if self._three is not None:
            await self._three.close()
        if self._impl is not None:
            await self._impl.close()

    async def health(self) -> PanelHealth:
        if self._three is not None:
            return await self._three.health()
        assert self._impl is not None
        return cast(PanelHealth, await self._impl.health())

    async def inbounds(self) -> list[dict[str, Any]]:
        return await self._three.inbounds() if self._three is not None else []

    async def subscriptions(self) -> list[Subscription]:
        return await self._impl.subscriptions() if self._impl is not None else []

    async def panel_update_info(self) -> dict[str, Any]:
        if self._three is not None:
            return await self._three.panel_update_info()
        return {}

    async def logs(self, count: int = 50) -> str:
        if self._three is None:
            raise PanelError("логи панели доступны только для 3X-UI")
        return await self._three.logs(count)

    async def restart_xray(self) -> dict[str, Any]:
        if self._three is not None:
            return await self._three.restart_xray()
        assert self._impl is not None
        return cast(dict[str, Any], await self._impl.restart_xray())


def build_panel(settings: Settings) -> PanelFacade:
    return PanelFacade(settings)
