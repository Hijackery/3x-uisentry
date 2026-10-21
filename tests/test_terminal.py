"""Безопасность терминала: initData, TOTP, тикеты, конфиг."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from uisentry.terminal.auth import (
    AuthError,
    TicketStore,
    totp_code,
    validate_init_data,
    verify_totp,
)
from uisentry.terminal.config import TerminalConfigError, TerminalSettings
from uisentry.terminal.server import create_app

TOKEN = "123456789:AAterminal-test"
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _init_data(user_id: int = 42, *, auth_date: int | None = None) -> str:
    data = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps(
            {"id": user_id, "first_name": "Admin"}, separators=(",", ":")
        ),
    }
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**data, "hash": digest})


def _settings(tmp_path) -> TerminalSettings:  # type: ignore[no-untyped-def]
    env = tmp_path / "terminal.env"
    env.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={TOKEN}",
                "TELEGRAM_ADMIN_IDS=42",
                "TERMINAL_SECRET=" + "s" * 40,
                f"TERMINAL_TOTP_SECRET={TOTP_SECRET}",
                "TERMINAL_ALLOW_ROOT=1",
                "TERMINAL_PORT=8788",
                "TERMINAL_PUBLIC_ORIGIN=https://term.example.com",
            ]
        ),
        encoding="utf-8",
    )
    return TerminalSettings.load(env)


def test_init_data_valid_and_tampered() -> None:
    assert validate_init_data(_init_data(), TOKEN)["id"] == 42
    raw = _init_data().replace("Admin", "Hacker")
    with pytest.raises(AuthError):
        validate_init_data(raw, TOKEN)


def test_init_data_expired() -> None:
    with pytest.raises(AuthError, match="устарел"):
        validate_init_data(_init_data(auth_date=int(time.time()) - 3600), TOKEN)


def test_totp_rfc_vector() -> None:
    # RFC 6238, SHA-1, 6 цифр, T=59.
    assert totp_code(TOTP_SECRET, at=59) == "287082"
    assert verify_totp(TOTP_SECRET, "287082", at=59)


def test_ticket_is_one_time() -> None:
    store = TicketStore("x" * 40, ttl=30)
    ticket = store.issue(42, root=False)
    assert store.consume(ticket).user_id == 42
    with pytest.raises(AuthError, match="использован"):
        store.consume(ticket)
    with pytest.raises(AuthError):
        store.consume(ticket + "0")


def test_ticket_expires() -> None:
    store = TicketStore("x" * 40, ttl=5)
    ticket = store.issue(42, root=True)
    with pytest.raises(AuthError, match="истёк"):
        store.consume(ticket, now=time.time() + 10)


def test_config_rejects_weak_secret(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "terminal.env"
    env.write_text(
        f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_ADMIN_IDS=42\n"
        f"TERMINAL_SECRET=short\nTERMINAL_TOTP_SECRET={TOTP_SECRET}\n",
        encoding="utf-8",
    )
    with pytest.raises(TerminalConfigError, match="TERMINAL_SECRET"):
        TerminalSettings.load(env)


def test_http_session_requires_valid_auth(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    response = client.post("/api/session", json={"totp": "000000"})
    assert response.status_code == 401
    assert "X-Content-Type-Options" in response.headers


def test_session_accepts_valid_totp(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    response = client.post(
        "/api/session",
        json={
            "initData": _init_data(),
            "totp": totp_code(settings.totp_secret),
            "root": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticket"]
    assert body["user"]["id"] == 42


def test_root_requires_flag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    response = client.post(
        "/api/session",
        json={
            "initData": _init_data(),
            "totp": totp_code(settings.totp_secret),
            "root": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["root"] is True


def test_totp_replay_normalizes_separators(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from uisentry.totp import normalize_totp

    settings = _settings(tmp_path)
    code = totp_code(settings.totp_secret)
    assert normalize_totp(f"{code[:3]} {code[3:]}") == code
    client = TestClient(create_app(settings))
    first = client.post(
        "/api/session",
        json={"initData": _init_data(), "totp": f"{code[:3]} {code[3:]}"},
    )
    assert first.status_code == 200, first.text
    # Тот же код без пробела — это повтор, а не новый вход.
    replay = client.post("/api/session", json={"initData": _init_data(), "totp": code})
    assert replay.status_code == 401
