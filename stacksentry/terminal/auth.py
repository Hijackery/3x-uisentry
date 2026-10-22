"""Аутентификация терминала: Telegram initData, TOTP, одноразовые тикеты."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from ..totp import ReplayGuard, totp_code, verify_totp

__all__ = [
    "AuthError",
    "RateLimiter",
    "ReplayGuard",
    "Ticket",
    "TicketStore",
    "totp_code",
    "validate_init_data",
    "verify_totp",
]


class AuthError(RuntimeError):
    """Отказ аутентификации с безопасным текстом для клиента."""


# --------------------------------------------------------------- initData


def validate_init_data(
    raw: str,
    bot_token: str,
    *,
    max_age: int = 300,
    now: float | None = None,
) -> dict[str, Any]:
    """Проверяет подпись Telegram initData и возвращает объект user.

    Формула Telegram: secret = HMAC_SHA256("WebAppData", bot_token),
    hash = HMAC_SHA256(secret, data_check_string).
    """
    if not raw:
        raise AuthError("initData пуст")
    # Ограничиваем до HMAC: иначе гигантская строка/тысячи полей жгут CPU.
    if len(raw) > 8192:
        raise AuthError("initData слишком большой")
    try:
        pairs = dict(
            parse_qsl(
                raw,
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=64,
            )
        )
    except ValueError as exc:
        raise AuthError("initData не разобран") from exc
    received = pairs.get("hash", "")
    if not received:
        raise AuthError("initData без подписи")
    moment = time.time() if now is None else now
    try:
        auth_date = int(pairs.get("auth_date", "0") or 0)
    except ValueError as exc:
        raise AuthError("auth_date неверен") from exc
    if max_age > 0 and (moment - auth_date) > max_age:
        raise AuthError("initData устарел — переоткройте Mini App")
    if auth_date > moment + 60:
        raise AuthError("auth_date из будущего")

    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    def matches(fields: dict[str, str]) -> bool:
        check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        try:
            return hmac.compare_digest(calculated.encode(), received.encode())
        except (TypeError, UnicodeError):
            return False

    fields = {key: value for key, value in pairs.items() if key != "hash"}
    if not matches(fields):
        # Новые клиенты добавляют поле signature; в HMAC оно не участвует.
        without_signature = {k: v for k, v in fields.items() if k != "signature"}
        if not matches(without_signature):
            raise AuthError("подпись initData не совпала")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except ValueError as exc:
        raise AuthError("user в initData не JSON") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise AuthError("в initData нет пользователя")
    return user


# ------------------------------------------------------------------- TOTP


# ----------------------------------------------------------------- тикеты


@dataclass(frozen=True, slots=True)
class Ticket:
    user_id: int
    root: bool
    expires_at: float
    nonce: str


class TicketStore:
    """Короткоживущие одноразовые тикеты для WebSocket-подключения."""

    def __init__(self, secret: str, ttl: int = 30) -> None:
        self.secret = secret.encode()
        self.ttl = ttl
        self._used: dict[str, float] = {}

    def issue(self, user_id: int, *, root: bool) -> str:
        payload = {
            "uid": user_id,
            "root": bool(root),
            "exp": time.time() + self.ttl,
            "nonce": secrets.token_urlsafe(18),
        }
        body = (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        signature = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def consume(self, ticket: str, *, now: float | None = None) -> Ticket:
        body, separator, signature = ticket.partition(".")
        if not separator or not body:
            raise AuthError("тикет неверен")
        expected = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        try:
            matched = hmac.compare_digest(expected.encode(), signature.encode())
        except (TypeError, UnicodeError):
            matched = False
        if not matched:
            raise AuthError("подпись тикета не совпала")
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(body + "=" * ((4 - len(body) % 4) % 4))
            )
        except (ValueError, TypeError) as exc:
            raise AuthError("тикет повреждён") from exc
        moment = time.time() if now is None else now
        expires_at = float(payload.get("exp", 0))
        if expires_at < moment:
            raise AuthError("тикет истёк — подключитесь заново")
        nonce = str(payload.get("nonce", ""))
        self._prune(moment)
        if not nonce or nonce in self._used:
            raise AuthError("тикет уже использован")
        self._used[nonce] = expires_at + 60
        return Ticket(
            user_id=int(payload.get("uid", 0)),
            root=bool(payload.get("root")),
            expires_at=expires_at,
            nonce=nonce,
        )

    def _prune(self, now: float) -> None:
        if len(self._used) < 256:
            return
        self._used = {key: exp for key, exp in self._used.items() if exp > now}


class RateLimiter:
    """Простой лимит попыток входа: N за окно."""

    def __init__(self, limit: int = 5, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        hits = [item for item in self._hits.get(key, []) if moment - item < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(moment)
        self._hits[key] = hits
        return True
