"""Привилегированные действия через sudo-обёртку.

Бот никогда не строит shell-команды сам: он вызывает ``xui-ctl`` с одним из
фиксированных подкоманд, а белый список и проверка аргументов живут в скрипте
(см. ``install/xui-ctl``). Даже если бот скомпрометирован, произвольный код
не выполнится — sudo-правило разрешает только эти подкоманды.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from .collectors.panel import PanelClient, PanelError
from .command import run_command
from .models import CommandResult
from .settings import Settings

#: Ключи действий, доступных администратору.
ACTION_RESTART_SERVICE = "restart_service"
ACTION_RESTART_XRAY = "restart_xray"
ACTION_JOURNAL = "journal"
ACTION_UPGRADE = "apt_upgrade"
ACTION_DIST_UPGRADE = "dist_upgrade"
ACTION_REBOOT = "reboot"
ACTION_COMMAND = "run_command"


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
            created_at=time.time(),
            description=description,
            owner_id=owner_id,
        )
        self._prune()
        self._pending[token] = pending
        return pending

    def consume(self, token: str, user_id: int | None = None) -> PendingAction | None:
        """Отдаёт действие по токену и сразу забывает его."""
        pending = self._pending.pop(token, None)
        if pending is None:
            return None
        if time.time() - pending.created_at > self.settings.confirm_ttl:
            raise ActionError("Время подтверждения истекло, повторите команду")
        # Токен подтверждения подписан на администратора, который его запросил:
        # чужой (в т.ч. пересланный) токен подтвердить нельзя.
        if pending.owner_id and user_id is not None and pending.owner_id != user_id:
            raise ActionError("Подтверждение запросил другой администратор")
        return pending

    def cancel(self, token: str) -> None:
        self._pending.pop(token, None)

    def _prune(self) -> None:
        deadline = time.time() - self.settings.confirm_ttl
        for token in [t for t, p in self._pending.items() if p.created_at < deadline]:
            self._pending.pop(token, None)

    def _check_cooldown(self, action: str) -> None:
        last = self._last_run.get(action)
        if last is None:
            return
        remaining = self.settings.action_cooldown - (time.time() - last)
        if remaining > 0:
            raise ActionError(
                f"Действие «{action}» выполнено недавно, подождите {remaining:.0f} с"
            )

    def _mark_run(self, action: str) -> None:
        self._last_run[action] = time.time()

    # ------------------------------------------------------------- действия

    async def restart_service(self, unit: str) -> CommandResult:
        if unit not in self.settings.services:
            raise ActionError(f"Сервис {unit} не входит в список наблюдаемых")
        return await self._privileged(
            [ACTION_RESTART_SERVICE, unit], ACTION_RESTART_SERVICE
        )

    async def restart_xray(self) -> CommandResult:
        """Рестарт xray через API панели — без sudo и без остановки самой панели."""
        panel = PanelClient(self.settings)
        try:
            await panel.restart_xray()
        except PanelError as exc:
            raise ActionError(f"Панель отклонила рестарт xray: {exc}") from exc
        finally:
            await panel.close()

        return CommandResult(
            action=ACTION_RESTART_XRAY,
            command="POST /panel/api/server/restartXrayService",
            returncode=0,
            stdout="Запрос на рестарт xray отправлен панели",
            stderr="",
            duration=0.0,
        )

    async def journal(self, unit: str, lines: int = 40) -> str:
        """Журнал сервиса через sudo-обёртку.

        Напрямую ``journalctl`` сервисному пользователю обычно недоступен: он не
        входит ни в `systemd-journal`, ни в `adm`, и видел бы только свои записи.
        Обёртка (`xui-ctl journal`) разрешена в sudoers и проверяет аргументы.
        """
        if unit not in self.settings.services:
            raise ActionError(f"Сервис {unit} не входит в список наблюдаемых")
        result = await run_command(
            [
                self.settings.sudo_wrapper,
                ACTION_JOURNAL,
                unit,
                str(max(1, min(lines, 500))),
            ],
            timeout=20.0,
            action=ACTION_JOURNAL,
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

    async def dist_upgrade(self) -> CommandResult:
        """Проверка и запуск do-release-upgrade на Ubuntu."""
        return await self._privileged(
            [ACTION_DIST_UPGRADE],
            ACTION_DIST_UPGRADE,
            timeout=self.settings.command_timeout,
        )

    async def reboot(self) -> CommandResult:
        return await self._privileged([ACTION_REBOOT], ACTION_REBOOT, timeout=30.0)

    async def run_allowed_command(self, command: str) -> CommandResult:
        allowlist = self.settings.command_allowlist
        if not allowlist:
            raise ActionError("COMMAND_ALLOWLIST пуст — произвольные команды запрещены")
        if command not in allowlist:
            allowed = "\n".join(f"• {item}" for item in allowlist)
            raise ActionError(f"Команда не разрешена. Разрешено только:\n{allowed}")
        # Команда передаётся одним аргументом: sudo-правило разрешает
        # ``xui-ctl run_command *``, а белый список проверяется уже в скрипте.
        return await self._privileged(
            [ACTION_COMMAND, command], ACTION_COMMAND, timeout=300.0
        )

    # -------------------------------------------------------------- запуск

    async def _privileged(
        self,
        args: list[str],
        action: str,
        *,
        timeout: float = 60.0,
    ) -> CommandResult:
        self._check_cooldown(action)
        argv = [self.settings.sudo_wrapper, *args]
        async with self._lock:
            result = await run_command(argv, timeout=timeout, action=action)
        self._mark_run(action)
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
        if pending.action == ACTION_DIST_UPGRADE:
            return await self.dist_upgrade()
        if pending.action == ACTION_REBOOT:
            return await self.reboot()
        if pending.action == ACTION_COMMAND:
            return await self.run_allowed_command(args[0])
        raise ActionError(f"Неизвестное действие: {pending.action}")
