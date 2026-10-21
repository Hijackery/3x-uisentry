"""Адаптеры API панелей: 3X-UI, Marzban, Marzneshin, PasarGuard,
Remnawave, S-UI, Hiddify и Outline.

Единый фасад для Monitor/ActionManager. 3X-UI остаётся на существующем
PanelClient; другие панели читают пользователей и отдают Subscription.
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
    # Панели отдают naive-время в UTC; не зависим от часового пояса сервера.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def _gigabytes_to_bytes(value: Any) -> int:
    """Hiddify отдаёт лимит/расход в гигабайтах (1 GB = 1024**3)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    return int(number * 1024**3)


def _hiddify_expiry_ms(user: dict[str, Any]) -> int:
    """Срок = start_date + package_days; 0 дней/нет даты — без срока."""
    days = user.get("package_days")
    start = user.get("start_date")
    if not isinstance(days, int) or days <= 0 or not start:
        return 0
    try:
        parsed = dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int((parsed + dt.timedelta(days=days)).timestamp() * 1000)


class _HttpClient:
    def __init__(self, settings: Settings, api_prefix: str = "") -> None:
        self.settings = settings
        verify: bool | str = settings.panel_verify_tls
        if settings.panel_ca_bundle:
            verify = settings.panel_ca_bundle
        self.base = f"{settings.panel_url.rstrip('/')}{api_prefix}"
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
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        json: Any = None,
    ) -> Any:
        if json is not None:
            response = await self._client.post(
                f"{self.base}{path}", headers=headers, json=json
            )
        else:
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
                        email=str(
                            user.get("username", "")
                            or user.get("email", "")
                            or user.get("uuid", "")
                        ),
                        inbound_remark="remnawave",
                        enabled=status.upper() == "ACTIVE",
                        expiry_time_ms=_expiry_ms(
                            user.get("expireAt")
                            or user.get("expireDate")
                            or user.get("expiryTime")
                        ),
                        total_bytes=_int(
                            user.get("trafficLimitBytes") or user.get("dataLimit")
                        ),
                        used_bytes=_int(
                            user.get("trafficUsedBytes")
                            or user.get("usedTrafficBytes")
                            or user.get("usedTraffic")
                        ),
                        sub_id=str(
                            user.get("subscriptionUrl")
                            or user.get("shortUuid")
                            or user.get("uuid")
                            or user.get("username", "")
                        ),
                    )
                )
            start += len(users)
            if not users or start >= total:
                return result

    async def restart_xray(self) -> dict[str, Any]:
        raise PanelError("Remnawave: рестарт xray из бота не поддерживается")


class MarzneshinClient(_HttpClient):
    """Marzneshin: OAuth2 на ``/api/admins/token``, пользователи по страницам."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._token = ""

    async def _login(self) -> str:
        payload = await self.post(
            "/api/admins/token",
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
            raise PanelForbidden("Marzneshin не выдал access_token")
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
            await self._auth_get("/api/admins/current")
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"Marzneshin: {exc}",
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
        page_number = 1
        while True:
            page = await self._auth_get(f"/api/users?page={page_number}&size=1000")
            users = page.get("items", []) if isinstance(page, dict) else []
            pages = _int(page.get("pages", 0)) if isinstance(page, dict) else 0
            if pages <= 0 and isinstance(page, dict):
                total = _int(page.get("total", 0))
                size = _int(page.get("size", len(users))) or len(users) or 1000
                pages = (total + size - 1) // size
            for user in users:
                if not isinstance(user, dict):
                    continue
                status = bool(user.get("enabled", user.get("is_active", True)))
                result.append(
                    Subscription(
                        email=str(user.get("username", "")),
                        inbound_remark="marzneshin",
                        enabled=status,
                        expiry_time_ms=_expiry_ms(user.get("expire_date")),
                        total_bytes=_int(user.get("data_limit")),
                        used_bytes=_int(user.get("used_traffic")),
                        sub_id=str(
                            user.get("subscription_url") or user.get("username", "")
                        ),
                    )
                )
            if not users or page_number >= pages:
                return result
            page_number += 1

    async def restart_xray(self) -> dict[str, Any]:
        raise PanelError("Marzneshin: рестарт ядра из бота не поддерживается")


class PasarGuardClient(_HttpClient):
    """PasarGuard: Marzban-совместимый OAuth2, ``/api/users`` и ``/api/cores``."""

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
            raise PanelForbidden("PasarGuard не выдал access_token")
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
            await self._auth_get("/api/system")
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"PasarGuard: {exc}",
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
                        inbound_remark="pasarguard",
                        enabled=status.lower() == "active",
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
        token = self._token or await self._login()
        headers = {"Authorization": f"Bearer {token}"}
        cores = await self.get("/api/cores?offset=0&limit=1", headers=headers)
        items = cores.get("cores", []) if isinstance(cores, dict) else []
        if not items or not isinstance(items[0], dict):
            raise PanelError("PasarGuard: не найден core для рестарта")
        core_id = _int(items[0].get("id"))
        if core_id <= 0:
            raise PanelError("PasarGuard: core без id")
        await self.post(f"/api/core/{core_id}/restart", headers=headers)
        return {"success": True}


class OutlineClient(_HttpClient):
    """Outline Server API.

    ``PANEL_URL`` — полный адрес вместе с секретным ``SB_API_PREFIX``
    (например ``https://host:port/SecretPath``); отдельного токена нет,
    доступ защищён самим путём.
    """

    async def health(self) -> PanelHealth:
        version = ""
        try:
            info = await self.get("/server")
            if isinstance(info, dict):
                version = str(info.get("version", ""))
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"Outline: {exc}",
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
            panel_version=version,
            api_authorized=True,
        )

    async def subscriptions(self) -> list[Subscription]:
        try:
            payload = await self.get("/access-keys")
        except (PanelError, httpx.HTTPError):
            return []
        keys = payload.get("accessKeys", []) if isinstance(payload, dict) else []
        transferred: dict[str, Any] = {}
        try:
            metrics = await self.get("/metrics/transfer")
            if isinstance(metrics, dict):
                raw = metrics.get("bytesTransferredByUserId")
                if isinstance(raw, dict):
                    transferred = raw
        except (PanelError, httpx.HTTPError):
            transferred = {}
        result: list[Subscription] = []
        for key in keys:
            if not isinstance(key, dict):
                continue
            key_id = str(key.get("id", ""))
            name = str(key.get("name") or key_id)
            limit = key.get("dataLimit")
            total = 0
            if isinstance(limit, dict):
                total = _int(limit.get("bytes"))
            result.append(
                Subscription(
                    email=name,
                    inbound_remark="outline",
                    enabled=True,
                    expiry_time_ms=0,
                    total_bytes=total,
                    used_bytes=_int(transferred.get(key_id)),
                    sub_id=str(key.get("accessUrl") or key_id),
                )
            )
        return result

    async def restart_xray(self) -> dict[str, Any]:
        raise PanelError("Outline: рестарт ядра из бота не поддерживается")


class SUIClient(_HttpClient):
    """S-UI REST API v2.

    База: ``<PANEL_URL>/<webPath>`` (по умолчанию ``http://host:2095/app``),
    запросы уходят в ``/apiv2/...`` с заголовком ``Token``. Ошибки приходят
    с HTTP 200 и ``success=false`` — это проверяется отдельно.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, api_prefix="/apiv2")

    def _headers(self) -> dict[str, str]:
        return {"Token": self.settings.panel_api_token}

    def _parse(self, response: httpx.Response, path: str) -> Any:
        payload = super()._parse(response, path)
        if isinstance(payload, dict) and payload.get("success") is False:
            msg = str(payload.get("msg", "")).strip() or "ошибка панели"
            lowered = msg.lower()
            if "token" in lowered or "unauthorized" in lowered or "login" in lowered:
                raise PanelForbidden(f"S-UI: {msg}")
            raise PanelError(f"S-UI: {msg}")
        if isinstance(payload, dict) and "obj" in payload:
            return payload["obj"]
        return payload

    async def health(self) -> PanelHealth:
        try:
            await self.get("/status?r=sbd", headers=self._headers())
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"S-UI: {exc}",
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
        try:
            data = await self.get("/clients", headers=self._headers())
        except (PanelError, httpx.HTTPError):
            return []
        clients: list[Any] = []
        if isinstance(data, dict):
            raw = data.get("clients")
            clients = raw if isinstance(raw, list) else []
        elif isinstance(data, list):
            clients = data
        for client in clients:
            if not isinstance(client, dict):
                continue
            name = str(client.get("name") or client.get("remark") or "")
            expiry_seconds = _int(client.get("expiry"))
            result.append(
                Subscription(
                    email=name,
                    inbound_remark="s-ui",
                    enabled=bool(client.get("enable", True)),
                    expiry_time_ms=expiry_seconds * 1000 if expiry_seconds > 0 else 0,
                    total_bytes=_int(client.get("volume")),
                    used_bytes=_int(client.get("up")) + _int(client.get("down")),
                    sub_id=str(client.get("name") or client.get("id") or name),
                )
            )
        return result

    async def restart_xray(self) -> dict[str, Any]:
        try:
            await self.post("/restartSb", headers=self._headers())
        except (PanelError, httpx.HTTPError) as exc:
            raise PanelError(f"S-UI: {exc}") from exc
        return {"success": True}


class HiddifyClient(_HttpClient):
    """Hiddify Manager API v2.

    ``PANEL_URL`` должен включать admin proxy path, например
    ``https://example.com/AbCdEf``; запросы идут в ``/api/v2/...``
    с заголовком ``Hiddify-API-Key``.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, api_prefix="/api/v2")

    def _headers(self) -> dict[str, str]:
        return {"Hiddify-API-Key": self.settings.panel_api_token}

    async def health(self) -> PanelHealth:
        version = ""
        try:
            info = await self.get("/panel/info/", headers=self._headers())
            if isinstance(info, dict):
                version = str(info.get("version", ""))
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=None,
                latency_ms=None,
                error=f"Hiddify: {exc}",
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
            panel_version=version,
            api_authorized=True,
        )

    async def subscriptions(self) -> list[Subscription]:
        result: list[Subscription] = []
        try:
            users = await self.get("/admin/users/", headers=self._headers())
        except (PanelError, httpx.HTTPError):
            return []
        if isinstance(users, dict):
            raw = users.get("users") or users.get("items") or users.get("obj")
            items = raw if isinstance(raw, list) else []
        elif isinstance(users, list):
            items = users
        else:
            items = []
        for user in items:
            if not isinstance(user, dict):
                continue
            name = str(
                user.get("name") or user.get("uuid") or user.get("username") or ""
            )
            result.append(
                Subscription(
                    email=name,
                    inbound_remark="hiddify",
                    enabled=bool(user.get("enable", user.get("is_active", True))),
                    expiry_time_ms=_hiddify_expiry_ms(user),
                    total_bytes=_gigabytes_to_bytes(user.get("usage_limit_GB")),
                    used_bytes=_gigabytes_to_bytes(user.get("current_usage_GB")),
                    sub_id=str(user.get("uuid") or name),
                )
            )
        return result

    async def restart_xray(self) -> dict[str, Any]:
        raise PanelError("Hiddify: рестарт xray из бота не поддерживается")


class PanelFacade:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._three = PanelClient(settings) if settings.panel_type == "3x-ui" else None
        if settings.panel_type == "marzban":
            self._impl: Any = MarzbanClient(settings)
        elif settings.panel_type == "marzneshin":
            self._impl = MarzneshinClient(settings)
        elif settings.panel_type == "pasarguard":
            self._impl = PasarGuardClient(settings)
        elif settings.panel_type == "remnawave":
            self._impl = RemnawaveClient(settings)
        elif settings.panel_type == "s-ui":
            self._impl = SUIClient(settings)
        elif settings.panel_type == "hiddify":
            self._impl = HiddifyClient(settings)
        elif settings.panel_type == "outline":
            self._impl = OutlineClient(settings)
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
        if self._impl is None:
            return PanelHealth(
                reachable=False,
                status_code=None,
                latency_ms=None,
                error="неизвестный panel_type",
            )
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
        if self._impl is None:
            raise PanelError("рестарт недоступен")
        return cast(dict[str, Any], await self._impl.restart_xray())


def build_panel(settings: Settings) -> PanelFacade:
    return PanelFacade(settings)
