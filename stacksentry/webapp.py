"""Read-only Telegram Mini App: auth initData + JSON API + статический дашборд.

Сервис работает от того же непривилегированного пользователя, что и бот, и
только читает состояние. Действий здесь нет — они остаются в боте/терминале.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .monitor import Monitor
from .settings import ConfigError, Settings, load_settings
from .storage import Storage
from .terminal.auth import validate_init_data

COOKIE_NAME = "stacksentry_session"
STATIC_DIR = Path(__file__).resolve().parent / "webapp_static"


@dataclass(frozen=True, slots=True)
class WebAppConfig:
    settings: Settings
    secret: str
    host: str
    port: int
    public_origin: str
    session_ttl: int

    @classmethod
    def load(cls, env_file: Path | None = None) -> WebAppConfig:
        settings = load_settings(env_file)
        if len(settings.webapp_secret) < 32:
            raise ConfigError("WEBAPP_SECRET: нужен случайный секрет ≥ 32 символов")
        if (
            settings.webapp_public_origin
            and not settings.webapp_public_origin.startswith("https://")
        ):
            raise ConfigError("WEBAPP_PUBLIC_ORIGIN должен начинаться с https://")
        return cls(
            settings=settings,
            secret=settings.webapp_secret,
            host=settings.webapp_host,
            port=settings.webapp_port,
            public_origin=settings.webapp_public_origin,
            session_ttl=max(300, settings.webapp_session_ttl),
        )


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def issue_cookie(secret: str, user_id: int, ttl: int) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"uid": user_id, "exp": time.time() + ttl}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{body}.{_sign(secret, body)}"


def verify_cookie(secret: str, cookie: str) -> int | None:
    body, sep, signature = cookie.partition(".")
    if not sep:
        return None
    try:
        matched = hmac.compare_digest(_sign(secret, body).encode(), signature.encode())
    except (TypeError, UnicodeError):
        matched = False
    if not matched:
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(body + "=" * ((4 - len(body) % 4) % 4))
        )
    except (ValueError, TypeError):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    uid = payload.get("uid")
    return int(uid) if isinstance(uid, int) else None


def _snapshot_json(snapshot: Any, age: float) -> dict[str, Any]:
    return {
        "system": asdict(snapshot.system) if snapshot.system else None,
        "services": [asdict(item) for item in snapshot.services],
        "panel": asdict(snapshot.panel),
        "inbounds": [asdict(item) for item in snapshot.inbounds],
        "subscriptions": [asdict(item) for item in snapshot.subscriptions],
        "warnings": list(snapshot.warnings),
        "unavailable": sorted(snapshot.unavailable),
        "total_used_bytes": snapshot.total_used_bytes,
        "active_clients": snapshot.active_clients,
        "age": round(time.monotonic() - age, 1),
    }


class SessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(default="", alias="initData")


def create_app(
    config: WebAppConfig,
    *,
    monitor: Monitor | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    monitor = monitor or Monitor(config.settings)
    storage = storage or Storage(config.settings.state_path)
    snapshot_cache: dict[str, Any] = {"value": None, "at": 0.0}

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' "
            "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors https://web.telegram.org "
            "https://*.telegram.org"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def current_user(request: Request) -> int:
        cookie = request.cookies.get(COOKIE_NAME, "")
        uid = verify_cookie(config.secret, cookie) if cookie else None
        if uid is None or uid not in config.settings.admin_ids:
            raise HTTPException(status_code=401, detail="нужен вход через Telegram")
        return uid

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/api/session")
    async def session(body: SessionRequest, response: Response) -> dict[str, Any]:
        try:
            user = validate_init_data(body.init_data, config.settings.telegram_token)
        except Exception as exc:  # noqa: BLE001 — безопасный текст
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        user_id = int(user["id"])
        if user_id not in config.settings.admin_ids:
            raise HTTPException(status_code=403, detail="не администратор")
        cookie = issue_cookie(config.secret, user_id, config.session_ttl)
        response.set_cookie(
            COOKIE_NAME,
            cookie,
            max_age=config.session_ttl,
            httponly=True,
            # Telegram Web — iframe: строгий SameSite не отправит cookie.
            secure=bool(config.public_origin),
            samesite="none" if config.public_origin else "lax",
            path="/",
        )
        return {
            "user": {
                "id": user_id,
                "name": str(user.get("first_name") or user.get("username") or ""),
            },
            "panel_enabled": config.settings.panel_enabled,
            "services": list(config.settings.services),
        }

    @app.get("/api/meta")
    async def meta(user_id: int = Depends(current_user)) -> dict[str, Any]:
        return {
            "panel_enabled": config.settings.panel_enabled,
            "services": list(config.settings.services),
            "require_2fa": config.settings.require_2fa,
        }

    @app.get("/api/status")
    async def status(user_id: int = Depends(current_user)) -> dict[str, Any]:
        now = time.monotonic()
        if snapshot_cache["value"] is None or now - snapshot_cache["at"] > 15:
            snapshot_cache["value"] = await monitor.snapshot()
            snapshot_cache["at"] = now
        return _snapshot_json(snapshot_cache["value"], snapshot_cache["at"])

    @app.get("/api/history")
    async def history(hours: int = 24, user_id: int = Depends(current_user)) -> Any:
        hours = max(1, min(hours, 168))
        rows = await asyncio.to_thread(
            storage.get_series, time.time() - hours * 3600, min(hours, 168)
        )
        return [dict(row) for row in rows]

    @app.get("/api/audit")
    async def audit(limit: int = 30, user_id: int = Depends(current_user)) -> Any:
        limit = max(1, min(limit, 200))
        rows = await asyncio.to_thread(storage.recent_audit, limit)
        return [
            {
                "ts": row["ts"],
                "action": row["action"],
                "ok": bool(row["ok"]),
                "result": row["result"],
            }
            for row in rows
        ]

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def create_app_from_env() -> FastAPI:
    return create_app(WebAppConfig.load())
