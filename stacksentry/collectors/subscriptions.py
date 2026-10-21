"""Подписки и трафик клиентов из списка inbound'ов панели."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..models import InboundSummary, Subscription
from .panel import PanelError, PanelForbidden, parse_inbound
from .panels import PanelFacade


class SubscriptionCollector:
    """Превращает ответ ``/panel/api/inbounds/list`` в сводки и подписки."""

    def __init__(self, panel: PanelFacade) -> None:
        self.panel = panel
        self.last_error: str | None = None
        self.forbidden = False

    async def collect(
        self,
    ) -> tuple[list[InboundSummary], list[Subscription]]:
        self.last_error = None
        self.forbidden = False
        panel_type = getattr(
            getattr(self.panel, "settings", None), "panel_type", "3x-ui"
        )
        if panel_type in {"marzban", "remnawave"}:
            try:
                subs = await self.panel.subscriptions()
            except PanelForbidden as exc:
                self.forbidden = True
                self.last_error = str(exc)
                return [], []
            except PanelError as exc:
                self.last_error = str(exc)
                return [], []
            return [], subs
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
        merged: dict[str, Subscription] = {}

        # Панель может вернуть что угодно: типы проверяем на месте, чтобы один
        # битый inbound не утащил за собой весь отчёт.
        payload: list[Any] = list(raw_inbounds)
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            summary, clients = parse_inbound(raw)
            # Per-client трафик лежит не в settings.clients, а в clientStats.
            # Без него у клиентов с лимитом всегда было бы used=0.
            stats_used: dict[str, int] = {}
            raw_stats = raw.get("clientStats")
            if isinstance(raw_stats, list):
                for stat in raw_stats:
                    if not isinstance(stat, dict):
                        continue
                    stat_email = str(stat.get("email", ""))
                    if stat_email:
                        stats_used[stat_email] = stats_used.get(stat_email, 0) + (
                            _as_int(stat.get("up")) + _as_int(stat.get("down"))
                        )
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
                email = str(client.get("email", ""))
                sub = _to_subscription(summary, client, stats_used.get(email))
                # Один клиент может быть привязан к нескольким inbound'ам:
                # суммируем трафик и не показываем его дважды.
                key = sub.sub_id or sub.email or f"{summary['remark']}:{len(merged)}"
                if key in merged:
                    merged[key] = _merge_subscriptions(merged[key], sub)
                else:
                    merged[key] = sub

        return summaries, list(merged.values())

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


def _merge_subscriptions(left: Subscription, right: Subscription) -> Subscription:
    remarks = {
        item.strip()
        for item in (left.inbound_remark, right.inbound_remark)
        if item.strip()
    }
    expiries = [
        value for value in (left.expiry_time_ms, right.expiry_time_ms) if value > 0
    ]
    return replace(
        left,
        inbound_remark=", ".join(sorted(remarks)),
        enabled=left.enabled or right.enabled,
        expiry_time_ms=min(expiries) if expiries else 0,
        total_bytes=max(left.total_bytes, right.total_bytes),
        used_bytes=left.used_bytes + right.used_bytes,
    )


def _to_subscription(
    summary: dict[str, Any],
    client: dict[str, Any],
    stats_used: int | None = None,
) -> Subscription:
    # В 3X-UI поле называется totalGB, но содержит БАЙТЫ (фронтенд делит на
    # 1024**3 только для показа). Раньше бот умножал его ещё раз — лимит и
    # «трафик исчерпан» становились недостижимыми.
    total_bytes = _as_int(client.get("totalGB"))
    if stats_used is not None:
        used = stats_used
    else:
        used = _as_int(client.get("up")) + _as_int(client.get("down"))
        if used == 0 and total_bytes == 0:
            # Некоторые версии панели хранят трафик только на уровне inbound.
            used = _as_int(summary.get("up")) + _as_int(summary.get("down"))
    return Subscription(
        email=str(client.get("email", "")),
        inbound_remark=summary["remark"],
        enabled=bool(client.get("enable", summary["enabled"])),
        expiry_time_ms=_as_int(client.get("expiryTime")),
        total_bytes=total_bytes,
        used_bytes=used,
        sub_id=str(client.get("subId", "")),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
