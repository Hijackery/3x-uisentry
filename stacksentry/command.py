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


async def _read_tail(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    """Читает поток и держит только последние ``limit`` байт.

    ``communicate()`` копил весь вывод: dmesg/apt могли съесть память до
    обрезки. Здесь хвост ограничен ещё во время чтения.
    """
    if stream is None:
        return b""
    tail = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        tail.extend(chunk)
        if len(tail) > limit:
            del tail[:-limit]
    return bytes(tail)


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
    # Некоторые тестовые/встраиваемые процессы не отдают потоковые пайпы.
    streaming = getattr(process, "stdout", None) is not None
    out_task = (
        asyncio.create_task(_read_tail(process.stdout, MAX_CAPTURED_OUTPUT))
        if streaming
        else None
    )
    err_task = (
        asyncio.create_task(_read_tail(process.stderr, MAX_CAPTURED_OUTPUT))
        if streaming
        else None
    )
    try:
        if streaming:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            assert out_task is not None and err_task is not None
            stdout = await asyncio.wait_for(out_task, timeout=5)
            stderr = await asyncio.wait_for(err_task, timeout=5)
        else:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
    except ASYNC_TIMEOUTS:
        _terminate(process)
        # Забираем остаток вывода, но не бесконечно: процесс мог оставить
        # детей, которые держат пайпы открытыми.
        if streaming:
            with suppress(*ASYNC_TIMEOUTS, asyncio.CancelledError):
                await asyncio.wait_for(process.wait(), timeout=5)
        else:
            with suppress(*ASYNC_TIMEOUTS, asyncio.CancelledError, AttributeError):
                await asyncio.wait_for(process.communicate(), timeout=5)
        if out_task is not None:
            with suppress(*ASYNC_TIMEOUTS, asyncio.CancelledError):
                stdout = await asyncio.wait_for(out_task, timeout=2)
            out_task.cancel()
        if err_task is not None:
            with suppress(*ASYNC_TIMEOUTS, asyncio.CancelledError):
                stderr = await asyncio.wait_for(err_task, timeout=2)
            err_task.cancel()
        return CommandResult(
            action=action or command,
            command=command,
            returncode=124,
            stdout=_decode(stdout),
            stderr=(
                f"Таймаут {timeout:.0f}s — команда прервана. {_decode(stderr)}".strip()
            ),
            duration=time.monotonic() - started,
        )
    except asyncio.CancelledError:
        # Бота выключают или задача отменена: без этого дочерний процесс
        # (например, apt) оставался работать в фоне после остановки бота.
        _terminate(process)
        if out_task is not None:
            out_task.cancel()
        if err_task is not None:
            err_task.cancel()
        with suppress(BaseException):
            await asyncio.wait_for(process.wait(), timeout=2)
        raise

    return CommandResult(
        action=action or command,
        command=command,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=_decode(stdout),
        stderr=_decode(stderr),
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
