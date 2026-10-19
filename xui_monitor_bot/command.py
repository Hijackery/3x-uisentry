"""Запуск внешних команд без shell."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import suppress

from .models import CommandResult

#: Максимальный объём вывода, который держим в памяти (хвост команды informative).
MAX_CAPTURED_OUTPUT = 16_000

#: До Python 3.11 asyncio бросал СВОЙ TimeoutError, не связанный со встроенным.
#: `except TimeoutError` на 3.10 не срабатывал, и таймаут уносил исключение
#: наружу: команда не прерывалась, а процесс оставался жить.
ASYNC_TIMEOUTS: tuple[type[BaseException], ...] = (TimeoutError, asyncio.TimeoutError)


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
    # Свой сеанс (process group): при таймауте сможем погасить и потомков
    # (do-release-upgrade → apt), иначе внук держит пайпы открытыми.
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )

    # Заводим заранее: если остаток вывода дочитать не удастся (внук держит
    # пайпы), переменные уже определены — иначе был UnboundLocalError, из-за
    # которого фоновая задача падала без отчёта («зависло»).
    stdout: bytes = b""
    stderr: bytes = b""
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except ASYNC_TIMEOUTS:
        _terminate(process)
        # Забираем остаток вывода, но не бесконечно: процесс мог оставить
        # детей, которые держат пайпы открытыми.
        with suppress(*ASYNC_TIMEOUTS, asyncio.CancelledError):
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
        # Дожидаемся переработки процесса: иначе транспорт всплывёт при сборке
        # мусора уже после закрытия цикла событий.
        with suppress(BaseException):
            await asyncio.wait_for(process.wait(), timeout=2)
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
    """Гасит процесс и его группу (kill уже мёртвого — не ошибка)."""
    pid = getattr(process, "pid", None)
    if pid:
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    with suppress(ProcessLookupError):
        process.kill()


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
