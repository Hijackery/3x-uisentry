"""Клиент API панели 3X-UI и проверка её доступности.

Авторизация — Bearer-токен панели (``Authorization: Bearer ...``).
У 3X-UI такой запрос помечается как api_authed и освобождается от CSRF-проверки,
в отличие от авторизации через логин/пароль. Токен создаётся в панели
(Settings → API Tokens) или через ``x-ui setting -getApiToken true``.

Области действия токена (``-tokenScope``):
* ``monitor``   — только ``/panel/api/server/*`` в режиме чтения;
* ``node-sync`` — плюс список inbound'ов, клиенты, рестарт xray;
* ``admin``     — полный доступ.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from ..models import PanelHealth
from ..settings import Settings

#: Пауза между повторами запроса при 5xx и сетевых сбоях.
RETRY_DELAY = 1.0


class PanelError(RuntimeError):
    """Панель ответила ошибкой или недоступна."""


class PanelForbidden(PanelError):
    """Токен есть, но его область действия не покрывает запрос."""


class PanelClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.api_base
        self._client: httpx.AsyncClient | None = None
        self._authorized: bool | None = None

    def _session(self) -> httpx.AsyncClient:
        if self._client is None:
            # Свой CA-файл важнее флага проверки: он задаёт, чему именно
            # доверяем (пиннинг сертификата панели или внутреннего CA).
            verify: bool | str = self.settings.panel_verify_tls
            if self.settings.panel_ca_bundle:
                verify = self.settings.panel_ca_bundle
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.panel_timeout),
                verify=verify,
                headers={
                    "Authorization": f"Bearer {self.settings.panel_api_token}",
                    "Accept": "application/json",
                    "User-Agent": "uisentry-bot/1.0",
                },
                # Редиректы намеренно не следуем: иначе панель (или прокси перед
                # ней) могла бы увести запрос на сторонний хост, и httpx отправил
                # бы туда заголовок Authorization с токеном панели.
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, path: str) -> str:
        """Полный URL метода API: .../panel/api/<path>."""
        return f"{self.base}/{path.lstrip('/')}"

    async def _request(self, path: str, method: str = "GET") -> Any:
        if not self.settings.panel_enabled:
            raise PanelError(
                "API панели не настроен — заполните PANEL_URL и PANEL_API_TOKEN "
                "(или используйте режим monitor-only)"
            )
        session = self._session()
        url = self._url(path)

        for attempt in range(2):
            try:
                response = await session.request(method, url)
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                raise PanelError(f"панель недоступна: {exc}") from exc

            if response.status_code in (401, 403):
                self._authorized = False
                hint = (
                    "область действия токена не покрывает этот метод"
                    if response.status_code == 403
                    else "токен отвергнут панелью"
                )
                raise PanelForbidden(f"HTTP {response.status_code}: {hint} ({path})")

            if response.status_code >= 500 and attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
                continue

            if 300 <= response.status_code < 400:
                location = response.headers.get("location", "")
                raise PanelError(
                    f"панель ответила редиректом HTTP {response.status_code} "
                    f"на {location or 'другой адрес'}: укажите в PANEL_URL "
                    "точную схему (http/https) и порт панели"
                )

            if response.status_code >= 400:
                raise PanelError(f"HTTP {response.status_code} на {path}")

            self._authorized = True
            try:
                payload = response.json()
            except ValueError as exc:
                raise PanelError(f"панель вернула не-JSON на {path}") from exc

            if isinstance(payload, dict):
                if payload.get("success") is False:
                    raise PanelError(
                        f"ошибка панели: {payload.get('msg', 'без описания')}"
                    )
                if "obj" in payload:
                    return payload["obj"]
            return payload

        raise PanelError(f"панель не ответила на {path}")

    # ------------------------------------------------------------- проверки

    async def health(self) -> PanelHealth:
        """Доступность веб-панели: код ответа, задержка, версии xray и панели.

        Веб-интерфейс и API проверяются отдельно: панель может отвечать на
        ``/``, но не пускать бота в API (неверный токен или область действия).
        """
        # Нельзя наследовать «авторизован» от прошлого опроса: иначе после
        # успешного запроса сбой API выглядит как «токен принят».
        self._authorized = None
        loop = asyncio.get_running_loop()
        started = loop.time()

        try:
            response = await self._session().get(
                f"{self.settings.panel_web_base}/",
                timeout=self.settings.panel_timeout,
            )
        except httpx.HTTPError as exc:
            return PanelHealth(
                reachable=False,
                status_code=None,
                latency_ms=None,
                error=str(exc),
            )

        latency_ms = round((loop.time() - started) * 1000, 1)
        if response.status_code >= 400:
            return PanelHealth(
                reachable=False,
                status_code=response.status_code,
                latency_ms=latency_ms,
                error=(
                    f"HTTP {response.status_code} — проверьте PANEL_URL"
                    " и PANEL_BASE_PATH (webBasePath панели)"
                ),
            )

        try:
            status = await self.server_status()
        except PanelForbidden as exc:
            return PanelHealth(
                reachable=True,
                status_code=response.status_code,
                latency_ms=latency_ms,
                error=f"API не пускает бота: {exc}",
                api_authorized=False,
            )
        except PanelError as exc:
            return PanelHealth(
                reachable=True,
                status_code=response.status_code,
                latency_ms=latency_ms,
                error=f"API недоступен: {exc}",
                api_authorized=False,
            )

        xray = status.get("xray")
        if not isinstance(xray, dict):
            xray = {}
        version = xray.get("version") or status.get("xrayVersion", "")
        return PanelHealth(
            reachable=True,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error=None,
            panel_version=str(status.get("panelVersion", "")),
            xray_version=str(version),
            xray_state=str(xray.get("state", "")),
            api_authorized=self._authorized is True,
        )

    async def server_status(self) -> dict[str, Any]:
        result = await self._request("server/status")
        return result if isinstance(result, dict) else {}

    async def inbounds(self) -> list[dict[str, Any]]:
        result = await self._request("inbounds/list")
        return list(result) if isinstance(result, list) else []

    async def panel_update_info(self) -> dict[str, Any]:
        result = await self._request("server/getPanelUpdateInfo")
        return result if isinstance(result, dict) else {}

    async def logs(self, count: int = 50) -> str:
        """Логи x-ui через API (POST — так зарегистрирован маршрут в панели)."""
        result = await self._request(
            f"server/logs/{max(1, min(count, 500))}", method="POST"
        )
        if isinstance(result, list):
            return "\n".join(str(item) for item in result)
        return str(result)

    async def restart_xray(self) -> dict[str, Any]:
        result = await self._request("server/restartXrayService", method="POST")
        return result if isinstance(result, dict) else {}


def parse_inbound(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Делит inbound на сводку и список клиентов.

    Поле ``settings`` в панели приходит JSON-строкой, поэтому разбирается
    вручную; битые значения не должны ронять весь сбор.
    """
    settings_field = raw.get("settings")
    clients: list[dict[str, Any]] = []
    if isinstance(settings_field, str):
        try:
            parsed = json.loads(settings_field)
            if isinstance(parsed, dict):
                raw_clients = parsed.get("clients") or []
                clients = [item for item in raw_clients if isinstance(item, dict)]
        except json.JSONDecodeError:
            clients = []
    elif isinstance(settings_field, dict):
        raw_clients = settings_field.get("clients") or []
        clients = [item for item in raw_clients if isinstance(item, dict)]

    summary = {
        "remark": str(raw.get("remark", "")),
        "port": _as_int(raw.get("port")),
        "protocol": str(raw.get("protocol", "")),
        "enabled": bool(raw.get("enable", False)),
        "up": _as_int(raw.get("up")),
        "down": _as_int(raw.get("down")),
        "total": _as_int(raw.get("total")),
        "expiry_time": _as_int(raw.get("expiryTime")),
    }
    return summary, clients


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def now_ms() -> int:
    return int(time.time() * 1000)
