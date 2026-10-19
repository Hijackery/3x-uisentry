"""Подписки и трафик клиентов из списка inbound'ов панели."""

from __future__ import annotations

from typing import Any

from ..models import InboundSummary, Subscription
from .panel import PanelClient, PanelError, PanelForbidden, parse_inbound


class SubscriptionCollector:
    """Превращает ответ ``/panel/api/inbounds/list`` в сводки и подписки."""

    def __init__(self, panel: PanelClient) -> None:
        self.panel = panel
        self.last_error: str | None = None
        self.forbidden = False

    async def collect(
        self,
    ) -> tuple[list[InboundSummary], list[Subscription]]:
        self.last_error = None
        self.forbidden = False
        try:
            raw_inbounds = await self.panel.inbounds()
        except PanelForbidden as exc:
            # Токен со scope=monitor не видит inbound'ы — это ожидаемо.
            self.forbidden = True
            self.last_error = str(exc)
            return [], []
        except PanelError as exc:
            self.last_error = str(exc)
            return [], []

        summaries: list[InboundSummary] = []
        subscriptions: list[Subscription] = []

        # Панель может вернуть что угодно: типы проверяем на месте, чтобы один
        # битый inbound не утащил за собой весь отчёт.
        payload: list[Any] = list(raw_inbounds)
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            summary, clients = parse_inbound(raw)
            summaries.append(
                InboundSummary(
                    remark=summary["remark"],
                    port=summary["port"],
                    protocol=summary["protocol"],
                    enabled=summary["enabled"],
                    clients=len(clients),
                    up_bytes=summary["up"],
                    down_bytes=summary["down"],
                    total_bytes=summary["total"],
                )
            )
            for client in clients:
                subscriptions.append(_to_subscription(summary, client))

        return summaries, subscriptions

    @staticmethod
    def problems(
        inbounds: list[InboundSummary],
        subscriptions: list[Subscription],
        *,
        expiring_soon_days: int,
    ) -> list[str]:
        """Что требует внимания: истёкшие, на исходе, исчерпавшие трафик."""
        issues: list[str] = []
        disabled = [item.remark for item in inbounds if not item.enabled]
        if disabled:
            issues.append(f"выключенные inbound'ы: {', '.join(disabled[:5])}")

        expired = [item for item in subscriptions if item.enabled and item.is_expired]
        if expired:
            issues.append(f"истёкшие подписки: {len(expired)}")

        soon = [
            item
            for item in subscriptions
            if item.enabled
            and not item.is_expired
            and item.expires_in_days is not None
            and item.expires_in_days <= expiring_soon_days
        ]
        if soon:
            issues.append(f"истекают в ближайшие {expiring_soon_days} дн.: {len(soon)}")

        depleted = [item for item in subscriptions if item.enabled and item.is_depleted]
        if depleted:
            issues.append(f"трафик исчерпан: {len(depleted)}")

        if not any(item.enabled for item in inbounds):
            issues.append("нет включённых inbound'ов")
        return issues


def _to_subscription(summary: dict[str, Any], client: dict[str, Any]) -> Subscription:
    total_gb = _as_int(client.get("totalGB"))
    used = _as_int(client.get("up")) + _as_int(client.get("down"))
    if used == 0 and total_gb == 0:
        # Некоторые версии панели хранят трафик только на уровне inbound.
        used = _as_int(summary.get("up")) + _as_int(summary.get("down"))
    return Subscription(
        email=str(client.get("email", "")),
        inbound_remark=summary["remark"],
        enabled=bool(client.get("enable", summary["enabled"])),
        expiry_time_ms=_as_int(client.get("expiryTime")),
        total_gb=total_gb,
        used_bytes=used,
        sub_id=str(client.get("subId", "")),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
