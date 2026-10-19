# История изменений / Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Релизы и теги проект не публикует: актуальная версия всегда в ветке `main`,
обновление — `sudo xui-menu` (пункт 11) или повторный запуск установщика.

## [Unreleased]

### Возможности

- Telegram-бот мониторинга: система, systemd-службы, панель 3X-UI, xray,
  inbound'ы, подписки и трафик; алерты с гистерезисом, кулдауном и мьютом
  «знаю, до утра» (хранится в SQLite).
- Безопасные действия по подтверждению: рестарт сервиса и xray, `apt`
  full/phased в transient-юните, проверка релиз-обновления, перезагрузка
  сервера, команда из allowlist.
- Инструкция внутри бота: `/help` и кнопка «❓ Помощь» с расшифровкой метрик.
- Консольная панель `xui-menu`: состояние, показ и правка настроек (безопасный
  белый список ключей), рестарт, журнал, обновление, удаление.
- Установка одной командой с GitHub: мастер спрашивает токены четвёртым шагом
  (до установки зависимостей), проверяет токен бота и доступность панели,
  умеет выпустить токен панели автоматически; самопроверка из 8 проверок.
- Поддержка systemd, OpenRC (Alpine, Gentoo) и SysVinit: установщик сам
  определяет менеджер служб (`--init` для явного выбора), для не-systemd систем
  ставится init-скрипт и используется файл лога вместо `journalctl`.
- Логи чтения: журнал сервиса с фильтром «🔎 Только ошибки» и добором строк,
  `/audit` с фильтрами «отказы / действия».

### Исправлено

- Юнит больше не использует `ProcSubset=pid`: он оставлял в `/proc` только
  процессы и прятал `/proc/stat`, `/proc/meminfo`, `/proc/net` — из-за этого
  psutil не мог собрать метрики и бот писал «Системные метрики недоступны».
- Проблемы железа и проблемы подписок разделены: раньше предупреждения о метриках
  попадали в находку «Подписки: нужна проверка», а одинаковые строки дублировались.

- Установка на чистой машине: каталог `/opt/xui-monitor-bot` создаётся до записи
  `.env` (иначе установщик падал на шаге 4).
- Недозаполненный `.env` после прерванного запуска больше не требует правки
  руками — мастер дозаполняет недостающие значения.
- Адрес панели: лишние пробелы и слеши убираются (`https://host:5580/`), путь из
  адреса переносится в `webBasePath`, двойной слеш невозможен ни в проверке, ни
  в адресе API.
- `asyncio.TimeoutError` на Python 3.10: таймауты команд работали только на
  3.11+; теперь ловятся оба имени, процесс действительно прерывается.
- Процессы не остаются сиротами при отмене задачи или выключении бота;
  `RetryAfter` (flood control) не теряет сообщение; кулдаун проверяется под
  блокировкой, подтверждение не расходуется чужим нажатием.
- Разметка: `render_overview` не падает на NULL-метриках, длинные сообщения
  режутся по строкам, HTML-теги не рвутся.
- Совместимость с `sudo-rs` (Ubuntu 26.04+, `visudo-rs`): правила теперь
  генерируются точным списком команд — без шаблонов в аргументах, без алиасов и
  без `Defaults`, которые sudo-rs не принимает. `journal`/`journal_errors`/
  `journal_more` — отдельные подкоманды, `run_command` получает ключ команды.
- Установщик больше не скрывает причину отказа `visudo`: строка ошибки попадает в
  вывод, а если сборка sudo не принимает расширенные `Defaults`, ставится
  упрощённый вариант правил — бот устанавливается в любом случае.

### Безопасность

- Секреты не попадают в логи, Telegram ID маскируются, `xui-menu show/check` не
  печатают значения токенов.
- `PANEL_CA_BUNDLE` для пиннинга сертификата панели; установщик умеет сверять
  архив по `--sha256`; клиент панели не следует редиректам.
- Команды и кнопки принимаются только в личном чате администратора; отказы
  пишутся в журнал; обёртка `xui-ctl` работает только по узкому sudoers-списку.
- `run_command` запускает фиксированныеargv без `bash -c`; аргументы подкоманд
  валидируются (`safe_unit`, `safe_int`, запрет лишних аргументов).

---

## English

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). No releases or
tags are published — the current version is always the `main` branch; update via
`sudo xui-menu` (item 11) or by re-running the installer.

### Added

- Monitoring bot: system metrics, systemd services, 3X-UI panel, xray, inbounds,
  subscriptions and traffic; alerts with hysteresis, cooldown and an “I know,
  until morning” mute stored in SQLite.
- Confirmed actions: service/xray restart, `apt` full and phased in a transient
  unit, release-upgrade check, server reboot, allow-listed command.
- In-bot manual (`/help` and the “❓ Help” button) explaining every metric.
- `xui-menu` terminal panel: status, masked settings view, allow-listed editing,
  restart, log, update, removal.
- One-command GitHub install: the wizard asks for tokens as step 4 (before
  installing dependencies), verifies the bot token and panel reachability, can
  issue the panel token itself; an 8-check self-test at the end.
- systemd, OpenRC (Alpine, Gentoo) and SysVinit support with automatic service
  manager detection (`--init` to force) and an init script plus log file fallback
  for non-systemd hosts.

### Fixed

- Clean-machine install: `/opt/xui-monitor-bot` is created before `.env` is
  written; an incomplete `.env` after an aborted run is completed by the wizard.
- Panel address handling: spaces/trailing slashes stripped, a path pasted with the
  address is moved into `webBasePath`, double slashes are impossible.
- `asyncio.TimeoutError` on Python 3.10 (command timeouts did not fire); orphan
  processes on cancellation; `RetryAfter` no longer drops messages; cooldown
  checked under lock; confirmation tokens not consumed by a foreign tap.

### Security

- Secrets never reach logs (scrubbing formatter), Telegram IDs masked,
  `xui-menu show/check` never print secrets.
- `PANEL_CA_BUNDLE` certificate pinning, `--sha256` archive verification, no
  redirect following for the panel client.
- Commands accepted only in the administrator's private chat, denials logged,
  narrow sudoers allowlist for `xui-ctl`, `run_command` executes fixed argv
  without `bash -c`.
