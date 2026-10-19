"""Конфигурация бота из переменных окружения и файла .env."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

BASE_DIR = Path(__file__).resolve().parent.parent

#: Значение-заглушка из .env.example: с ним бот стартует, но Telegram
#: отвечает 401, поэтому ловим его на этапе конфигурации.
PLACEHOLDER_TOKEN = "ExampleTokenReplaceMe"

_TRUE = frozenset({"1", "true", "yes", "on", "да", "y"})
_FALSE = frozenset({"0", "false", "no", "off", "нет", "n"})


class ConfigError(RuntimeError):
    """Некорректная конфигурация: бот не должен стартовать вслепую."""


#: Значения последнего прочитанного .env. Хранятся отдельно от os.environ,
#: иначе load_dotenv() навсегда залипает в процессе: первая же загруженная
#: конфигурация начинает перебивать все последующие вызовы load_settings().
_NO_FILE_VALUES: Mapping[str, str | None] = MappingProxyType({})
_FILE_VALUES: ContextVar[Mapping[str, str | None]] = ContextVar(
    "env_file_values", default=_NO_FILE_VALUES
)


def _raw(name: str) -> str:
    """Значение из окружения процесса, иначе из .env.

    Окружение приоритетнее файла: systemd отдаёт `.env` через EnvironmentFile
    именно как окружение, а разработчик может переопределить одно значение,
    не правя весь файл.
    """
    from_env = os.getenv(name)
    if from_env is not None and from_env.strip():
        return from_env.strip()
    from_file = _FILE_VALUES.get().get(name)
    if from_file is not None:
        return from_file.strip()
    return ""


def env_str(name: str, default: str = "") -> str:
    return _raw(name) or default


def env_bool(name: str, default: bool) -> bool:
    raw = _raw(name).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name}={raw!r}: ожидается true/false")


def env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается целое число") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value}: должно быть не меньше {minimum}")
    return value


def env_float(name: str, default: float) -> float:
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается число") from exc
    if not 0.0 < value <= 100.0:
        raise ConfigError(f"{name}={value}: ожидается процент в диапазоне (0, 100]")
    return value


def env_positive_float(name: str, default: float) -> float:
    """Число больше нуля без верхней границы (таймауты, интервалы)."""
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается число") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name}={value}: ожидается конечное число больше нуля")
    return value


def env_int_list(name: str, default: list[int] | None = None) -> list[int]:
    raw = _raw(name).replace(";", ",")
    if not raw:
        return list(default or [])
    try:
        return [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ConfigError(
            f"{name}={raw!r}: ожидается список чисел через запятую"
        ) from exc


def env_str_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = _raw(name).replace(";", ",")
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_token: str
    telegram_api_url: str
    admin_ids: tuple[int, ...]
    alert_chat_id: int | None
    state_path: Path
    log_path: Path
    log_level: str

    panel_url: str
    panel_api_token: str
    panel_verify_tls: bool
    panel_ca_bundle: str
    panel_timeout: float
    panel_base_path: str

    services: tuple[str, ...]
    sudo_wrapper: str
    command_allowlist: tuple[str, ...]

    check_interval: int
    action_cooldown: int
    confirm_ttl: int
    command_timeout: int

    cpu_warn: float
    cpu_crit: float
    mem_warn: float
    mem_crit: float
    disk_warn: float
    disk_crit: float
    alert_cooldown: int
    alert_hysteresis: float
    expiring_soon_days: int
    disk_path: str

    extra: dict[str, str] = field(default_factory=dict, repr=False)

    def is_admin(self, telegram_id: int | None) -> bool:
        return telegram_id is not None and telegram_id in self.admin_ids

    @property
    def panel_web_base(self) -> str:
        """Корень веб-панели: ``PANEL_URL`` + ``PANEL_BASE_PATH`` (webBasePath)."""
        base = self.panel_url.rstrip("/")
        prefix = self.panel_base_path.strip("/")
        return f"{base}/{prefix}" if prefix else base

    @property
    def api_base(self) -> str:
        """Корень API панели с учётом webBasePath: .../panel/api."""
        return f"{self.panel_web_base}/panel/api"


def load_settings(env_file: Path | None = None) -> Settings:
    """Читает .env и валидирует конфиг.

    Приоритет: переменные окружения процесса, для отсутствующих ключей —
    значения из .env. Окружение процесса не изменяется, поэтому повторный
    вызов с другим файлом даёт другой, а не «смешанный» результат.
    """
    from dotenv import dotenv_values

    path = env_file or (BASE_DIR / ".env")
    values = dict(dotenv_values(path)) if path.exists() else {}
    token = _FILE_VALUES.set(values)
    try:
        return _build_settings()
    finally:
        _FILE_VALUES.reset(token)


def _build_settings() -> Settings:
    settings = Settings(
        telegram_token=env_str("TELEGRAM_BOT_TOKEN"),
        telegram_api_url=env_str("TELEGRAM_API_URL", "https://api.telegram.org"),
        admin_ids=tuple(env_int_list("TELEGRAM_ADMIN_IDS")),
        alert_chat_id=env_int("ALERT_CHAT_ID", 0) or None,
        state_path=Path(env_str("STATE_PATH", str(BASE_DIR / "state.db"))),
        log_path=Path(env_str("LOG_PATH", str(BASE_DIR / "logs" / "bot.log"))),
        log_level=env_str("LOG_LEVEL", "INFO").upper(),
        # Нормализуем сразу при чтении: даже если в .env вручную вписали
        # «https://host:5580/» или «/panel/», адрес API склеится без «//».
        panel_url=env_str("PANEL_URL", "https://127.0.0.1:2053").rstrip("/"),
        panel_api_token=env_str("PANEL_API_TOKEN"),
        panel_verify_tls=env_bool("PANEL_VERIFY_TLS", True),
        panel_ca_bundle=env_str("PANEL_CA_BUNDLE", ""),
        panel_timeout=env_positive_float("PANEL_TIMEOUT", 15.0),
        panel_base_path=env_str("PANEL_BASE_PATH", "").strip("/"),
        services=tuple(env_str_list("SERVICES", ["x-ui"])),
        sudo_wrapper=env_str("SUDO_WRAPPER", "/usr/local/bin/xui-ctl"),
        command_allowlist=tuple(env_str_list("COMMAND_ALLOWLIST")),
        check_interval=env_int("CHECK_INTERVAL", 60, minimum=15),
        action_cooldown=env_int("ACTION_COOLDOWN", 30, minimum=0),
        confirm_ttl=env_int("CONFIRM_TTL", 120, minimum=10),
        command_timeout=env_int("COMMAND_TIMEOUT", 600, minimum=30),
        cpu_warn=env_float("CPU_WARN", 80.0),
        cpu_crit=env_float("CPU_CRIT", 95.0),
        mem_warn=env_float("MEM_WARN", 85.0),
        mem_crit=env_float("MEM_CRIT", 95.0),
        disk_warn=env_float("DISK_WARN", 85.0),
        disk_crit=env_float("DISK_CRIT", 93.0),
        alert_cooldown=env_int("ALERT_COOLDOWN", 1800, minimum=60),
        alert_hysteresis=env_float("ALERT_HYSTERESIS", 5.0),
        expiring_soon_days=env_int("EXPIRING_SOON_DAYS", 3, minimum=0),
        disk_path=env_str("DISK_PATH", "/"),
    )

    problems = _validate(settings)
    if problems:
        raise ConfigError("Ошибки конфигурации:\n- " + "\n- ".join(problems))
    return settings


def _service_problems(services: tuple[str, ...]) -> list[str]:
    """Имена юнитов попадают в callback_data кнопок Telegram (лимит 64 байта).

    Слишком длинное или «мусорное» имя сломало бы клавиатуру целиком, поэтому
    такие значения отсекаем ещё на этапе конфигурации.
    """
    problems: list[str] = []
    for unit in services:
        if not re.fullmatch(r"[A-Za-z0-9._@\-]+", unit):
            problems.append(f"SERVICES: недопустимое имя юнита {unit!r}")
        elif len(f"ask:restart_service:{unit}".encode()) > 64:
            problems.append(
                f"SERVICES: имя юнита {unit!r} слишком длинное для кнопки Telegram"
            )
    return problems


def _validate(settings: Settings) -> list[str]:
    problems: list[str] = []
    if not settings.telegram_token:
        problems.append("TELEGRAM_BOT_TOKEN не задан")
    elif PLACEHOLDER_TOKEN in settings.telegram_token:
        problems.append(
            "TELEGRAM_BOT_TOKEN остался значением-заглушкой из .env.example: "
            "возьмите настоящий токен у @BotFather"
        )
    elif ":" not in settings.telegram_token:
        problems.append(
            "TELEGRAM_BOT_TOKEN выглядит не как токен Telegram "
            "(ожидается <bot_id>:<секрет> от @BotFather)"
        )
    if not settings.telegram_api_url.startswith(("http://", "https://")):
        problems.append("TELEGRAM_API_URL должен начинаться с http:// или https://")
    if not settings.admin_ids:
        problems.append("TELEGRAM_ADMIN_IDS пуст: ботом никто не сможет управлять")
    if not settings.panel_url:
        problems.append("PANEL_URL не задан")
    elif not settings.panel_url.startswith(("http://", "https://")):
        problems.append("PANEL_URL должен начинаться с http:// или https://")
    if not settings.panel_api_token:
        problems.append("PANEL_API_TOKEN не задан (создайте токен в 3X-UI)")
    if "://" in settings.panel_base_path:
        problems.append(
            "PANEL_BASE_PATH выглядит как адрес — здесь нужен только путь панели "
            "(например /panel), а адрес указывается в PANEL_URL"
        )
    if settings.panel_ca_bundle and not Path(settings.panel_ca_bundle).is_file():
        problems.append(
            f"PANEL_CA_BUNDLE={settings.panel_ca_bundle}: файл не найден "
            "(нужен PEM-файл с сертификатом панели или её CA)"
        )
    if not settings.services:
        problems.append("SERVICES пуст: нечего проверять через systemd")
    problems.extend(_service_problems(settings.services))
    if settings.cpu_warn >= settings.cpu_crit:
        problems.append("CPU_WARN должен быть меньше CPU_CRIT")
    if settings.mem_warn >= settings.mem_crit:
        problems.append("MEM_WARN должен быть меньше MEM_CRIT")
    if settings.disk_warn >= settings.disk_crit:
        problems.append("DISK_WARN должен быть меньше DISK_CRIT")
    return problems
