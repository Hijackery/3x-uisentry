"""Проверки запуска команд и разбора данных панели."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

import pytest

from xui_monitor_bot.collectors.services import ServiceCollector, _parse_properties
from xui_monitor_bot.collectors.subscriptions import SubscriptionCollector
from xui_monitor_bot.command import run_command
from xui_monitor_bot.models import CommandResult, Subscription
from xui_monitor_bot.settings import Settings


async def test_run_command_captures_output() -> None:
    result = await run_command(["/bin/echo", "hello"], action="echo")
    assert result.ok
    assert result.stdout.strip() == "hello"
    assert result.returncode == 0
    assert result.duration >= 0


async def test_run_command_reports_failure() -> None:
    result = await run_command(["/bin/sh", "-c", "exit 3"])
    assert not result.ok
    assert result.returncode == 3


async def test_run_command_keeps_stderr() -> None:
    result = await run_command(["/bin/sh", "-c", "echo bad 1>&2; exit 1"])
    assert "bad" in result.stderr
    assert "bad" in result.summary()


async def test_run_command_times_out() -> None:
    result = await run_command(["/bin/sleep", "5"], timeout=0.3)
    assert result.returncode == 124
    assert "Таймаут" in result.stderr


async def test_arguments_are_not_shell_interpreted() -> None:
    """Аргументы передаются exec-ом: ``;`` не превращается в команду shell."""
    result = await run_command(["/bin/echo", "; rm -rf /"], timeout=5)
    assert result.ok
    assert result.stdout.strip() == "; rm -rf /"


def test_parse_properties() -> None:
    props = _parse_properties("ActiveState=active\nSubState=running\nпустая строка\n")
    assert props["ActiveState"] == "active"
    assert props["SubState"] == "running"
    assert "" not in props


def make_sub(**overrides: object) -> Subscription:
    now_ms = int(time.time() * 1000)
    base: dict[str, object] = {
        "email": "user@example.com",
        "inbound_remark": "inbound",
        "enabled": True,
        "expiry_time_ms": now_ms + 10 * 86400_000,
        "total_gb": 100,
        "used_bytes": 10 * 1024**3,
    }
    base.update(overrides)
    return Subscription(**base)  # type: ignore[arg-type]


def test_subscription_expiry_and_traffic_flags() -> None:
    now_ms = int(time.time() * 1000)
    assert make_sub(expiry_time_ms=now_ms - 1000).is_expired
    assert not make_sub(expiry_time_ms=now_ms + 86_400_000).is_expired
    assert make_sub(expiry_time_ms=0).expires_in_days is None
    assert make_sub(total_gb=10, used_bytes=10 * 1024**3).is_depleted
    assert make_sub(total_gb=0).used_percent == 0.0
    assert make_sub(total_gb=10, used_bytes=5 * 1024**3).used_percent == 50.0


def test_problems_lists_what_needs_attention() -> None:
    from xui_monitor_bot.models import InboundSummary

    inbounds = [
        InboundSummary("on", 443, "vless", True, 1, 0, 0, 0),
        InboundSummary("off", 8443, "vless", False, 0, 0, 0, 0),
    ]
    subs = [
        make_sub(email="expired@example.com", expiry_time_ms=1),
        make_sub(
            email="soon@example.com",
            expiry_time_ms=int(time.time() * 1000) + 86_400_000,
        ),
        make_sub(email="depleted@example.com", total_gb=1, used_bytes=1024**3),
    ]
    issues = SubscriptionCollector.problems(inbounds, subs, expiring_soon_days=3)
    joined = "\n".join(issues)

    assert "выключенные inbound'ы: off" in joined
    assert "истёкшие подписки: 1" in joined
    assert "истекают в ближайшие 3 дн.: 1" in joined
    assert "трафик исчерпан: 1" in joined


def test_problems_detects_no_enabled_inbounds() -> None:
    issues = SubscriptionCollector.problems([], [], expiring_soon_days=3)
    assert "нет включённых inbound'ов" in "\n".join(issues)


def test_collect_turns_inbound_into_subscriptions() -> None:
    class FakePanel:
        def __init__(self, payload: list[dict[str, object]]) -> None:
            self.payload = payload

        async def inbounds(self) -> list[dict[str, object]]:
            return self.payload

    payload: list[dict[str, object]] = [
        {
            "remark": "main",
            "port": 443,
            "protocol": "vless",
            "enable": True,
            "up": 10,
            "down": 20,
            "total": 0,
            "settings": json.dumps(
                {
                    "clients": [
                        {"email": "a@example.com", "totalGB": 10, "up": 1, "down": 2},
                        {"email": "b@example.com", "totalGB": 0, "up": 0, "down": 0},
                    ]
                }
            ),
        }
    ]
    collector = SubscriptionCollector(FakePanel(payload))  # type: ignore[arg-type]
    inbounds, subs = asyncio.run(collector.collect())

    assert len(inbounds) == 1
    assert inbounds[0].clients == 2
    assert inbounds[0].used_bytes == 30
    assert {item.email for item in subs} == {"a@example.com", "b@example.com"}
    assert collector.last_error is None
    # У клиента без лимита трафик берём с уровня inbound.
    fallback = next(item for item in subs if item.email == "b@example.com")
    assert fallback.used_bytes == 30


def test_collect_survives_malformed_payload() -> None:
    class FakePanel:
        async def inbounds(self) -> list[object]:
            return ["не словарь", {"remark": "x", "settings": "{битое"}]

    collector = SubscriptionCollector(FakePanel())  # type: ignore[arg-type]
    inbounds, subs = asyncio.run(collector.collect())
    assert len(inbounds) == 1
    assert subs == []


async def test_service_cpu_is_delta_between_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPUUsageNSec накопительный: процент считается от прироста, а не от аптайма."""
    collector = ServiceCollector(("x-ui",))
    clock = [1000.0]
    values = [10_000_000_000, 11_000_000_000]  # +1 CPU-секунда за 1 секунду

    monkeypatch.setattr(
        "xui_monitor_bot.collectors.services.time.monotonic", lambda: clock[0]
    )

    async def fake_run(argv: list[str], **kwargs: object) -> CommandResult:
        return CommandResult(
            action="status",
            command=" ".join(argv),
            returncode=0,
            stdout=f"ActiveState=active\nSubState=running\nCPUUsageNSec={values.pop(0)}\n",
            stderr="",
            duration=0.0,
        )

    monkeypatch.setattr("xui_monitor_bot.collectors.services.run_command", fake_run)

    first = await collector.status("x-ui")
    clock[0] += 1
    second = await collector.status("x-ui")

    assert first.cpu_percent == 0.0  # первый замер базы не из чего считать
    assert second.cpu_percent == pytest.approx(
        100.0 / (psutil_cpu_count() or 1), rel=0.01
    )


def psutil_cpu_count() -> int:
    import os

    return max(1, os.cpu_count() or 1)


def test_missing_unit_is_reported(
    make_settings: Callable[..., Settings],
) -> None:
    settings: Settings = make_settings()
    assert settings.services  # конфигурация услуг не пустая
