"""Запуск внешних команд без shell."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from .models import CommandResult

#: Максимальный объём вывода, который держим в памяти (хвост команды informative).
MAX_CAPTURED_OUTPUT = 16_000


async def run_command(
    argv: list[str],
    *,
    timeout: float = 60.0,
    action: str = "",
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Выполняет ``argv`` и возвращает результат.

    Команда выполняется без ``shell=True``, поэтому аргументы из Telegram
    не могут превратиться в произвольный шелл-код.
    """
    command = " ".join(argv)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        _terminate(process)
        # Забираем остаток вывода, но не бесконечно: процесс мог оставить
        # детей, которые держат пайпы открытыми.
        with suppress(TimeoutError, asyncio.CancelledError):
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        return CommandResult(
            action=action or command,
            command=command,
            returncode=124,
            stdout=_decode(stdout)[-MAX_CAPTURED_OUTPUT:],
            stderr=(
                f"Таймаут {timeout:.0f}s — команда прервана. {_decode(stderr)}".strip()
            )[-MAX_CAPTURED_OUTPUT:],
            duration=time.monotonic() - started,
        )
    except asyncio.CancelledError:
        # Бота выключают или задача отменена: без этого дочерний процесс
        # (например, apt) оставался работать в фоне после остановки бота.
        _terminate(process)
        raise

    return CommandResult(
        action=action or command,
        command=command,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=_decode(stdout)[-MAX_CAPTURED_OUTPUT:],
        stderr=_decode(stderr)[-MAX_CAPTURED_OUTPUT:],
        duration=time.monotonic() - started,
    )


def _terminate(process: asyncio.subprocess.Process) -> None:
    """Убивает процесс, если он ещё жив (kill уже мёртвого — не ошибка)."""
    with suppress(ProcessLookupError):
        process.kill()


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
