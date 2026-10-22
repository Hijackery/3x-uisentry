"""TOTP и защита от повторного использования кода (общая для бота и терминала)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import threading
import time
from collections.abc import Callable


def totp_code(secret_b32: str, *, at: float | None = None, step: int = 30) -> str:
    normalized = secret_b32.strip().upper().replace(" ", "")
    padded = normalized + "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(padded)
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def normalize_totp(code: str) -> str:
    """Оставляет только ASCII-цифры: «123 456» и «123-456» — один и тот же код."""
    return "".join(ch for ch in code if ch in "0123456789")


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    window: int = 1,
    at: float | None = None,
) -> bool:
    candidate = normalize_totp(code)
    if len(candidate) != 6:
        return False
    moment = time.time() if at is None else at
    return any(
        hmac.compare_digest(totp_code(secret_b32, at=moment + offset * 30), candidate)
        for offset in range(-window, window + 1)
    )


class ReplayGuard:
    """Не даёт повторно использовать тот же TOTP-код в пределах окна.

    ``claim`` позволяет подключить durable-хранилище (SQLite). Без него
    остаётся потокобезопасный in-memory fallback для тестов и утилит.
    """

    def __init__(
        self,
        ttl: float = 120.0,
        *,
        claim: Callable[[str, float], bool] | None = None,
    ) -> None:
        self.ttl = ttl
        self._claim = claim
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self._claim is not None:
            return self._claim(key, self.ttl)
        with self._lock:
            if len(self._seen) > 512:
                self._seen = {k: exp for k, exp in self._seen.items() if exp > moment}
            expires = self._seen.get(key)
            if expires is not None and expires > moment:
                return False
            self._seen[key] = moment + self.ttl
            return True
