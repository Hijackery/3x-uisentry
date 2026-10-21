#!/usr/bin/env bash
#
# 3X-UI Sentry — установка на Linux-сервер одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/Hijackery/3x-uisentry/main/install.sh | sudo bash
#
# Вопросы читаются с терминала (/dev/tty), поэтому пайп не мешает диалогу.
# Альтернатива, как у 3X-UI:  sudo bash <(curl -fsSL .../install.sh)
# Удаление:                   sudo bash install.sh --uninstall
# Меню:                       sudo bash install.sh --menu
#
# Скрипт сам скачивает исходники, ставит зависимости, задаёт несколько вопросов
# (токен от @BotFather, ваш Telegram ID, адрес и API-токен панели), создаёт
# непривилегированного пользователя, включает автозапуск через systemd и в
# конце проверяет, что всё действительно работает.
#
# Что нужно до старта:
#   • токен Telegram-бота от @BotFather (BotFather → /newbot);
#   • ваш Telegram ID (узнать у @userinfobot);
#   • адрес панели 3X-UI и её API-токен (выпускается на сервере).
#
# Повторный запуск обновляет бота и сохраняет ваш .env, токены и состояние.
#
set -euo pipefail

# Всё, что создаёт установщик, включает токены: не даём файлам родиться
# читаемыми для посторонних (права уточняются ниже по месту).
umask 077

# Откуда читать ответы на вопросы. При установке одной строкой через пайп
# (`curl … | sudo bash`) stdin занят самим скриптом; без этого блока вопросы
# молча пропускались бы и установка шла на значениях по умолчанию. Поэтому
# при недоступном stdin спрашиваем терминал напрямую (/dev/tty). Вариант
# `sudo bash <(curl …)` работает и без этого: там stdin — уже терминал.
READ_FD=0
INTERACTIVE=0
if [ -t 0 ]; then
    INTERACTIVE=1
elif { exec 3</dev/tty; } 2>/dev/null; then
    READ_FD=3
    INTERACTIVE=1
fi

REPO_OWNER="${REPO_OWNER:-Hijackery}"
REPO_NAME="${REPO_NAME:-3x-uisentry}"
REPO_REF="${REPO_REF:-main}"
REPO_URL="${REPO_URL:-}"
TARBALL_URL="${TARBALL_URL:-}"
SOURCE_SHA256="${SOURCE_SHA256:-}"
REPO_SLUG="${REPO:-}"

# Приводит адрес репозитория к согласованному виду (owner/name, REPO_URL,
# TARBALL_URL). Отдельная функция: опции --repo/--ref разбираются уже после
# чтения переменных окружения.
resolve_repo() {
    if [ -n "$REPO_SLUG" ]; then
        case "$REPO_SLUG" in
            */*) REPO_OWNER="${REPO_SLUG%%/*}"; REPO_NAME="${REPO_SLUG##*/}" ;;
            *) fail "--repo ожидает owner/name, получено: $REPO_SLUG" ;;
        esac
    fi

    [ -n "$REPO_URL" ] || REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"

    # Явный REPO_URL должен определять и откуда скачиваем: иначе подмена молча
    # игнорируется и скрипт тянет заведомо несуществующий OWNER/...
    case "$REPO_URL" in
        *github.com/*/*)
            _url_owner="$(printf '%s' "$REPO_URL" | sed -E 's#.*github\.com/([^/]+)/([^/]+).*#\1#')"
            _url_repo="$(printf '%s' "$REPO_URL" | sed -E 's#.*github\.com/([^/]+)/([^/]+).*#\2#')"
            _url_repo="${_url_repo%.git}"
            if [ -n "$_url_owner" ] && [ -n "$_url_repo" ]; then
                REPO_OWNER="$_url_owner"
                REPO_NAME="$_url_repo"
            fi
            ;;
    esac

    [ -n "$TARBALL_URL" ] \
        || TARBALL_URL="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REPO_REF}"
}

# Ссылки на источник по порядку попыток: указанная ветка/тег, затем обычные
# main и master — у нового репозитория имя основной ветки может быть любым.
source_candidates() {
    printf '%s\n' "$TARBALL_URL"
    case "$TARBALL_URL" in
        *codeload.github.com/*/tar.gz/refs/heads/*)
            _cand_base="${TARBALL_URL%/*}"
            [ "$REPO_REF" = "main" ] || printf '%s\n' "${_cand_base}/main"
            [ "$REPO_REF" = "master" ] || printf '%s\n' "${_cand_base}/master"
            ;;
    esac
}

repo_is_placeholder() {
    [ "$REPO_OWNER" = "OWNER" ] || [ "$REPO_NAME" = "REPO" ]
}

# Проверка целостности архива: с SOURCE_SHA256 установка привязана к конкретной
# версии, и подмена архива (прокси, форк, скомпрометированный CDN) не пройдёт.
verify_archive() {
    [ -n "${SOURCE_SHA256:-}" ] || return 0
    command -v sha256sum >/dev/null 2>&1 \
        || fail "Задан SOURCE_SHA256, но утилита sha256sum не найдена (пакет coreutils)."
    _actual="$(sha256sum "$1" | awk '{print $1}')"
    if [ "$_actual" != "$SOURCE_SHA256" ]; then
        warn "Контрольная сумма не совпала: ожидалось $SOURCE_SHA256, получено $_actual"
        return 1
    fi
    ok "Контрольная сумма архива совпала"
    return 0
}


# ---------------------------------------------------------------- оформление

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

STEP=0
FAILURES=0
CHECKS=0

step() {
    STEP=$((STEP + 1))
    printf '\n%s[%d/%d] %s%s\n' "$BOLD$BLUE" "$STEP" "$TOTAL_STEPS" "$1" "$RESET"
}
info()  { printf '      %s\n' "$1"; }
ok()    { printf '      %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '      %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
bad()   { printf '      %s✗%s %s\n' "$RED" "$RESET" "$1"; }
fail()  { printf '\n%s✗ %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }
ask()   { printf '      %s' "$1"; }
section() { printf '\n%s%s%s\n' "$BOLD$BLUE" "$1" "$RESET"; }

# check_ok / check_fail — строки самопроверки в конце установки
check_ok()   { CHECKS=$((CHECKS + 1)); ok "$1"; }
check_fail() { CHECKS=$((CHECKS + 1)); FAILURES=$((FAILURES + 1)); bad "$1"; }
check_note() { printf '      %s•%s %s\n' "$DIM" "$RESET" "$1"; }

# Печатает IP-адреса хоста через пробел. `hostname -I` есть не везде
# (BusyBox и урезанные системы), поэтому есть запасной путь через `ip`.
#: Приводит адрес к каноническому виду: без пробелов и завершающих слешей.
#: Без этого вставленный «https://host:5580/» и путь «/panel/» давали в проверке
#: двойной слеш, а бот потом строил кривой API-адрес.
normalize_url() {
    printf '%s' "$1" | tr -d '[:space:]' | sed -E 's#/*$##'
}

#: Путь webBasePath: без пробелов и слешей по краям (пусто — значит не задан).
normalize_path() {
    printf '%s' "$1" | tr -d '[:space:]' | sed -E 's#^/*##; s#/*$##'
}

#: Отделяет путь от адреса, если его вставили вместе с хостом:
#: «https://host:5580/panel/» → «https://host:5580 panel». Так делает большинство
#: людей (копируют адрес из браузера), а потом панель отвечает 404, потому что
#: путь попадает в адрес API как «/panel/panel/api».
split_panel_address() {
    _address="$1"
    _scheme=""
    case "$_address" in
        *://*) _scheme="${_address%%://*}://" ;;
    esac
    _rest="${_address#"$_scheme"}"
    _host="${_rest%%/*}"
    _path="${_rest#"$_host"}"
    printf '%s %s' "${_scheme}${_host}" "$(normalize_path "$_path")"
}

#: Сворачивает повторные слеши в адресе («https://host//» → «https://host/»),
#: не трогая «://» в схеме. Последняя страховка перед проверкой панели.
collapse_slashes() {
    printf '%s' "$1" | sed -E 's#([^:])//+#\1/#g'
}

detect_ips() {
    local v4=""
    if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
        # Только IPv4: IPv6-литерал без скобок даёт невалидный URL.
        v4="$(hostname -I 2>/dev/null | tr ' ' '\n' \
            | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | tr '\n' ' ' \
            | sed 's/ *$//')"
    fi
    if [ -z "$v4" ] && command -v ip >/dev/null 2>&1; then
        v4="$(ip -4 -o addr show scope global 2>/dev/null \
            | awk '{print $4}' | cut -d/ -f1 | tr '\n' ' ' | sed 's/ *$//')"
    fi
    printf '%s' "$v4"
}

cleanup() {
    [ -n "${TMP_DIR:-}" ] && [ -d "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR"
    return 0
}
trap cleanup EXIT

# ------------------------------------------------------------------ параметры

PREFIX="${PREFIX:-/opt/uisentry}"
SERVICE_USER="${SERVICE_USER:-uisentry}"
# Каталог состояния (база SQLite и файл лога) живёт отдельно от кода:
# код принадлежит root, состояние — сервисному пользователю.
STATE_DIR="${STATE_DIR:-/var/lib/uisentry}"
SERVICE_NAME="uisentry-bot"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/uisentry-ctl}"
MENU_PATH="${MENU_PATH:-/usr/local/bin/uisentry-menu}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
SUDOERS_PATH="/etc/sudoers.d/${SERVICE_NAME}"
START_SERVICE=1
INIT="${INIT:-}"
ASSUME_YES=0
KEEP_CONFIG=1
UNINSTALL=0
KEEP_STATE=0
MENU=0
PRINT_SOURCE=0

usage() {
    cat <<EOF
${BOLD}Установка 3X-UI Sentry${RESET}

  sudo bash install.sh [опции]

Опции:
  --prefix DIR     каталог установки (по умолчанию: /opt/uisentry)
  --user NAME      системный пользователь бота (по умолчанию: uisentry)
  --tarball URL    откуда скачать исходники (tar.gz), минуя репозиторий по умолчанию
  --repo OWNER/NAME  репозиторий на GitHub (краткая форма вместо REPO_OWNER/REPO_NAME)
  --ref BRANCH     ветка или тег репозитория (по умолчанию main)
  --print-source   показать, откуда будут взяты исходники, и выйти
  --sha256 HASH    сверить скачанный архив с контрольной суммой (привязка к версии)
  --menu           меню: установить/обновить, удалить, справка
                   (после установки для правок настроек: sudo uisentry-menu)
  --uninstall      удалить бота целиком: служба, sudoers, обёртка, меню, код,
                   состояние (/var/lib/uisentry) и системный пользователь
  --purge          то же самое (синоним, оставлен для совместимости)
  --keep-state     не трогать состояние и пользователя
  --yes            без вопросов: берём значения из окружения или готового .env
  --force-config   перезаписать существующий .env значениями по умолчанию
  --no-start       установить, но не запускать сервис
  --init TYPE      менеджер служб: systemd, openrc или sysv (по умолчанию — авто)
  --help           эта справка

Переменные окружения (для --yes и unattended-установки):
  REPO, REPO_OWNER, REPO_NAME, REPO_REF, REPO_URL, TARBALL_URL, GIT_URL,
  SOURCE_SHA256, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, TELEGRAM_API_URL,
  PANEL_URL, PANEL_BASE_PATH, PANEL_API_TOKEN, SERVICES

${BOLD}English${RESET}
  Install: bash <(curl -Ls .../main/install.sh)
  Options:  --prefix DIR · --user NAME · --repo owner/name · --ref BRANCH
            --tarball URL · --sha256 HASH · --init systemd|openrc|sysv
            --print-source · --menu · --uninstall · --purge · --no-start
            --force-config · --yes · --help
  Docs:     README.en.md (English) · README.md (Русский)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)       PREFIX="${2:?--prefix требует путь}"; shift 2 ;;
        --user)         SERVICE_USER="${2:?--user требует имя}"; shift 2 ;;
        --tarball)      TARBALL_URL="${2:?--tarball требует URL}"; shift 2 ;;
        --repo)         REPO_SLUG="${2:?--repo требует owner/name}"; shift 2 ;;
        --ref)          REPO_REF="${2:?--ref требует ветку или тег}"; shift 2 ;;
        --print-source) PRINT_SOURCE=1; shift ;;
        --sha256)       SOURCE_SHA256="${2:?--sha256 требует хеш}"; shift 2 ;;
        --menu|-m)      MENU=1; shift ;;
        --uninstall)    UNINSTALL=1; shift ;;
        --purge)        UNINSTALL=1; shift ;;   # синоним: удаление и так полное
        --keep-state)   KEEP_STATE=1; shift ;;
        --yes|-y)       ASSUME_YES=1; shift ;;
        --force-config) KEEP_CONFIG=0; shift ;;
        --no-start)     START_SERVICE=0; shift ;;
        --init)         INIT="${2:?--init требует systemd|openrc|sysv}"; shift 2 ;;
        --help|-h)      usage; exit 0 ;;
        *) fail "Неизвестная опция: $1 (см. --help)" ;;
    esac
done

# --- защита от опасного --prefix/STATE_DIR ---------------------------------
# Без этого `--prefix /etc` приводил к chmod -R по системному каталогу, а
# `--uninstall` — к rm -rf. Разрешаем только предсказуемые места установки.
canon_path() { readlink -m "$1" 2>/dev/null || printf '%s' "$1"; }
is_safe_prefix() {
    case "$1" in
        /opt/?*|/usr/local/?*|/srv/?*|/var/opt/?*|/home/?*/?*) return 0 ;;
        *) return 1 ;;
    esac
}
is_safe_state() {
    case "$1" in
        /var/lib/?*|/var/opt/?*|/opt/?*|/srv/?*|/home/?*/?*) return 0 ;;
        *) return 1 ;;
    esac
}
PREFIX="$(canon_path "$PREFIX")"
STATE_DIR="$(canon_path "$STATE_DIR")"
if [ "${ALLOW_UNSAFE_PREFIX:-0}" != "1" ]; then
    is_safe_prefix "$PREFIX" || fail "Небезопасный --prefix: $PREFIX
  Разрешено: /opt/..., /usr/local/..., /srv/..., /var/opt/..., /home/<user>/...
  Осознанно разрешить: ALLOW_UNSAFE_PREFIX=1"
    is_safe_state "$STATE_DIR" || fail "Небезопасный STATE_DIR: $STATE_DIR
  Разрешено: /var/lib/..., /var/opt/..., /opt/..., /srv/..., /home/<user>/..."
fi

resolve_repo

# Диагностика без правки системы: удобно проверять адреса и тесты.
if [ "$PRINT_SOURCE" -eq 1 ]; then
    printf 'REPO_URL=%s\n' "$REPO_URL"
    printf 'REPO_REF=%s\n' "$REPO_REF"
    printf 'TARBALL_URL=%s\n' "$TARBALL_URL"
    printf 'GIT_URL=%s\n' "${GIT_URL:-$REPO_URL}"
    printf 'CANDIDATES=%s\n' "$(source_candidates | tr '\n' ' ')"
    if repo_is_placeholder; then
        printf 'placeholder=yes\n'
    else
        printf 'placeholder=no\n'
    fi
    exit 0
fi

# -------------------------------------------------------- удаление / меню

uninstall() {
    [ "$(id -u)" -eq 0 ] || fail "Запускать от root: sudo bash install.sh --uninstall"

    section "Удаление бота $SERVICE_NAME"
    if [ "$ASSUME_YES" -eq 0 ] && [ "$INTERACTIVE" -eq 1 ]; then
        ask "Удалить бота целиком (код, база, пользователь)? [y/N]"
        read -r answer <&$READ_FD || answer=""
        case "$answer" in
            [yYдД]*) ;;
            *) info "Отменено, ничего не тронуто"; return 0 ;;
        esac
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$UNIT_PATH" \
        "/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}.service" \
        "/etc/systemd/system/uisentry-apt-update.service" \
        "/etc/systemd/system/uisentry-apt-upgrade.service" \
        "/etc/systemd/system/uisentry-apt-phased.service" \
        "/etc/systemd/system/uisentry-release-upgrade.service"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi

    # Служба могла быть поставлена init-скриптом (OpenRC/SysV) — убираем и её.
    INIT_SCRIPT="/etc/init.d/${SERVICE_NAME}"
    if [ -f "$INIT_SCRIPT" ]; then
        "$INIT_SCRIPT" stop >/dev/null 2>&1 || true
        rm -f "$INIT_SCRIPT"
        if command -v rc-update >/dev/null 2>&1; then
            rc-update del "$SERVICE_NAME" default >/dev/null 2>&1 || true
        fi
        if command -v update-rc.d >/dev/null 2>&1; then
            update-rc.d -f "$SERVICE_NAME" remove >/dev/null 2>&1 || true
        fi
        if command -v chkconfig >/dev/null 2>&1; then
            chkconfig --del "$SERVICE_NAME" >/dev/null 2>&1 || true
        fi
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    rm -f "$SUDOERS_PATH" "$WRAPPER_PATH" "$MENU_PATH"
    rm -rf "$PREFIX"

    if [ "$KEEP_STATE" -eq 1 ]; then
        warn "По --keep-state оставлены база и пользователь: $STATE_DIR, $SERVICE_USER"
    else
        # По умолчанию вычищаем под ноль: база и логи больше не нужны.
        rm -rf "$STATE_DIR"
        if id "$SERVICE_USER" >/dev/null 2>&1; then
            userdel "$SERVICE_USER" >/dev/null 2>&1 \
                || warn "Пользователя $SERVICE_USER удалить не удалось"
        fi
    fi

    printf '\n%s%sБот удалён.%s\n' "$BOLD" "$GREEN" "$RESET"
}

interactive_menu() {
    if [ "$INTERACTIVE" -eq 0 ]; then
        fail "--menu требует терминал. Без него: --yes (установка) или --uninstall."
    fi
    printf '\n%s3X-UI Sentry — установка%s\n\n' "$BOLD" "$RESET"
    printf '  1) Установить или обновить бота\n'
    printf '  2) Удалить бота целиком (код, база, пользователь)\n'
    printf '  3) Удалить бота, оставив базу и пользователя\n'
    printf '  h) Справка\n'
    printf '  0) Выход\n\n'
    ask "Выберите пункт [1]: "
    read -r choice <&$READ_FD || choice=""
    case "${choice:-1}" in
        1) return 0 ;;
        2) UNINSTALL=1 ;;
        3) UNINSTALL=1; KEEP_STATE=1 ;;
        h|H|help|--help) usage; exit 0 ;;
        0|q|Q) exit 0 ;;
        *) fail "Не понял выбор: $choice" ;;
    esac
}

if [ "$MENU" -eq 1 ]; then
    interactive_menu
fi

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall
    exit 0
fi

# Шаги: проверка системы, пакеты, пользователь, копирование, venv, конфиг,
# sudo-обёртка, systemd-юнит, запуск, самопроверка, итог — 11 шагов.
# Ветки «Запускаем бота» и «Запуск пропущен» — это один и тот же шаг,
# поэтому счётчик не зависит от --no-start.
TOTAL_STEPS=11

# --------------------------------------------------------------- проверки

step "Проверяем систему"

# Страховка: если репозиторий снова станет заглушкой OWNER (или REPO), загрузка
# из интернета заведомо не сработает — предупреждаем сразу, а не в конце.
if repo_is_placeholder; then
    warn "REPO_OWNER=OWNER — это заглушка: загрузка из интернета не сработает."
    warn "Укажите свой репозиторий: --repo ваш-логин/uisentry-bot (или REPO_OWNER/REPO_NAME)."
fi

[ "$(id -u)" -eq 0 ] || fail "Запускать от root: sudo bash install.sh"

# --- менеджер служб: systemd, OpenRC или SysV ---
# Бот работает и там, где systemd нет: Alpine/Gentoo (OpenRC) и старые SysVinit.
# Права и ограничения задаёт sudoers-обёртка, а не менеджер служб, поэтому
# функциональность сохраняется; теряется только systemd-изоляция юнита.
if [ -z "$INIT" ]; then
    if [ -d /run/systemd/system ]; then
        INIT=systemd
    elif command -v rc-service >/dev/null 2>&1; then
        INIT=openrc
    elif command -v service >/dev/null 2>&1; then
        INIT=sysv
    else
        fail "Не найден менеджер служб: ни systemd, ни OpenRC, ни SysVinit."
    fi
fi
case "$INIT" in
    systemd)
        command -v systemctl >/dev/null 2>&1 || fail "INIT=systemd, но systemctl не найден"
        ok "Менеджер служб: systemd"
        ;;
    openrc)
        command -v rc-service >/dev/null 2>&1 || fail "INIT=openrc, но rc-service не найден"
        ok "Менеджер служб: OpenRC (Alpine/Gentoo)"
        ;;
    sysv)
        command -v service >/dev/null 2>&1 || fail "INIT=sysv, но service не найден"
        ok "Менеджер служб: SysVinit"
        ;;
    *)
        fail "INIT=$INIT не поддерживается (systemd, openrc, sysv)"
        ;;
esac
SYSTEMD_ACTIVE=0
[ "$INIT" = systemd ] && SYSTEMD_ACTIVE=1
INIT_SCRIPT="/etc/init.d/${SERVICE_NAME}"

command -v sudo >/dev/null 2>&1 \
    || fail "Нужен sudo: через него бот выполняет привилегированные операции."

# --- дистрибутив и пакетный менеджер ---
PKG_INSTALL=""
PKG_UPDATE=""
VENV_PKGS="python3-venv"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091  # /etc/os-release — штатный файл дистрибутива
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_NAME="${PRETTY_NAME:-$OS_ID}"
else
    OS_ID="unknown"
    OS_NAME="неизвестная ОС"
    warn "Не удалось прочитать /etc/os-release"
fi

case "$OS_ID" in
    debian|ubuntu|linuxmint|pop|raspbian|kali|neon|elementary|zorin|devuan)
        PKG_INSTALL="apt-get install -y"
        PKG_UPDATE="apt-get update -qq"
        VENV_PKGS="python3-venv"
        ;;
    fedora)
        PKG_INSTALL="dnf install -y"
        VENV_PKGS="python3"
        ;;
    rhel|centos|rocky|almalinux|ol|oracle|scientific)
        if command -v dnf >/dev/null 2>&1; then
            PKG_INSTALL="dnf install -y"
        else
            PKG_INSTALL="yum install -y"
        fi
        VENV_PKGS="python3"
        ;;
    opensuse*|sles|sled)
        PKG_INSTALL="zypper --non-interactive install"
        PKG_UPDATE="zypper --non-interactive refresh"
        VENV_PKGS="python3"
        ;;
    arch|manjaro|endeavouros|garuda)
        PKG_INSTALL="pacman -S --noconfirm --needed"
        VENV_PKGS="python"
        ;;
    *)
        # Дистрибутив не из списка — выбираем пакетный менеджер по факту наличия.
        for pair in \
            "apt-get:apt-get install -y:python3-venv" \
            "dnf:dnf install -y:python3" \
            "yum:yum install -y:python3" \
            "zypper:zypper --non-interactive install:python3" \
            "pacman:pacman -S --noconfirm --needed:python"
        do
            _mgr="${pair%%:*}"
            _rest="${pair#*:}"
            _cmd="${_rest%%:*}"
            _pkgs="${_rest#*:}"
            if command -v "$_mgr" >/dev/null 2>&1; then
                PKG_INSTALL="$_cmd"
                VENV_PKGS="$_pkgs"
                break
            fi
        done
        warn "Дистрибутив $OS_NAME не из проверенных — полагаюсь на пакетный менеджер"
        ;;
esac
ok "ОС: $OS_NAME"

# --- Python 3.10+: подходит любой интерпретатор из PATH ---
MIN_PY="3.10"
PYTHON_BIN=""
# shellcheck disable=SC2086  # ${PYTHON:-} может быть пустым — это нормально
for candidate in ${PYTHON:-} python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (${MIN_PY//./, }) else 1)" 2>/dev/null; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    fail "Нужен Python ${MIN_PY} или новее. Установите его, например:
      Debian/Ubuntu:  sudo apt-get install python3 python3-venv
      RHEL 9:         sudo dnf install python3.11
      Fedora/Arch:    sudo dnf install python3   |   sudo pacman -S python
    и повторите установку."
fi
ok "Python $("$PYTHON_BIN" -c 'import platform; print(platform.python_version())') → $PYTHON_BIN"

# ---------------------------------------------------------------- исходники

SOURCE_DIR=""
for candidate in "$PWD" "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")"; do
    if [ -f "$candidate/uisentry/app.py" ]; then
        SOURCE_DIR="$(readlink -f "$candidate")"
        break
    fi
done

if [ -z "$SOURCE_DIR" ]; then
    TMP_DIR="$(mktemp -d)"

    # 1) tar.gz: основной способ — работает без git и за брандмауэрами.
    if command -v curl >/dev/null 2>&1 && [ -n "$TARBALL_URL" ]; then
        while IFS= read -r _candidate; do
            [ -n "$_candidate" ] || continue
            # У codeload.github.com кэшируется архив ветки: сразу после push по
            # refs/heads/* может прилететь прошлый снимок. Параметр-«метка
            # времени» меняет ключ кэша, поэтому версия всегда свежая.
            _fetch_url="$_candidate"
            case "$_fetch_url" in
                *codeload.github.com/*)
                    _fetch_url="${_fetch_url}?t=$(date +%s)"
                    ;;
            esac
            info "Скачиваю исходники: $_candidate"
            if curl -fsSL --max-time 120 "$_fetch_url" -o "$TMP_DIR/src.tar.gz" \
                && verify_archive "$TMP_DIR/src.tar.gz" \
                && tar -xzf "$TMP_DIR/src.tar.gz" -C "$TMP_DIR" 2>/dev/null; then
                SOURCE_DIR="$(find "$TMP_DIR" -maxdepth 2 -name uisentry \
                    -type d -exec dirname {} \; | head -1)"
                if [ -n "$SOURCE_DIR" ]; then
                    break
                fi
            fi
        done <<EOF
$(source_candidates)
EOF
    fi

    # Если задана контрольная сумма, непроверенный git-клон недопустим:
    # иначе --sha256 обходится одним недоступным архивом.
    if [ -z "$SOURCE_DIR" ] && [ -n "$SOURCE_SHA256" ]; then
        fail "Задан --sha256, но ни один архив не скачался или сумма не совпала.
  Git-fallback отключён: непроверенный код ставить нельзя.
  Проверьте доступ к архиву или снимите --sha256."
    fi

    # 2) git clone: запасной путь, когда тарбол недоступен и сумма не задана.
    if [ -z "$SOURCE_DIR" ] && [ -z "$SOURCE_SHA256" ] \
        && command -v git >/dev/null 2>&1; then
        GIT_URL="${GIT_URL:-$REPO_URL}"
        info "Тарбол не подошёл — пробую git clone $GIT_URL ($REPO_REF)"
        if git clone --depth 1 --branch "$REPO_REF" "$GIT_URL" "$TMP_DIR/repo" \
            >/dev/null 2>&1 && [ -d "$TMP_DIR/repo/uisentry" ]; then
            SOURCE_DIR="$TMP_DIR/repo"
        fi
    fi

    if [ -z "$SOURCE_DIR" ] || [ ! -d "$SOURCE_DIR" ]; then
        if repo_is_placeholder; then
            fail "Исходники не найдены, а репозиторий — заглушка OWNER.
  Укажите свой репозиторий GitHub, например:
      sudo bash install.sh --repo ваш-логин/uisentry-bot
  (одной строкой:  sudo bash <(curl -Ls https://raw.githubusercontent.com/ваш-логин/uisentry-bot/main/install.sh))"
        fi
        fail "Не удалось получить исходники. Варианты:
      • свой репозиторий:  --repo owner/name  [--ref ветка]
      • прямой архив:      --tarball https://host/uisentry-bot.tar.gz
      • git:               GIT_URL=https://github.com/вас/repo.git
      • или скачайте репозиторий вручную и запустите install.sh из его каталога"
    fi
    ok "Исходники получены во временном каталоге"
fi
ok "Исходники: $SOURCE_DIR"

UISENTRY_PRESENT=0
if systemctl is-active --quiet x-ui 2>/dev/null; then
    ok "Панель 3X-UI запущена (юнит x-ui)"
    UISENTRY_PRESENT=1
elif systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service'; then
    warn "Юнит x-ui есть, но панель сейчас не запущена"
    UISENTRY_PRESENT=1
else
    warn "Юнит x-ui не найден — бот запустится, но следить за панелью не сможет"
fi

# --------------------------------------------------------------- пакеты

step "Ставим системные зависимости"

if [ -n "$PKG_UPDATE" ]; then
    info "обновляем индекс пакетов…"
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive $PKG_UPDATE >/dev/null 2>&1 \
        || warn "обновление индекса пакетов не отработало, продолжаем"
fi

MISSING=""
if ! "$PYTHON_BIN" -c 'import venv' >/dev/null 2>&1 \
    || ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
    MISSING="$VENV_PKGS"
fi

if [ -n "$MISSING" ]; then
    if [ -n "$PKG_INSTALL" ]; then
        info "устанавливаем:$MISSING"
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive $PKG_INSTALL $MISSING >/dev/null 2>&1 \
            || fail "Не удалось поставить:$MISSING"
        ok "Установлено:$MISSING"
    else
        warn "Не хватает:$MISSING — поставьте вручную: sudo $PKG_INSTALL$MISSING"
    fi
fi
ok "Системные зависимости на месте"

# --------------------------------------------------------- пользователь

step "Создаём системного пользователя $SERVICE_USER"

# Оболочка «без логина» лежит в разных местах в зависимости от дистрибутива:
# /usr/sbin/nologin (Debian/RHEL/SUSE), /usr/bin/nologin (Arch), /bin/false.
NOLOGIN_SHELL=""
for candidate_shell in /usr/sbin/nologin /sbin/nologin /usr/bin/nologin \
    /bin/nologin /bin/false; do
    if [ -x "$candidate_shell" ]; then
        NOLOGIN_SHELL="$candidate_shell"
        break
    fi
done
NOLOGIN_SHELL="${NOLOGIN_SHELL:-/bin/false}"

if id "$SERVICE_USER" >/dev/null 2>&1; then
    ok "Пользователь $SERVICE_USER уже есть"
elif [ "$SERVICE_USER" = "root" ]; then
    fail "SERVICE_USER=root — бот получил бы полные права. Выберите другого пользователя."
else
    useradd --system --no-create-home --home-dir "$PREFIX" \
        --shell "$NOLOGIN_SHELL" --comment "3X-UI monitor bot" "$SERVICE_USER" \
        || fail "Не удалось создать пользователя $SERVICE_USER"
    ok "Создан системный пользователь $SERVICE_USER"
fi
# Группа может не совпадать с именем пользователя (существующий пользователь
# с другой основной группой): права на .env и unit Group= берём из неё.
SERVICE_GROUP="$(id -gn "$SERVICE_USER" 2>/dev/null || printf '%s' "$SERVICE_USER")"

# ------------------------------------------------- миграция старой установки

migrate_legacy_install() {
    # Старые имена (xui-monitor-bot). Переносим .env и базу, затем убираем
    # старую службу/юниты/sudoers/обёртки, чтобы не осталось двух ботов.
    local old_prefix="/opt/xui-monitor-bot"
    local old_state="/var/lib/xui-monitor"
    local old_user="xui-monitor"
    local old_service="xui-monitor-bot"
    [ -d "$old_prefix" ] || id "$old_user" >/dev/null 2>&1 || return 0
    # Разрешаем и новый путь, и старый (если пользователь по привычке передал
    # --prefix /opt/xui-monitor-bot): в обоих случаях .env и база переносятся.
    if [ "$PREFIX" != "/opt/uisentry" ] && [ "$PREFIX" != "$old_prefix" ]; then
        warn "Найдена старая установка $old_prefix, но путь $PREFIX нестандартный — миграцию пропускаю"
        return 0
    fi
    if [ "$PREFIX" = "$old_prefix" ]; then
        warn "Установка остаётся в старом каталоге $old_prefix."
        warn "Для полного переименования запустите установщик без --prefix: /opt/uisentry"
    else
        info "Найдена старая установка $old_prefix — переношу конфиг и состояние"
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$old_service" >/dev/null 2>&1 || true
    fi
    mkdir -p "$PREFIX" "$STATE_DIR"
    if [ -f "$old_prefix/.env" ]; then
        [ -f "$PREFIX/.env" ] || cp -a "$old_prefix/.env" "$PREFIX/.env"
        # Пути состояния и обёртки — уже новые.
        sed -i "s#^STATE_PATH=.*#STATE_PATH=$STATE_DIR/state.db#; \
                s#^LOG_PATH=.*#LOG_PATH=$STATE_DIR/bot.log#; \
                s#^SUDO_WRAPPER=.*#SUDO_WRAPPER=$WRAPPER_PATH#" "$PREFIX/.env" 2>/dev/null || true
    fi
    if [ -f "$old_state/state.db" ] && [ ! -f "$STATE_DIR/state.db" ]; then
        cp -a "$old_state"/state.db* "$STATE_DIR/" 2>/dev/null || true
        cp -a "$old_state"/bot.log* "$STATE_DIR/" 2>/dev/null || true
    fi
    chown -R "$SERVICE_USER":"${SERVICE_GROUP:-$SERVICE_USER}" "$STATE_DIR" 2>/dev/null || true
    if [ -f "$PREFIX/.env" ]; then
        chown root:"${SERVICE_GROUP:-$SERVICE_USER}" "$PREFIX/.env" 2>/dev/null || true
        chmod 640 "$PREFIX/.env"
    fi
    rm -f "/etc/systemd/system/${old_service}.service" \
        /etc/systemd/system/xui-monitor-apt-update.service \
        /etc/systemd/system/xui-monitor-apt-upgrade.service \
        /etc/systemd/system/xui-monitor-apt-phased.service \
        /etc/systemd/system/xui-monitor-release-upgrade.service \
        "/etc/sudoers.d/${old_service}" \
        "/etc/sudoers.d/${old_service}.invalid" \
        /usr/local/bin/xui-ctl /usr/local/bin/xui-menu \
        "/etc/init.d/${old_service}"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if [ "$old_prefix" != "$PREFIX" ] && [ -f "$PREFIX/.env" ]; then
        rm -rf "$old_prefix"
    fi
    [ "$old_state" != "$STATE_DIR" ] && rm -rf "$old_state"
    if id "$old_user" >/dev/null 2>&1; then
        userdel "$old_user" >/dev/null 2>&1 || true
    fi
    ok "Старая установка перенесена и удалена"
}

migrate_legacy_install

step "Настраиваем бота (.env)"

ENV_FILE="$PREFIX/.env"

# Мастер спрашивает раньше копирования кода, поэтому каталог установки нужно
# создать прямо здесь: иначе .env писать некуда (на чистой машине /opt пуст).
mkdir -p "$PREFIX" || fail "Не удалось создать каталог $PREFIX"
chmod 755 "$PREFIX"

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -1; }

set_env() {
    grep -vE "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    # umask установщика — 077: после mv .env стал бы root:root 600 и сервис
    # (User=uisentry) не смог бы его прочитать при старте.
    chown root:"${SERVICE_GROUP:-$SERVICE_USER}" "$ENV_FILE" 2>/dev/null || true
    chmod 640 "$ENV_FILE" 2>/dev/null || true
}

question() {
    # question ПЕРЕМЕННАЯ "подсказка" "значение по умолчанию"
    local __var="$1" __prompt="$2" __default="$3" __answer=""
    if [ "$INTERACTIVE" -eq 1 ] && [ "$ASSUME_YES" -eq 0 ]; then
        ask "${BOLD}${__prompt}${RESET}"
        if [ -n "$__default" ]; then
            printf ' %s[%s]%s ' "$DIM" "$__default" "$RESET"
        else
            printf ' '
        fi
        read -r __answer <&$READ_FD || __answer=""
    fi
    printf -v "$__var" '%s' "${__answer:-$__default}"
}

panel_settings() {
    # Печатает порт и webBasePath панели в формате "порт base_path".
    command -v x-ui >/dev/null 2>&1 || { printf '2053 \n'; return 0; }
    x-ui settings 2>/dev/null | "$PYTHON_BIN" -c '
import json, re, sys

raw = sys.stdin.read()
data = {}
try:
    data = json.loads(raw)
except ValueError:
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().strip("\"'\''")
        value = value.strip().strip("\"'\'',")
        if key:
            data[key] = value

port = data.get("webPort") or data.get("webListen") or ""
match = re.search(r"[0-9]+", str(port))
port = match.group(0) if match else "2053"
base = str(data.get("webBasePath") or "").strip("/")
print(port, base)
'
}

panel_api_code() {
    # HTTP-код API панели без токена. В 3X-UI правильный путь отвечает 401
    # (нужен токен), неверный — 404. Так webBasePath проверяется до токена.
    curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
        -H 'X-Requested-With: XMLHttpRequest' \
        "$1/panel/api/server/status" 2>/dev/null || echo 000
}

resolve_panel_base() {
    # $1 — адрес панели без пути, $2 — предполагаемый webBasePath.
    # Печатает подтверждённый webBasePath (пусто, если путь не нужен).
    # В 3X-UI адрес в браузере всегда оканчивается на /panel — это не
    # webBasePath, и «/panel» в поле настроек даёт лишний сегмент (404).
    local url="$1" guess="$2" code
    if [ -n "$guess" ]; then
        code="$(panel_api_code "$(collapse_slashes "$url/$guess")")"
        case "$code" in 200|301|302|401|403) printf '%s\n' "$guess"; return 0 ;; esac
    fi
    code="$(panel_api_code "$url")"
    case "$code" in 200|301|302|401|403) printf '\n'; return 0 ;; esac
    printf '%s\n' "$guess"
}

detect_panel_token() {
    # x-ui сам создаёт токен и печатает его. Новое имя токена заменяет старое,
    # поэтому вызываем функцию только осознанно.
    command -v x-ui >/dev/null 2>&1 || return 1
    x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync 2>/dev/null \
        | "$PYTHON_BIN" -c '
import re, sys

text = sys.stdin.read()
# Сначала значение рядом со словом token («Token: <значение>»), иначе — любое
# длинное слово, кроме названий областей доступа: x-ui печатает рядом список
# scope (например «update-all-geofiles»), и раньше в .env попадало именно
# название области, а не токен.
labelled = re.search(r"(?im)^[^:\n]*token[^:\n]*[:=]\s*([A-Za-z0-9_-]{16,})\s*$", text)
if labelled:
    print(labelled.group(1))
    raise SystemExit(0)

deny = ("successful", "tokenname", "tokenscope", "geofile", "scope", "sync", "admin", "monitor")
found = [
    item
    for item in re.findall(r"[A-Za-z0-9_-]{16,}", text)
    if not any(bad in item.lower() for bad in deny)
]
print(found[0] if found else "")
'
}

# .env может остаться после прерванного запуска: тогда в нём заглушки или пустые
# значения. В этом случае не требуем править файл руками, а дозаполняем мастером
# (существующие значения подставляются как значения по умолчанию).
CONFIG_COMPLETE=0
if [ -f "$ENV_FILE" ] && [ "$KEEP_CONFIG" -eq 1 ]; then
    CONFIG_COMPLETE=1
    for _key in TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_IDS PANEL_URL PANEL_API_TOKEN; do
        _value="$(env_get "$_key")"
        case "$_value" in
            "" | *ExampleTokenReplaceMe*) CONFIG_COMPLETE=0 ;;
        esac
    done
fi

if [ "$CONFIG_COMPLETE" -eq 1 ]; then
    ok "Файл .env уже есть — сохраняю ваши токены"
    info "Изменить: sudo uisentry-menu (или nano $ENV_FILE)"
else
    if [ -f "$ENV_FILE" ] && [ "$KEEP_CONFIG" -eq 1 ]; then
        warn "В $ENV_FILE не всё заполнено — дозаполняю через мастер"
    fi
    [ -f "$ENV_FILE" ] || { cp "$SOURCE_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"; }

    read -r DETECTED_PORT DETECTED_BASE <<<"$(panel_settings)"
    HOST="127.0.0.1"
    IP_LIST="$(detect_ips)"
    FIRST_IP="${IP_LIST%% *}"
    if [ -n "$FIRST_IP" ] && [ "$FIRST_IP" != "127.0.0.1" ]; then
        HOST="$FIRST_IP"
    fi
    if [ -n "$IP_LIST" ]; then
        info "IP-адреса этого сервера: $IP_LIST"
        info "Можно вписать любой из них — в ответе укажите адрес, который слышит ваш телефон/VPN."
    fi
    SUGGESTED_URL="${PANEL_URL:-http://${HOST}:${DETECTED_PORT:-2053}}"

    question TG_TOKEN "Токен Telegram-бота от @BotFather" \
        "$(printf '%s' "${TELEGRAM_BOT_TOKEN:-$(env_get TELEGRAM_BOT_TOKEN)}")"
    [ -n "$TG_TOKEN" ] || fail "Без токена бота запускать бессмысленно. Создайте бота у @BotFather и вставьте токен."
    case "$TG_TOKEN" in
        *:*) ;;
        *) fail "Токен @BotFather выглядит как 123456:AA... — проверьте вставку" ;;
    esac

    # Первичная сверка с Telegram: ошибка токена видна сразу, а не после
    # всей установки. При отсутствии сети просто предупреждаем.
    TG_API_VALUE="${TELEGRAM_API_URL:-https://api.telegram.org}"
    TG_PROBE="$(curl -sS --max-time 15 "${TG_API_VALUE%/}/bot${TG_TOKEN}/getMe" 2>/dev/null || true)"
    if printf '%s' "$TG_PROBE" | grep -q '"ok":true'; then
        TG_USERNAME="$(printf '%s' "$TG_PROBE" | sed -E 's/.*"username":"([^"]+)".*/\1/')"
        ok "Telegram принял токен: бот @$TG_USERNAME"
    elif printf '%s' "$TG_PROBE" | grep -q '"ok":false'; then
        case "$TG_TOKEN" in
            *ExampleTokenReplaceMe*)
                fail "TELEGRAM_BOT_TOKEN — заглушка. Откройте @BotFather -> /newbot и вставьте настоящий токен." ;;
            *)
                fail "Telegram отклонил токен (${TG_API_VALUE}). Скопируйте его заново из @BotFather." ;;
        esac
    else
        warn "Telegram не отвечает — проверим токен в самопроверке в конце"
    fi

    question ADMIN_IDS "Ваш Telegram ID (у @userinfobot), можно несколько через запятую" \
        "$(printf '%s' "${TELEGRAM_ADMIN_IDS:-$(env_get TELEGRAM_ADMIN_IDS)}")"
    [ -n "$ADMIN_IDS" ] || fail "Нужен хотя бы один Telegram ID администратора"
    printf '%s' "$ADMIN_IDS" | grep -qE '^[0-9]+(,[0-9]+)*$' \
        || fail "ADMIN_IDS должны быть числами через запятую, получено: $ADMIN_IDS"

    question PANEL_URL_VALUE "Адрес панели 3X-UI (хост и порт)" "$SUGGESTED_URL"
    PANEL_URL_VALUE="$(normalize_url "$PANEL_URL_VALUE")"
    # Адрес можно вставлять прямо из браузера, вместе с путём — отделяем путь.
    read -r PANEL_URL_VALUE URL_PATH_SUGGEST <<<"$(split_panel_address "$PANEL_URL_VALUE")"
    if [ -n "$URL_PATH_SUGGEST" ]; then
        info "В адресе был путь — переношу его в webBasePath: /$URL_PATH_SUGGEST"
    fi
    case "$PANEL_URL_VALUE" in
        http://*|https://*) ;;
        *) PANEL_URL_VALUE="$(normalize_url "$SUGGESTED_URL")"
           warn "Некорректный адрес, беру автоопределённый: $PANEL_URL_VALUE" ;;
    esac

    question PANEL_BASE "webBasePath — поле «URL панели» в настройках 3X-UI (обычно /, тогда просто Enter). Это НЕ путь /panel из адресной строки" \
        "${PANEL_BASE_PATH:-${URL_PATH_SUGGEST:-${DETECTED_BASE}}}"
    PANEL_BASE="$(normalize_path "$PANEL_BASE")"
    # Если путь был в адресе, а поле оставили пустым — берём его оттуда.
    if [ -z "$PANEL_BASE" ] && [ -n "$URL_PATH_SUGGEST" ]; then
        PANEL_BASE="$URL_PATH_SUGGEST"
    fi
    case "$PANEL_BASE" in
        *://*)
            # Частая ошибка: в webBasePath вставили полный адрес панели.
            warn "В webBasePath вставлен адрес — использую его как адрес панели, путь очищаю"
            PANEL_URL_VALUE="$(normalize_url "$PANEL_BASE")"
            PANEL_BASE=""
            ;;
    esac

    # Ровно один слеш между адресом и путём: иначе панель отвечает 404.
    PROBE_URL="$PANEL_URL_VALUE"
    [ -n "$PANEL_BASE" ] && PROBE_URL="$PANEL_URL_VALUE/$PANEL_BASE"
    PROBE_URL="$(collapse_slashes "$PROBE_URL")"
    PANEL_URL_VALUE="$(collapse_slashes "$PANEL_URL_VALUE")"

    # Проверяем webBasePath и при необходимости исправляем, не дожидаясь токена.
    _entered_base="$PANEL_BASE"
    PANEL_BASE="$(resolve_panel_base "$PANEL_URL_VALUE" "$PANEL_BASE")"
    if [ "$PANEL_BASE" != "$_entered_base" ]; then
        warn "webBasePath «${_entered_base:-/}» не подтвердился — беру «${PANEL_BASE:-/}»"
        info "В 3X-UI интерфейс всегда на .../panel, а webBasePath задаётся отдельным"
        info "полем «URL панели» (обычно /). Путь /panel в webBasePath даёт лишний сегмент."
    fi
    PROBE_URL="$PANEL_URL_VALUE"
    [ -n "$PANEL_BASE" ] && PROBE_URL="$PANEL_URL_VALUE/$PANEL_BASE"
    PROBE_URL="$(collapse_slashes "$PROBE_URL")"
    if curl -sS --max-time 5 -o /dev/null "$PROBE_URL/" 2>/dev/null; then
        ok "Панель отвечает: $PROBE_URL"
    else
        warn "Панель не ответила на $PROBE_URL/ — если адрес верный, это заметит самопроверка"
    fi

    info "3X-UI v3: «Настройки панели → Учётная запись → API-токен» (scope node-sync"
    info "или admin) — значение показывается один раз, копируйте сразу."
    info "3X-UI 2.x: «Настройки → API Tokens» либо sudo x-ui setting -getApiToken true"
    info "-tokenName monitor-bot -tokenScope node-sync (в v3 эта команда больше не создаёт токены)."
    question PANEL_TOKEN "API-токен панели" "${PANEL_API_TOKEN:-$(env_get PANEL_API_TOKEN)}"
    if [ -z "$PANEL_TOKEN" ]; then
        if [ "$UISENTRY_PRESENT" -eq 1 ] && { [ "$ASSUME_YES" -eq 0 ] || [ -n "${ALLOW_TOKEN_GEN:-}" ]; }; then
            warn "Выпускаю токен monitor-bot (scope node-sync) в панели."
            warn "Внимание: если такой токен уже был, старый перестанет работать."
            PANEL_TOKEN="$(detect_panel_token || true)"
        fi
        [ -n "$PANEL_TOKEN" ] || fail "Нужен API-токен панели:
      3X-UI v3: «Настройки панели → Учётная запись → API-токен» → создать со scope
      node-sync (или admin) и скопировать значение сразу — панель показывает его
      только один раз. То же доступно по API: POST /panel/api/setting/apiTokens/create
      (name, scope, expiresAt; нужен уже существующий admin-токен).
      3X-UI 2.x: «Настройки → API Tokens», либо из терминала:
      sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
   Полученное значение впишите в PANEL_API_TOKEN."
    fi

    # Сразу проверяем токен: без этого в .env попадал мусор (например, название
    # области доступа), и бот потом ругался «токен не принят».
    if [ -n "$PANEL_TOKEN" ] && [ -n "$PROBE_URL" ]; then
        _tok_tmp="$(mktemp)"
        _token_http="$(curl -sS --max-time 5 -o "$_tok_tmp" -w '%{http_code}' \
            -H "Authorization: Bearer $PANEL_TOKEN" \
            "${PROBE_URL}/panel/api/server/status" 2>/dev/null || echo 000)"
        _token_reply="$(cat "$_tok_tmp" 2>/dev/null || true)"
        rm -f "$_tok_tmp"
        if printf '%s' "$_token_reply" | grep -q '"success":true'; then
            ok "Токен панели принят"
        elif printf '%s' "$_token_reply" | grep -q '"success":false' || [ "$_token_http" = "401" ] || [ "$_token_http" = "403" ]; then
            if [ "$ASSUME_YES" -eq 1 ]; then
                fail "Панель отклонила API-токен (HTTP ${_token_http}).
      3X-UI v3: «Настройки панели → Учётная запись → API-токен» — создайте токен со
      scope node-sync (или admin) и вставьте значение; посмотреть его позже панель
      не даст, копируйте сразу.
      3X-UI 2.x: «Настройки → API Tokens» либо
      sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync"
            fi
            warn "Панель отклонила токен (HTTP ${_token_http}) — проверьте значение и его область (нужен node-sync или admin)."
            info "3X-UI v3: Настройки панели → Учётная запись → API-токен (создать, скопировать сразу);"
            info "то же по API: POST /panel/api/setting/apiTokens/create с scope node-sync (нужен admin-токен)."
            info "3X-UI 2.x: sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync"
            info "В PANEL_API_TOKEN вставляется значение токена, а не название области — например update-all-geofiles."
        elif [ "$_token_http" = "404" ]; then
            warn "Панель ответила 404 — похоже, адрес API неверен (PANEL_URL/webBasePath)"
        else
            warn "Проверить токен не удалось (HTTP ${_token_http}, панель не ответила) — это заметит самопроверка"
        fi
    fi

    DETECTED_SERVICES="x-ui"
    if systemctl list-unit-files 2>/dev/null | grep -q '^fail2ban\.service'; then
        DETECTED_SERVICES="x-ui,fail2ban"
    fi
    info "Сервисы — это systemd-юниты через запятую. Обычно достаточно x-ui;"
    info "fail2ban подставится сам, если он установлен."
    question SERVICES_LIST "Сервисы под наблюдением (Enter — оставить как есть)" \
        "${SERVICES:-${DETECTED_SERVICES}}"

    info "Чат для алертов — необязательное поле: Enter — алерты придут в личку"
    info "всем ID из TELEGRAM_ADMIN_IDS."
    info "Нужен общий чат? Впишите его ID (число, у групп оно отрицательное,"
    info "например -1001234567890). Узнать: добавьте бота в группу, отправьте там"
    info "сообщение и посмотрите ID у @userinfobot или @getidsbot."
    question START_CHAT "Чат для алертов (Enter — админам в личку)" ""

    info "Итого: бот ${TG_USERNAME:+@$TG_USERNAME }<$ADMIN_IDS>, панель $PANEL_URL_VALUE${PANEL_BASE:+/$PANEL_BASE}, сервисы $SERVICES_LIST, токен панели ***${PANEL_TOKEN: -4}"

    set_env TELEGRAM_BOT_TOKEN "$TG_TOKEN"
    set_env TELEGRAM_ADMIN_IDS "$ADMIN_IDS"
    set_env PANEL_URL "$PANEL_URL_VALUE"
    set_env PANEL_BASE_PATH "$PANEL_BASE"
    set_env PANEL_API_TOKEN "$PANEL_TOKEN"
    set_env SERVICES "$SERVICES_LIST"
    set_env ALERT_CHAT_ID "$START_CHAT"
    set_env STATE_PATH "$STATE_DIR/state.db"
    set_env LOG_PATH "$STATE_DIR/bot.log"
    set_env SUDO_WRAPPER "$WRAPPER_PATH"
    if [ -n "${TELEGRAM_API_URL:-}" ]; then
        set_env TELEGRAM_API_URL "$TELEGRAM_API_URL"
    fi
    ok "Конфигурация записана в $ENV_FILE"
fi

# .env содержит оба токена. Владелец — root, сервису доступно только чтение
# (systemd отдаёт переменные сам, а бот не может переписать свой конфиг и
# пережить таким образом компрометацию).
chown root:"${SERVICE_GROUP:-$SERVICE_USER}" "$ENV_FILE"
chmod 640 "$ENV_FILE"

for key in TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_IDS PANEL_URL PANEL_API_TOKEN; do
    [ -n "$(env_get "$key")" ] || fail "В $ENV_FILE пусто $key — дозаполните: sudo uisentry-menu (или повторите установку)"
done

# Заглушка из .env.example: с ней бот стартует, но Telegram отвечает 401.
case "$(env_get TELEGRAM_BOT_TOKEN)" in
    *ExampleTokenReplaceMe*)
        fail "TELEGRAM_BOT_TOKEN в $ENV_FILE — заглушка из .env.example.
  Возьмите настоящий токен: откройте @BotFather -> /newbot и впишите
  выданное значение вида 123456789:AA... в TELEGRAM_BOT_TOKEN."
        ;;
esac
case "$(env_get TELEGRAM_BOT_TOKEN)" in
    *:*) ;;
    *) fail "TELEGRAM_BOT_TOKEN должен содержать «<bot_id>:<секрет>» — так выдаёт @BotFather" ;;
esac


# ------------------------------------------------------------- файлы бота

step "Копируем бота в $PREFIX"

mkdir -p "$PREFIX"
if [ "$(readlink -f "$SOURCE_DIR")" = "$(readlink -f "$PREFIX")" ]; then
    info "Исходники уже в $PREFIX — копирую поверх"
    cp -a "$SOURCE_DIR/uisentry/." "$PREFIX/uisentry/"
    cp -a "$SOURCE_DIR/install/." "$PREFIX/install/"
else
    cp -a "$SOURCE_DIR/uisentry" "$SOURCE_DIR/install" "$PREFIX/"
fi
# Копия установщика лежит рядом с ботом: `uisentry-menu update` берёт её локально
# и обновляет сама себя при каждом запуске.
cp -a "$SOURCE_DIR/install.sh" "$PREFIX/install.sh" 2>/dev/null || true
cp -a "$SOURCE_DIR/requirements.txt" \
    "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/README.md" "$SOURCE_DIR/.env.example" \
    "$PREFIX/" 2>/dev/null || true
# Код и venv — только root: иначе скомпрометированный бот перепишет сам себя
# и переживёт рестарт. Сервисному пользователю принадлежит лишь изменяемое
# состояние: база и логи (это ровно то, что разрешает юнит).
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"
chmod 755 "$PREFIX"

# .env мастер пишет ещё до копирования — возвращаем ему владельца и права,
# которые сбросил chown -R выше (сервису нужно только чтение).
if [ -f "$ENV_FILE" ]; then
    chown root:"${SERVICE_GROUP:-$SERVICE_USER}" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
fi

# Каталог состояния создаёт и пересоздаёт systemd (StateDirectory=), но на
# этапе установки он нужен уже сейчас — самопроверка пишет в базу.
install -d -m 0750 -o "$SERVICE_USER" -g "${SERVICE_GROUP:-$SERVICE_USER}" "$STATE_DIR"
ok "Файлы на месте (код — root, состояние — $SERVICE_USER в $STATE_DIR)"

step "Создаём venv и ставим Python-зависимости"

if [ ! -x "$PREFIX/.venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$PREFIX/.venv" || fail "Не удалось создать venv"
    ok "Окружение создано"
else
    info "Окружение уже есть, обновляем пакеты"
fi
"$PREFIX/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 \
    || warn "Не удалось обновить pip, продолжаем"
info "python-telegram-bot, httpx, psutil…"
"$PREFIX/.venv/bin/pip" install -q -r "$PREFIX/requirements.txt" >/dev/null 2>&1 \
    || fail "Не удалось поставить зависимости из requirements.txt"
"$PREFIX/.venv/bin/python" -c 'import telegram, httpx, psutil, dotenv' \
    || fail "Зависимости установились, но не импортируются — проверьте venv"
# umask 077 нужен для секретов, но venv создаётся уже после него и получил бы
# права 700 (только root). Сервисный пользователь обязан уметь запускать
# интерпретатор, поэтому возвращаем чтение и исполнение для всех.
chmod -R a+rX "$PREFIX/.venv"
ok "Зависимости установлены"

# ---------------------------------------------------------------- конфиг

# ------------------------------------------------------- sudo-обёртка

step "Настраиваем привилегированные операции (sudo-обёртка)"

install -o root -g root -m 0755 "$PREFIX/install/uisentry-ctl" "$WRAPPER_PATH" \
    || fail "Не удалось поставить $WRAPPER_PATH"
# Консольная панель для быстрых правок настроек (sudo uisentry-menu).
install -o root -g root -m 0755 "$PREFIX/install/uisentry-menu" "$MENU_PATH" \
    || fail "Не удалось поставить $MENU_PATH"

# visudo лежит в sbin и не всегда в PATH у не-login root-сессии.
VISUDO="$(command -v visudo 2>/dev/null || true)"
if [ -z "$VISUDO" ]; then
    for candidate_visudo in /usr/sbin/visudo /sbin/visudo /usr/local/sbin/visudo; do
        if [ -x "$candidate_visudo" ]; then
            VISUDO="$candidate_visudo"
            break
        fi
    done
fi

SUDOERS_FILE="$SUDOERS_PATH"
# Правила перечисляем ТОЧНО, без шаблонов: классический sudo допускает `*` в
# аргументах, а sudo-rs (Ubuntu 26.04+) отвечает «wildcards are not allowed in
# command arguments». Юниты берём из SERVICES, ключи команд — из
# COMMAND_ALLOWLIST (пробел → «_»), поэтому файл остаётся строгим и коротким.
SUDO_UNITS="$(env_get SERVICES | tr ',' ' ')"
# Миграция старого короткого списка на расширенный безопасный набор.
_OLD_ALLOW="journalctl --disk-usage,systemctl list-timers --all,free -h,df -h,uptime"
_NEW_ALLOW="os_logs,os_errors,kernel_logs,xui_logs,dmesg,sockets,net_summary,addresses,routes,failed_units,xui_service,uname,top_cpu,log_size,inodes,upgradable,upgrade_sim,vmstat,time_status,logged_in,boot_time,cpu_info,block_devices,journalctl --disk-usage,systemctl list-timers --all,free -h,df -h,uptime,x-ui status"
if [ "$(env_get COMMAND_ALLOWLIST)" = "$_OLD_ALLOW" ]; then
    set_env COMMAND_ALLOWLIST "$_NEW_ALLOW"
    info "Список безопасных команд расширен (старое значение заменено)."
fi

# После любых set_env возвращаем .env владельца/права: сервис читает его сам.
chown root:"${SERVICE_GROUP:-$SERVICE_USER}" "$ENV_FILE" 2>/dev/null || true
chmod 640 "$ENV_FILE" 2>/dev/null || true

SUDO_KEYS="$(env_get COMMAND_ALLOWLIST | tr ',' '\n' | sed 's/^ *//; s/ *$//' \
    | tr ' ' '_' | tr '\n' ' ')"
[ -n "$SUDO_UNITS" ] || SUDO_UNITS="$SERVICE_NAME"

sudoers_rules() {
    local rules="" unit sub key
    for unit in $SUDO_UNITS; do
        for sub in is_active restart_service journal journal_more journal_errors; do
            rules="$rules${rules:+, }$WRAPPER_PATH $sub $unit"
        done
    done
    for sub in \
        apt_update apt_upgrade apt_upgrade_phased dist_upgrade release_upgrade reboot; do
        rules="$rules${rules:+, }$WRAPPER_PATH $sub"
    done
    for kind in update upgrade phased release; do
        rules="$rules${rules:+, }$WRAPPER_PATH apt_status $kind"
    done
    for key in $SUDO_KEYS; do
        rules="$rules${rules:+, }$WRAPPER_PATH run_command $key"
    done
    # Варианты логов для кнопок «Ещё 200 строк»/«Только ошибки»: read-only.
    for key in os_logs_more os_logs_errors xui_logs_more xui_logs_errors \
        kernel_logs_more kernel_logs_errors dmesg_errors; do
        rules="$rules${rules:+, }$WRAPPER_PATH run_command $key"
    done
    printf '%s' "$rules"
}
{
    printf '# Сгенерировано установщиком бота мониторинга 3X-UI.\n'
    printf '# Точные команды без шаблонов: совместимо с sudo и sudo-rs.\n'
    printf '%s ALL=(root) NOPASSWD: %s\n' "$SERVICE_USER" "$(sudoers_rules)"
} > "$SUDOERS_FILE"
chown root:root "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"

if [ -z "$VISUDO" ]; then
    warn "visudo не найден — синтаксис sudoers не проверен (файл собран из шаблона)"
elif _visudo_out="$("$VISUDO" -cf "$SUDOERS_FILE" 2>&1)"; then
    ok "Правила sudo валидны (проверено visudo)"
else
    # Печатаем вывод visudo как есть: обрезать его нельзя, теряется причина.
    warn "visudo ответил:"
    printf '%s\n' "$_visudo_out" | sed 's/^/        /'
    warn "версия: $("$VISUDO" -V 2>&1 | head -1)"
    info "Пробую упрощённые правила — без строки Defaults (совместимость со сборками sudo)."
    grep -vE '^(Defaults|# Defaults$)' "$SUDOERS_FILE" > "$SUDOERS_FILE.tmp" 2>/dev/null || true
    install -o root -g root -m 0440 "$SUDOERS_FILE.tmp" "$SUDOERS_FILE"
    rm -f "$SUDOERS_FILE.tmp"
    if _visudo_out2="$("$VISUDO" -cf "$SUDOERS_FILE" 2>&1)"; then
        ok "Правила sudo валидны (упрощённый вариант, без Defaults)"
    else
        # Файл в sudoers.d держать нельзя (sudo сломается), но и терять нельзя:
        # сохраняем рядом с суффиксом .invalid (sudo такие игнорирует).
        mv -f "$SUDOERS_FILE" "${SUDOERS_FILE}.invalid" 2>/dev/null || true
        warn "Правила не приняты сборкой sudo. Что прислать в issue:"
        printf '        %s\n' "visudo (raw):"
        printf '%s\n' "$_visudo_out2" | sed 's/^/          /'
        printf '        %s\n' "содержимое ${SUDOERS_FILE}.invalid:"
        sudo_cat="$(cat "${SUDOERS_FILE}.invalid" 2>/dev/null || echo '(нет файла)')"
        printf '%s\n' "$sudo_cat" | sed 's/^/          /'
        fail "Синтаксис sudoers невалиден — правила отключены, sudo не тронут."
    fi
fi
ok "Обёртка $WRAPPER_PATH установлена"

# --------------------------------------------------------------- сервис

if [ "$INIT" = systemd ]; then
    step "Готовим systemd-юнит"

    UNIT_FILE="$UNIT_PATH"
    sed -e "s#^User=.*#User=${SERVICE_USER}#" \
        -e "s#^Group=.*#Group=${SERVICE_GROUP:-$SERVICE_USER}#" \
        -e "s#/opt/uisentry#${PREFIX}#g" \
        -e "s#/usr/local/bin/uisentry-ctl#${WRAPPER_PATH}#g" \
        -e "s#/var/lib/uisentry#${STATE_DIR}#g" \
        "$PREFIX/install/${SERVICE_NAME}.service" > "$UNIT_FILE"
    if [ "$STATE_DIR" != "/var/lib/uisentry" ]; then
        # StateDirectory умеет создавать только /var/lib/<name>; для своего
        # каталога его отключаем — каталог уже создан установщиком.
        sed -i "s#^StateDirectory=.*#\# StateDirectory отключён: каталог создан установщиком#" "$UNIT_FILE"
    fi
    chmod 644 "$UNIT_FILE"
    # Отдельные постоянные юниты для apt. systemd-run в daemon-контексте на
    # новых systemd (Ubuntu 26.04) уходит в polkit и отбивается «требуется
    # интерактивная аутентификация» — transient-юнит создать нельзя. А обычный
    # юнит с User=root запускается через systemctl и работает.
    for _pair in \
        "uisentry-apt-update:_apt_update_worker:apt update" \
        "uisentry-apt-upgrade:_apt_worker:apt full-upgrade" \
        "uisentry-apt-phased:_apt_worker_phased:apt phased upgrade" \
        "uisentry-release-upgrade:_release_upgrade_worker:do-release-upgrade"; do
        _apt_unit="${_pair%%:*}"
        _rest="${_pair#*:}"
        _apt_worker="${_rest%%:*}"
        _apt_title="${_rest#*:}"
        cat > "/etc/systemd/system/${_apt_unit}.service" <<EOF
[Unit]
Description=${_apt_title} for ${SERVICE_NAME} (one-shot, requested by the bot)
After=network-online.target

[Service]
Type=oneshot
ExecStart=${WRAPPER_PATH} ${_apt_worker}
EOF
        chmod 644 "/etc/systemd/system/${_apt_unit}.service"
    done
    systemctl daemon-reload
    systemctl preset "$SERVICE_NAME" >/dev/null 2>&1 || true
    if systemctl enable "$SERVICE_NAME" >/dev/null 2>&1; then
        ok "Автозапуск включён: бот поднимется сам после перезагрузки"
    elif [ "$SYSTEMD_ACTIVE" -eq 1 ]; then
        fail "Не удалось включить автозапуск (systemctl enable $SERVICE_NAME)"
    else
        warn "Автозапуск не включён: хост загружен не через systemd"
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        ok "Автозапуск: enabled"
    elif [ "$SYSTEMD_ACTIVE" -eq 0 ]; then
        warn "Автозапуск не проверить: хост загружен не через systemd"
    else
        warn "Автозапуск не подтверждён: systemctl is-enabled $SERVICE_NAME"
    fi

    if command -v systemd-analyze >/dev/null 2>&1; then
        systemd-analyze verify "$UNIT_FILE" >/dev/null 2>&1 \
            || warn "systemd-analyze нашёл замечания к юниту (посмотрите: systemd-analyze verify $UNIT_FILE)"
    fi
else
    step "Ставим init-скрипт ($INIT)"

    sed -e "s#@SERVICE_USER@#${SERVICE_USER}#g" \
        -e "s#@STATE_DIR@#${STATE_DIR}#g" \
        -e "s#^PREFIX=.*#PREFIX=${PREFIX}#" \
        "$PREFIX/install/${SERVICE_NAME}.init" > "$INIT_SCRIPT" \
        || fail "Не удалось записать $INIT_SCRIPT"
    chmod 0755 "$INIT_SCRIPT"

    case "$INIT" in
        openrc)
            rc-update add "$SERVICE_NAME" default >/dev/null 2>&1 \
                || warn "rc-update не сработал — добавьте автозапуск вручную"
            ;;
        sysv)
            if command -v update-rc.d >/dev/null 2>&1; then
                update-rc.d "$SERVICE_NAME" defaults >/dev/null 2>&1 \
                    || warn "update-rc.d не сработал — добавьте автозапуск вручную"
            elif command -v chkconfig >/dev/null 2>&1; then
                chkconfig --add "$SERVICE_NAME" >/dev/null 2>&1 \
                    || warn "chkconfig не сработал — добавьте автозапуск вручную"
            else
                warn "нет update-rc.d/chkconfig — автозапуск настройте вручную"
            fi
            ;;
    esac
    ok "Служба зарегистрирована: $INIT_SCRIPT"
fi

if [ "$START_SERVICE" -eq 1 ]; then
    step "Запускаем бота"

    if [ "$INIT" = systemd ]; then
        if systemctl restart "$SERVICE_NAME"; then
            sleep 4
            systemctl is-active --quiet "$SERVICE_NAME" \
                || {
                    warn "Сервис не активен, последние строки журнала:"
                    journalctl -u "$SERVICE_NAME" -n 15 --no-pager | sed 's/^/        /'
                    fail "Бот не стартовал. Подробности: journalctl -u $SERVICE_NAME -f"
                }
            ok "Сервис запущен"
        else
            fail "systemctl restart не отработал: journalctl -u $SERVICE_NAME -n 30 --no-pager"
        fi
    elif "$INIT_SCRIPT" start; then
        ok "Служба запущена"
    else
        fail "Не удалось запустить: $INIT_SCRIPT start (лог: $STATE_DIR/bot.log)"
    fi
else
    step "Запуск пропущен"
    if [ "$INIT" = systemd ]; then
        info "Запустить позже: systemctl enable --now $SERVICE_NAME"
    else
        info "Запустить позже: $INIT_SCRIPT start"
    fi
fi

# ------------------------------------------------------------ самопроверка

step "Проверяем, что всё работает"

PY="$PREFIX/.venv/bin/python"
CFG_OK=0
# .env НЕ подключаем через source: значения вроде COMMAND_ALLOWLIST содержат
# пробелы, и оболочка приняла бы хвост значения за команду. Бот читает файл сам.
if (cd "$PREFIX" && "$PY" -c 'from uisentry.settings import load_settings; s = load_settings(); print(s.api_base, s.telegram_token[:6])' >/dev/null 2>&1); then
    check_ok "Конфигурация читается ботом без ошибок"
    CFG_OK=1
else
    check_fail "Конфигурация не проходит проверку — откройте $ENV_FILE редактором (содержимое содержит токены: не пересылайте его третьим лицам)"
fi

TG_TOKEN_VALUE="$(env_get TELEGRAM_BOT_TOKEN)"
TG_API_URL_VALUE="$(env_get TELEGRAM_API_URL)"
[ -n "$TG_API_URL_VALUE" ] || TG_API_URL_VALUE="https://api.telegram.org"
# Без -f: на HTTP 401 тело ответа («Unauthorized») как раз нужно для диагноза.
TG_RESULT="$(curl -sS --max-time 15 "${TG_API_URL_VALUE%/}/bot${TG_TOKEN_VALUE}/getMe" 2>/dev/null || true)"
if printf '%s' "$TG_RESULT" | grep -q '"ok":true'; then
    TG_NAME="$(printf '%s' "$TG_RESULT" | sed -E 's/.*"username":"([^"]+)".*/\1/')"
    check_ok "Telegram отвечает, бот @$TG_NAME"
else
    case "$TG_RESULT" in
        *401*|*"Unauthorized"*)
            check_fail "Токен @BotFather отклонён Telegram — впишите правильный TELEGRAM_BOT_TOKEN" ;;
        *)
            check_fail "Не удалось проверить токен Telegram (нет сети или он неверный)" ;;
    esac
fi

if [ "$CFG_OK" -eq 1 ]; then
    PANEL_API="$(
        cd "$PREFIX" || exit 1
        "$PY" -c 'from uisentry.settings import load_settings; print(load_settings().api_base)' 2>/dev/null || true
    )"
    PANEL_TOKEN_VALUE="$(env_get PANEL_API_TOKEN)"
    if [ -n "$PANEL_API" ] && [ -n "$PANEL_TOKEN_VALUE" ]; then
        # Без -f: HTTP-код нужен, чтобы отличить «токен не принят» (401/403)
        # от «путь не тот» (404) и «панель недоступна» (000/сеть).
        PANEL_HTTP_DIR="$(mktemp -d)"
        panel_http() {
            curl -sS --max-time 15 -o "$PANEL_HTTP_DIR/body" -w '%{http_code}' \
                -H "Authorization: Bearer ${PANEL_TOKEN_VALUE}" \
                "${PANEL_API}/$1" 2>/dev/null || echo 000
        }
        STATUS_CODE="$(panel_http server/status)"
        PANEL_VER="$(sed -n 's/.*"panelVersion":"\([^"]*\)".*/\1/p' "$PANEL_HTTP_DIR/body" | head -1)"
        if [ "$STATUS_CODE" = "200" ] && grep -q '"success":true' "$PANEL_HTTP_DIR/body"; then
            if [ -n "$PANEL_VER" ]; then
                check_ok "API панели принимает токен (3X-UI v$PANEL_VER, $PANEL_API/server/status)"
            else
                check_ok "API панели принимает токен ($PANEL_API/server/status)"
            fi
        else
            case "$STATUS_CODE" in
                401|403)
                    check_fail "Панель отклонила токен (HTTP $STATUS_CODE) — значение неверное или у токена нет нужного scope. Создайте токен со scope node-sync (или admin): 3X-UI v3 — «Настройки панели → Учётная запись → API-токен» (значение видно один раз, копируйте сразу); 3X-UI 2.x — «Настройки → API Tokens»." ;;
                404)
                    check_fail "Панель ответила 404 на $PANEL_API/server/status — проверьте PANEL_URL и PANEL_BASE_PATH. Помните: PANEL_BASE_PATH — это webBasePath панели, он входит в адрес как префикс перед /panel/api." ;;
                000)
                    check_fail "Панель недоступна по $PANEL_API — проверьте PANEL_URL, порт и что служба панели запущена" ;;
                *)
                    check_fail "Панель вернула HTTP $STATUS_CODE на $PANEL_API/server/status: $(head -c 160 "$PANEL_HTTP_DIR/body")" ;;
            esac
        fi

        if [ "$STATUS_CODE" = "200" ]; then
            INBOUND_CODE="$(panel_http inbounds/list)"
            if [ "$INBOUND_CODE" = "200" ] && grep -q '"success":true' "$PANEL_HTTP_DIR/body"; then
                check_ok "Список inbound'ов и подписок доступен"
            else
                check_fail "inbounds/list недоступен (HTTP $INBOUND_CODE) — токену нужен scope node-sync или admin"
            fi
        fi
        rm -rf "$PANEL_HTTP_DIR"
    else
        check_fail "Не удалось вычислить адрес API панели"
    fi

    WRITE_OUT="$(
        cd "$PREFIX" || exit 1
        sudo -u "$SERVICE_USER" "$PY" -c '
from uisentry.settings import load_settings
from uisentry.storage import Storage
s = load_settings()
db = Storage(s.state_path)
db.audit("installer_selfcheck", telegram_id=None, ok=True, result="ok")
rows = db.recent_audit(1)
db.close()
print(rows[0]["result"] if rows else "empty")
' 2>&1 || true
    )"
    WRITE_RESULT="$(printf '%s' "$WRITE_OUT" | head -1)"
    if [ "$WRITE_RESULT" = "ok" ]; then
        check_ok "Бот может писать состояние в $(env_get STATE_PATH)"
    else
        check_fail "Нет записи в $(env_get STATE_PATH): $(printf '%s' "$WRITE_OUT" | tail -1 | head -c 200)"
    fi
fi

SUDO_RESULT="$(sudo -u "$SERVICE_USER" "$WRAPPER_PATH" is_active x-ui 2>&1 || true)"
case "$SUDO_RESULT" in
    active)   check_ok "sudo-обёртка работает (юнит x-ui: active)" ;;
    inactive) check_ok "sudo-обёртка работает (юнит x-ui: inactive — запустите панель)" ;;
    *)        check_fail "sudo-обёртка не отвечает: $SUDO_RESULT" ;;
esac

if [ "$INIT" = systemd ]; then
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        check_ok "Автозапуск: systemctl is-enabled $SERVICE_NAME = enabled"
    elif [ "$SYSTEMD_ACTIVE" -eq 0 ]; then
        check_note "Автозапуск не проверен: хост загружен не через systemd"
    else
        check_fail "Автозапуск выключен: systemctl enable $SERVICE_NAME"
    fi
elif ls /etc/runlevels/*/"$SERVICE_NAME" /etc/rc*.d/*"$SERVICE_NAME" >/dev/null 2>&1; then
    check_ok "Автозапуск: init-скрипт зарегистрирован ($INIT)"
else
    check_note "Автозапуск не подтверждён: добавьте $SERVICE_NAME в ($INIT) вручную"
fi

if [ "$INIT" = systemd ]; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        check_ok "Сервис активен"
    elif [ "$START_SERVICE" -eq 0 ]; then
        check_ok "Сервис не запущен — так и просили (--no-start), автозапуск включён"
        info "Запустить вручную: systemctl start $SERVICE_NAME"
    elif [ "$SYSTEMD_ACTIVE" -eq 0 ]; then
        check_note "Состояние сервиса не проверено: хост загружен не через systemd"
    else
        check_fail "Сервис не активен: systemctl status $SERVICE_NAME"
    fi
elif "$INIT_SCRIPT" status >/dev/null 2>&1; then
    check_ok "Сервис активен"
elif [ "$START_SERVICE" -eq 0 ]; then
    check_ok "Сервис не запущен — так и просили (--no-start), автозапуск включён"
    info "Запустить вручную: $INIT_SCRIPT start"
else
    check_fail "Сервис не активен: $INIT_SCRIPT status"
fi

LOG_VIEW="journalctl -u ${SERVICE_NAME} -n 50"

step "Итог самопроверки"

if [ "$FAILURES" -eq 0 ]; then
    printf '\n%s%sВсе %d проверок пройдены. Бот работает.%s\n' \
        "$BOLD" "$GREEN" "$CHECKS" "$RESET"
else
    printf '\n%s%sПройдено %d из %d проверок.%s\n' "$BOLD" "$YELLOW" \
        "$((CHECKS - FAILURES))" "$CHECKS" "$RESET"
    info "Бот установлен и запущен. Что чинить — в списке выше и в $LOG_VIEW"
fi

# ---------------------------------------------------------------- итог

printf '\n%s%sГотово%s\n' "$BOLD" "$GREEN" "$RESET"
cat <<EOF

  Бот:        systemctl status ${SERVICE_NAME}
  Журнал:     journalctl -u ${SERVICE_NAME} -f
  Конфиг:     ${ENV_FILE}
  Состояние:  $(env_get STATE_PATH)
  Логи:       $(env_get LOG_PATH)

  Что дальше:
    • откройте бота в Telegram и нажмите /start
    • /status — сводка, /subs — подписки, /maint — обслуживание
    • токены лежат в ${ENV_FILE}: TELEGRAM_BOT_TOKEN (от @BotFather) и PANEL_API_TOKEN (панель)
    • после правки конфига: systemctl restart ${SERVICE_NAME}

  Обновление бота:
    sudo bash install.sh --prefix ${PREFIX}
  Меню настроек:
    sudo uisentry-menu            # правки настроек, рестарт, журнал, обновление

  Удаление бота:
    sudo bash install.sh --uninstall

EOF