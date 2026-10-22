"""Привилегированные действия через sudo-обёртку.

Бот никогда не строит shell-команды сам: он вызывает ``stacksentry-ctl`` с одним из
фиксированных подкоманд, а белый список и проверка аргументов живут в скрипте
(см. ``install/stacksentry-ctl``). Даже если бот скомпрометирован, произвольный код
не выполнится — sudo-правило разрешает только эти подкоманды.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from .collectors.panel import PanelError
from .collectors.panels import build_panel
from .command import run_command
from .models import CommandResult
from .settings import Settings

#: Ключи действий, доступных администратору.
ACTION_RESTART_SERVICE = "restart_service"
ACTION_RESTART_XRAY = "restart_xray"
ACTION_JOURNAL = "journal"
ACTION_JOURNAL_MORE = "journal_more"
ACTION_JOURNAL_ERRORS = "journal_errors"
ACTION_UPGRADE = "apt_upgrade"
ACTION_UPGRADE_PHASED = "apt_upgrade_phased"
ACTION_APT_UPDATE = "apt_update"
ACTION_DIST_UPGRADE = "dist_upgrade"
ACTION_RELEASE_UPGRADE = "release_upgrade"
ACTION_REBOOT = "reboot"
ACTION_COMMAND = "run_command"
ACTION_UPDATE_BOT = "update_bot"


#: Какие действия идут в отдельном systemd-юните и требуют наблюдения.
APT_KINDS: dict[str, str] = {
    ACTION_APT_UPDATE: "update",
    ACTION_UPGRADE: "upgrade",
    ACTION_UPGRADE_PHASED: "phased",
    ACTION_RELEASE_UPGRADE: "release",
}


#: Логи, для которых в чате есть кнопки «Ещё 200 строк» и «Только ошибки».
LOG_MORE: dict[str, str] = {
    "os_logs": "os_logs_more",
    "xui_logs": "xui_logs_more",
    "kernel_logs": "kernel_logs_more",
}
LOG_ERRORS: dict[str, str] = {
    "os_logs": "os_logs_errors",
    "xui_logs": "xui_logs_errors",
    "kernel_logs": "kernel_logs_errors",
    "dmesg": "dmesg_errors",
}
LOG_BASE_COMMANDS = frozenset(LOG_MORE) | frozenset(LOG_ERRORS)
LOG_VARIANT_BASE: dict[str, str] = {
    **{variant: base for base, variant in LOG_MORE.items()},
    **{variant: base for base, variant in LOG_ERRORS.items()},
}


def command_key(command: str) -> str:
    """Ключ команды для sudoers: «free -h» → «free_-h».

    Пробелы заменяем на «_», потому что sudo-rs не принимает шаблоны в
    аргументах, а перечислять команды со пробелами в sudoers неудобно.
    """
    return command.replace(" ", "_")


class ActionError(RuntimeError):
    """Действие отклонено: нет прав, идёт кулдаун, нет такого действия."""


@dataclass(frozen=True, slots=True)
class PendingAction:
    """Отложенное действие, ждущее подтверждения по кнопке."""

    action: str
    token: str
    args: tuple[str, ...]
    created_at: float
    description: str
    #: Кто запросил действие: подтвердить его может только этот администратор.
    owner_id: int


class ActionManager:
    """Выполняет действия, ведёт кулдаун и требует двухшагового подтверждения."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pending: dict[str, PendingAction] = {}
        self._last_run: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- механизм

    def request(
        self, action: str, description: str, *args: str, owner_id: int = 0
    ) -> PendingAction:
        """Регистрирует действие и возвращает токен для кнопки подтверждения."""
        token = secrets.token_urlsafe(8)
        pending = PendingAction(
            action=action,
            token=token,
            args=tuple(args),
            created_at=time.monotonic(),
            description=description,
            owner_id=owner_id,
        )
        self._prune()
        self._pending[token] = pending
        return pending

    def consume(
        self,
        token: str,
        user_id: int | None = None,
        expected: str | None = None,
    ) -> PendingAction | None:
        """Отдаёт действие по токену и забывает его — но только после проверок.

        Раньше токен удалялся сразу: неудачная попытка (просроченная или чужая)
        уничтожала подтверждение законного администратора, и он больше не мог
        подтвердить своё действие.
        """
        pending = self._pending.get(token)
        if pending is None:
            return None
        if time.monotonic() - pending.created_at > self.settings.confirm_ttl:
            self._pending.pop(token, None)
            raise ActionError("Время подтверждения истекло, повторите команду")
        # Токен подтверждения подписан на администратора, который его запросил:
        # чужой (в т.ч. пересланный) токен подтвердить нельзя.
        if pending.owner_id and user_id is not None and pending.owner_id != user_id:
            raise ActionError("Подтверждение запросил другой администратор")
        # Сверяем действие ДО удаления токена: иначе несовпадение уничтожало
        # законное подтверждение администратора.
        if expected is not None and pending.action != expected:
            raise ActionError("Действие не совпадает с подтверждением")
        return self._pending.pop(token, None)

    def cancel(self, token: str, user_id: int | None = None) -> bool:
        """Снимает подтверждение. False — снимать было нечего/чужое."""
        pending = self._pending.get(token)
        if pending is None:
            return False
        if pending.owner_id and user_id is not None and pending.owner_id != user_id:
            return False
        self._pending.pop(token, None)
        return True

    def _prune(self) -> None:
        deadline = time.monotonic() - self.settings.confirm_ttl
        for token in [t for t, p in self._pending.items() if p.created_at < deadline]:
            self._pending.pop(token, None)

    def _check_cooldown(self, action: str) -> None:
        last = self._last_run.get(action)
        if last is None:
            return
        remaining = self.settings.action_cooldown - (time.monotonic() - last)
        if remaining > 0:
            raise ActionError(
                f"Действие «{action}» выполнено недавно, подождите {remaining:.0f} с"
            )

    def _mark_run(self, action: str) -> None:
        self._last_run[action] = time.monotonic()

    # ------------------------------------------------------------- действия

    async def restart_service(self, unit: str) -> CommandResult:
        if unit not in self.settings.services:
            raise ActionError(f"Сервис {unit} не входит в список наблюдаемых")
        return await self._privileged(
            [ACTION_RESTART_SERVICE, unit],
            ACTION_RESTART_SERVICE,
            cooldown_key=f"{ACTION_RESTART_SERVICE}:{unit}",
        )

    async def restart_xray(self) -> CommandResult:
        """Рестарт xray через API панели — без sudo и без остановки самой панели.

        Кулдаун здесь так же обязателен, как и для sudo-действий: без него
        подтверждённую кнопку можно было нажимать подряд, заливая панель
        запросами на рестарт.
        """
        async with self._lock:
            self._check_cooldown(ACTION_RESTART_XRAY)
            panel = build_panel(self.settings)
            try:
                await panel.restart_xray()
            except PanelError as exc:
                raise ActionError(f"Панель отклонила рестарт xray: {exc}") from exc
            finally:
                # Отмечаем и неудачную попытку: иначе панель можно заливать
                # запросами без паузы.
                self._mark_run(ACTION_RESTART_XRAY)
                await panel.close()

        return CommandResult(
            action=ACTION_RESTART_XRAY,
            command="POST /panel/api/server/restartXrayService",
            returncode=0,
            stdout="Запрос на рестарт xray отправлен панели",
            stderr="",
            duration=0.0,
        )

    async def journal(self, unit: str, lines: int = 40, *, errors: bool = False) -> str:
        """Журнал сервиса через sudo-обёртку.

        Напрямую ``journalctl`` сервисному пользователю обычно недоступен: он не
        входит ни в `systemd-journal`, ни в `adm`, и видел бы только свои записи.
        Обёртка (`stacksentry-ctl journal`) разрешена в sudoers и проверяет аргументы.
        """
        if unit not in self.settings.services:
            raise ActionError(f"Сервис {unit} не входит в список наблюдаемых")
        # Отдельная подкоманда для «только ошибки»: бот не собирает
        # произвольные аргументы journalctl.
        # Аргумент-число не передаём: в sudoers перечислены точные команды
        # (sudo-rs запрещает шаблоны в аргументах), поэтому «больше строк» —
        # отдельная подкоманда.
        if errors:
            subcommand = ACTION_JOURNAL_ERRORS
        elif lines > 40:
            subcommand = ACTION_JOURNAL_MORE
        else:
            subcommand = ACTION_JOURNAL
        result = await run_command(
            ["sudo", "-n", "--", self.settings.sudo_wrapper, subcommand, unit],
            timeout=20.0,
            action=subcommand,
        )
        return result.summary()

    async def apt_upgrade(self) -> CommandResult:
        """Ставит apt update + full-upgrade в очередь systemd.

        Само обновление идёт в отдельном transient-юните: сервис бота работает
        с ProtectSystem=strict и не может писать в /var/lib/dpkg. Команда
        возвращается сразу после постановки задачи.
        """
        return await self._privileged(
            [ACTION_UPGRADE], ACTION_UPGRADE, timeout=self.settings.command_timeout
        )

    async def apt_upgrade_phased(self) -> CommandResult:
        """Обновление с разрешёнными phased-обновлениями (Ubuntu).

        Обычный `apt upgrade` часть пакетов придерживает, поэтому кнопка
        выглядит нерабочей: «обновлений нет», хотя они есть.
        """
        return await self._privileged(
            [ACTION_UPGRADE_PHASED],
            ACTION_UPGRADE_PHASED,
            timeout=self.settings.command_timeout,
        )

    async def dist_upgrade(self) -> CommandResult:
        """Проверка и запуск do-release-upgrade на Ubuntu.

        Это лишь проверка доступности релиза, поэтому таймаут короткий: если
        сеть/зеркало не отвечает, нечего ждать COMMAND_TIMEOUT (900 с) —
        раньше это выглядело как «зависло».
        """
        return await self._privileged(
            [ACTION_DIST_UPGRADE],
            ACTION_DIST_UPGRADE,
            timeout=min(float(self.settings.command_timeout), 120.0),
        )

    async def apt_update(self) -> CommandResult:
        """Обновляет только индексы пакетов (apt update)."""
        return await self._privileged(
            [ACTION_APT_UPDATE], ACTION_APT_UPDATE, timeout=60.0
        )

    async def release_upgrade(self) -> CommandResult:
        """Ставит do-release-upgrade в очередь отдельным systemd-юнитом."""
        return await self._privileged(
            [ACTION_RELEASE_UPGRADE], ACTION_RELEASE_UPGRADE, timeout=60.0
        )

    async def update_bot(self) -> CommandResult:
        """Ставит обновление StackSentry из main в очередь.

        Установщик запускается отдельным юнитом: сервис бота не может писать в
        свой каталог из-за ProtectSystem=strict, а после обновления он сам
        перезапустит службу.
        """
        return await self._privileged(
            [ACTION_UPDATE_BOT], ACTION_UPDATE_BOT, timeout=60.0
        )

    async def apt_status(self, action: str) -> CommandResult:
        """Состояние фонового apt-юнита + хвост журнала (без кулдауна)."""
        kind = APT_KINDS.get(action)
        if kind is None:
            raise ActionError(f"Нет фонового статуса для {action}")
        return await run_command(
            ["sudo", "-n", "--", self.settings.sudo_wrapper, "apt_status", kind],
            timeout=20.0,
            action="apt_status",
        )

    async def reboot(self) -> CommandResult:
        return await self._privileged([ACTION_REBOOT], ACTION_REBOOT, timeout=30.0)

    async def run_allowed_command(self, command: str) -> CommandResult:
        allowlist = self.settings.command_allowlist
        if not allowlist:
            raise ActionError("COMMAND_ALLOWLIST пуст — произвольные команды запрещены")
        if command not in allowlist:
            # Варианты логов («ещё строк»/«только ошибки») разрешены, только
            # если в allowlist есть их базовая команда. Сами варианты — такие
            # же фиксированные read-only argv, как и базовые.
            base = LOG_VARIANT_BASE.get(command)
            if base is None or base not in allowlist:
                allowed = "\n".join(f"• {item}" for item in allowlist)
                raise ActionError(f"Команда не разрешена. Разрешено только:\n{allowed}")
        # Команда передаётся одним аргументом: sudo-правило разрешает
        # ``stacksentry-ctl run_command *``, а белый список проверяется уже в скрипте.
        # В sudoers ключ (без пробелов) — там запрещены шаблоны в аргументах.
        return await self._privileged(
            [ACTION_COMMAND, command_key(command)],
            ACTION_COMMAND,
            timeout=float(self.settings.command_timeout),
            cooldown_key=f"{ACTION_COMMAND}:{command}",
        )

    # -------------------------------------------------------------- запуск

    async def _privileged(
        self,
        args: list[str],
        action: str,
        *,
        timeout: float = 60.0,
        cooldown_key: str | None = None,
    ) -> CommandResult:
        # Бот работает под непривилегированным пользователем, поэтому обёртка
        # запускается через sudo -n (без пароля). Правило в sudoers разрешает
        # только точные подкоманды stacksentry-ctl. Без sudo привилегированные действия
        # молча уходили бы в «Access denied» / polkit.
        argv = ["sudo", "-n", "--", self.settings.sudo_wrapper, *args]
        # Проверка и отметка внутри блокировки: иначе два одновременных нажатия
        # успевали пройти кулдаун и выполнялись оба.
        key = cooldown_key or action
        async with self._lock:
            self._check_cooldown(key)
            result = await run_command(argv, timeout=timeout, action=action)
            self._mark_run(key)
        return result

    async def execute(self, pending: PendingAction) -> CommandResult:
        """Выполняет подтверждённое действие."""
        args = pending.args
        if pending.action == ACTION_RESTART_SERVICE:
            return await self.restart_service(args[0])
        if pending.action == ACTION_RESTART_XRAY:
            return await self.restart_xray()
        if pending.action == ACTION_UPGRADE:
            return await self.apt_upgrade()
        if pending.action == ACTION_UPGRADE_PHASED:
            return await self.apt_upgrade_phased()
        if pending.action == ACTION_DIST_UPGRADE:
            return await self.dist_upgrade()
        if pending.action == ACTION_REBOOT:
            return await self.reboot()
        if pending.action == ACTION_COMMAND:
            return await self.run_allowed_command(args[0])
        raise ActionError(f"Неизвестное действие: {pending.action}")
