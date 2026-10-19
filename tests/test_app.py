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

from xui_monitor_bot.app import MonitorBot
from xui_monitor_bot.settings import Settings

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
