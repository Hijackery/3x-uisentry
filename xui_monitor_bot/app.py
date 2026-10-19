"""Telegram-бот мониторинга сервера и панели 3X-UI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Final, TypeVar

from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from . import ui
from .actions import (
    ACTION_APT_UPDATE,
    ACTION_COMMAND,
    ACTION_DIST_UPGRADE,
    ACTION_JOURNAL,
    ACTION_REBOOT,
    ACTION_RELEASE_UPGRADE,
    ACTION_RESTART_SERVICE,
    ACTION_RESTART_XRAY,
    ACTION_UPGRADE,
    ACTION_UPGRADE_PHASED,
    APT_KINDS,
    ActionError,
    ActionManager,
)
from .alerts import AlertEngine, Finding, Rule
from .models import MonitorSnapshot, Severity
from .monitor import Monitor
from .settings import ConfigError, Settings, load_settings
from .storage import Storage, StorageError

#: PTB обобщает Application шестью параметрами; конкретные типы здесь не важны.
BotApplication = Application[Any, Any, Any, Any, Any, Any]

logger = logging.getLogger(__name__)


def _user_id(update: Update) -> int | None:
    """ID автора апдейта или None, если его нет."""
    return update.effective_user.id if update.effective_user else None


SNAPSHOT_MAX_AGE = 30.0
SAMPLE_RETENTION_DAYS = 7
REBOOT_WATCH_ATTEMPTS = 20
REBOOT_WATCH_STEP = 30
#: Не чаще раза в 5 секунд на администратора: журнал читается через sudo.
HEAVY_COOLDOWN = 5.0
#: Пауза для «Обновить»: кнопка заставляет панель и systemctl работать.
REFRESH_COOLDOWN = 2.0
#: «Знаю, до утра»: глушим алерт на 8 часов.
MUTE_HOURS = 8
REBOOT_WATCH_GRACE = 1200
#: Как часто и как долго следим за фоновым apt-юнитом.
APT_WATCH_STEP = 10
APT_WATCH_MAX = 2 * 3600
APT_WATCH_ERROR_LIMIT = 6

#: Действия, которые ставят задачу в systemd и требуют отчёта в чат.
QUEUED_ACTIONS = (
    ACTION_APT_UPDATE,
    ACTION_UPGRADE,
    ACTION_UPGRADE_PHASED,
    ACTION_RELEASE_UPGRADE,
)

#: Короткие заголовки для отчёта о выполненном действии (в чате, не в логе).
ACTION_TITLES: dict[str, str] = {
    ACTION_RESTART_SERVICE: "Сервис перезапущен",
    ACTION_RESTART_XRAY: "xray перезапущен",
    ACTION_APT_UPDATE: "Список пакетов обновлён",
    ACTION_UPGRADE: "Обновление системы завершено",
    ACTION_UPGRADE_PHASED: "Обновление (phased) завершено",
    ACTION_DIST_UPGRADE: "Проверка релиз-обновления выполнена",
    ACTION_RELEASE_UPGRADE: "Релиз-обновление завершено",
    ACTION_REBOOT: "Сервер перезагружается",
    ACTION_COMMAND: "Команда выполнена",
    ACTION_JOURNAL: "Журнал прочитан",
}


CONFIRM_DESCRIPTIONS: dict[str, str] = {
    ACTION_RESTART_SERVICE: "Перезапустить сервис",
    ACTION_RESTART_XRAY: "Перезапустить xray через API панели",
    ACTION_APT_UPDATE: "Обновить список пакетов (apt update)",
    ACTION_UPGRADE: "Поставить apt update + full-upgrade в очередь systemd",
    ACTION_UPGRADE_PHASED: "Поставить apt update + upgrade (phased) в очередь systemd",
    ACTION_DIST_UPGRADE: "Проверить доступность релиз-обновления ОС",
    ACTION_RELEASE_UPGRADE: (
        "Запустить do-release-upgrade (обновление версии ОС; надолго)"
    ),
    ACTION_REBOOT: "Перезагрузить сервер",
}


class MonitorBot:
    """Собирает хендлеры, фоновые задачи и подтверждение опасных действий."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.state_path)
        self.monitor = Monitor(settings)
        self.engine = AlertEngine(settings, self.storage)
        self.actions = ActionManager(settings)
        self._snapshot: MonitorSnapshot | None = None
        self._snapshot_at: float = 0.0
        #: Последнее обращение к «тяжёлым» командам (журнал через sudo).
        #: Троттлинг по «ведрам»: {"journal": {user_id: время}, "refresh": {...}}
        self._throttle_seen: dict[str, dict[int, float]] = {}
        #: Последний «экран» в чате: при переходе к новому меню старый удаляем,
        #: чтобы не плодить вереницу сообщений. Алерты и отчёты не трогаем.
        self._last_screen: dict[int, int] = {}
        self.app: BotApplication = self._build()

    # ------------------------------------------------------------ сборка

    def _build(self) -> BotApplication:
        app = (
            ApplicationBuilder()
            .token(self.settings.telegram_token)
            # Свой Bot API (локальный сервер, корпоративный прокси): базовый URL
            # плюс «/bot», как у официального api.telegram.org.
            .base_url(f"{self.settings.telegram_api_url.rstrip('/')}/bot")
            .concurrent_updates(False)
            # HTML по умолчанию: разметка в отчётах не требует указания mode
            # в каждом вызове send_message.
            .defaults(Defaults(parse_mode=ParseMode.HTML))
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        app.bot_data["bot"] = self
        for name, handler in (
            ("start", self.cmd_start),
            ("help", self.cmd_help),
            ("status", self.cmd_status),
            ("services", self.cmd_services),
            ("panel", self.cmd_panel),
            ("subs", self.cmd_subs),
            ("history", self.cmd_history),
            ("audit", self.cmd_audit),
            ("logs", self.cmd_logs),
            ("maint", self.cmd_maintenance),
            ("traffic", self.cmd_traffic),
            ("run", self.cmd_run),
        ):
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_error_handler(self.on_error)

        job_queue = app.job_queue
        if job_queue is None:  # требуется python-telegram-bot[job-queue]
            raise RuntimeError(
                "JobQueue недоступен: поставьте python-telegram-bot[job-queue]"
            )
        job_queue.run_repeating(
            self.job_check,
            interval=timedelta(seconds=self.settings.check_interval),
            first=timedelta(seconds=10),
            name="periodic-check",
        )
        job_queue.run_repeating(
            self.job_prune,
            interval=timedelta(hours=6),
            first=timedelta(minutes=10),
            name="prune",
        )
        return app

    async def _post_init(self, _app: BotApplication) -> None:
        logger.info(
            "Бот запущен. Сервисы: %s, панель: %s, админов: %s",
            ", ".join(self.settings.services),
            self.settings.api_base,
            len(self.settings.admin_ids),
        )
        await self._verify_after_reboot()
        await self._resume_apt_watches()

    async def _post_shutdown(self, _app: BotApplication) -> None:
        await self.monitor.close()
        self.storage.close()
        logger.info("Бот остановлен")

    async def _verify_after_reboot(self) -> None:
        """Если перед ребутом нажали «Перезагрузить», подтверждаем возврат.

        Сама перезагрузка убивает процесс бота, поэтому проверять сервер
        приходится уже после старта: так сообщение о возврате точно дойдёт.
        """
        job_queue = self.app.job_queue
        if job_queue is None:  # pragma: no cover - JobQueue проверяется при сборке
            return
        rows = await asyncio.to_thread(self.storage.recent_audit, 5)
        reboot = next(
            (row for row in rows if row["action"] == ACTION_REBOOT and row["ok"]),
            None,
        )
        if reboot is None or time.time() - reboot["ts"] > REBOOT_WATCH_GRACE:
            return
        job_queue.run_once(
            self._job_reboot_watch,
            timedelta(seconds=REBOOT_WATCH_STEP),
            name="reboot-watch-after-start",
            data={
                "chat_id": int(reboot["telegram_id"] or 0),
                "attempt": 0,
                "verify": True,
            },
        )

    # -------------------------------------------------------- снапшоты

    async def snapshot(self, *, force: bool = False) -> MonitorSnapshot:
        """Отдаёт свежий снимок или кэш, если он не старше SNAPSHOT_MAX_AGE."""
        age = time.monotonic() - self._snapshot_at
        if not force and self._snapshot is not None and age < SNAPSHOT_MAX_AGE:
            return self._snapshot
        snapshot = await self.monitor.snapshot()
        self._snapshot = snapshot
        self._snapshot_at = time.monotonic()
        return snapshot

    def invalidate(self) -> None:
        self._snapshot = None
        self._snapshot_at = 0.0

    # -------------------------------------------------------------- utils

    def is_admin(self, user_id: int | None) -> bool:
        return self.settings.is_admin(user_id)

    async def _notify(
        self,
        chat_id: int,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        delivered = True
        for chunk in _split(text, 3500):
            try:
                await _send_with_retry(
                    self.app.bot.send_message,
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            except TelegramError as exc:
                logger.error(
                    "Не удалось отправить сообщение в чат %s: %s",
                    _mask_id(chat_id),
                    exc,
                )
                delivered = False
        return delivered

    async def _alert_targets(self) -> list[int]:
        if self.settings.alert_chat_id:
            return [self.settings.alert_chat_id]
        return list(self.settings.admin_ids)

    # --------------------------------------------------------- хендлеры

    async def on_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logger.error("Ошибка при обработке апдейта", exc_info=context.error)
        message = getattr(update, "effective_message", None)
        if isinstance(message, Message):
            await message.reply_text(
                "😔 Внутренняя ошибка. Повторите команду; если повторяется — "
                "журнал бота:\n<code>journalctl -u xui-monitor-bot -n 50</code>",
                parse_mode=ParseMode.HTML,
            )

    async def cmd_start(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        user = update.effective_user
        allowed = self.is_admin(user.id if user else None)
        # Гостю кнопки не показываем вовсе: каждая из них всё равно ответила бы
        # отказом — это только вводит в заблуждение.
        await self._reply_screen(
            message,
            "🛰 Бот мониторинга сервера и панели 3X-UI.\n\n"
            + (
                "Показывает состояние системы, сервисов, панели, подписок и "
                "трафика, а также позволяет перезапускать сервисы и обновлять "
                "систему.\n\nНачните с /status или выберите раздел кнопками."
                if allowed
                else "⛔ Этот бот доступен только администратору сервера."
            ),
            markup=ui.main_menu(True) if allowed else None,
        )

    async def cmd_help(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        allowed = self.is_admin(_user_id(update))
        await self._reply_screen(
            message,
            ui.render_help(allowed),
            markup=ui.main_menu(True) if allowed else None,
        )

    async def cmd_status(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        await message.reply_chat_action(ChatAction.TYPING)
        snapshot = await self.snapshot()
        await self._reply_screen(
            message,
            ui.render_overview(snapshot, self.settings, age=self._snapshot_age()),
            markup=ui.main_menu(self.is_admin(_user_id(update))),
        )

    async def cmd_services(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        await self._reply_screen(
            message,
            ui.render_services(await self.snapshot()),
            markup=ui.services_menu(
                self.settings.services, self.is_admin(_user_id(update))
            ),
        )

    async def cmd_panel(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        text = ui.render_panel(await self.snapshot())
        try:
            update_info = await self.monitor.panel.panel_update_info()
        except Exception as exc:  # доп. информация не критична
            logger.info("getPanelUpdateInfo недоступен: %s", exc)
            update_info = {}
        if update_info:
            latest = update_info.get("tagName") or update_info.get("version") or "?"
            text += f"\nПоследний релиз панели: {ui.esc(latest)}"
        await self._reply_screen(
            message, text, markup=ui.main_menu(self.is_admin(_user_id(update)))
        )

    async def cmd_subs(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        text, markup = await self._subs_view(0)
        await self._reply_screen(message, text, markup=markup)

    async def _subs_view(self, offset: int) -> tuple[str, InlineKeyboardMarkup]:
        """Страница подписок и клавиатура к ней (общая для /subs и кнопок)."""
        subs = (await self.snapshot()).subscriptions
        soon = self.settings.expiring_soon_days
        ordered = ui.ordered_subscriptions(subs, soon)
        if offset < 0 or offset >= len(ordered):
            offset = 0
        has_more = offset + ui.SUBSCRIPTIONS_PAGE < len(ordered)
        return (
            ui.render_subscriptions(subs, soon_days=soon, offset=offset),
            ui.subscriptions_menu(offset, has_more=has_more),
        )

    async def cmd_history(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        rows = await asyncio.to_thread(
            self.storage.get_series, time.time() - 24 * 3600, 24
        )
        await self._reply_screen(
            message,
            ui.render_history(rows, title="Последние 24 часа"),
            markup=ui.main_menu(self.is_admin(_user_id(update))),
        )

    async def cmd_audit(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        rows = await asyncio.to_thread(self.storage.recent_audit, 15)
        await self._reply_screen(message, ui.render_audit(rows), markup=ui.audit_menu())

    async def cmd_logs(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        await self._reply_screen(
            message,
            "📜 <b>Журналы сервисов</b>",
            markup=ui.logs_menu(self.settings.services),
        )

    async def cmd_maintenance(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        await self._reply_screen(
            message,
            "🛠 <b>Обслуживание</b>\n\n"
            "Все действия требуют подтверждения и пишутся в журнал.",
            markup=ui.maintenance_menu(),
        )

    async def cmd_traffic(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        await self._reply_screen(
            message,
            ui.render_traffic(await self.snapshot()),
            markup=ui.main_menu(self.is_admin(_user_id(update))),
        )

    async def cmd_run(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/run <команда>` — команда из белого списка из любого раздела."""
        message = update.effective_message
        if message is None or not await self._guard(message, update.effective_user):
            return
        text = " ".join((message.text or "").split())
        arg = text.partition(" ")[2].strip()
        if not arg:
            allowed = (
                "\n".join(
                    f"• <code>{ui.esc(item)}</code>"
                    for item in self.settings.command_allowlist
                )
                or "—"
            )
            await self._reply_screen(
                message,
                "💻 <b>Команда на сервер</b>\n\n"
                "Пришлите команду следующим сообщением в любом разделе или "
                "<code>/run &lt;команда&gt;</code>.\n\n"
                f"Разрешено:\n{allowed}",
                markup=ui.main_menu(True),
            )
            return
        if context.user_data is not None:
            context.user_data["awaiting_command"] = True
        await self._request_command(message, _user_id(update) or 0, arg)

    async def _request_command(
        self, message: Message, user_id: int, command: str
    ) -> None:
        if command not in self.settings.command_allowlist:
            allowed = ", ".join(self.settings.command_allowlist) or "пусто"
            await self._reply_screen(
                message,
                "⛔ Команда не в белом списке.\n"
                f"Разрешено: <code>{ui.esc(allowed)}</code>",
                markup=ui.main_menu(self.is_admin(user_id)),
            )
            return
        pending = self.actions.request(
            ACTION_COMMAND, command, command, owner_id=user_id
        )
        await self._reply_screen(
            message,
            f"⚠️ Выполнить команду?\n<code>{ui.esc(command)}</code>",
            markup=ui.confirm_menu(ACTION_COMMAND, pending.token),
        )

    async def _reply_screen(
        self,
        message: Message,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
    ) -> Message | None:
        """Показывает новый экран и убирает предыдущий, чтобы меню не копились."""
        chat_id = message.chat_id
        try:
            sent = await _send_with_retry(
                message.reply_text,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except TelegramError as exc:
            logger.warning("Не удалось отправить экран: %s", exc)
            return None
        if sent is None:
            return None
        mid = getattr(sent, "message_id", None)
        if not isinstance(mid, int):
            # Тесты и нестандартные транспорты могут не вернуть Message.
            return sent
        previous = self._last_screen.get(chat_id)
        self._last_screen[chat_id] = mid
        if previous and previous != mid:
            try:
                await message.get_bot().delete_message(chat_id, previous)
            except TelegramError as exc:
                logger.debug("Не удалось удалить прошлый экран: %s", exc)
        return sent

    async def _guard(self, message: Message, user: User | None) -> bool:
        """Пропускает администратора в личном чате; отказ пишет в журнал."""
        if not self._chat_allowed(message.chat):
            await message.reply_text(
                "⛔ Команды принимаю только в личном чате администратора."
            )
            return False
        if self.is_admin(user.id if user else None):
            return True
        await self._audit_denied(user, message.text or "")
        await message.reply_text("⛔ Только для администраторов")
        return False

    def _chat_allowed(self, chat: Chat) -> bool:
        """Ответы сервера не должны попадать в группы и каналы.

        Проверка по ID админа пропускает его же команды из группы, поэтому
        тип чата приходится проверять отдельно: иначе /status выложит в чат
        адреса, подписки и трафик.
        """
        # Только личка. alert_chat_id разрешает исходящие алерты в группу, но
        # не управление: иначе /status и подписки увидели бы все участники.
        return chat.type == ChatType.PRIVATE

    async def _audit_denied(self, user: User | None, text: str) -> None:
        """Попытка доступа не-администратора: след в журнале для разбора."""
        try:
            await asyncio.to_thread(
                self.storage.audit,
                "denied",
                telegram_id=user.id if user else None,
                ok=False,
                result=f"команда {text[:40]!r} от не-администратора",
            )
        except sqlite3.Error as exc:  # журнал не должен ломать отказ
            logger.warning("Не удалось записать отказ в журнал: %s", exc)

    def _snapshot_age(self) -> float:
        """Сколько секунд назад собран текущий снимок (0 — только что)."""
        if self._snapshot_at <= 0:
            return 0.0
        return max(0.0, time.monotonic() - self._snapshot_at)

    def _throttle(self, bucket: str, key: int, seconds: float) -> float:
        """Пауза для (ведро, ключ): 0 — можно выполнять, иначе остаток."""
        now = time.monotonic()
        seen = self._throttle_seen.setdefault(bucket, {})
        remaining = seconds - (now - seen.get(key, 0.0))
        if remaining > 0:
            return remaining
        seen[key] = now
        return 0.0

    def _heavy_wait(self, user_id: int) -> float:
        """Сколько секунд осталось до следующего тяжёлого запроса (0 — можно)."""
        return self._throttle("journal", user_id, HEAVY_COOLDOWN)

    # ---------------------------------------------------------- callbacks

    async def on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query: CallbackQuery | None = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()
        data = query.data
        user_id = query.from_user.id

        # Команды и кнопки — только в личном чате (см. _chat_allowed).
        message = query.message
        if isinstance(message, Message) and not self._chat_allowed(message.chat):
            await query.answer(
                "⛔ Кнопки работают только в личном чате", show_alert=True
            )
            return

        # Гейт стоит до разбора callback_data: иначе гость, угадав токен
        # подтверждения, мог отменить действие администратора.
        if not self.is_admin(user_id):
            await query.answer("⛔ Только для администраторов", show_alert=False)
            await self._edit(query, "⛔ Только для администраторов.")
            return

        # Мгновенный отклик: сбор данных может занять секунды (медленная
        # панель), и без этой строки кажется, что бот завис.
        if data in {
            "home",
            "refresh",
            "server",
            "panel",
            "traffic",
            "subs",
            "history",
            "details",
        } or data.startswith("subs:"):
            await self._edit(query, "⏳ Собираю данные…")

        if data.startswith("mute:"):
            key = data[5:]
            until = await asyncio.to_thread(self.storage.mute, key, MUTE_HOURS * 3600)
            await self._edit(
                query,
                f"😴 Понял: <code>{ui.esc(key)}</code> молчит до "
                f"{time.strftime('%H:%M', time.localtime(until))}. "
                "Если проблема пройдёт сама — сообщу, когда срок выйдет.",
                markup=ui.main_menu(self.is_admin(user_id)),
            )
            return

        if data.startswith("ask:"):
            await self._ask_confirmation(query, data)
            return
        if data.startswith("do:"):
            await self._run_confirmed(query, data, user_id)
            return
        if data.startswith("no:"):
            cancelled = self.actions.cancel(data[3:])
            await self._edit(
                query,
                "❌ Отменено — действие не выполнено."
                if cancelled
                else "⌛ Подтверждение уже неактуально, отменять нечего.",
                markup=ui.main_menu(self.is_admin(user_id)),
            )
            return

        if data == "home" or data == "refresh":
            # «Обновить» — единственный путь, который каждый раз дёргает панель
            # и systemctl, поэтому держим паузу: спам кнопкой не должен
            # превращаться в нагрузку на сервер.
            wait = self._throttle("refresh", user_id, REFRESH_COOLDOWN)
            if wait > 0:
                await query.answer(f"⏳ Обновление через {wait:.0f} с")
                return
            self.invalidate()
            await self._edit(
                query,
                ui.render_overview(
                    await self.snapshot(force=True),
                    self.settings,
                    age=0.0,
                    compact=True,
                ),
                markup=ui.overview_menu(self.is_admin(user_id), compact=True),
            )
        elif data in {"details", "server"}:
            await self._edit(
                query,
                ui.render_overview(
                    await self.snapshot(), self.settings, age=self._snapshot_age()
                ),
                markup=ui.overview_menu(self.is_admin(user_id), compact=False),
            )
        elif data == "services":
            await self._edit(
                query,
                ui.render_services(await self.snapshot()),
                markup=ui.services_menu(self.settings.services, self.is_admin(user_id)),
            )
        elif data == "panel":
            await self._edit(
                query,
                ui.render_panel(await self.snapshot()),
                markup=ui.main_menu(self.is_admin(user_id)),
            )
        elif data == "traffic":
            await self._edit(
                query,
                ui.render_traffic(await self.snapshot()),
                markup=ui.main_menu(self.is_admin(user_id)),
            )
        elif data == "subs" or data.startswith("subs:"):
            offset = (
                int(data[5:]) if data.startswith("subs:") and data[5:].isdigit() else 0
            )
            text, markup = await self._subs_view(offset)
            await self._edit(query, text, markup=markup)
        elif data == "history":
            rows = await asyncio.to_thread(
                self.storage.get_series, time.time() - 24 * 3600, 24
            )
            await self._edit(
                query,
                ui.render_history(rows, title="Последние 24 часа"),
                markup=ui.main_menu(self.is_admin(user_id)),
            )
        elif data == "logs_menu":
            await self._edit(
                query, "📜 Выберите сервис", markup=ui.logs_menu(self.settings.services)
            )
        elif data.startswith("audit:"):
            mode = data[6:]
            rows = await asyncio.to_thread(
                self.storage.recent_audit,
                15,
                only="" if mode == "all" else mode,
            )
            title = {"denied": " (отказы)", "actions": " (действия)"}.get(mode, "")
            await self._edit(
                query,
                ui.render_audit(rows).replace(
                    "Журнал действий", f"Журнал действий{title}"
                ),
                markup=ui.audit_menu(),
            )
        elif data == "help":
            await self._edit(
                query,
                ui.render_help(self.is_admin(user_id)),
                markup=ui.main_menu(self.is_admin(user_id)),
            )
        elif data == "maintenance":
            await self._edit(
                query,
                "🛠 <b>Обслуживание</b>\n\n"
                "Все действия требуют подтверждения и пишутся в журнал.",
                markup=ui.maintenance_menu(),
            )
        elif data.startswith("logs:"):
            await self._show_logs(query, data[5:])
        elif data.startswith("logs200:"):
            await self._show_logs(query, data[8:], lines=200)
        elif data.startswith("logs_err:"):
            await self._show_logs(query, data[9:], errors=True)
        elif data == "command_prompt":
            await self._prompt_command(query, context)
        else:
            await self._edit(
                query,
                "Неизвестная кнопка. Возможно, клавиатура устарела.",
                markup=ui.main_menu(self.is_admin(user_id)),
            )

    # ------------------------------------------------------- подтверждения

    async def _ask_confirmation(self, callback: CallbackQuery, data: str) -> None:
        if not self.is_admin(callback.from_user.id):
            await self._edit(callback, "⛔ Только для администраторов.")
            return

        parts = data.split(":", 2)
        if len(parts) < 3:
            await self._edit(
                callback,
                "Неизвестная кнопка. Возможно, клавиатура устарела.",
                markup=ui.main_menu(self.is_admin(callback.from_user.id)),
            )
            return
        _, action, arg = parts
        base = CONFIRM_DESCRIPTIONS.get(action)
        if base is None:
            await self._edit(
                callback,
                "Неизвестное действие. Возможно, клавиатура устарела.",
                markup=ui.main_menu(self.is_admin(callback.from_user.id)),
            )
            return

        description = (
            f"{base} <code>{ui.esc(arg)}</code>?"
            if action == ACTION_RESTART_SERVICE
            else f"{base}?"
        )
        if action == ACTION_REBOOT:
            description = (
                "<b>Перезагрузить сервер?</b> "
                "Бот и панель будут недоступны ~1-2 минуты."
            )

        pending = self.actions.request(
            action, description, arg, owner_id=callback.from_user.id
        )
        await self._edit(
            callback,
            f"⚠️ {description}",
            markup=ui.confirm_menu(action, pending.token),
        )

    async def _run_confirmed(
        self, callback: CallbackQuery, data: str, user_id: int
    ) -> None:
        if not self.is_admin(user_id):
            await self._edit(callback, "⛔ Только для администраторов.")
            return

        parts = data.split(":", 2)
        if len(parts) < 3:
            await self._edit(
                callback,
                "Неизвестная кнопка. Возможно, клавиатура устарела.",
                markup=ui.main_menu(self.is_admin(user_id)),
            )
            return
        _, action, token = parts
        try:
            pending = self.actions.consume(token, user_id)
        except ActionError as exc:
            await self._edit(
                callback,
                f"⏳ {ui.esc(str(exc))}",
                markup=ui.main_menu(self.is_admin(callback.from_user.id)),
            )
            return
        if pending is None:
            await self._edit(
                callback,
                "⏳ Подтверждение не найдено или уже использовано.",
                markup=ui.main_menu(self.is_admin(callback.from_user.id)),
            )
            return
        if pending.action != action:
            await self._edit(
                callback,
                "⛔ Действие не совпадает с подтверждением.",
                markup=ui.main_menu(self.is_admin(callback.from_user.id)),
            )
            return

        await self._dispatch(callback, pending.action, pending.args, user_id)

    async def _dispatch(
        self,
        query: CallbackQuery,
        action: str,
        args: tuple[str, ...],
        user_id: int,
    ) -> None:
        chat_id = _chat_id(query)
        # Переиспользуем сообщение с подтверждением, а не плодим ещё одно.
        status = _message_of(query)
        await _safe_edit(status, "⏳ Выполняю…", markup=None)
        if chat_id:
            self._last_screen[chat_id] = status.message_id
        self.invalidate()

        try:
            if action == ACTION_REBOOT:
                await self._do_reboot(status, user_id)
                return
            if action in QUEUED_ACTIONS or action == ACTION_DIST_UPGRADE:
                self._schedule_long_action(action, chat_id, user_id)
                await _safe_edit(
                    status,
                    "⏳ Запущено в фоне — отчёт придёт по завершении.",
                    markup=ui.result_menu(),
                )
                return
            if action == ACTION_COMMAND:
                result = await self.actions.run_allowed_command(args[0])
            elif action == ACTION_RESTART_SERVICE:
                result = await self.actions.restart_service(args[0])
                self.monitor.services.forget(args[0])
            elif action == ACTION_RESTART_XRAY:
                result = await self.actions.restart_xray()
            else:
                raise ActionError(f"Неизвестное действие: {action}")
        except Exception as exc:  # noqa: BLE001 — сбой возвращаем в чат, не «зависаем»
            logger.error("Действие %s не удалось: %s", action, exc, exc_info=True)
            await asyncio.to_thread(
                self.storage.audit,
                action,
                telegram_id=user_id,
                ok=False,
                result=str(exc) or exc.__class__.__name__,
            )
            await _safe_edit(
                status,
                f"❌ {ui.esc(str(exc) or exc.__class__.__name__)}",
                markup=ui.result_menu(),
            )
            return

        icon = "✅" if result.ok else "⚠️"
        title = ACTION_TITLES.get(action, action)
        body = ui.esc(result.summary(1500))
        extra: list[InlineKeyboardButton] = []
        if action == ACTION_RESTART_SERVICE and args:
            unit = args[0]
            extra.append(
                InlineKeyboardButton(f"📜 Журнал {unit}", callback_data=f"logs:{unit}")
            )
        tail = "" if result.ok else f"\nКод возврата: {result.returncode}"
        await _safe_edit(
            status,
            f"{icon} <b>{ui.esc(title)}</b> за {result.duration:.1f} с\n"
            f"<code>{ui.esc(result.command)}</code>\n\n<pre>{body}</pre>{tail}",
            markup=ui.result_menu(extra),
        )
        await asyncio.to_thread(
            self.storage.audit,
            action,
            telegram_id=user_id,
            params={"args": list(args)},
            ok=result.ok,
            result=result.summary(500),
        )

    # ------------------------------------------------------- долгие операции

    def _schedule_long_action(self, action: str, chat_id: int, user_id: int) -> None:
        job_queue = self.app.job_queue
        if job_queue is None:  # pragma: no cover - JobQueue проверяется при сборке
            return
        job_queue.run_once(
            self._job_long_action,
            timedelta(seconds=1),
            name=f"long-{action}",
            data={"action": action, "chat_id": chat_id, "user_id": user_id},
        )

    async def _job_long_action(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        data = dict(getattr(context.job, "data", None) or {})
        action = str(data.get("action", ""))
        chat_id = int(data.get("chat_id", 0) or 0)
        user_id = int(data.get("user_id", 0) or 0)

        try:
            if action == ACTION_APT_UPDATE:
                result = await self.actions.apt_update()
            elif action == ACTION_UPGRADE:
                result = await self.actions.apt_upgrade()
            elif action == ACTION_UPGRADE_PHASED:
                result = await self.actions.apt_upgrade_phased()
            elif action == ACTION_RELEASE_UPGRADE:
                result = await self.actions.release_upgrade()
            else:
                result = await self.actions.dist_upgrade()
        except Exception as exc:  # noqa: BLE001 — фон не должен молча «зависать»
            logger.error("Фоновая задача %s упала: %s", action, exc, exc_info=True)
            await self._notify(
                chat_id, f"❌ {ui.esc(str(exc) or exc.__class__.__name__)}"
            )
            await asyncio.to_thread(
                self.storage.audit,
                action,
                telegram_id=user_id,
                ok=False,
                result=str(exc) or exc.__class__.__name__,
            )
            return

        # Задача в systemd поставлена. Дальше следим за юнитом сами и пришлём
        # отчёт в чат — пользователю не нужно ничего вводить.
        if action in QUEUED_ACTIONS and result.ok:
            kind = APT_KINDS[action]
            job_id = await asyncio.to_thread(
                self.storage.add_pending_job, action, kind, chat_id, user_id
            )
            self._schedule_apt_watch(
                job_id, action, chat_id, user_id, created_at=time.time()
            )
            await self._notify(
                chat_id,
                "⏳ <b>" + ui.esc(ACTION_TITLES.get(action, action)) + " запущено</b>\n"
                "Задача выполняется в фоне. Пришлю отчёт сюда, когда закончится — "
                "ничего вводить не нужно.",
                markup=ui.result_menu(),
            )
            return

        icon = "✅" if result.ok else "⚠️"
        text = (
            f"{icon} <b>{ui.esc(ACTION_TITLES.get(action, result.command))}</b>\n"
            f"код возврата {result.returncode},"
            f" длительность {result.duration:.0f} с\n\n"
            f"<pre>{ui.esc(result.summary(2500))}</pre>"
        )
        if _needs_reboot():
            text += (
                "\n\n🔁 <b>Требуется перезагрузка</b> для применения ядра/пакетов. "
                "Кнопка «Перезагрузить» в разделе Обслуживание."
            )
        delivered = await self._notify(chat_id, text, markup=ui.result_menu())
        await asyncio.to_thread(
            self.storage.audit,
            action,
            telegram_id=user_id,
            params={"background": True},
            ok=result.ok,
            result=result.summary(500),
        )
        self.invalidate()
        if not delivered:
            logger.warning("Отчёт по %s не доставлен", action)

    # ------------------------------------------------------- apt watcher

    def _schedule_apt_watch(
        self,
        job_id: int,
        action: str,
        chat_id: int,
        user_id: int,
        *,
        created_at: float,
        attempts: int = 0,
        errors: int = 0,
    ) -> None:
        job_queue = self.app.job_queue
        if job_queue is None:  # pragma: no cover
            return
        job_queue.run_once(
            self._job_watch_apt,
            timedelta(seconds=APT_WATCH_STEP),
            name=f"apt-watch-{job_id}",
            data={
                "job_id": job_id,
                "action": action,
                "chat_id": chat_id,
                "user_id": user_id,
                "created_at": created_at,
                "attempts": attempts,
                "errors": errors,
            },
        )

    async def _resume_apt_watches(self) -> None:
        rows = await asyncio.to_thread(self.storage.list_pending_jobs)
        for row in rows:
            created_at = float(row["created_at"])
            if time.time() - created_at > APT_WATCH_MAX:
                await asyncio.to_thread(self.storage.remove_pending_job, int(row["id"]))
                continue
            self._schedule_apt_watch(
                int(row["id"]),
                str(row["action"]),
                int(row["chat_id"]),
                int(row["user_id"]),
                created_at=created_at,
            )

    async def _job_watch_apt(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        data = dict(getattr(context.job, "data", None) or {})
        job_id = int(data.get("job_id", 0) or 0)
        action = str(data.get("action", ""))
        chat_id = int(data.get("chat_id", 0) or 0)
        user_id = int(data.get("user_id", 0) or 0)
        created_at = float(data.get("created_at", time.time()))
        attempts = int(data.get("attempts", 0) or 0)
        errors = int(data.get("errors", 0) or 0)

        try:
            status = await self.actions.apt_status(action)
            fields, tail = _parse_apt_status(status.stdout)
        except Exception as exc:  # noqa: BLE001 — сеть/права/юнит
            errors += 1
            logger.warning("Не удалось узнать статус %s: %s", action, exc)
            if (
                errors < APT_WATCH_ERROR_LIMIT
                and time.time() - created_at < APT_WATCH_MAX
            ):
                self._schedule_apt_watch(
                    job_id,
                    action,
                    chat_id,
                    user_id,
                    created_at=created_at,
                    attempts=attempts,
                    errors=errors,
                )
            else:
                await self._finish_apt_watch(
                    job_id,
                    action,
                    chat_id,
                    user_id,
                    ok=False,
                    text="⚠️ Не удалось получить отчёт об обновлении. "
                    f"Проверьте: <code>sudo xui-menu apt-logs</code>\n"
                    f"<pre>{ui.esc(str(exc))}</pre>",
                )
            return

        state = fields.get("STATE", "unknown")
        if state in {"active", "activating", "reloading", "deactivating"}:
            if time.time() - created_at > APT_WATCH_MAX:
                await self._finish_apt_watch(
                    job_id,
                    action,
                    chat_id,
                    user_id,
                    ok=False,
                    text="⚠️ Обновление идёт слишком долго (больше 2 часов). "
                    "Проверьте: <code>sudo xui-menu apt-logs</code>",
                )
            else:
                self._schedule_apt_watch(
                    job_id,
                    action,
                    chat_id,
                    user_id,
                    created_at=created_at,
                    attempts=attempts + 1,
                    errors=errors,
                )
            return

        # Юнит ещё не успел появиться (например, только что скомандовали start).
        if (
            state in {"unknown", ""}
            and fields.get("LOAD") == "not-found"
            and attempts < 3
        ):
            self._schedule_apt_watch(
                job_id,
                action,
                chat_id,
                user_id,
                created_at=created_at,
                attempts=attempts + 1,
                errors=errors,
            )
            return

        ok = fields.get("RESULT") == "success" and fields.get("EXIT") == "0"
        icon = "✅" if ok else "⚠️"
        body = tail[-2500:] if tail else "(вывод пуст)"
        text = (
            f"{icon} <b>{ui.esc(ACTION_TITLES.get(action, action))}</b>\n"
            f"код возврата {ui.esc(fields.get('EXIT', 'unknown'))}\n\n"
            f"<pre>{ui.esc(body)}</pre>"
        )
        if ok and _needs_reboot():
            text += (
                "\n\n🔁 <b>Требуется перезагрузка</b> для применения ядра/пакетов. "
                "Кнопка «Перезагрузить» в разделе Обслуживание."
            )
        await self._finish_apt_watch(job_id, action, chat_id, user_id, ok=ok, text=text)

    async def _finish_apt_watch(
        self,
        job_id: int,
        action: str,
        chat_id: int,
        user_id: int,
        *,
        ok: bool,
        text: str,
    ) -> None:
        await asyncio.to_thread(self.storage.remove_pending_job, job_id)
        delivered = await self._notify(chat_id, text, markup=ui.result_menu())
        await asyncio.to_thread(
            self.storage.audit,
            action,
            telegram_id=user_id,
            params={"background": True},
            ok=ok,
            result="watcher",
        )
        self.invalidate()
        if not delivered:
            logger.warning("Отчёт по %s не доставлен", action)

    # --------------------------------------------------------------- reboot

    async def _do_reboot(self, status: Message, user_id: int) -> None:
        chat_id = status.chat_id
        await _safe_edit(status, "🔁 Перезагружаю сервер…")
        try:
            result = await self.actions.reboot()
        except ActionError as exc:
            await asyncio.to_thread(
                self.storage.audit,
                ACTION_REBOOT,
                telegram_id=user_id,
                ok=False,
                result=str(exc),
            )
            await _safe_edit(status, f"❌ {ui.esc(str(exc))}", markup=ui.result_menu())
            return

        await asyncio.to_thread(
            self.storage.audit,
            ACTION_REBOOT,
            telegram_id=user_id,
            ok=result.ok,
            result=result.summary(300),
        )
        if not result.ok:
            await _safe_edit(
                status,
                f"⚠️ Reboot не выполнен: {ui.esc(result.summary(300))}",
                markup=ui.result_menu(),
            )
            return

        await _safe_edit(
            status,
            "🔁 Сервер перезагружается. После старта я проверю, что всё поднялось.",
        )
        self._schedule_reboot_watch(chat_id, verify=True)

    def _schedule_reboot_watch(self, chat_id: int, *, verify: bool) -> None:
        job_queue = self.app.job_queue
        if job_queue is None:  # pragma: no cover
            return
        job_queue.run_once(
            self._job_reboot_watch,
            timedelta(seconds=REBOOT_WATCH_STEP),
            name="reboot-watch",
            data={"chat_id": chat_id, "attempt": 0, "verify": verify},
        )

    async def _job_reboot_watch(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Проверяет возврат сервера, не блокируя очередь задач.

        Пока сервер не поднялся, задача перепланирует сама себя: sleep внутри
        JobQueue остановил бы плановые проверки и алерты.
        """
        job_queue = context.job_queue
        if job_queue is None:  # pragma: no cover - очередь есть при run_polling
            return
        data = dict(getattr(context.job, "data", None) or {})
        chat_id = int(data.get("chat_id", 0) or 0)
        attempt = int(data.get("attempt", 0)) + 1
        verify = bool(data.get("verify", False))

        if not await self.monitor.services.is_active(self.settings.services[0]):
            if attempt < REBOOT_WATCH_ATTEMPTS:
                job_queue.run_once(
                    self._job_reboot_watch,
                    timedelta(seconds=REBOOT_WATCH_STEP),
                    name="reboot-watch",
                    data={"chat_id": chat_id, "attempt": attempt, "verify": verify},
                )
                return
            await self._notify(
                chat_id,
                "⚠️ Сервер не поднялся дольше 10 минут. Проверьте консоль VDS.",
            )
            return

        if not verify:
            return

        snapshot = await self.snapshot(force=True)
        services_ok = all(service.active for service in snapshot.services)
        panel_ok = snapshot.panel.reachable
        verdict = (
            "✅ Сервер вернулся" if services_ok and panel_ok else "⚠️ Вернулся частично"
        )
        await self._notify(
            chat_id,
            f"{verdict}\n"
            f"Сервисы: {'ок' if services_ok else 'есть проблемы'} · "
            f"панель: {'отвечает' if panel_ok else 'не отвечает'}",
        )

    # ------------------------------------------------------------- текст

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команду сервера можно прислать в любом разделе, без /maint."""
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if not self._chat_allowed(message.chat) or not self.is_admin(user.id):
            return
        command = " ".join((message.text or "").split())
        if not command:
            return
        user_data = context.user_data or {}
        awaiting = bool(user_data.pop("awaiting_command", False))
        allowed = self.settings.command_allowlist
        if command in allowed:
            await self._request_command(message, user.id, command)
        elif awaiting:
            listed = ", ".join(allowed) or "пусто"
            await self._reply_screen(
                message,
                "⛔ Команда не в белом списке.\n"
                f"Разрешено: <code>{ui.esc(listed)}</code>",
                markup=ui.main_menu(True),
            )

    async def _prompt_command(
        self,
        callback: CallbackQuery,
        context: CallbackContext[Any, Any, Any, Any],
    ) -> None:
        if not self.is_admin(callback.from_user.id):
            await self._edit(callback, "⛔ Только для администраторов.")
            return
        if context.user_data is not None:
            context.user_data["awaiting_command"] = True
        allowed = ", ".join(self.settings.command_allowlist) or "—"
        await self._edit(
            callback,
            "💻 Пришлите команду следующим сообщением. Отмена — кнопка в меню.\n"
            f"Разрешено: <code>{ui.esc(allowed)}</code>",
            markup=ui.main_menu(True),
        )

    # ---------------------------------------------------------- фоновые

    async def job_check(self, _context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            snapshot = await self.monitor.snapshot()
        except Exception:  # фоновая задача не должна падать
            logger.exception("Плановый сбор не удался")
            return

        self._snapshot = snapshot
        self._snapshot_at = time.monotonic()

        if snapshot.system is not None:
            try:
                await asyncio.to_thread(self.storage.record_sample, snapshot.system)
                await asyncio.to_thread(
                    self.storage.prune_samples,
                    time.time() - SAMPLE_RETENTION_DAYS * 86400,
                )
            except sqlite3.Error as exc:
                logger.warning("Не удалось записать метрики в базу: %s", exc)

        findings = self.engine.evaluate(snapshot)
        try:
            outbox = await asyncio.to_thread(self.engine.update, findings)
        except sqlite3.Error as exc:
            logger.warning("Не удалось обновить состояние алертов: %s", exc)
            return
        if not outbox:
            return

        chunks = _split_alerts(outbox)
        markup = ui.alert_menu(
            _alert_actions(outbox, self.settings), mute_key=outbox[0].state_key
        )
        delivered = True
        for chat_id in await self._alert_targets():
            for index, chunk in enumerate(chunks):
                delivered = (
                    await self._notify(
                        chat_id, chunk, markup=markup if index == 0 else None
                    )
                    and delivered
                )

        if delivered:
            await asyncio.to_thread(
                self.engine.mark_sent, [finding.state_key for finding in outbox]
            )
        else:
            logger.warning("Часть алертов не доставлена — повторим на следующем цикле")

    async def job_prune(self, _context: ContextTypes.DEFAULT_TYPE) -> None:
        await asyncio.to_thread(
            self.storage.prune_samples, time.time() - SAMPLE_RETENTION_DAYS * 86400
        )

    # ---------------------------------------------------------- рендеринг

    async def _show_logs(
        self,
        callback: CallbackQuery,
        unit: str,
        *,
        lines: int = 40,
        errors: bool = False,
    ) -> None:
        if not self.is_admin(callback.from_user.id):
            await self._edit(callback, "⛔ Только для администраторов.")
            return
        wait = self._heavy_wait(callback.from_user.id)
        if wait > 0:
            await callback.answer(f"Журнал: следующий запрос через {wait:.0f} с")
            return
        await self._edit(
            callback,
            f"📜 Журнал <code>{ui.esc(unit)}</code>…",
            markup=ui.log_view_menu(unit),
        )
        try:
            output = await self.actions.journal(unit, lines, errors=errors)
        except ActionError as exc:
            await self._edit(
                callback,
                f"❌ {ui.esc(str(exc))}",
                markup=ui.log_view_menu(unit),
            )
            return
        where = "только ошибки" if errors else "последние строки"
        await self._edit(
            callback,
            f"📜 <b>{ui.esc(unit)}</b> · {where}\n<pre>{ui.esc(output[-2500:])}</pre>",
            markup=ui.log_view_menu(unit),
        )

    async def _edit(
        self,
        callback: CallbackQuery,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            screen = _message_of(callback)
            self._last_screen[screen.chat_id] = screen.message_id
        except RuntimeError:
            pass
        try:
            await _send_with_retry(
                callback.edit_message_text,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=markup,
            )
            return
        except BadRequest as exc:
            # Повторное нажатие «Обновить» с теми же данными: содержимое не
            # изменилось — это норма, а не ошибка. Иначе мы бы продублировали
            # сообщение ответом ниже.
            if "not modified" in str(exc).lower():
                logger.debug("Сообщение не изменилось, обновление пропущено")
                return
            logger.warning("Не удалось обновить сообщение: %s", exc)
        except TelegramError as exc:
            logger.warning("Не удалось обновить сообщение: %s", exc)
        message = _message_of(callback)
        try:
            await _send_with_retry(
                message.reply_text,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        except TelegramError as exc:
            logger.warning("Не удалось отправить сообщение: %s", exc)


# --------------------------------------------------------------- утилиты


def _parse_apt_status(stdout: str) -> tuple[dict[str, str], str]:
    """Разбирает вывод `xui-ctl apt_status`: шапку STATE/RESULT/EXIT/LOAD и лог."""
    fields: dict[str, str] = {}
    tail: list[str] = []
    header = True
    for line in stdout.splitlines():
        if header:
            key, sep, value = line.partition("=")
            if sep and key in {"STATE", "RESULT", "EXIT", "LOAD"}:
                fields[key] = value.strip()
                continue
            header = False
        tail.append(line)
    return fields, "\n".join(tail).strip()


def _message_of(query: CallbackQuery) -> Message:
    message = query.message
    if isinstance(message, Message):
        return message
    raise RuntimeError("Кнопка не привязана к обычному сообщению")


def _chat_id(query: CallbackQuery) -> int:
    try:
        return _message_of(query).chat_id
    except RuntimeError:
        return 0


async def _clear_markup(query: CallbackQuery) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError as exc:
        logger.debug("Не удалось убрать кнопки: %s", exc)


async def _safe_edit(
    status: Message, text: str, *, markup: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await _send_with_retry(
            status.edit_text,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except TelegramError as exc:
        logger.warning("Не удалось обновить статус: %s", exc)


def _alert_actions(
    findings: list[Finding], settings: Settings
) -> list[InlineKeyboardButton]:
    """Кнопки под алертом: починить сервис, о котором идёт речь.

    Сигнал без действия заставляет ночью искать раздел руками, поэтому для
    находки о сервисе сразу даём рестарт и журнал.
    """
    buttons: list[InlineKeyboardButton] = []
    for finding in findings:
        if finding.rule is not Rule.service or ":" not in finding.state_key:
            continue
        unit = finding.state_key.split(":", 1)[1]
        if unit not in settings.services:
            continue
        buttons.append(
            InlineKeyboardButton(
                f"♻️ Рестарт {unit}", callback_data=f"ask:restart_service:{unit}"
            )
        )
        buttons.append(
            InlineKeyboardButton(f"📜 Журнал {unit}", callback_data=f"logs:{unit}")
        )
        break
    return buttons


def _needs_reboot() -> bool:
    return os.path.exists("/var/run/reboot-required")


#: Дольше этого flood control не ждём: иначе обработчик «зависает» на минуты
#: и Telegram присылает те же апдейты повторно.
MAX_FLOOD_WAIT: Final = 30.0
FLOOD_ATTEMPTS: Final = 3


_T = TypeVar("_T")


async def _send_with_retry(
    func: Callable[..., Awaitable[_T]],
    **kwargs: object,
) -> _T | None:
    """Отправка с уважением к flood control Telegram.

    Без этого `RetryAfter` просто логировался, и сообщение (например, результат
    рестарта сервиса) бесследно терялось. Здесь ждём указанную паузу и пробуем
    ещё раз; если Telegram просит слишком долго — сдаёмся с записью в лог.
    """
    for attempt in range(FLOOD_ATTEMPTS):
        try:
            return await func(**kwargs)
        except RetryAfter as exc:
            raw_delay = exc.retry_after
            delay = (
                raw_delay.total_seconds()
                if isinstance(raw_delay, timedelta)
                else float(raw_delay)
            ) + 0.5
            if attempt == FLOOD_ATTEMPTS - 1 or delay > MAX_FLOOD_WAIT:
                logger.warning("Flood control %.0f c: сообщение не отправлено", delay)
                raise
            logger.warning("Flood control: пауза %.0f c, повтор отправки", delay)
            await asyncio.sleep(delay)
    return None


def _split(text: str, limit: int = 3500) -> list[str]:
    """Режет длинный HTML на части, не разрывая теги.

    Прямое деление по символам рвёт разметку Telegram («can't parse entities»),
    поэтому режем по переводам строк, а уже отдельную чрезмерно длинную строку —
    вынужденно жёстко.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + len(line) + 1 <= limit:
            current = f"{current}\n{line}"
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


def _split_alerts(findings: list[Finding]) -> list[str]:
    order = {
        Severity.critical: 0,
        Severity.warning: 1,
        Severity.unknown: 2,
        Severity.ok: 3,
    }
    ordered = sorted(findings, key=lambda item: order[item.severity])
    header = (
        f"🔔 <b>Требует внимания: {len(findings)}</b> · {time.strftime('%H:%M:%S')}"
    )
    body = "\n\n".join(ui.render_finding(finding) for finding in ordered)
    return _split(header + "\n\n" + body)


def create_app(settings: Settings) -> BotApplication:
    return MonitorBot(settings).app


def main() -> None:
    # Секреты (база, лог) не должны быть доступны другим пользователям хоста,
    # даже если файлы создаются с учёткой по умолчанию.
    os.umask(0o077)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации:\n{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    _setup_logging(settings)
    _warn_on_unusual_api(settings)
    if not settings.panel_verify_tls:
        logger.warning(
            "PANEL_VERIFY_TLS=false: TLS-сертификат панели не проверяется — "
            "бот уязвим к подмене панели в сети. Включайте только для отладки."
        )
    try:
        app = create_app(settings)
    except StorageError as exc:
        logger.error("%s", exc)
        raise SystemExit(2) from exc
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _warn_on_unusual_api(settings: Settings) -> None:
    """Нестандартный Bot API — повод для предупреждения.

    В `TELEGRAM_API_URL` можно подменить адрес, и тогда токен бота уйдёт на
    чужой сервер. Официальный адрес молчит, любой другой — заметен в логе.
    """
    if settings.telegram_api_url.rstrip("/") != "https://api.telegram.org":
        logger.warning(
            "Используется неофициальный Bot API: %s — токен уходит туда",
            settings.telegram_api_url,
        )


#: Шаблон токена бота внутри URL: api.telegram.org/bot123456:AA.../getMe
_BOT_TOKEN_IN_URL = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")


class _ScrubbingFormatter(logging.Formatter):
    """Форматирует запись и вырезает секреты — в том числе из трейсбеков.

    Фильтры на уровне логгера не спасают от чужих библиотек и от текста
    исключений, поэтому чистим уже готовую строку: и токен бота в URL, и точные
    значения секретов из конфигурации.
    """

    def __init__(self, fmt: str, secrets: tuple[str, ...]) -> None:
        super().__init__(fmt)
        #: Короткие значения не вырезаем, чтобы не портить обычный текст.
        self._secrets = tuple(secret for secret in secrets if len(secret) >= 8)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        text = _BOT_TOKEN_IN_URL.sub("bot<redacted>", text)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def _mask_id(value: int | None) -> str:
    """Telegram ID в логах — персональные данные: оставляем хвост для сверки."""
    if value is None:
        return "—"
    text = str(value)
    return f"…{text[-3:]}" if len(text) > 3 else "…"


def _setup_logging(settings: Settings) -> None:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(logging.FileHandler(settings.log_path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # Поверх всех обработчиков — вырезаем секреты из готовых строк (включая
    # трейсбеки): токен бота из URL и точные значения токенов из конфигурации.
    formatter = _ScrubbingFormatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        (settings.telegram_token, settings.panel_api_token),
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    # Сторонние библиотеки на DEBUG печатают то, что обязано остаться в тайне:
    # telegram логирует «Set Bot API URL: https://api.telegram.org/bot<TOKEN>»,
    # httpx — полные URL запросов. Приглушаем их независимо от LOG_LEVEL,
    # иначе включённый для отладки DEBUG сливает токен бота в файл лога.
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


if __name__ == "__main__":
    main()
