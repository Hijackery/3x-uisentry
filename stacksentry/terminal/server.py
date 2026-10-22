"""Веб-терминал: FastAPI + PTY + WebSocket.

Сервис запускается отдельным пользователем (stacksentry-term) и по умолчанию
слушает только 127.0.0.1. Root-режим (`sudo -n /bin/bash -l`) выключен, пока
явно не разрешён в terminal.env и sudoers.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import json
import logging
import os
import pty
import signal
import struct
import termios
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..storage import Storage
from ..totp import normalize_totp
from .auth import (
    AuthError,
    RateLimiter,
    ReplayGuard,
    TicketStore,
    validate_init_data,
    verify_totp,
)
from .config import TerminalSettings

logger = logging.getLogger("stacksentry.terminal")
STATIC_DIR = Path(__file__).resolve().parent / "static"


MAX_AUTH_BODY = 64 * 1024


class SessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(default="", alias="initData", max_length=8192)
    token: str = Field(default="", max_length=512)
    totp: str = Field(default="", max_length=32)
    root: bool = False


class SetupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(default="", alias="initData", max_length=8192)


def _security_headers(
    response: JSONResponse | Any,
    *,
    frame_ancestors: str = "'self' https://web.telegram.org https://*.telegram.org",
) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        f"form-action 'self'; frame-ancestors {frame_ancestors}"
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


def terminal_command(settings: TerminalSettings, *, root: bool) -> list[str]:
    """Команда оболочки: локальный PTY или SSH с ключом терминала.

    Без SSH-ключа обычная сессия запускается в отдельном пользователе
    ``stacksentry-shell`` — у него нет sudo-правила на root. Root-сессия
    всегда требует явного разрешения и свежего TOTP.
    """
    if root:
        remote = ["sudo", "-n", "/bin/bash", "-l"]
    else:
        remote = [
            "sudo",
            "-n",
            "-u",
            settings.shell_user,
            "-H",
            "/bin/bash",
            "-l",
        ]
    if not settings.ssh_key:
        return remote
    return [
        "ssh",
        "-tt",
        "-i",
        settings.ssh_key,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        f"UserKnownHostsFile={settings.ssh_known_hosts}",
        "-p",
        str(settings.ssh_port),
        f"{settings.ssh_user}@{settings.ssh_host}",
        *remote,
    ]


async def _bridge(ws: WebSocket, settings: TerminalSettings, *, root: bool) -> None:
    master, slave = pty.openpty()
    command = terminal_command(settings, root=root)
    # Не наследуем окружение сервиса: там TELEGRAM_BOT_TOKEN, TOTP и
    # TERMINAL_ACCESS_TOKEN. Ребёнку достаточно PATH/TERM/LANG/HOME/USER.
    env = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "HOME": os.environ.get("HOME", "/var/lib/stacksentry-term"),
    }
    for name in ("USER", "LOGNAME", "SHELL"):
        value = os.environ.get(name)
        if value:
            env[name] = value
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
    preauth_limiter = RateLimiter(limit=20, window=60.0)
    replay_store = Storage(Path(settings.replay_db)) if settings.replay_db else None
    claim = replay_store.claim_replay if replay_store is not None else None
    app.state.replay_store = replay_store
    replay = ReplayGuard(ttl=120.0, claim=claim)
    init_replay = ReplayGuard(ttl=360.0, claim=claim)
    sessions: dict[int, WebSocket] = {}

    def enrolled_path() -> Path | None:
        if settings.totp_enrolled_file:
            return Path(settings.totp_enrolled_file)
        # Старые установки: отметка рядом с durable replay DB.
        if settings.replay_db:
            return Path(settings.replay_db).with_name("totp-enrolled")
        return None

    def totp_enrolled() -> bool:
        path = enrolled_path()
        return path is not None and path.exists()

    def mark_totp_enrolled() -> None:
        target = enrolled_path()
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(int(time.time())), encoding="utf-8")
            target.chmod(0o600)
        except OSError as exc:
            logger.warning("не удалось сохранить отметку TOTP-настройки: %s", exc)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Any:
        if request.method == "POST" and request.url.path in {
            "/api/session",
            "/api/setup",
        }:
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > MAX_AUTH_BODY:
                return JSONResponse(
                    {"detail": "слишком большой запрос"}, status_code=413
                )
        response = await call_next(request)
        _security_headers(response, frame_ancestors=settings.frame_ancestors)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "root": settings.allow_root,
            "ssh": bool(settings.ssh_key),
            "totp": True,
            "setup": enrolled_path() is not None and not totp_enrolled(),
        }

    @app.post("/api/setup")
    async def setup(request: Request, body: SetupRequest) -> dict[str, Any]:
        """Одноразовая выдача QR для терминала после проверки Telegram initData."""
        client = request.client.host if request.client else "unknown"
        if not preauth_limiter.allow(client):
            raise HTTPException(status_code=429, detail="слишком много попыток")
        origin = request.headers.get("origin", "")
        if origin and not _origin_allowed(origin, settings):
            raise HTTPException(status_code=403, detail="чужой Origin")
        if enrolled_path() is None:
            raise HTTPException(status_code=404, detail="настройка недоступна")
        if totp_enrolled():
            raise HTTPException(status_code=403, detail="TOTP уже настроен")
        try:
            user = validate_init_data(body.init_data, settings.bot_token, max_age=120)
            user_id = int(user["id"])
        except (AuthError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if user_id not in settings.admin_ids:
            raise HTTPException(status_code=403, detail="не администратор")
        label = quote("StackSentry:terminal", safe=":")
        uri = (
            f"otpauth://totp/{label}?secret={settings.totp_secret}"
            "&issuer=StackSentry&algorithm=SHA1&digits=6&period=30"
        )
        svg = segno.make(uri, error="m").svg_inline(scale=5, border=2, omitsize=True)
        return {"secret": settings.totp_secret, "otpauth": uri, "qr_svg": svg}

    @app.post("/api/session")
    async def session(request: Request, body: SessionRequest) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        # Лимит до проверки подписи/TOTP: не даём перебирать и жечь HMAC.
        if not preauth_limiter.allow(client):
            raise HTTPException(status_code=429, detail="слишком много попыток")
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
            code = normalize_totp(body.totp)
            if not verify_totp(settings.totp_secret, code):
                raise AuthError("неверный код TOTP")
            if not replay.allow(f"{user_id}:{code}"):
                raise AuthError("этот код TOTP уже использован")
            if body.root and not settings.allow_root:
                raise AuthError("root-терминал выключен")
            if body.init_data:
                key = hashlib.sha256(body.init_data.encode()).hexdigest()
                if not init_replay.allow(f"init:{key}"):
                    raise AuthError(
                        "initData уже использован — откройте Mini App заново"
                    )
            ticket = tickets.issue(user_id, root=body.root and settings.allow_root)
            mark_totp_enrolled()
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
            if sessions.get(info.user_id) is ws:
                sessions.pop(info.user_id, None)
            logger.info("terminal closed: user=%s", info.user_id)
            with suppress(BaseException):
                await ws.close(code=1000)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def create_app_from_env() -> FastAPI:
    return create_app(TerminalSettings.load())
