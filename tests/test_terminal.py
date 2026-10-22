"""Безопасность терминала: initData, TOTP, тикеты, конфиг."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from stacksentry.terminal import config
from stacksentry.terminal.auth import (
    AuthError,
    TicketStore,
    totp_code,
    validate_init_data,
    verify_totp,
)
from stacksentry.terminal.config import TerminalConfigError, TerminalSettings
from stacksentry.terminal.server import create_app, terminal_command

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


def test_unreadable_env_path_does_not_crash_startup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def deny(_path: Path) -> bool:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", deny)
    assert isinstance(config._read_env(Path("/etc/stacksentry/terminal.env")), dict)


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
    from stacksentry.totp import normalize_totp

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


def test_non_root_uses_isolated_shell_user(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    command = terminal_command(settings, root=False)
    assert command == [
        "sudo",
        "-n",
        "-u",
        settings.shell_user,
        "-H",
        "/bin/bash",
        "-l",
    ]
    assert terminal_command(settings, root=True) == [
        "sudo",
        "-n",
        "/bin/bash",
        "-l",
    ]


def test_ssh_key_transport_command(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    settings = _settings(tmp_path)
    key = tmp_path / "id_ed25519"
    known = tmp_path / "known_hosts"
    key.write_text("key", encoding="utf-8")
    known.write_text("host", encoding="utf-8")
    settings = replace(
        settings,
        ssh_key=str(key),
        ssh_user="stacksentry-term",
        ssh_host="127.0.0.1",
        ssh_port=22,
        ssh_known_hosts=str(known),
    )
    command = terminal_command(settings, root=True)
    assert command[:6] == ["ssh", "-tt", "-i", str(key), "-o", "IdentitiesOnly=yes"]
    assert "BatchMode=yes" in command
    assert f"UserKnownHostsFile={known}" in command
    assert command[-5] == "stacksentry-term@127.0.0.1"
    assert command[-4:] == ["sudo", "-n", "/bin/bash", "-l"]
    assert "StrictHostKeyChecking=yes" in command


def test_csp_allows_miniapp_frame(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    headers = client.get("/api/health").headers
    policy = headers["Content-Security-Policy"]
    assert "frame-ancestors" in policy
    assert "https://web.telegram.org" in policy
    assert client.get("/api/health").json()["totp"] is True


def test_config_loads_ssh_and_frame_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    key = tmp_path / "id_ed25519"
    known = tmp_path / "known_hosts"
    key.write_text("key", encoding="utf-8")
    known.write_text("host", encoding="utf-8")
    env = tmp_path / "terminal.env"
    env.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={TOKEN}",
                "TELEGRAM_ADMIN_IDS=42",
                "TERMINAL_SECRET=" + "s" * 40,
                f"TERMINAL_TOTP_SECRET={TOTP_SECRET}",
                "TERMINAL_FRAME_ANCESTORS=\"'self' https://app.example.com\"",
                f"TERMINAL_SSH_KEY={key}",
                f"TERMINAL_SSH_KNOWN_HOSTS={known}",
            ]
        ),
        encoding="utf-8",
    )
    settings = TerminalSettings.load(env)
    assert settings.ssh_key == str(key)
    assert settings.ssh_known_hosts == str(known)
    assert settings.frame_ancestors == "'self' https://app.example.com"
    assert settings.ssh_port == 22


def test_config_rejects_missing_ssh_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "terminal.env"
    env.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={TOKEN}",
                "TELEGRAM_ADMIN_IDS=42",
                "TERMINAL_SECRET=" + "s" * 40,
                f"TERMINAL_TOTP_SECRET={TOTP_SECRET}",
                f"TERMINAL_SSH_KEY={tmp_path / 'missing'}",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(TerminalConfigError, match="TERMINAL_SSH_KEY"):
        TerminalSettings.load(env)
