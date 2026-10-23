# Политика безопасности / Security Policy

**Русский** · [English](#english)

## Поддерживаемые версии

| Версия | Поддержка |
|---|---|
| `main` | единственная поддерживаемая версия: исправления безопасности и регрессий |
| старые установки | обновляйте из `main`; критическое исправление выходит в `main` |

GitHub Releases и теги не публикуются. Обновляйтесь кнопкой «Проверить
обновления» в боте, через `sudo stacksentry-menu update` (пункт 24) или повторный
установщик. GitHub может отдавать ветку из кэша пару минут, сверяйте commit
через `/version` или `git rev-parse`.

## Как сообщить об уязвимости

- Не открывайте публичный issue: используйте **GitHub Security Advisories**
  («Security» → «Report a vulnerability») или письмо владельцу репозитория.
- Что приложить: коммит/дата установки, дистрибутив и версия Python, шаги
  воспроизведения, логи. **Токены и ID маскируйте**. Не пересылайте `.env`.
- Ответ: подтверждение в течение 72 часов, исправление после проверки;
  по желанию укажем вас в описании исправления.

## Модель безопасности (что уже защищено)

- Сервис работает непривилегированным пользователем `stacksentry`; код и venv
  принадлежат `root`, `.env` имеет права `640 root:stacksentry`.
- Привилегированные операции выполняются только через `/usr/local/bin/stacksentry-ctl` и узкий
  sudoers-список: 9 фиксированных подкоманд, аргументы проверяются (`safe_unit`,
  `safe_int`, запрет лишних аргументов). Произвольного shell нет: `run_command`
  запускает шесть литералов из белого списка через `exec "$@"` без `bash -c`.
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

## Зависимости и обновления

- Зависимости обновляются через Dependabot; код анализируется CodeQL.
- Версии указаны в SemVer в `stacksentry/__init__.py`; теги и GitHub Releases не
  публикуются. Порядок обновления и откат описаны в
  [docs/RELEASING.md](docs/RELEASING.md).
- Проверяйте `CHANGELOG.md` и `git log` перед обновлением продакшена.
- Кнопка обновления запускает установщик из `main`; она требует подтверждения и
  свежего TOTP (если 2FA включена) и сохраняет `.env`/состояние.
- Не устанавливайте непроверенные форки и сторонние сборки `.env`/скриптов.

## Вне области рассмотрения

- Доступ к серверу под root, физический доступ, компрометация аккаунта
  администратора в Telegram.
- Уязвимости самой панели 3X-UI, Xray и сторонних пакетов: сообщайте их авторам.
- Социальная инженерия и последствия явно подтверждённых действий (`reboot`,
  `apt upgrade`): они доступны администратору по замыслу.

---

## English

**Supported versions:** `main` is the only supported version and receives
security and regression fixes. GitHub Releases and tags are not published; update
via the in-bot check/update buttons, `sudo stacksentry-menu update` (item 24), or
the installer. GitHub may serve a cached branch for a couple of minutes, so verify
the commit with `/version` or `git rev-parse`.

**Dependencies and updates:** Dependabot and CodeQL are enabled; versions are
SemVer in `stacksentry/__init__.py`. The self-update button runs the installer
from `main`, requires confirmation and a fresh TOTP (when 2FA is enabled), and
keeps `.env`/state. See [docs/RELEASING.md](docs/RELEASING.md).


**Reporting:** use GitHub Security Advisories (“Security” → “Report a
vulnerability”) or email the repository owner. Do not open a public issue.
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
