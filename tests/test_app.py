"""Тесты сборки Telegram-приложения и прав доступа.

Сборка Application проверяется отдельно от сети: именно здесь однажды
оказался несуществующий метод билдера, и бот падал на старте с
AttributeError, не доходя до сети.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery, Update, User
from telegram.ext import CommandHandler

from uisentry.app import MonitorBot
from uisentry.settings import Settings

ADMIN_ID = 42
GUEST_ID = 999


@pytest.fixture
async def bot_app(make_settings: Callable[..., Settings]) -> AsyncIterator[MonitorBot]:
    instance = MonitorBot(make_settings(admin_ids=(ADMIN_ID,)))
    try:
        yield instance
    finally:
        await instance.monitor.close()
        instance.storage.close()


def test_application_is_built_and_handlers_registered(
    bot_app: MonitorBot,
) -> None:
    app = bot_app.app
    assert app.bot_data["bot"] is bot_app

    commands = {
        command
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    assert {
        "start",
        "help",
        "status",
        "services",
        "panel",
        "subs",
        "history",
        "logs",
        "audit",
        "maint",
    } <= commands


@pytest.mark.parametrize(
    "data",
    [
        "refresh",
        "server",
        "services",
        "panel",
        "traffic",
        "subs",
        "history",
        "maintenance",
        "logs_menu",
        "logs:x-ui",
        "command_prompt",
        "ask:apt_upgrade:",
        "no:abc",
    ],
)
async def test_guest_cannot_use_any_button(bot_app: MonitorBot, data: str) -> None:
    """Гость не должен получать данные сервера ни через одну кнопку."""
    telegram_bot = AsyncMock()
    query = CallbackQuery(
        id="1",
        from_user=User(id=GUEST_ID, first_name="guest", is_bot=False),
        chat_instance="ci",
        data=data,
        # Редактируем сообщение инлайн-кнопки: у CallbackQuery без message
        # иначе нет объекта, который можно править.
        inline_message_id="inline-1",
    )
    # В PTB бот в объект апдейта подставляет Application, в тестах — вручную.
    query.set_bot(telegram_bot)
    update = Update(update_id=1, callback_query=query)

    await bot_app.on_callback(update, _Context())  # type: ignore[arg-type]

    assert telegram_bot.edit_message_text.await_count == 1
    text = telegram_bot.edit_message_text.await_args.kwargs["text"]
    assert "Только для администраторов" in text


def test_is_admin_matches_configuration(bot_app: MonitorBot) -> None:
    assert bot_app.is_admin(ADMIN_ID) is True
    assert bot_app.is_admin(GUEST_ID) is False
    assert bot_app.is_admin(None) is False


async def test_custom_telegram_api_url_reaches_ptb_builder(
    make_settings: Callable[..., Settings],
) -> None:
    """Свой Bot API: URL из конфигурации должен дойти до клиента PTB."""
    instance = MonitorBot(make_settings(telegram_api_url="http://127.0.0.1:8081"))
    try:
        base_url = instance.app.bot.base_url
        assert base_url.startswith("http://127.0.0.1:8081/bot")
        assert base_url.endswith("123456789:AAconftest")
    finally:
        await instance.monitor.close()
        instance.storage.close()


class _Context:
    """Заглушка Context для вызовов, которым очередь задач не нужна."""

    def __init__(self) -> None:
        self.job_queue = None
        self.user_data: dict[str, object] | None = None


async def test_apt_watcher_reports_finished_job(
    bot_app: MonitorBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отчёт об apt приходит в чат сам — без journalctl и лишних команд."""
    from uisentry.models import CommandResult

    job_id = bot_app.storage.add_pending_job(
        "apt_upgrade", "upgrade", chat_id=1, user_id=ADMIN_ID
    )

    async def fake_status(_action: str) -> CommandResult:
        return CommandResult(
            action="apt_status",
            command="apt_status upgrade",
            returncode=0,
            stdout=(
                "STATE=inactive\nRESULT=success\nEXIT=0\nLOAD=loaded\n"
                "Setting up x-ui...\nDone"
            ),
            stderr="",
            duration=0.1,
        )

    notified: list[tuple[int, str]] = []

    async def fake_notify(chat_id: int, text: str, **_kwargs: object) -> bool:
        notified.append((chat_id, text))
        return True

    monkeypatch.setattr(bot_app.actions, "apt_status", fake_status)
    monkeypatch.setattr(bot_app, "_notify", fake_notify)

    class Job:
        data = {
            "job_id": job_id,
            "action": "apt_upgrade",
            "chat_id": 1,
            "user_id": ADMIN_ID,
            "created_at": 0.0,
            "attempts": 0,
            "errors": 0,
        }

    class Ctx:
        job = Job()

    await bot_app._job_watch_apt(Ctx())  # type: ignore[arg-type]

    assert notified
    assert "Обновление системы завершено" in notified[0][1]
    assert "Setting up x-ui" in notified[0][1]
    assert bot_app.storage.list_pending_jobs() == []


async def test_apt_watcher_keeps_polling_while_active(
    bot_app: MonitorBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uisentry.models import CommandResult

    job_id = bot_app.storage.add_pending_job(
        "apt_upgrade", "upgrade", chat_id=1, user_id=ADMIN_ID
    )

    async def fake_status(_action: str) -> CommandResult:
        return CommandResult(
            action="apt_status",
            command="apt_status upgrade",
            returncode=0,
            stdout="STATE=active\nRESULT=unknown\nEXIT=unknown\nLOAD=loaded\n",
            stderr="",
            duration=0.1,
        )

    scheduled: list[int] = []
    monkeypatch.setattr(bot_app.actions, "apt_status", fake_status)
    monkeypatch.setattr(
        bot_app, "_schedule_apt_watch", lambda jid, *_a, **_k: scheduled.append(jid)
    )

    class Job:
        data = {
            "job_id": job_id,
            "action": "apt_upgrade",
            "chat_id": 1,
            "user_id": ADMIN_ID,
            "created_at": __import__("time").time(),
            "attempts": 0,
            "errors": 0,
        }

    class Ctx:
        job = Job()

    await bot_app._job_watch_apt(Ctx())  # type: ignore[arg-type]

    assert scheduled == [job_id]
    assert len(bot_app.storage.list_pending_jobs()) == 1
