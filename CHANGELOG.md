# История изменений / Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Релизы и теги проект не публикует: актуальная версия всегда в ветке `main`,
обновление — `sudo xui-menu` (пункт 21) или повторный запуск установщика.

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
- Меню `xui-menu` оформлено рамкой (как в панели 3X-UI): пункты сгруппированы по
  разделам, сверху видно состояние службы, ширина рамки подстраивается под текст,
  неверный номер даёт подсказку с диапазоном. Добавлены подменю порогов, правка
  служб и уровня логирования и перезапуск xray в панели через API
  (`xui-menu restart-xray`).
- Установка одной командой с GitHub: мастер спрашивает токены четвёртым шагом
  (до установки зависимостей), проверяет токен бота и доступность панели,
  умеет выпустить токен панели автоматически; самопроверка из 8 проверок.
- Поддержка systemd, OpenRC (Alpine, Gentoo) и SysVinit: установщик сам
  определяет менеджер служб (`--init` для явного выбора), для не-systemd систем
  ставится init-скрипт и используется файл лога вместо `journalctl`.
- Логи чтения: журнал сервиса с фильтром «🔎 Только ошибки» и добором строк,
  `/audit` с фильтрами «отказы / действия».

### Исправлено

- Удаление бота: отчёт теперь короткий — одна строка «Бот удалён.» вместо
  списка того, что и где стёрто (`--keep-state` по-прежнему предупреждает об
  оставшихся базе и пользователе). `xui-menu` после удаления завершается, а не
  открывает меню заново — возвращаться уже некуда.
- Раздел «Обслуживание» больше не «зависает»: при таймауте команды в
  `run_command` переменные вывода не были инициализированы и задача падала с
  `UnboundLocalError` без отчёта. Теперь вывод заводится заранее, а при
  таймауте гасится вся группа процессов — внук (`do-release-upgrade` → `apt`)
  не держит пайпы и не переживает прерывание.
- Любая ошибка действия (не только `ActionError`) теперь возвращается в чат и
  пишется в аудит, а не оставляет сообщение «Выполняю…» навсегда.
- Проверка релиз-обновления: `do-release-upgrade` писал в read-only
  `PREFIX/.cache` (ProtectSystem=strict) и падал с `mkdir() failed`. Ему выдают
  записываемый `HOME`/`XDG_CACHE_HOME` в каталоге состояния, а таймаут проверки
  ограничен 120 с вместо `COMMAND_TIMEOUT`.
- Обновление системы: `systemd-run` в daemon-контексте новых systemd
  (Ubuntu 26.04) отбивается polkit («требуется интерактивная аутентификация»),
  и кнопка молча не работала. Установщик ставит постоянные юниты
  `xui-monitor-apt-upgrade.service` и `xui-monitor-apt-phased.service`
  (`User=root`), обёртка запускает их через `systemctl`; при их отсутствии —
  transient-юнит, затем фоновый запуск.
- `webBasePath`: путь `/panel` из адресной строки — не webBasePath (в 3X-UI
  интерфейс всегда на `.../panel`, а «URL панели» задаётся отдельно, обычно `/`).
  Установщик теперь проверяет путь до запроса токена (правильный отвечает 401,
  неверный — 404) и молча исправляет лишний сегмент `.../panel/panel/api`.
- При неверном токене панель отдаёт пустой 401, поэтому установщик и
  самопроверка писали «панель не ответила». Теперь различаются 401/403 (токен и
  его scope), 404 (адрес API) и недоступность, а в самопроверке виден HTTP-код.
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
`sudo xui-menu` (item 21) or by re-running the installer.

### Added

- Monitoring bot: system metrics, systemd services, 3X-UI panel, xray, inbounds,
  subscriptions and traffic; alerts with hysteresis, cooldown and an “I know,
  until morning” mute stored in SQLite.
- Confirmed actions: service/xray restart, `apt` full and phased in a transient
  unit, release-upgrade check, server reboot, allow-listed command.
- In-bot manual (`/help` and the “❓ Help” button) explaining every metric.
- `xui-menu` terminal panel: status, masked settings view, allow-listed editing,
  restart, log, update, removal.
- `xui-menu` menu redrawn in a frame (like the 3X-UI panel): items grouped into
  sections, live service state on top, frame width follows the longest line, a
  wrong number prints the valid range. Added threshold submenus, services/log-level
  editing and an xray restart via the panel API (`xui-menu restart-xray`).
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
- `webBasePath` is now verified before the token is requested (a valid API path
  answers 401, a wrong one 404): the `/panel` browser path — which is not
  `webBasePath` — no longer produces a doubled `.../panel/panel/api` segment.
  A bad token (empty 401) is no longer reported as “panel did not respond”;
  401/403, 404 and unreachable are told apart in the wizard and the self-check.
- `asyncio.TimeoutError` on Python 3.10 (command timeouts did not fire); orphan
  processes on cancellation; `RetryAfter` no longer drops messages; cooldown
  checked under lock; confirmation tokens not consumed by a foreign tap.
- Uninstall now prints a one-line “Bot removed.” report instead of listing what
  and where was deleted (`--keep-state` still warns about the leftover database
  and user); `xui-menu` exits after removal instead of re-opening the menu.
- The Maintenance section no longer “hangs”: on a command timeout `run_command`
  left its output variables unset and the job died with `UnboundLocalError` and
  no report. Output is now pre-initialised, and on timeout the whole process
  group is killed — a grandchild (`do-release-upgrade` → `apt`) can no longer
  hold the pipes or survive. Any action error (not just `ActionError`) is now
  reported in the chat and audited.
- Release-upgrade check: `do-release-upgrade` wrote to the read-only
  `PREFIX/.cache` (ProtectSystem=strict) and failed with `mkdir() failed`; it now
  gets a writable `HOME`/`XDG_CACHE_HOME` under the state dir, and the check is
  capped at 120 s instead of `COMMAND_TIMEOUT`.
- System upgrade: `systemd-run` goes through polkit in a daemon context on new
  systemd (Ubuntu 26.04) and is denied (“interactive authentication required”),
  so the button silently did nothing. The installer now adds persistent
  `xui-monitor-apt-upgrade.service` / `xui-monitor-apt-phased.service`
  (`User=root`) units started via `systemctl`, with transient and detached
  fallbacks.

### Security

- Secrets never reach logs (scrubbing formatter), Telegram IDs masked,
  `xui-menu show/check` never print secrets.
- `PANEL_CA_BUNDLE` certificate pinning, `--sha256` archive verification, no
  redirect following for the panel client.
- Commands accepted only in the administrator's private chat, denials logged,
  narrow sudoers allowlist for `xui-ctl`, `run_command` executes fixed argv
  without `bash -c`.
