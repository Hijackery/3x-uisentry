# xui-monitor-bot

Telegram-бот для мониторинга VDS с установленной панелью 3X-UI: система,
systemd-сервисы, панель и xray, inbound'ы, подписки и трафик. При сбоях
присылает алерты, а по подтверждению в чате умеет перезапускать сервисы,
перезапускать xray через API панели и обновлять систему.

## Требования

- Linux с **systemd**: Debian/Ubuntu (и производные), RHEL/CentOS/Rocky/Alma,
  Fedora, openSUSE, Arch/Manjaro. Установщик сам определяет пакетный менеджер
  (`apt`/`dnf`/`yum`/`zypper`/`pacman`).
- **Python 3.10+** (Ubuntu 22.04+, Debian 12, Fedora; на RHEL 9 —
  `dnf install python3.11`). Установщик ищет подходящий интерпретатор
  (`python3.13` … `python3.10`, `python3`).
- `curl`, `tar`, `sudo`, `systemd`. Для загрузки исходников — `curl` или `git`.
- Панель 3X-UI с доступным HTTPS/TCP-портом (по умолчанию `2053`).

## 1. Токен Telegram-бота — от @BotFather

Токен бота **не** выдаётся панелью и не связан с ней. Он создаётся в Telegram:

1. Откройте [@BotFather](https://t.me/BotFather).
2. Отправьте `/newbot`, задайте имя и username.
3. Скопируйте выданный токен вида `123456789:AA...` — это и есть
   `TELEGRAM_BOT_TOKEN`.

## 2. Ваш Telegram ID — администратор

Узнать numeric ID можно у [@userinfobot](https://t.me/userinfobot). Один или
несколько ID перечисляются через запятую в `TELEGRAM_ADMIN_IDS`. Все команды
и кнопки бота доступны только им.

## 3. API-токен панели 3X-UI

Раздел панели **Настройки → Настройки API**. Нужен токен со scope `node-sync`:
только он отдаёт список inbound'ов и данные подписок.

Через консоль на сервере:

```bash
sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
```

Внимание: команда создаёт новый токен. Значение вписывается в
`PANEL_API_TOKEN` — это другой секрет, не путать с токеном `@BotFather`.

## Установка

```bash
# одна команда: скрипт сам скачает репозиторий, спросит токены и проверит себя
curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh \
  | sudo bash

# тот же скрипт в стиле 3X-UI (стандартный ввод остаётся терминалом)
sudo bash <(curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)

# интерактивное меню: установить/обновить или удалить
sudo bash <(curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh) --menu
```

Вопросы установщика читаются напрямую с терминала (`/dev/tty`), поэтому диалог
работает и при установке через пайп `curl | bash`.

Unattended-режим (без вопросов) — передайте значения через окружение:

```bash
sudo env \
  TELEGRAM_BOT_TOKEN='123456789:AA...' \
  TELEGRAM_ADMIN_IDS='11111111' \
  PANEL_URL='https://panel.example.com:2053' \
  PANEL_API_TOKEN='...' \
  bash install.sh --yes
```

Если исходники лежат не в GitHub, а на своём сервере или в своём Git:

```bash
sudo bash install.sh --tarball https://host/xui-monitor-bot.tar.gz
sudo bash install.sh --repo мой-логин/xui-monitor-bot --ref main
sudo env GIT_URL=https://git.example.com/me/xui-monitor-bot.git bash install.sh --yes
```

Проверить, откуда именно будет взята версия (не трогая систему):

```bash
bash install.sh --print-source --repo мой-логин/xui-monitor-bot
```

Полезные опции: `--prefix DIR`, `--user NAME`, `--tarball URL`, `--repo
owner/name`, `--ref BRANCH`, `--print-source`, `--menu`, `--uninstall`,
`--purge`, `--no-start`, `--force-config`, `--help`.

Из уже скачанного репозитория:

```bash
sudo bash install.sh
```

Установщик спрашивает `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `PANEL_URL`
и `PANEL_API_TOKEN` и записывает их в `/opt/xui-monitor-bot/.env`. IP-адреса
сервера определяются автоматически и показываются в подсказке; токен бота
проверяется у Telegram сразу, не дожидаясь конца установки. Существующий
`.env` при повторной установке сохраняется.

## Что делает самопроверка

`install.sh` не ограничивается «зелёной галочкой» — он проверяет:

- конфигурация читается ботом без ошибок;
- токен Telegram рабочий: запрос `getMe` к Bot API;
- `PANEL_URL` отвечает и `api_base` вычислен верно;
- API панели отвечает: `/server/status` и `/inbounds/list` (нужен scope
  `node-sync`);
- бот пишет в SQLite;
- sudo-обёртка отвечает на `xui-ctl is_active x-ui`;
- sudoers проходит `visudo`;
- `systemctl is-enabled` — автозапуск включён;
- `systemctl is-active` — сервис жив (или ожидаемо не запущен при `--no-start`).

Итог печатается счётчиком «N из N проверок пройдены».

## Автозапуск

Юнит `xui-monitor-bot.service` включён через `systemctl enable`, то есть бот
поднимается сам после перезагрузки.

```bash
systemctl status  xui-monitor-bot
journalctl -u xui-monitor-bot -f
systemctl restart xui-monitor-bot
```

Состояние (SQLite) и лог лежат в `/var/lib/xui-monitor/` — вне каталога с
кодом. Код в `/opt/xui-monitor-bot` принадлежит `root`: скомпрометированный
бот не сможет переписать сам себя.

## Команды бота

| Команда | Что делает |
|---|---|
| `/status` | сводка: сервер, сервисы, панель, трафик |
| `/services` | состояние systemd-юнитов |
| `/panel` | доступность и версии панели и xray |
| `/subs` | подписки: сроки, трафик, отключённые |
| `/history` | почасовые средние CPU/RAM/диск |
| `/logs` | журнал выбранного сервиса |
| `/audit` | журнал действий бота |
| `/maint` | обслуживание: рестарт сервиса/xray, apt, релиз-обновление, reboot |
| `/help` | список команд |

## Настройки (`.env`)

| Ключ | Смысл |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от `@BotFather` |
| `TELEGRAM_ADMIN_IDS` | ID администраторов через запятую |
| `ALERT_CHAT_ID` | отдельный чат для алертов (по умолчанию — админы) |
| `TELEGRAM_API_URL` | базовый URL Bot API (свой сервер/прокси, по умолчанию `https://api.telegram.org`) |
| `PANEL_URL` | адрес панели, например `https://panel.example.com:2053` |
| `PANEL_BASE_PATH` | `webBasePath` из панели, если он задан |
| `PANEL_API_TOKEN` | токен панели со scope `node-sync` |
| `PANEL_VERIFY_TLS` | проверка TLS-сертификата (по умолчанию `true`; `false` — только для отладки) |
| `PANEL_TIMEOUT` | таймаут HTTP к панели, секунды |
| `SERVICES` | systemd-юниты для проверки: `x-ui,fail2ban` |
| `DISK_PATH` | каталог для проверки места |
| `CPU_WARN` | порог предупреждения по CPU, % |
| `CPU_CRIT` | порог критического CPU, % |
| `MEM_WARN` | порог предупреждения по RAM, % |
| `MEM_CRIT` | порог критического RAM, % |
| `DISK_WARN` | порог предупреждения по диску, % |
| `DISK_CRIT` | порог критического диска, % |
| `ALERT_HYSTERESIS` | на сколько % не срабатывает сброс порога |
| `ALERT_COOLDOWN` | минимальный интервал между повторами алерта, сек |
| `EXPIRING_SOON_DAYS` | за сколько дней подписка считается «скоро истечёт» |
| `CHECK_INTERVAL` | период фоновой проверки, сек (минимум 15) |
| `ACTION_COOLDOWN` | минимум между одинаковыми действиями, сек |
| `CONFIRM_TTL` | сколько живёт вопрос «точно?» |
| `COMMAND_TIMEOUT` | таймаут разрешённых команд, сек |
| `COMMAND_ALLOWLIST` | белый список команд диагностики |
| `STATE_PATH` | путь к базе SQLite |
| `LOG_PATH` | путь к файлу лога |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `SUDO_WRAPPER` | путь к привилегированной обёртке |

Приоритет: переменные окружения процесса важнее `.env`; systemd отдаёт
`.env` через `EnvironmentFile`, поэтому файл и окружение согласованы.

## Модель безопасности

- Бот работает непривилегированным пользователем `xui-monitor`.
- Привилегированные операции — только через `/usr/local/bin/xui-ctl` и
  sudoers с фиксированным списком подкоманд; произвольные строки в shell не
  попадают.
- Опасные действия требуют подтверждения кнопкой, записываются в
  `audit_log`, повторяются не чаще `ACTION_COOLDOWN`. Токен подтверждения
  привязан к администратору, который его запросил.
- `apt` запускается отдельным transient-юнитом systemd: сервис бота работает
  с `ProtectSystem=strict` и не может писать в `/var/lib/dpkg`.
- `do-release-upgrade` требует терминала, поэтому бот только проверяет
  доступность релиз-обновления и печатает инструкцию для SSH.

Дополнительно (защита на случай компрометации бота):

- **Секреты не принадлежат боту.** `.env` — `root:xui-monitor`, права `640`:
  сервис читает токены, но не может переписать собственный конфиг. Код и venv
  принадлежат `root`, `go-w`.
- **Файлы состояния закрыты.** `UMask=0077` в юните и `umask 077` в
  установщике: база SQLite и лог создаются с правами `0600`.
- **Песочница systemd.** `ProtectSystem=strict`, `ProtectHome=true`,
  `PrivateTmp`, `PrivateDevices`, `ProtectProc=invisible`, `RestrictNamespaces`,
  `RestrictAddressFamilies`, `SystemCallArchitectures=native`, лимиты
  `MemoryMax`/`TasksMax`. `NoNewPrivileges` выключен сознательно — иначе sudo
  не сможет получить права.
- **Клиент панели не ходит по редиректам.** Иначе панель или прокси перед ней
  могла бы увести запрос на сторонний хост вместе с заголовком
  `Authorization: Bearer <токен панели>`.
- **Токен бота не попадает в логи.** Логгеры `telegram`, `httpx`, `httpcore`,
  `apscheduler` приглушены до `WARNING` независимо от `LOG_LEVEL` (на `DEBUG`
  они печатают URL вида `.../bot<TOKEN>/getMe`).
- **HTML-инъекции исключены.** Значения из панели (remarks, e-mail) и вывод
  команд проходят `html.escape` перед вставкой в HTML-разметку Telegram.
- **Двухсторонняя проверка команд.** Список `COMMAND_ALLOWLIST` сверяется и в
  боте, и в обёртке; для `run_command` нет `shell=True`.
- `PANEL_VERIFY_TLS=true` по умолчанию; выключение проверки TLS пишется в лог
  предупреждением.

## Разработка

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy .
.venv/bin/pytest
```

## Удаление

Тем же скриптом:

```bash
sudo bash install.sh --uninstall   # служба, sudoers, обёртка, код
sudo bash install.sh --purge       # ещё и состояние + системный пользователь
sudo bash install.sh --menu        # интерактивное меню: поставить/удалить
```

Вручную (если скрипта под рукой нет):

```bash
sudo systemctl disable --now xui-monitor-bot
sudo rm -f /etc/systemd/system/xui-monitor-bot.service
sudo rm -f /etc/sudoers.d/xui-monitor-bot
sudo rm -f /usr/local/bin/xui-ctl
sudo rm -rf /opt/xui-monitor-bot /var/lib/xui-monitor
sudo userdel xui-monitor && sudo systemctl daemon-reload
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
