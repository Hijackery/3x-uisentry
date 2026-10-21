# StackSentry

> Мониторинг VPS, панелей и VPN в Telegram. Рабочее имя продукта — **StackSentry**;
> технические идентификаторы `stacksentry`/`stacksentry-*` сохранены для совместимости.

[![CI](https://github.com/Hijackery/stacksentry/actions/workflows/ci.yml/badge.svg)](https://github.com/Hijackery/stacksentry/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://www.python.org/)
![Platform](https://img.shields.io/badge/platform-systemd%20%7C%20openrc%20%7C%20sysv-4EAA25)
![License](https://img.shields.io/badge/license-MIT-blue)

**Русский** · [English](README.en.md)

**StackSentry** — Telegram-бот для VPS: следит за железом, службами, панелями
(3X-UI, Marzban, Marzneshin, PasarGuard, Remnawave, S-UI, Hiddify, Outline) и VPN
(WireGuard, OpenVPN, AmneziaWG, sing-box, Xray, Shadowsocks, Tailscale),
присылает понятные алерты и показывает подписки. Опасные действия — рестарт,
обновление системы, перезагрузка — только по кнопке с подтверждением и только
администратору.

Текущая версия — **1.0.0**. Проект публичный:
[документация](docs/README.md), [политика безопасности](SECURITY.md).
Обновления ставятся из `main` — кнопкой «Проверить обновления» в боте или через
`sudo stacksentry-menu update`; GitHub Releases и теги не используются.

Установка одной командой:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh)
```

Альтернативы без `curl | bash`:

```bash
# скачать и проверить установщик отдельно
curl -fsSLo /tmp/stacksentry-install.sh \
  https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh
sudo bash /tmp/stacksentry-install.sh

# GitHub CLI
gh api repos/Hijackery/stacksentry/contents/install.sh \
  -H "Accept: application/vnd.github.raw" > /tmp/stacksentry-install.sh
sudo bash /tmp/stacksentry-install.sh

# приватный форк или корпоративный GitHub — с токеном
GH_TOKEN=... bash -c 'curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
  https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh | bash'
```

> Одна команда: мастер сам скачает код, спросит токены, поставит зависимости,
> создаст отдельного пользователя, включит автозапуск и проверит, что всё работает.

## Содержание

1. [Зачем это нужно](#зачем-это-нужно)
2. [Как это выглядит](#как-это-выглядит)
3. [Совместимость](#совместимость)
4. [Быстрый старт](#быстрый-старт)
5. [Подключение к панели](#подключение-к-панели)
6. [Как устроено](#как-устроено)
7. [Команды и экраны](#команды-и-экраны)
8. [Меню настроек `stacksentry-menu`](#меню-настроек-stacksentry-menu)
9. [Настройки](#настройки)
10. [Безопасность](#безопасность)
11. [Обновление и удаление](#обновление-и-удаление)
12. [Если что-то не работает](#если-что-то-не-работает)
13. [Документация](#документация)
14. [Версии и обновление](#версии-и-обновление)
15. [Разработка](#разработка)
16. [Проект](#проект)

---

## Зачем это нужно

Панель 3X-UI показывает состояние, но в неё нужно заходить. Бот приносит самое
важное в Telegram и не мешает, когда всё в порядке.

| | |
|---|---|
| **Мониторинг** | CPU, RAM, swap, диск, сеть, load; службы; панель и xray; стек панелей/VPN; inbound'ы, подписки и трафик |
| **Алерты** | гистерезис без «дребезга», кулдаун, мьют «знаю, до утра»; под алертом сразу кнопки «Рестарт» и «Журнал» |
| **Действия** | рестарт службы и xray, `apt` full/phased, проверка релиз-обновления, перезагрузка, диагностические команды из белого списка |
| **История** | почасовые средние CPU/RAM/диска за сутки — видно, как вёл себя сервер ночью |
| **Установка** | одна команда, вопросы с терминала (работает и через `curl \| bash`), самопроверка из 8 пунктов |
| **Универсальность** | systemd, OpenRC, SysVinit; `apt`/`dnf`/`yum`/`zypper`/`pacman`; Python 3.10+ |
| **Терминал** | `stacksentry-menu` — правки настроек, рестарт, журнал, обновление; секреты маскируются |
| **Безопасность** | отдельный непривилегированный пользователь, узкий sudoers из точных команд, всё опасное — с подтверждением и в журнале |

## Как это выглядит

Сводка начинается с вердикта — ночью достаточно одного взгляда:

```text
🟢 Состояние сервера: всё в норме · CPU 12% · RAM 41% · Диск 63% · x-ui 🟢 · панель 🟢

CPU  ▓▓░░░░░░░░  12.0% 🟢
RAM  ▓▓▓▓░░░░░░  41.0% 🟢
Диск ▓▓▓▓▓▓░░░░  63.0% 🟢
```

Алерт — с причиной и действиями:

```text
🔴 КРИТИЧНО · Сервис x-ui не работает · 03:12
Статус failed, SubState failed

[♻️ Рестарт x-ui] [📜 Журнал x-ui]
[📊 Текущее состояние] [🏠 В меню]
[😴 Знаю, до утра]
```

Результат действия — с кодом возврата и контекстной кнопкой:

```text
✅ x-ui перезапущен за 1.2 с
Сервис x-ui перезапущен
[📜 Журнал x-ui] [🏠 В меню]
```

## Совместимость

| Что | Поддержка |
|---|---|
| **Службы** | systemd (Debian/Ubuntu, RHEL/Rocky/Alma, Fedora, openSUSE, Arch), OpenRC (Alpine, Gentoo), SysVinit |
| **Пакеты** | `apt`, `dnf`, `yum`, `zypper`, `pacman`, `apk`, `xbps`, `emerge`, `eopkg`, `pkg` — определяются автоматически |
| **Python** | 3.10 и новее; установщик подбирает интерпретатор сам |
| **Панели** | API: 3X-UI, Marzban, Marzneshin, PasarGuard, Remnawave, S-UI, Hiddify, Outline. Остальные VPN/прокси — детект и мониторинг службы/порта/процесса |
| **Рестарт ядра** | 3X-UI, Marzban, Marzneshin, PasarGuard, S-UI — через API; Remnawave/Hiddify/Outline — только служба/контейнер |
| **sudo** | классический sudo и `sudo-rs` (Ubuntu 26.04+): правила генерируются без шаблонов и алиасов |
| **Архитектуры** | x86_64, ARM — всё, где есть Python и поддерживаемая инициализация |

Выбрать менеджер служб вручную можно через `--init systemd|openrc|sysv`.
Если systemd нет, бот работает так же: журнал читается из `bot.log`, а состояние
служб помечается как недоступное (без ложных «crit»).

## Быстрый старт

### 1. Что понадобится

- сервер с Linux (любой поддерживаемый дистрибутив) и, по желанию, панелью;
- токен Telegram-бота от [@BotFather](https://t.me/BotFather);
- ваш numeric ID (например, [@userinfobot](https://t.me/userinfobot));
- доступ к панели: токен (3X-UI/Remnawave/S-UI/Hiddify), логин/пароль
  (Marzban/Marzneshin/PasarGuard) или секретный путь (Outline). Полная матрица —
  в [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

### 2. Установка

Установка одной командой:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh)
```

Альтернативы без `curl | bash`:

```bash
# скачать и проверить установщик отдельно
curl -fsSLo /tmp/stacksentry-install.sh \
  https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh
sudo bash /tmp/stacksentry-install.sh

# GitHub CLI
gh api repos/Hijackery/stacksentry/contents/install.sh \
  -H "Accept: application/vnd.github.raw" > /tmp/stacksentry-install.sh
sudo bash /tmp/stacksentry-install.sh

# приватный форк или корпоративный GitHub — с токеном
GH_TOKEN=... bash -c 'curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
  https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh | bash'
```

Или через пайп (так можно с `sudo`):

```bash
curl -fsSL https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh | sudo bash
```

> Не добавляйте `sudo` к форме `bash <(curl …)`: sudo закрывает файловый
> дескриптор process substitution, и bash отвечает
> `bash: /dev/fd/63: No such file or directory`. Либо запускайте `bash <(…)`
> уже от root (без sudo), либо используйте пайп `curl … | sudo bash`.

Мастер задаёт вопросы **четвёртым шагом, до установки зависимостей**, поэтому
отвечать нужно в начале. Вопросы читаются с терминала (`/dev/tty`), так что
диалог работает и при установке через пайп.

Что спросит мастер:

1. токен бота и ID администраторов (токен сразу проверяется через Telegram);
2. адрес панели, `webBasePath` и API-токен (адрес проверяется);
3. список служб под наблюдением;
4. необязательный чат для алертов.

### 3. После установки

```bash
sudo stacksentry-menu          # меню: состояние, настройки, рестарт, журнал, обновление
systemctl status stacksentry-bot
```

Откройте бота в Telegram, нажмите `/start` → `/status`. Если что-то не так,
бот сам скажет, что именно.

### Без вопросов

```bash
sudo env \
  TELEGRAM_BOT_TOKEN='123456789:AA...' \
  TELEGRAM_ADMIN_IDS='11111111' \
  PANEL_URL='https://panel.example.com:2053' \
  PANEL_API_TOKEN='...' \
  bash install.sh --yes
```

Полезные опции: `--repo owner/name`, `--ref ветка`, `--prefix DIR`, `--user NAME`,
`--no-start`, `--force-config`, `--print-source` (показать источник версии),
`--sha256 HASH`, `--init TYPE`, `--menu`. Полный список — `--help`.

Если `.env` остался от прерванного запуска, мастер дозаполнит недостающие
значения, а существующие подставит по умолчанию — править файл руками не нужно.

## Подключение к панели

### API панелей

| Панель | Авторизация | Что даёт |
|---|---|---|
| 3X-UI 2.x/3.x | Bearer API token | статус, inbound'ы, подписки, трафик, рестарт xray |
| Marzban | `PANEL_USERNAME` + `PANEL_PASSWORD` (OAuth2) | статус, пользователи, трафик, рестарт xray |
| Remnawave | Bearer API token | статус, пользователи, трафик |
| Marzneshin | логин/пароль (OAuth2 `/api/admins/token`) | статус, пользователи, трафик, рестарт backend через переприменение конфига |
| PasarGuard | логин/пароль (OAuth2 `/api/admin/token`) | статус, пользователи, трафик, рестарт core |
| S-UI | `PANEL_API_TOKEN` в заголовке `Token` | статус, клиенты, трафик, рестарт sing-box |
| Hiddify | `PANEL_API_TOKEN` (admin UUID) в `Hiddify-API-Key` | статус, пользователи, трафик |
| Outline | секретный путь `SB_API_PREFIX` в `PANEL_URL` | статус, access keys, трафик |

Переключение: `sudo stacksentry-menu set PANEL_TYPE s-ui` (и адрес/токен),
затем рестарт. Для 3X-UI/Remnawave/S-UI/Hiddify — `PANEL_API_TOKEN`,
для Marzban — логин/пароль.

Особенности адресов:
- S-UI: `PANEL_URL` — корень панели вместе с её web path, например
  `http://host:2095/app`; бот сам добавит `/apiv2/`.
- Hiddify: `PANEL_URL` — корень вместе с admin proxy path, например
  `https://example.com/AbCdEf`; бот сам добавит `/api/v2/`.
- Outline: `PANEL_URL` — полный адрес с секретным `SB_API_PREFIX`, например
  `https://host:port/SecretPath`; отдельный токен не нужен.
- 3X-UI: `PANEL_BASE_PATH` — webBasePath панели; `/panel/api` добавит бот.

Установщик, меню и бот подстраиваются под сервер. Мастер определяет ОС,
пакетный менеджер, службы и тип панели, затем спрашивает только нужное:
токен для 3X-UI/Remnawave/S-UI/Hiddify, логин/пароль для
Marzban/Marzneshin/PasarGuard или секретный путь для Outline, с проверкой API.
`stacksentry-menu detect` показывает найденное, в меню есть пункт «Тип панели»,
а рестарт ядра сам выбирает endpoint. Главное меню бота не показывает
«Трафик»/«Подписки», пока API не настроен, и показывает команды обновления для
фактического пакетного менеджера (APT-специфичные — только на APT).

### Что детектируется

Панели: 3X-UI, S-UI, Marzban, Marzneshin, PasarGuard, Hiddify, Remnawave,
Outline. Прокси/ядра: Xray, V2Ray, sing-box, Hysteria, TUIC, Trojan, NaiveProxy,
Mieru, ShadowTLS, Brook, mtg, Shadowsocks, Squid, Dante, 3proxy, GOST. VPN/mesh:
WireGuard, AmneziaWG, OpenVPN, IPsec/StrongSwan, SoftEther, Tailscale, ZeroTier,
NetBird, Headscale, Nebula.

Определение — по юнитам (systemd/OpenRC/SysV), путям конфигов и именам
процессов (`ps`). API-интеграция есть у 3X-UI (inbound'ы, подписки, рестарт
xray), Marzban, Marzneshin, PasarGuard, Remnawave, S-UI, Hiddify и Outline
(пользователи/клиенты, трафик); остальные показываются как службы/порты и
честно помечаются «API не поддерживается». Docker-контейнеры видны как служба `docker`, но не
опрашиваются через Docker API (это дало бы боту root-эквивалентные права).

### Если панели нет: monitor-only

Установщик сам находит панели/VPN по службам, путям и портам. Если поддерживаемая
панель не найдена, можно оставить `PANEL_URL` и `PANEL_API_TOKEN` пустыми — бот будет
наблюдать службы, VPN-стек, железо и алерты, а разделы панели/подписок честно
скажут, что API не настроен. Позже включить API: `sudo stacksentry-menu` →
адрес панели и токен (или переустановка с `--panel`).

### Токен 3X-UI

Токен создаётся **в самой панели**, а не в Telegram и не в боте.

**3X-UI v3.x**

1. «Настройки панели → Учётная запись → API-токен» → создать.
2. Scope: **`node-sync`** (или `admin`).
3. **Скопируйте значение сразу** — панель показывает его один раз, позже
   остаётся только отметка «задано».

То же по API: `POST /panel/api/setting/apiTokens/create` с `name`, `scope`,
`expiresAt` (нужен уже существующий admin-токен).

**3X-UI 2.x**

«Настройки → API Tokens» либо из терминала:

```bash
sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
```

В v3 эта команда новых токенов не создаёт — она лишь ротирует служебный
`cli-fallback`.

Почему не `monitor`: этот scope даёт только статус панели. Для inbound'ов и
подписок нужен `node-sync`, для действий в панели — `admin`.

### `webBasePath` — частая ошибка

Поле `webBasePath` — это **отдельная настройка «URL панели»** в 3X-UI. Обычно
она равна `/`, и тогда поле в мастере оставляют пустым.

Путь `/panel` из адресной строки вводить **не нужно**: интерфейс 3X-UI всегда
живёт на `.../panel`, а API — на `.../panel/api`. Если вписать `/panel` в
`webBasePath`, получится лишний сегмент `.../panel/panel/api`.

Установщик проверяет путь до запроса токена (верный путь без токена отвечает
`401`, неверный — `404`) и молча исправляет ошибку. Полный адрес, вставленный в
это поле, он тоже распознаёт.

### Проверка вручную

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Requested-With: XMLHttpRequest' \
  https://panel.example.com:2053/panel/api/server/status
```

`401` — путь верный, нужен токен. С токеном:

```bash
curl -fsS -H "Authorization: Bearer <токен>" \
  https://panel.example.com:2053/panel/api/server/status
```

Ответ должен содержать `"success":true`.

### Адрес: IP, домен, localhost

- Если в панели задан **«Домен панели»** (`webDomain`), 3X-UI сверяет заголовок
  `Host` и отвечает `403` на всё остальное. Тогда в `PANEL_URL` должно быть
  ровно это имя, а не IP.
- Имя должно резолвиться **с самого сервера**. Если домен указывает на публичный
  IP и обратного NAT нет, используйте `http://127.0.0.1:2053` с тем же
  `PANEL_BASE_PATH`. IPv6 пишите в скобках: `http://[::1]:2053`.
- `http` допустим, когда панель слушает localhost: трафик не выходит за пределы
  машины. Если панель доступна по публичному адресу — только `https://`, иначе
  токен и содержимое API идут открытым текстом. Для самоподписанного
  сертификата не отключайте проверку, а укажите `PANEL_CA_BUNDLE`.
- Бот не следует редиректам: если `http` перенаправляет на `https`, просто
  поменяйте схему.

## Как устроено

```text
Telegram  ⇄  бот (непривилегированный пользователь stacksentry)
                 ├─ psutil / systemctl / journalctl        — метрики и службы
                 ├─ API панели (Bearer, scope node-sync)    — inbound'ы, подписки, рестарт xray
                 └─ sudo stacksentry-ctl (узкий sudoers)           — рестарт, apt, reboot, журнал
```

- **Код** — `/opt/stacksentry`, принадлежит `root`; бот не может переписать
  сам себя.
- **Секреты** — `.env` с правами `640 root:stacksentry`.
- **Состояние** — SQLite в `/var/lib/stacksentry` (история, аудит, мьюты).
- **Привилегии** — только через `sudo stacksentry-ctl`; список разрешённых подкоманд
  перечислен точно, без шаблонов.
- **`apt`** — выполняется отдельным systemd-юнитом
  (`stacksentry-apt-*.service`), потому что бот живёт с `ProtectSystem=strict`.

## Команды и экраны

| Команда | Что показывает |
|---|---|
| `/status` | вердикт одной строкой, замечания, метрики, службы и панель |
| `/traffic` | трафик: inbound'ы/клиенты или пользователи панели |
| `/services` | состояние служб; рестарт и журнал рядом |
| `/panel` | доступность панели и API, версия и обновления |
| `/subs` | подписки: сроки, трафик, исчерпанные и отключённые |
| `/history` | почасовые средние CPU/RAM/диска за сутки |
| `/logs` | журнал выбранного сервиса, фильтр «только ошибки» |
| `/audit` | журнал действий бота, фильтры «отказы / действия» |
| `/stack` | что найдено на сервере: панели, VPN, прокси, службы |
| `/maint` | обслуживание: обновления пакетов, перезагрузка, команда из allowlist |
| `/run <команда>` | выполнить диагностическую команду из белого списка |
| `/version` | версия StackSentry, Python, ОС, пакетный менеджер и панель |
| `/updatecheck` | проверить обновления StackSentry в `main` |
| `/help` | помощь простыми словами: что нажать и что значат цифры |

Кнопки дублируют команды; на каждом экране есть «🏠 В меню», так что в тупике
оказаться нельзя. Гостям кнопки не показываются. При переходе между меню
предыдущий экран заменяется — вереница сообщений не копится (это не касается
«Подробнее» и «Свернуть»: они разворачивают тот же экран).

**Команду на сервер можно прислать в любом разделе** — просто отправьте её
сообщением (или `/run <команда>`). Бот попросит подтверждение и вернёт вывод.
Разрешены только безопасные диагностические команды из `COMMAND_ALLOWLIST`;
список покажет `/run` без аргументов. Произвольный shell недоступен.

**Обновления в `/maint` разделены и отчитываются в чат.** Кнопка обновления
индексов вызывает команду фактического пакетного менеджера (`apt update`,
`dnf makecache`, `pacman -Sy` и т. п.); кнопка обновления системы — его штатный
upgrade/full-upgrade. `phased` и «запустить релиз» (`do-release-upgrade`) —
только APT/Ubuntu и на других системах скрыты. Все фоновые задачи бот ведёт сам:
когда юнит завершится, в чат придёт код возврата и хвост журнала — вводить
`journalctl` не нужно. Одно действие — одно сообщение: «в очереди» → «запущено» →
отчёт, без вереницы. Вручную журнал смотреть так: `sudo stacksentry-menu apt-logs`.

### Безопасные команды для диагностики

`/run <команда>` (или просто текст в чате) запускает только команды из
`COMMAND_ALLOWLIST`. Все они **только читают** и запускаются как argv без shell:
ни pipes, ни перенаправлений, ни записи. Если команды нет в системе, бот честно
скажет, что она не установлена.

| Команда | Что показывает |
|---|---|
| `os_logs` / `os_errors` | журнал ОС: предупреждения / только ошибки (100 строк) |
| `kernel_logs` / `dmesg` | сообщения ядра |
| `xui_logs` | журнал службы `x-ui` |
| `xui_service` / `x-ui status` | статус панели |
| `failed_units` | упавшие systemd-юниты |
| `sockets` / `net_summary` | слушающие порты / сводка сокетов |
| `addresses` / `routes` | IP-адреса / маршруты |
| `top_cpu` | процессы по CPU и памяти |
| `uname` / `cpu_info` / `block_devices` | система, CPU, диски |
| `log_size` / `inodes` / `df -h` / `free -h` / `uptime` | место и ресурсы |
| `upgradable` / `upgrade_sim` | доступные обновления / симуляция `apt` |
| `vmstat` / `time_status` / `logged_in` / `boot_time` | нагрузка, время, сеансы |
| `journalctl --disk-usage` / `systemctl list-timers --all` | журнал и таймеры |

Примеры: `/run os_logs`, `/run failed_units`, `/run sockets`. Команда `/run`
без аргументов показывает этот список прямо в боте — с группами и описаниями.
У логов (`os_logs`, `xui_logs`, `kernel_logs`, `dmesg`) под выводом есть кнопки
«📜 Ещё 200 строк» и «🔎 Только ошибки» — как у журналов служб.

**Команды и кнопки работают только в личном чате администратора.** В группе бот
отвечает отказом: адреса, подписки и трафик не должны утекать в общий чат.
`ALERT_CHAT_ID` разрешает только *исходящие* алерты в группу — управление там
недоступно.

`/status` начинается с вердикта, детали спрятаны за «📋 Подробнее». Метрики
выровнены моноширинным блоком, время подписано «время сервера». Под алертом
сразу есть `♻️ Рестарт`, `📜 Журнал` и `😴 Знаю, до утра` — мьют на 8 часов,
хранится в SQLite и переживает перезапуск.

## Меню настроек `stacksentry-menu`

После установки доступна консольная панель:

```bash
sudo stacksentry-menu              # интерактивное меню
sudo stacksentry-menu show         # настройки (секреты замаскированы)
sudo stacksentry-menu keys         # какие ключи разрешено менять
stacksentry-menu check PANEL_TIMEOUT 20   # проверка без изменений (без root)
sudo stacksentry-menu set PANEL_TIMEOUT 20
sudo stacksentry-menu restart       # применить
sudo stacksentry-menu detect        # что найдено: ОС, панели, VPN, службы
sudo stacksentry-menu restart-xray  # перезапустить ядро (где поддерживается)
sudo stacksentry-menu apt-logs 100  # журнал фоновых apt-обновлений
sudo stacksentry-menu logs 100
```

Интерактивное меню рисуется в рамке и сгруппировано по разделам; сверху — живое
состояние службы:

```text
╔════════════════════════════════════════════════════════╗
│        StackSentry — панель управления                 │
│  Служба stacksentry-bot: ● работает                    │
│   Диагностика                                          │
│    1. Состояние службы и последние ошибки              │
│    2. Показать настройки (секреты замаскированы)       │
│    3. Журнал бота (последние 50 строк)                 │
│    4. Что найдено на сервере (ОС, панели, VPN)         │
│────────────────────────────────────────────────────────│
│   Telegram                                             │
│    5. Токен бота (@BotFather)                          │
│    6. ID администраторов                               │
│    7. Чат для алертов                                  │
│   …                                                    │
│   25. Проверить обновления StackSentry                 │
│   26. Удалить бота                                     │
│    0. Выход                                            │
╚════════════════════════════════════════════════════════╝
```

### Что делает каждый пункт и что в него вписать

| Пункт | Что делает | Что ввести | Пример |
|---|---|---|---|
| 1 | состояние службы и последние ошибки | — | — |
| 2 | показывает настройки, секреты замаскированы | — | — |
| 3 | журнал бота, 50 строк | — | — |
| 4 | автоопределение: ОС, пакетный менеджер, панели, VPN, службы | — | — |
| 5 | токен Telegram-бота | значение от @BotFather | `123456789:AAH...` |
| 6 | ID администраторов | числа через запятую | `11111111,22222222` |
| 7 | чат для алертов | ID чата; у групп он отрицательный; пусто — в личку | `-1001234567890` |
| 8 | тип панели (`PANEL_TYPE`) | один из поддерживаемых типов | `s-ui` |
| 9 | адрес панели (`PANEL_URL`) | схема, хост, порт и (для S-UI/Hiddify) путь панели | `https://panel.example.com:5580` |
| 10 | путь панели (`PANEL_BASE_PATH`) | `webBasePath` 3X-UI; у S-UI/Hiddify путь обычно уже в адресе | пусто или `mypanel` |
| 11 | API-токен панели | токен 3X-UI/Remnawave/S-UI или admin UUID Hiddify | `AbCdEf...` |
| 12 | логин и пароль Marzban (подменю) | `PANEL_USERNAME`, `PANEL_PASSWORD` | `admin`, `***` |
| 13 | TLS и CA (подменю) | `PANEL_VERIFY_TLS`: `true`/`false`; `PANEL_CA_BUNDLE`: путь к PEM | `true`, `/etc/ssl/panel-ca.pem` |
| 14 | порог CPU (подменю) | `CPU_WARN` и `CPU_CRIT`, % | `80`, `95` |
| 15 | порог памяти (подменю) | `MEM_WARN` и `MEM_CRIT`, % | `85`, `95` |
| 16 | порог диска (подменю) | `DISK_WARN` и `DISK_CRIT`, % | `85`, `93` |
| 17 | гистерезис и кулдаун (подменю) | `ALERT_HYSTERESIS` в %, `ALERT_COOLDOWN` в сек | `5`, `1800` |
| 18 | за сколько дней предупреждать о подписке | целое число дней | `3` |
| 19 | службы под наблюдением | имена служб через запятую | `x-ui,fail2ban` |
| 20 | уровень логирования | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| 21 | любой ключ из белого списка | сначала имя ключа, затем значение | `DISK_PATH` → `/` |
| 22 | перезапуск бота с проверкой | — | — |
| 23 | перезапуск ядра панели | подтверждение `y` | `y` |
| 24 | обновление из `main` | — | — |
| 25 | проверка обновлений StackSentry | — | — |
| 26 | удаление | `yes` для подтверждения | `yes` |

Пустое значение очищает настройку: `sudo stacksentry-menu set PANEL_BASE_PATH ""`.
Для пунктов 5–20 можно не ходить в меню, а сразу писать:
`sudo stacksentry-menu set KEY VALUE` (то же, что пункт 18, но одной командой).

```bash
# примеры значений
sudo stacksentry-menu set PANEL_TYPE s-ui
sudo stacksentry-menu set PANEL_URL https://panel.example.com:2095/app
sudo stacksentry-menu set PANEL_BASE_PATH ""          # «URL панели» = /
sudo stacksentry-menu set TELEGRAM_ADMIN_IDS 11111111,22222222
sudo stacksentry-menu set ALERT_CHAT_ID -1001234567890
sudo stacksentry-menu set SERVICES x-ui,fail2ban
sudo stacksentry-menu set CPU_WARN 80
sudo stacksentry-menu set LOG_LEVEL INFO

# проверить значение, ничего не меняя
stacksentry-menu check CPU_WARN 80

# проверить обновления StackSentry и посмотреть журнал пакетных обновлений
sudo stacksentry-menu update-check
sudo stacksentry-menu apt-logs 100
```

Что важно знать:

- ввод проверяется: неверный номер показывает диапазон, `q` — выход;
- менять можно только ключи из белого списка (`stacksentry-menu keys`); значения
  проверяются до записи;
- `.env` перезаписывается атомарно с правами `640 root:<сервисный пользователь>`;
- секреты не печатаются целиком;
- пути можно переопределить: `sudo PREFIX=/opt/бот ENV_FILE=/opt/бот/.env stacksentry-menu`;
- автоочистку экрана можно выключить: `STACKSENTRY_NO_CLEAR=1 sudo stacksentry-menu`.

## Настройки

Все параметры — в `/opt/stacksentry/.env`; пример с комментариями —
[`.env.example`](.env.example). Переменные окружения процесса имеют приоритет
над файлом.

| Ключ | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от @BotFather |
| `TELEGRAM_ADMIN_IDS` | ID администраторов через запятую |
| `ALERT_CHAT_ID` | чат для алертов (пусто — админам в личку) |
| `TELEGRAM_API_URL` | свой Bot API или прокси (по умолчанию официальный) |
| `PANEL_URL` | адрес панели, например `https://panel.example.com:2053` |
| `PANEL_BASE_PATH` | «URL панели» (`webBasePath`), если задан |
| `PANEL_API_TOKEN` | токен панели (3X-UI `node-sync`, Remnawave, S-UI `Token`, Hiddify admin UUID) |
| `PANEL_TYPE` | `3x-ui`, `marzban`, `marzneshin`, `pasarguard`, `remnawave`, `s-ui`, `hiddify` или `outline` |
| `PANEL_USERNAME` | логин Marzban (только для `PANEL_TYPE=marzban`) |
| `PANEL_PASSWORD` | пароль Marzban (только для `PANEL_TYPE=marzban`) |
| `PANEL_VERIFY_TLS` | проверять TLS-сертификат панели |
| `PANEL_CA_BUNDLE` | свой CA-файл (PEM); перекрывает `PANEL_VERIFY_TLS` |
| `PANEL_TIMEOUT` | таймаут запросов к панели, сек |
| `SERVICES` | службы под наблюдением, через запятую (`x-ui,fail2ban`) |
| `DISK_PATH` | что проверять по месту |
| `CPU_WARN` | порог предупреждения по CPU, % |
| `CPU_CRIT` | порог критического CPU, % |
| `MEM_WARN` | порог предупреждения по памяти, % |
| `MEM_CRIT` | порог критической памяти, % |
| `DISK_WARN` | порог предупреждения по диску, % |
| `DISK_CRIT` | порог критического диска, % |
| `ALERT_HYSTERESIS` | на сколько % метрика должна отойти назад, чтобы снять алерт |
| `ALERT_COOLDOWN` | как часто повторять активный алерт, сек |
| `EXPIRING_SOON_DAYS` | за сколько дней подписка считается истекающей |
| `CHECK_INTERVAL` | период фоновой проверки, сек (минимум 15) |
| `ACTION_COOLDOWN` | пауза между одинаковыми действиями, сек |
| `CONFIRM_TTL` | сколько живёт кнопка подтверждения, сек |
| `TOTP_SECRET` | секрет TOTP (base32) для 2FA; пусто — 2FA выключена |
| `TOTP_REQUIRED` | `1` — требовать код для опасных действий |
| `ELEVATION_TTL` | сколько действует разблокировка после кода, сек |
| `TOTP_MAX_ATTEMPTS` | сколько попыток ввода кода до блокировки |
| `TOTP_LOCKOUT` | пауза после перебора кодов, сек |
| `WEBAPP_ENABLED` | `1` — включить read-only Mini App |
| `WEBAPP_HOST` | адрес прослушивания (по умолчанию `127.0.0.1`) |
| `WEBAPP_PORT` | порт Mini App (по умолчанию 8787) |
| `WEBAPP_PUBLIC_ORIGIN` | публичный HTTPS-адрес для кнопки в Telegram |
| `WEBAPP_SECRET` | секрет подписи cookie Mini App (≥ 32 символов) |
| `WEBAPP_SESSION_TTL` | срок жизни сессии Mini App, сек |
| `COMMAND_TIMEOUT` | таймаут разрешённых команд, сек |
| `COMMAND_ALLOWLIST` | безопасные команды диагностики (алиасы, см. раздел выше) |
| `STATE_PATH` | база SQLite |
| `LOG_PATH` | файл лога |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SUDO_WRAPPER` | путь к `stacksentry-ctl` |

### Куда приходят алерты

`ALERT_CHAT_ID` можно оставить пустым — тогда алерты приходят в личку всем ID из
`TELEGRAM_ADMIN_IDS`. Для общего чата добавьте туда бота, отправьте сообщение и
узнайте ID (`@userinfobot`, `@getidsbot`); у групп он отрицательный, например
`-1001234567890`.

Помните: в алертах есть адреса, подписки и трафик — в группе их увидят все
участники. Команды и кнопки при этом всё равно работают только в личке.

### Mini App (опционально, read-only)

Красивый дашборд в Telegram: обзор, службы, панель, трафик, подписки, история.
Только чтение — никаких действий и секретов в браузере.

```bash
sudo bash install.sh --webapp --webapp-origin https://sentry.example.com
```

- вход по подписи Telegram (`initData`), без паролей; только администраторы;
- cookie `HttpOnly`, `SameSite=Strict`, срок 1 час; CSP без сторонних скриптов;
- сервис `stacksentry-webapp` слушает `127.0.0.1:8787`, наружу — только Caddy/Nginx
  с TLS; без публичного адреса доступен через
  `ssh -L 8787:127.0.0.1:8787 root@сервер` → `http://127.0.0.1:8787`;
- при заданном `WEBAPP_PUBLIC_ORIGIN` бот добавляет кнопку «Панель» в меню
  Telegram (Mini App);
- если API панели не настроен, дашборд показывает службы/VPN/железо и честно
  пишет, что inbound'ы/подписки недоступны.

### Терминал (опционально)

Полноценный shell в браузере — отдельный сервис, не часть бота:

```bash
sudo bash install.sh --terminal              # shell пользователя stacksentry-term
sudo bash install.sh --root-terminal         # + root-сессия (sudo -i)
sudo bash install.sh --terminal --terminal-origin https://term.example.com
```

Что происходит:

- создаётся отдельный пользователь `stacksentry-term` и служба `stacksentry-terminal`;
- сервис слушает `127.0.0.1:8788`, наружу не выставлен;
- вход: Telegram `initData` + код TOTP (для доступа вне Telegram — ещё и
  access token из `/etc/stacksentry/terminal-secrets.env`, `600 root`);
- секреты терминала лежат в `/etc/stacksentry/terminal-secrets.env` с правами
  `600 root`; systemd передаёт их процессу, сам пользователь файл не читает;
- обычный shell работает под отдельным пользователем `stacksentry-shell` **без
  sudo**; root-сессия — только через точное правило
  `stacksentry-term ALL=(root) NOPASSWD: /bin/bash -l` (флаг `--root-terminal`);
- секреты терминала (TOTP, тикеты, access token, токен бота) **не передаются**
  в PTY: shell получает только PATH/TERM/LANG/HOME/USER.

Доступ по умолчанию — только с сервера/через SSH-туннель:

```bash
ssh -L 8788:127.0.0.1:8788 root@сервер
# затем откройте http://127.0.0.1:8788
```

Для доступа из Telegram задайте `--terminal-origin https://...` и поставьте
Caddy/Nginx с TLS перед `127.0.0.1:8788`.

> Root-терминал — это полноценный root-shell. TOTP защищает вход, но при
> компрометации веб-слоя/сессии/зависимостей защита падает. Держите его за
> VPN/SSH-туннелем, не публикуйте в интернет без второго периметра.

## 2FA для опасных действий

Просмотр состояния, журналов и подписок — **без кода**. Код нужен только там,
где можно сломать сервер (step-up):

| Уровень | Что | 2FA |
|---|---|---|
| 🟢 просмотр | статус, службы, панель, трафик, подписки, история, аудит | нет |
| 🟡 действия | рестарт службы/xray, `apt update`, команда из allowlist | код один раз, разблокировка на 15 мин |
| 🔴 разрушительное | `apt full/phased`, релиз-обновление, перезагрузка | **свежий** код (не старше 2 мин) |

Как это выглядит:

1. Нажимаете «Рестарт x-ui» → «🔐 Нужен код 2FA. Отправьте 6 цифр».
2. Присылаете код — бот **удаляет сообщение с кодом**, проверяет и пишет
   «🔓 Действия разблокированы до 14:35».
3. Появляется обычное подтверждение с последствиями. Дальше 15 минут опасные
   действия не спрашивают код (разрушительные — всегда свежий).
4. «🔐 Безопасность» в меню: статус, «Разблокировать», «Заблокировать сейчас».

Включение: установщик спросит при установке (или `--2fa`). Секрет показывается
один раз: добавьте его в Google Authenticator/Aegis/1Password. Посмотреть
секрет позже: `sudo stacksentry-menu totp` (только root).

Защита: код одноразовый в пределах окна, 3 попытки, блокировка на 5 минут,
все успехи/неудачи — в аудит. Секрет лежит в `.env` с правами
`640 root:<группа сервиса>`; логи его вырезают.

## Секреты и хранение

- Токены бота и панели — в `.env` с правами `640 root:<группа сервиса>`; сервис
  читает их, но не может переписать файл.
- Терминал (если включён) хранит свои секреты отдельно:
  `/etc/stacksentry/terminal.env`, `600 root`, systemd передаёт их процессу через
  `EnvironmentFile`. Сам сервисный пользователь файл не читает.
- Логи чистятся форматтером: токен бота в URL, токены панели/терминала, TOTP и
  access token вырезаются; `initData` не логируется; Telegram ID маскируются.
- База `state.db` — `0600`; в ней есть Telegram ID, аудит и подписки.
- Шифрование «на уровне приложения» с ключом на том же сервере даёт мало:
  компрометация хоста всё равно раскрывает ключ. Для защиты диска используйте
  LUKS/шифрование VPS, бэкапы — в шифрованном виде. Для терминала добавьте
  второй фактор (TOTP уже есть) и доступ через VPN/SSH-туннель.

## Безопасность

- Бот работает непривилегированным пользователем `stacksentry`; код и venv
  принадлежат `root`, `.env` доступен службе только на чтение.
- Все привилегированные операции идут через `sudo stacksentry-ctl` и узкий
  sudoers-список из **точных команд** (без шаблонов в аргументах — так требуют и
  классический sudo, и `sudo-rs`). Произвольного shell нет.
- `apt` выполняется отдельными systemd-юнитами `stacksentry-apt-*.service` — они
  не наследуют песочницу бота; при недоступности transient-юнита есть фолбэк.
- Опасные действия требуют подтверждения; токен привязан к запросившему
  администратору и одноразовый; всё пишется в `audit_log`.
- Клиент панели не следует редиректам, значения из панели экранируются, токен
  бота не попадает в логи даже на `DEBUG`.
- Повторы ограничены `ACTION_COOLDOWN`; flood control Telegram обрабатывается
  повтором с паузой, длинные сообщения режутся по строкам.
- Команды принимаются только в личном чате; попытки посторонних пишутся в журнал
  (`denied`).
- Установщик умеет проверять контрольную сумму архива (`--sha256`).
- Секреты вырезаются из логов форматтером (включая текст исключений), Telegram
  ID маскируются (`…789`), `stacksentry-menu show/check` не печатают секреты.
- Для самоподписанного сертификата есть `PANEL_CA_BUNDLE` — вместо отключения
  проверки TLS можно держать пиннинг.

Ресурсы службы ограничены (`MemoryMax=256M`, `TasksMax=64`), вывод команд
обрезается (16 КБ) и имеет таймаут.

## Обновление и удаление

```bash
# обновление (конфиг и состояние сохраняются)
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/stacksentry/main/install.sh)

# то же из меню
sudo stacksentry-menu          # пункт 24

# удалить целиком: служба, sudoers, обёртка, меню, код, база и пользователь
sudo bash install.sh --uninstall

# сохранить базу и пользователя
sudo bash install.sh --uninstall --keep-state

# интерактивное меню установщика
sudo bash install.sh --menu
```

Удаление печатает одну строку «Бот удалён.» и закрывает `stacksentry-menu`. При
`--keep-state` остаётся предупреждение о том, что база и пользователь сохранены.

> Релизы и теги проект не публикует: актуальная версия всегда в ветке `main`.
> GitHub может отдавать её из кэша пару минут — повторный запуск установщика
> решит проблему.

## Если что-то не работает

```bash
systemctl status stacksentry-bot
journalctl -u stacksentry-bot -n 50 --no-pager
```

| Симптом | Что делать |
|---|---|
| «Синтаксис sudoers невалиден» | установщик печатает строку от `visudo` — пришлите её в issue |
| «Системные метрики недоступны: `/proc/stat`» | в старой установке остался `ProcSubset=pid`: `sudo sed -i '/^ProcSubset=/d' /etc/systemd/system/stacksentry-bot.service && sudo systemctl daemon-reload && sudo systemctl restart stacksentry-bot` |
| «Токен отклонён» | проверьте `TELEGRAM_BOT_TOKEN` и `PANEL_API_TOKEN` через `sudo stacksentry-menu` → «Показать настройки» |
| `inbounds/list недоступен` | у токена панели нет scope `node-sync` |
| Панель отвечает `404` на API | проверьте `PANEL_URL`/`PANEL_BASE_PATH` (частая ошибка — `/panel` в `webBasePath`) |
| Логи сервиса не видны | проверьте правило `journal` в sudoers и запуск через `sudo` |
| Ubuntu 26.04+ | `sudo-rs` запрещает шаблоны в аргументах — установщик это уже учитывает |

Больше — в [SECURITY.md](SECURITY.md) и [CHANGELOG.md](CHANGELOG.md).

## Документация

- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) — ОС, init, пакеты, панели, протоколы, рестарт.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — компоненты, поток данных, безопасность, расширение.
- [docs/RELEASING.md](docs/RELEASING.md) — версии, проверка и установка обновлений.
- [SUPPORT.md](SUPPORT.md) — куда обращаться и что приложить.
- [SECURITY.md](SECURITY.md) — политика и модель безопасности.
- [CONTRIBUTING.md](CONTRIBUTING.md) — сборка, проверки, PR.
- [CHANGELOG.md](CHANGELOG.md) — история изменений.

## Версии и обновление

- Версия — SemVer в `stacksentry/__init__.py`; видна в боте (`/version`).
- GitHub Releases и теги **не публикуются**: актуальная версия — `main`.
- Проверка обновлений: кнопка «🔄 Проверить обновления StackSentry» в
  «Обслуживании», `sudo stacksentry-menu update-check` или `/updatecheck`.
- Если обновление есть, в боте появляется кнопка «⬆️ Обновить StackSentry».
  Она требует подтверждения и свежего TOTP (если 2FA включена), сохраняет `.env`
  и состояние, затем перезапускает бота.
- Ручное обновление: `sudo stacksentry-menu update` или повторный запуск
  установщика. Подробности и откат — в [docs/RELEASING.md](docs/RELEASING.md).

## Разработка

```bash
pip install -r requirements-dev.txt
ruff format --check .
ruff check .
mypy .
pytest -q
```

Тесты не требуют панели, Telegram и systemd: внешние вызовы подменяются.
CI гоняет Python 3.10 и 3.13, `shellcheck`, `bash -n` и `visudo -cf`.

## Проект

- [SECURITY.md](SECURITY.md) — как сообщить об уязвимости и что уже защищено.
- [CONTRIBUTING.md](CONTRIBUTING.md) — окружение, обязательные проверки, стиль.
- [SUPPORT.md](SUPPORT.md) — куда обращаться и что приложить.
- [docs/](docs/README.md) — совместимость, архитектура, обновления.
- [CHANGELOG.md](CHANGELOG.md) — история изменений.
- [`.env.example`](.env.example) — все настройки с комментариями.
- [English README](README.en.md).

## Лицензия

MIT — см. [LICENSE](LICENSE).
