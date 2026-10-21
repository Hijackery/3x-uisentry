"""Веб-терминал: FastAPI + PTY + WebSocket.

Сервис запускается отдельным пользователем (uisentry-term) и по умолчанию
слушает только 127.0.0.1. Root-режим (`sudo -n /bin/bash -l`) выключен, пока
явно не разрешён в terminal.env и sudoers.
"""

from __future__ import annotations

import asyncio
import fcntl
import hmac
import json
import logging
import os
import pty
import signal
import struct
import termios
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .auth import (
    AuthError,
    RateLimiter,
    ReplayGuard,
    TicketStore,
    validate_init_data,
    verify_totp,
)
from .config import TerminalSettings

logger = logging.getLogger("uisentry.terminal")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class SessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(default="", alias="initData")
    token: str = ""
    totp: str = ""
    root: bool = False


def _security_headers(response: JSONResponse | Any) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors https://web.telegram.org "
        "https://*.telegram.org"
    )


def _origin_allowed(origin: str, settings: TerminalSettings) -> bool:
    if settings.public_origin:
        return origin == settings.public_origin
    return origin in {
        f"http://127.0.0.1:{settings.port}",
        f"http://localhost:{settings.port}",
    }


def _resize(fd: int, cols: int, rows: int) -> None:
    cols = max(20, min(int(cols), 500))
    rows = max(5, min(int(rows), 300))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _bridge(ws: WebSocket, settings: TerminalSettings, *, root: bool) -> None:
    master, slave = pty.openpty()
    command = ["sudo", "-n", "/bin/bash", "-l"] if root else ["/bin/bash", "-l"]
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "HOME": os.environ.get("HOME", "/var/lib/uisentry-term"),
    }
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data or None)

    loop.add_reader(master, on_readable)

    async def pump() -> None:
        while True:
            data = await queue.get()
            if data is None:
                return
            await ws.send_bytes(data)

    async def receive() -> None:
        async for message in ws.iter_text():
            if message.startswith("{"):
                try:
                    payload = json.loads(message)
                except ValueError:
                    payload = {}
                if payload.get("type") == "resize":
                    _resize(
                        master,
                        int(payload.get("cols", 80)),
                        int(payload.get("rows", 24)),
                    )
                    continue
            os.write(master, message.encode())

    tasks = [asyncio.create_task(pump()), asyncio.create_task(receive())]
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=settings.idle_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            with suppress(BaseException):
                task.result()
    finally:
        loop.remove_reader(master)
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with suppress(BaseException):
            await asyncio.wait_for(process.wait(), timeout=3)
        with suppress(OSError):
            os.close(master)


def create_app(settings: TerminalSettings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    tickets = TicketStore(settings.secret, ttl=settings.session_ttl)
    limiter = RateLimiter(limit=5, window=60.0)
    replay = ReplayGuard(ttl=120.0)
    sessions: dict[int, WebSocket] = {}

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        _security_headers(response)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "root": settings.allow_root}

    @app.post("/api/session")
    async def session(request: Request, body: SessionRequest) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        origin = request.headers.get("origin", "")
        if settings.public_origin and origin and origin != settings.public_origin:
            raise HTTPException(status_code=403, detail="чужой Origin")
        try:
            user_id = 0
            name = ""
            if body.init_data:
                user = validate_init_data(body.init_data, settings.bot_token)
                user_id = int(user["id"])
                name = str(user.get("first_name") or user.get("username") or "")
            elif body.token:
                if not settings.access_token or not hmac.compare_digest(
                    body.token, settings.access_token
                ):
                    raise AuthError("access token неверен")
                user_id = settings.access_user
                name = "token"
            else:
                raise AuthError("нужен initData или access token")
            if user_id not in settings.admin_ids:
                raise AuthError("пользователь не администратор")
            if not limiter.allow(f"{client}:{user_id}"):
                raise HTTPException(status_code=429, detail="слишком много попыток")
            if not verify_totp(settings.totp_secret, body.totp):
                raise AuthError("неверный код TOTP")
            if not replay.allow(f"{user_id}:{body.totp}"):
                raise AuthError("этот код TOTP уже использован")
            if body.root and not settings.allow_root:
                raise AuthError("root-терминал выключен")
            ticket = tickets.issue(user_id, root=body.root and settings.allow_root)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        logger.info(
            "terminal session: user=%s root=%s ip=%s", user_id, body.root, client
        )
        return {
            "ticket": ticket,
            "expires_in": settings.session_ttl,
            "root": body.root and settings.allow_root,
            "user": {"id": user_id, "name": name},
        }

    @app.websocket("/ws")
    async def terminal(ws: WebSocket) -> None:
        origin = ws.headers.get("origin", "")
        if not _origin_allowed(origin, settings):
            await ws.close(code=1008)
            return
        ticket = ws.query_params.get("ticket", "")
        try:
            info = tickets.consume(ticket)
            if info.root and not settings.allow_root:
                raise AuthError("root-терминал выключен")
            if info.user_id not in settings.admin_ids:
                raise AuthError("не администратор")
        except AuthError as exc:
            logger.warning("terminal rejected: %s", exc)
            await ws.close(code=1008)
            return
        previous = sessions.get(info.user_id)
        if previous is not None:
            with suppress(BaseException):
                await previous.close(code=1000)
        if len(sessions) >= settings.max_sessions and info.user_id not in sessions:
            await ws.close(code=1013)
            return
        await ws.accept()
        sessions[info.user_id] = ws
        logger.info("terminal open: user=%s root=%s", info.user_id, info.root)
        try:
            await _bridge(ws, settings, root=info.root)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 — сессия не должна ронять сервис
            logger.exception("terminal crashed: %s", exc)
        finally:
            sessions.pop(info.user_id, None)
            logger.info("terminal closed: user=%s", info.user_id)
            with suppress(BaseException):
                await ws.close(code=1000)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def create_app_from_env() -> FastAPI:
    return create_app(TerminalSettings.load())
