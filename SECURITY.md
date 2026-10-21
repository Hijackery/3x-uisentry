# Политика безопасности / Security Policy

**Русский** · [English](#english)

## Поддерживаемые версии

Релизы (теги) не публикуются: актуальная и единственная поддерживаемая версия —
ветка `main`. Ставьте её командой из README, обновляйтесь через `sudo uisentry-menu`
(пункт 11) и не забывайте, что GitHub отдаёт ветку из кэша пару минут.

## Как сообщить об уязвимости

- Не открывайте публичный issue: используйте **GitHub Security Advisories**
  («Security» → «Report a vulnerability») или письмо владельцу репозитория.
- Что приложить: коммит/дата установки, дистрибутив и версия Python, шаги
  воспроизведения, логи. **Токены и ID маскируйте** — `.env` не пересылайте.
- Ответ: подтверждение в течение 72 часов, исправление — как только получится;
  по желанию укажем вас в описании исправления.

## Модель безопасности (что уже защищено)

- Сервис работает непривилегированным пользователем `uisentry`; код и venv
  принадлежат `root`, `.env` — `640 root:uisentry`.
- Привилегированные операции — только через `/usr/local/bin/uisentry-ctl` и узкий
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

## Вне области рассмотрения

- Доступ к серверу под root, физический доступ, компрометация аккаунта
  администратора в Telegram.
- Уязвимости самой панели 3X-UI, Xray и сторонних пакетов — сообщайте их авторам.
- Социальная инженерия и последствия явно подтверждённых действий (`reboot`,
  `apt upgrade`): они доступны администратору по замыслу.

---

## English

**Supported version:** there are no published releases; the single supported
version is the `main` branch. Install it with the command from the README and
update via `sudo uisentry-menu` (item 11).

**Reporting:** use GitHub Security Advisories (“Security” → “Report a
vulnerability”) or email the repository owner — do not open a public issue.
Include the commit/date, distribution, Python version, reproduction steps and
logs with **tokens and IDs masked** (never share `.env`). We acknowledge reports
within 72 hours.

**Already protected:** unprivileged service user, code owned by root, `.env` with
`640 root:uisentry`; privileged operations only through `uisentry-ctl` with a narrow
sudoers allowlist and argument validation (no arbitrary shell); confirmations,
cooldowns and an audit log for dangerous actions; commands accepted only in the
administrator's private chat with denials logged; secrets masked out of logs and
Telegram IDs masked; optional archive checksum verification (`--sha256`) and no
redirect following when talking to the panel.

**Out of scope:** root/physical access, a compromised Telegram admin account,
vulnerabilities in 3X-UI/Xray/third-party packages, and consequences of actions
the administrator explicitly confirmed.
