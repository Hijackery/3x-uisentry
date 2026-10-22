"""Состояние systemd-юнитов через systemctl (только чтение).

На системах без systemd (OpenRC, SysVinit) метрик служб нет: бот продолжает
работать и следить за железом, панелью и подписками, а состояние служб
показывается как недоступное с понятной причиной.
"""

from __future__ import annotations

import os
import shutil
import time

from ..command import run_command
from ..models import ServiceStatus

#: Свойства, которые запрашиваем у systemd одним вызовом.
SHOW_PROPERTIES = (
    "ActiveState",
    "SubState",
    "LoadState",
    "MainPID",
    "NRestarts",
    "MemoryCurrent",
    "CPUUsageNSec",
    "ActiveEnterTimestamp",
)


class ServiceCollector:
    """Читает состояние сервисов. Рестарт живёт в actions.py — здесь только чтение.

    CPU сервиса считаем по приросту ``CPUUsageNSec`` между опросами: значение в
    systemd накопительное с момента активации юнита, поэтому делить его на
    аптайм системы бессмысленно (юнит мог стартовать минуту назад).
    """

    def __init__(self, units: tuple[str, ...]) -> None:
        self.units = units
        self._prev_cpu: dict[str, tuple[float, int]] = {}

    async def collect(self) -> list[ServiceStatus]:
        if not self.units:
            return []
        if shutil.which("systemctl") is None:
            raise RuntimeError(
                "нет systemd: состояние служб недоступно "
                "(бот работает, управление службами — через init-скрипт)"
            )
        # Один запрос на все юниты: раньше на каждый сервис запускался
        # отдельный systemctl, и при десяти службах сбор заметно тормозил.
        result = await run_command(
            [
                "systemctl",
                "show",
                "--no-pager",
                f"--property={','.join(SHOW_PROPERTIES)}",
                *self.units,
            ],
            timeout=20.0,
            action="status",
        )
        if not result.ok:
            error = result.summary(limit=300)
            return [_failed(unit, error) for unit in self.units]

        blocks = _split_unit_blocks(result.stdout)
        if len(blocks) != len(self.units):
            # Экзотическая версия systemd или обрезанный вывод: не сопоставляем
            # блоки наугад, а честно опрашиваем каждый юнит отдельно.
            return [await self.status(unit) for unit in self.units]
        return [
            self._from_properties(unit, _parse_properties(block))
            for unit, block in zip(self.units, blocks, strict=True)
        ]

    async def status(self, unit: str) -> ServiceStatus:
        result = await run_command(
            [
                "systemctl",
                "show",
                unit,
                "--no-pager",
                f"--property={','.join(SHOW_PROPERTIES)}",
            ],
            timeout=15.0,
            action="status",
        )
        if not result.ok:
            return _failed(unit, result.summary(limit=300))
        return self._from_properties(unit, _parse_properties(result.stdout))

    def _from_properties(self, unit: str, props: dict[str, str]) -> ServiceStatus:
        load_state = props.get("LoadState", "unknown")
        active_state = props.get("ActiveState", "unknown")
        if load_state == "not-found":
            return _failed(unit, "юнит не найден в systemd")

        return ServiceStatus(
            name=unit,
            active=active_state == "active",
            sub_state=props.get("SubState", "unknown"),
            active_state=active_state,
            main_pid=_to_int(props.get("MainPID", "")),
            since=props.get("ActiveEnterTimestamp", ""),
            restarts=_to_int(props.get("NRestarts", "")),
            memory_bytes=_to_int(props.get("MemoryCurrent", "")),
            cpu_percent=self._cpu_percent(unit, props.get("CPUUsageNSec", "")),
            error=None,
        )

    def _cpu_percent(self, unit: str, cpu_ns: str) -> float:
        """Процент CPU между двумя опросами (0 на первом — нечего делить)."""
        now = time.monotonic()
        previous = self._prev_cpu.get(unit)
        self._prev_cpu[unit] = (now, _to_int(cpu_ns))
        if previous is None:
            return 0.0
        elapsed = now - previous[0]
        used_ns = _to_int(cpu_ns) - previous[1]
        if elapsed <= 0 or used_ns <= 0:
            return 0.0
        share = used_ns / 1e9 / elapsed / max(1, os.cpu_count() or 1) * 100
        return round(min(100.0, share), 1)

    def forget(self, unit: str) -> None:
        """Сбросить накопленный счётчик — например, после рестарта сервиса."""
        self._prev_cpu.pop(unit, None)

    async def is_active(self, unit: str) -> bool:
        # На OpenRC/SysV systemctl может отсутствовать: reboot-вотчер не должен
        # падать с FileNotFoundError.
        if shutil.which("systemctl"):
            result = await run_command(
                ["systemctl", "is-active", "--quiet", unit],
                timeout=10.0,
                action="is-active",
            )
            return result.ok
        if shutil.which("rc-service"):
            result = await run_command(
                ["rc-service", unit, "status"], timeout=10.0, action="is-active"
            )
            return result.ok
        if shutil.which("service"):
            result = await run_command(
                ["service", unit, "status"], timeout=10.0, action="is-active"
            )
            return result.ok
        return False

    async def logs(self, unit: str, lines: int = 40) -> str:
        result = await run_command(
            [
                "journalctl",
                "-u",
                unit,
                "-n",
                str(max(1, min(lines, 500))),
                "--no-pager",
                "-o",
                "short-iso",
            ],
            timeout=20.0,
            action="logs",
        )
        return result.summary()


def _failed(unit: str, error: str) -> ServiceStatus:
    return ServiceStatus(
        name=unit,
        active=False,
        sub_state="unknown",
        active_state="not-found",
        main_pid=0,
        since="",
        restarts=0,
        memory_bytes=0,
        cpu_percent=0.0,
        error=error,
    )


def _split_unit_blocks(output: str) -> list[str]:
    """Делит вывод ``systemctl show u1 u2`` на блоки свойств по пустой строке."""
    blocks: list[str] = []
    current: list[str] = []
    for line in output.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def _parse_properties(output: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key:
            props[key.strip()] = value.strip()
    return props


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
