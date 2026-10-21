# Политика безопасности / Security Policy

**Русский** · [English](#english)

## Поддерживаемые версии

| Версия | Поддержка |
|---|---|
| последний релиз `vX.Y.Z` | исправления безопасности и регрессий |
| `main` | разработка, может опережать релиз |
| старые минорные релизы | по возможности; критическое — отдельным patch-релизом |

Ставьте релиз тегом (`install.sh --ref vX.Y.Z`) или `main`; обновляйтесь через
`sudo stacksentry-menu` (пункт 24) или повторный установщик. GitHub может отдавать
ветку из кэша пару минут — сверяйте коммит через `/version` или `git rev-parse`.

## Как сообщить об уязвимости

- Не открывайте публичный issue: используйте **GitHub Security Advisories**
  («Security» → «Report a vulnerability») или письмо владельцу репозитория.
- Что приложить: коммит/дата установки, дистрибутив и версия Python, шаги
  воспроизведения, логи. **Токены и ID маскируйте** — `.env` не пересылайте.
- Ответ: подтверждение в течение 72 часов, исправление — как только получится;
  по желанию укажем вас в описании исправления.

## Модель безопасности (что уже защищено)

- Сервис работает непривилегированным пользователем `stacksentry`; код и venv
  принадлежат `root`, `.env` — `640 root:stacksentry`.
- Привилегированные операции — только через `/usr/local/bin/stacksentry-ctl` и узкий
  sudoers-список: 9 фиксированных подкоманд, аргументы проверяются (`safe_unit`,
  `safe_int`, запрет лишних аргументов). Произвольного shell нет: `run_command`
  — шесть литералов из белого списка, запуск через `exec "$@"` без `bash -c`.
- Опасные действия требуют подтверждения кнопкой (токен одноразовый, привязан к
  администратору), ограничены `ACTION_COOLDOWN`, журналируются в `audit_log`.
- Команды и кнопки работают только в личном чате администратора; отказы пишутся
  в журнал (`denied`).
- Секреты не попадают в логи: сторонние логгеры приглушены, а форматтер вырезает
  токен бота из URL и значения токенов из конфигурации, включая трейсбеки.
  Telegram ID в сообщениях маскируются.
- Установщик умеет проверять контрольную сумму архива (`--sha256`) и не следует
  редиректам при обращении к панели.
- Адаптеры панелей не следуют HTTP-редиректам, чтобы токен не ушёл на сторонний
  хост; поддерживается свой CA-bundle и отключение проверки TLS.
- Секреты панелей и Telegram не попадают в `argv`/`ps` при рестарте ядра из меню:
  меню вызывает те же Python-адаптеры, что и бот.

## Зависимости и релизы

- Зависимости обновляются через Dependabot; код анализируется CodeQL.
- Релизы — семантические теги `vX.Y.Z`; заметки и порядок выпуска описаны в
  [docs/RELEASING.md](docs/RELEASING.md).
- Проверяйте `CHANGELOG.md` и `git log` перед обновлением продакшена.
- Не устанавливайте непроверенные форки и сторонние сборки `.env`/скриптов.

## Вне области рассмотрения

- Доступ к серверу под root, физический доступ, компрометация аккаунта
  администратора в Telegram.
- Уязвимости самой панели 3X-UI, Xray и сторонних пакетов — сообщайте их авторам.
- Социальная инженерия и последствия явно подтверждённых действий (`reboot`,
  `apt upgrade`): они доступны администратору по замыслу.

---

## English

**Supported versions:** the latest `vX.Y.Z` release receives security and
regression fixes; `main` tracks development. Install a release with
`install.sh --ref vX.Y.Z` (or `main`) and update via `sudo stacksentry-menu`
(item 24). GitHub may serve a cached branch for a couple of minutes — verify the
commit with `/version` or `git rev-parse`.

**Dependencies and releases:** Dependabot and CodeQL are enabled; releases are
semantic `vX.Y.Z` tags with notes (see [docs/RELEASING.md](docs/RELEASING.md)).


**Reporting:** use GitHub Security Advisories (“Security” → “Report a
vulnerability”) or email the repository owner — do not open a public issue.
Include the commit/date, distribution, Python version, reproduction steps and
logs with **tokens and IDs masked** (never share `.env`). We acknowledge reports
within 72 hours.

**Already protected:** unprivileged service user, code owned by root, `.env` with
`640 root:stacksentry`; privileged operations only through `stacksentry-ctl` with a narrow
sudoers allowlist and argument validation (no arbitrary shell); confirmations,
cooldowns and an audit log for dangerous actions; commands accepted only in the
administrator's private chat with denials logged; secrets masked out of logs and
Telegram IDs masked; optional archive checksum verification (`--sha256`) and no
redirect following when talking to the panel. Panel adapters never follow HTTP
redirects, support a custom CA bundle, and the menu restarts the core through the
same Python adapters as the bot so tokens never appear in `argv`/`ps`.

**Out of scope:** root/physical access, a compromised Telegram admin account,
vulnerabilities in 3X-UI/Xray/third-party packages, and consequences of actions
the administrator explicitly confirmed.
