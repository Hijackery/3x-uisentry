# xui-monitor-bot

[![CI](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml)

Telegram-бот для сервера с панелью 3X-UI: следит за железом, systemd-сервисами
и панелью, присылает алерты, показывает подписки и трафик. Опасные действия
(рестарт сервиса, xray, обновление системы, перезагрузка) выполняются только
с подтверждением кнопкой и только администратором.

## Что нужно

- Linux с systemd (Debian/Ubuntu, RHEL/CentOS/Rocky/Alma, Fedora, openSUSE, Arch).
- Python 3.10+ — установщик подберёт подходящий интерпретатор сам.
- Панель 3X-UI и её API-токен.
- Бот в Telegram (токен от [@BotFather](https://t.me/BotFather)) и ваш numeric ID
  (узнать: [@userinfobot](https://t.me/userinfobot)).

## Установка

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)
```

или через пайп:

```bash
curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh | sudo bash
```

Скрипт сам скачает код, поставит зависимости, спросит токены, создаст системного
пользователя `xui-monitor`, включит автозапуск и в конце проверит, что всё
работает. Вопросы читаются с терминала, поэтому диалог идёт и при установке
через пайп.

GitHub кэширует ветку `main` на несколько минут: если репозиторий только что
обновили, установщик может взять предыдущую версию. Для воспроизводимости
укажите тег — `.../raw/v1.0.0/install.sh`.

API-токен панели нужен со scope `node-sync` — иначе бот не увидит inbound'ы и
подписки:

```bash
sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
```

Без вопросов (значения из окружения):

```bash
sudo env \
  TELEGRAM_BOT_TOKEN='123456789:AA...' \
  TELEGRAM_ADMIN_IDS='11111111' \
  PANEL_URL='https://panel.example.com:2053' \
  PANEL_API_TOKEN='...' \
  bash install.sh --yes
```

Полезные опции: `--repo owner/name`, `--ref ветка`, `--prefix DIR`,
`--user NAME`, `--no-start`, `--force-config`, `--print-source` (показать, откуда
будет взята версия), `--menu`. Полный список — `--help`.

## Обновление и удаление

```bash
# обновить (конфиг и состояние сохранятся)
bash <(curl -Ls .../install.sh)

# удалить бота (служба, sudoers, обёртка, код)
sudo bash install.sh --uninstall

# то же, но вместе с состоянием и системным пользователем
sudo bash install.sh --purge

# интерактивное меню
sudo bash install.sh --menu
```

## Команды

| Команда | Что показывает |
|---|---|
| `/status` | общая сводка: сервер, сервисы, панель, трафик |
| `/services` | состояние systemd-юнитов, рестарт, журнал |
| `/panel` | доступность панели и xray, версии, обновления |
| `/subs` | подписки: сроки, трафик, отключённые |
| `/history` | почасовые средние CPU/RAM/диска за сутки |
| `/logs` | журнал выбранного сервиса |
| `/audit` | журнал действий бота |
| `/maint` | обслуживание: apt, релиз-обновление, произвольная команда |
| `/help` | список команд |

Кнопки под сообщениями дублируют команды: у каждого экрана есть «🏠 В меню», так
что в тупике оказаться нельзя. Гостям бота кнопки не показываются.

## Настройки

Все параметры — в `/opt/xui-monitor-bot/.env`, пример с комментариями —
[`.env.example`](.env.example).

| Ключ | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от @BotFather |
| `TELEGRAM_ADMIN_IDS` | ID администраторов через запятую |
| `ALERT_CHAT_ID` | чат для алертов (пусто — админам в личку) |
| `TELEGRAM_API_URL` | свой Bot API или прокси (по умолчанию официальный) |
| `PANEL_URL` | адрес панели, например `https://panel.example.com:2053` |
| `PANEL_BASE_PATH` | `webBasePath` панели, если задан |
| `PANEL_API_TOKEN` | токен панели (scope `node-sync`) |
| `PANEL_VERIFY_TLS` | проверять TLS-сертификат панели (по умолчанию `true`) |
| `PANEL_TIMEOUT` | таймаут запросов к панели, сек |
| `SERVICES` | юниты под наблюдением, через запятую (`x-ui,fail2ban`) |
| `DISK_PATH` | что проверять по месту |
| `CPU_WARN` | порог предупреждения по CPU, % |
| `CPU_CRIT` | порог критического CPU, % |
| `MEM_WARN` | порог предупреждения по RAM, % |
| `MEM_CRIT` | порог критического RAM, % |
| `DISK_WARN` | порог предупреждения по диску, % |
| `DISK_CRIT` | порог критического диска, % |
| `ALERT_HYSTERESIS` | на сколько % метрика должна отойти назад, чтобы сбросить алерт |
| `ALERT_COOLDOWN` | как часто повторять активный алерт, сек |
| `EXPIRING_SOON_DAYS` | за сколько дней подписка считается истекающей |
| `CHECK_INTERVAL` | период фоновой проверки, сек (минимум 15) |
| `ACTION_COOLDOWN` | пауза между одинаковыми действиями, сек |
| `CONFIRM_TTL` | сколько живёт кнопка подтверждения, сек |
| `COMMAND_TIMEOUT` | таймаут разрешённых команд, сек |
| `COMMAND_ALLOWLIST` | список команд для диагностики (должен совпадать с `install/xui-ctl`) |
| `STATE_PATH` | база SQLite |
| `LOG_PATH` | файл лога |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SUDO_WRAPPER` | путь к `xui-ctl` |

Переменные окружения процесса имеют приоритет над файлом `.env`.

## Безопасность

- Бот работает непривилегированным пользователем `xui-monitor`; код и venv
  принадлежат root, `.env` доступен сервису только на чтение.
- Все привилегированные операции идут через `/usr/local/bin/xui-ctl` и узкий
  sudoers-список; произвольного shell нет. `apt` выполняется отдельным
  transient-юнитом systemd, потому что сервис живёт с `ProtectSystem=strict`.
- Опасные действия требуют подтверждения, токен привязан к запросившему
  администратору, всё пишется в `audit_log`.
- Клиент панели не следует редиректам (иначе токен мог уйти на чужой хост),
  значения из панели экранируются перед отправкой, токен бота не попадает в
  логи даже на `DEBUG`.
- Повторы защищены: одно подтверждение срабатывает один раз, одинаковые действия
  ограничены `ACTION_COOLDOWN` (включая рестарт xray), а сообщения, которые
  Telegram отклонил из-за flood control, отправляются повторно.

## Если что-то не работает

```bash
systemctl status xui-monitor-bot
journalctl -u xui-monitor-bot -n 50 --no-pager
```

- «Токен отклонён» — проверьте `TELEGRAM_BOT_TOKEN` и `PANEL_API_TOKEN`.
- `inbounds/list недоступен` — у токена панели нет scope `node-sync`.
- Логи сервиса не видны — проверьте, что в sudoers есть правило `journal`.

## Разработка

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m mypy .
.venv/bin/python -m pytest
```

Те же проверки (плюс shellcheck и `visudo -cf`) гоняются в GitHub Actions на
каждый push — см. `.github/workflows/ci.yml`.

## Лицензия

MIT — см. [LICENSE](LICENSE).
