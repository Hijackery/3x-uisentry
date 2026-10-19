"""Сборка полного среза состояния сервера и панели."""

from __future__ import annotations

import asyncio
import logging

from .collectors.panel import PanelClient
from .collectors.services import ServiceCollector
from .collectors.subscriptions import SubscriptionCollector
from .collectors.system import SystemCollector
from .models import (
    InboundSummary,
    MonitorSnapshot,
    PanelHealth,
    ServiceStatus,
    Subscription,
    SystemMetrics,
)
from .settings import Settings

logger = logging.getLogger(__name__)


class Monitor:
    """Опрашивает все источники и возвращает один снимок.

    Каждый источник изолирован: недоступность панели не должна превращаться
    в пустой отчёт по серверу, и наоборот.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.system = SystemCollector(settings.disk_path)
        self.services = ServiceCollector(settings.services)
        self.panel = PanelClient(settings)
        self.subscriptions = SubscriptionCollector(self.panel)

    async def close(self) -> None:
        await self.panel.close()

    async def snapshot(self) -> MonitorSnapshot:
        warnings: list[str] = []

        metrics = await self._collect_metrics(warnings)
        services = await self._collect_services(warnings)
        health = await self._collect_panel_health(warnings)
        inbounds, subs = await self._collect_subscriptions(warnings)

        if subs or inbounds:
            warnings.extend(
                SubscriptionCollector.problems(
                    inbounds,
                    subs,
                    expiring_soon_days=self.settings.expiring_soon_days,
                )
            )

        return MonitorSnapshot(
            system=metrics,
            services=services,
            panel=health,
            inbounds=inbounds,
            subscriptions=subs,
            warnings=warnings,
        )

    async def _collect_metrics(self, warnings: list[str]) -> SystemMetrics | None:
        try:
            return await asyncio.to_thread(self.system.collect)
        except Exception as exc:  # сбор не должен ронять весь отчёт
            logger.exception("Не удалось снять системные метрики")
            warnings.append(f"метрики системы недоступны: {exc}")
            return None

    async def _collect_services(self, warnings: list[str]) -> list[ServiceStatus]:
        try:
            return await self.services.collect()
        except Exception as exc:
            logger.exception("Не удалось прочитать состояние сервисов")
            warnings.append(f"состояние сервисов недоступно: {exc}")
            return []

    async def _collect_panel_health(self, warnings: list[str]) -> PanelHealth:
        try:
            return await self.panel.health()
        except Exception as exc:
            logger.exception("Проверка панели упала")
            warnings.append(f"проверка панели упала: {exc}")
            return PanelHealth(
                reachable=False, status_code=None, latency_ms=None, error=str(exc)
            )

    async def _collect_subscriptions(
        self, warnings: list[str]
    ) -> tuple[list[InboundSummary], list[Subscription]]:
        inbounds, subs = await self.subscriptions.collect()
        if self.subscriptions.forbidden:
            warnings.append(
                "список inbound'ов недоступен: у токена нет области node-sync/admin "
                f"({self.subscriptions.last_error})"
            )
        elif self.subscriptions.last_error:
            warnings.append(f"подписки не собраны: {self.subscriptions.last_error}")
        return inbounds, subs

    # ------------------------------------------------------------- действия
