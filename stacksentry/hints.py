"""Подсказки, зависящие от версии панели 3X-UI.

Интерфейс панели переехал: в 3X-UI v3 токены живут в «Настройки панели →
Учётная запись → API-токен» и значение показывается только при создании, а в 2.x
они лежат в «Настройки → API Tokens». Версию бот получает из
`/panel/api/server/status` (`panelVersion`), поэтому подсказка подставляется
подходящая, а не общая «посмотрите где-то в панели».
"""

from __future__ import annotations

import re

#: Версия неизвестна: панель не ответила или старый формат ответа.
UNKNOWN_PANEL_HINT = (
    "панель → «Учётная запись → API-токен» (3X-UI v3) или «Настройки → API Tokens» "
    "(2.x), токен со scope node-sync"
)


def _major(version: str) -> int:
    match = re.search(r"(\d+)", version or "")
    return int(match.group(1)) if match else 0


def panel_token_hint(version: str) -> str:
    """Где взять `PANEL_API_TOKEN` для этой версии панели."""
    major = _major(version)
    if major >= 3:
        return (
            "панель → «Настройки панели → Учётная запись → API-токен», "
            "scope node-sync или admin; значение показывается только при создании — "
            "скопируйте сразу"
        )
    if major == 2:
        return (
            "панель → «Настройки → API Tokens», токен со scope node-sync; "
            "либо sudo x-ui setting -getApiToken true -tokenName monitor-bot "
            "-tokenScope node-sync"
        )
    return UNKNOWN_PANEL_HINT
