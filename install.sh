#!/usr/bin/env bash
#
# Установка бота мониторинга 3X-UI на Linux-сервер одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh | sudo bash
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
REPO_NAME="${REPO_NAME:-xui-monitor-bot}"
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
detect_ips() {
    if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
        hostname -I 2>/dev/null | tr -s ' \n' ' ' | sed 's/ *$//'
    elif command -v ip >/dev/null 2>&1; then
        ip -4 -o addr show scope global 2>/dev/null \
            | awk '{print $4}' | cut -d/ -f1 | tr '\n' ' ' | sed 's/ *$//'
    fi
}

cleanup() {
    [ -n "${TMP_DIR:-}" ] && [ -d "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR"
    return 0
}
trap cleanup EXIT

# ------------------------------------------------------------------ параметры

PREFIX="${PREFIX:-/opt/xui-monitor-bot}"
SERVICE_USER="${SERVICE_USER:-xui-monitor}"
# Каталог состояния (база SQLite и файл лога) живёт отдельно от кода:
# код принадлежит root, состояние — сервисному пользователю.
STATE_DIR="${STATE_DIR:-/var/lib/xui-monitor}"
SERVICE_NAME="xui-monitor-bot"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/xui-ctl}"
MENU_PATH="${MENU_PATH:-/usr/local/bin/xui-menu}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
SUDOERS_PATH="/etc/sudoers.d/${SERVICE_NAME}"
START_SERVICE=1
ASSUME_YES=0
KEEP_CONFIG=1
UNINSTALL=0
PURGE=0
MENU=0
PRINT_SOURCE=0

usage() {
    cat <<EOF
${BOLD}Установка бота мониторинга 3X-UI${RESET}

  sudo bash install.sh [опции]

Опции:
  --prefix DIR     каталог установки (по умолчанию: /opt/xui-monitor-bot)
  --user NAME      системный пользователь бота (по умолчанию: xui-monitor)
  --tarball URL    откуда скачать исходники (tar.gz), минуя репозиторий по умолчанию
  --repo OWNER/NAME  репозиторий на GitHub (краткая форма вместо REPO_OWNER/REPO_NAME)
  --ref BRANCH     ветка или тег репозитория (по умолчанию main)
  --print-source   показать, откуда будут взяты исходники, и выйти
  --sha256 HASH    сверить скачанный архив с контрольной суммой (привязка к версии)
  --menu           меню: установить/обновить, удалить, справка
                   (после установки для правок настроек: sudo xui-menu)
  --uninstall      удалить бота: служба, sudoers, обёртка, код
  --purge          вместе с --uninstall удалить состояние и пользователя
  --yes            без вопросов: берём значения из окружения или готового .env
  --force-config   перезаписать существующий .env значениями по умолчанию
  --no-start       установить, но не запускать сервис
  --help           эта справка

Переменные окружения (для --yes и unattended-установки):
  REPO, REPO_OWNER, REPO_NAME, REPO_REF, REPO_URL, TARBALL_URL, GIT_URL,
  SOURCE_SHA256, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, TELEGRAM_API_URL,
  PANEL_URL, PANEL_BASE_PATH, PANEL_API_TOKEN, SERVICES
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
        --purge)        UNINSTALL=1; PURGE=1; shift ;;
        --yes|-y)       ASSUME_YES=1; shift ;;
        --force-config) KEEP_CONFIG=0; shift ;;
        --no-start)     START_SERVICE=0; shift ;;
        --help|-h)      usage; exit 0 ;;
        *) fail "Неизвестная опция: $1 (см. --help)" ;;
    esac
done

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
        ask "Удалить бота целиком (служба, sudoers, обёртка, код)? [y/N]"
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
        "/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}.service"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    ok "Служба и автозапуск удалены"

    rm -f "$SUDOERS_PATH" "$WRAPPER_PATH" "$MENU_PATH"
    ok "sudo-правила и обёртка удалены (привилегии отозваны)"

    rm -rf "$PREFIX"
    ok "Каталог с кодом удалён: $PREFIX"

    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$STATE_DIR"
        if id "$SERVICE_USER" >/dev/null 2>&1; then
            userdel "$SERVICE_USER" >/dev/null 2>&1 \
                || warn "Пользователя $SERVICE_USER удалить не удалось"
        fi
        ok "Состояние и системный пользователь удалены"
    else
        info "Состояние сохранено: $STATE_DIR (стереть: --purge)"
    fi

    printf '\n%s%sБот удалён.%s\n' "$BOLD" "$GREEN" "$RESET"
    if [ "$PURGE" -eq 0 ]; then
        info "Остался каталог состояния $STATE_DIR — он больше никому не мешает."
    fi
}

interactive_menu() {
    if [ "$INTERACTIVE" -eq 0 ]; then
        fail "--menu требует терминал. Без него: --yes (установка) или --uninstall."
    fi
    printf '\n%sБот мониторинга 3X-UI — установка%s\n\n' "$BOLD" "$RESET"
    printf '  1) Установить или обновить бота\n'
    printf '  2) Удалить бота (служба, sudoers, код)\n'
    printf '  3) Удалить бота вместе с состоянием и пользователем\n'
    printf '  h) Справка\n'
    printf '  0) Выход\n\n'
    ask "Выберите пункт [1]: "
    read -r choice <&$READ_FD || choice=""
    case "${choice:-1}" in
        1) return 0 ;;
        2) UNINSTALL=1 ;;
        3) UNINSTALL=1; PURGE=1 ;;
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
    warn "Укажите свой репозиторий: --repo ваш-логин/xui-monitor-bot (или REPO_OWNER/REPO_NAME)."
fi

[ "$(id -u)" -eq 0 ] || fail "Запускать от root: sudo bash install.sh"

# --- systemd: бот управляет сервисами через systemctl и стартует юнитом ---
command -v systemctl >/dev/null 2>&1 \
    || fail "Нужен systemd: systemctl не найден.
  Бот управляет сервисами через systemd. На хостах с OpenRC/SysV/BSD-стилем
  установка невозможна — используйте дистрибутив с systemd."
SYSTEMD_ACTIVE=1
if [ ! -d /run/systemd/system ]; then
    SYSTEMD_ACTIVE=0
    warn "Хост загружен не через systemd (нет /run/systemd/system)."
    warn "Установка продолжится, но автозапуск и управление сервисами будут недоступны."
fi

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
    if [ -f "$candidate/xui_monitor_bot/app.py" ]; then
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
                SOURCE_DIR="$(find "$TMP_DIR" -maxdepth 2 -name xui_monitor_bot \
                    -type d -exec dirname {} \; | head -1)"
                if [ -n "$SOURCE_DIR" ]; then
                    break
                fi
            fi
        done <<EOF
$(source_candidates)
EOF
    fi

    # 2) git clone: запасной путь, когда тарбол недоступен.
    if [ -z "$SOURCE_DIR" ] && command -v git >/dev/null 2>&1; then
        GIT_URL="${GIT_URL:-$REPO_URL}"
        info "Тарбол не подошёл — пробую git clone $GIT_URL ($REPO_REF)"
        if git clone --depth 1 --branch "$REPO_REF" "$GIT_URL" "$TMP_DIR/repo" \
            >/dev/null 2>&1 && [ -d "$TMP_DIR/repo/xui_monitor_bot" ]; then
            SOURCE_DIR="$TMP_DIR/repo"
        fi
    fi

    if [ -z "$SOURCE_DIR" ] || [ ! -d "$SOURCE_DIR" ]; then
        if repo_is_placeholder; then
            fail "Исходники не найдены, а репозиторий — заглушка OWNER.
  Укажите свой репозиторий GitHub, например:
      sudo bash install.sh --repo ваш-логин/xui-monitor-bot
  (одной строкой:  sudo bash <(curl -Ls https://raw.githubusercontent.com/ваш-логин/xui-monitor-bot/main/install.sh))"
        fi
        fail "Не удалось получить исходники. Варианты:
      • свой репозиторий:  --repo owner/name  [--ref ветка]
      • прямой архив:      --tarball https://host/xui-monitor-bot.tar.gz
      • git:               GIT_URL=https://github.com/вас/repo.git
      • или скачайте репозиторий вручную и запустите install.sh из его каталога"
    fi
    ok "Исходники получены во временном каталоге"
fi
ok "Исходники: $SOURCE_DIR"

XUI_PRESENT=0
if systemctl is-active --quiet x-ui 2>/dev/null; then
    ok "Панель 3X-UI запущена (юнит x-ui)"
    XUI_PRESENT=1
elif systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service'; then
    warn "Юнит x-ui есть, но панель сейчас не запущена"
    XUI_PRESENT=1
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

# ------------------------------------------------------------- файлы бота

step "Копируем бота в $PREFIX"

mkdir -p "$PREFIX"
if [ "$(readlink -f "$SOURCE_DIR")" = "$(readlink -f "$PREFIX")" ]; then
    info "Исходники уже в $PREFIX — копирую поверх"
    cp -a "$SOURCE_DIR/xui_monitor_bot/." "$PREFIX/xui_monitor_bot/"
    cp -a "$SOURCE_DIR/install/." "$PREFIX/install/"
else
    cp -a "$SOURCE_DIR/xui_monitor_bot" "$SOURCE_DIR/install" "$PREFIX/"
fi
cp -a "$SOURCE_DIR/requirements.txt" "$SOURCE_DIR/requirements-dev.txt" \
    "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/README.md" "$SOURCE_DIR/.env.example" \
    "$PREFIX/" 2>/dev/null || true
# Код и venv — только root: иначе скомпрометированный бот перепишет сам себя
# и переживёт рестарт. Сервисному пользователю принадлежит лишь изменяемое
# состояние: база и логи (это ровно то, что разрешает юнит).
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"
chmod 755 "$PREFIX"

# Каталог состояния создаёт и пересоздаёт systemd (StateDirectory=), но на
# этапе установки он нужен уже сейчас — самопроверка пишет в базу.
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
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

step "Настраиваем бота (.env)"

ENV_FILE="$PREFIX/.env"

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -1; }

set_env() {
    grep -vE "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
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

detect_panel_token() {
    # x-ui сам создаёт токен и печатает его. Новое имя токена заменяет старое,
    # поэтому вызываем функцию только осознанно.
    command -v x-ui >/dev/null 2>&1 || return 1
    x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync 2>/dev/null \
        | "$PYTHON_BIN" -c '
import re, sys

text = sys.stdin.read()
found = re.findall(r"[a-zA-Z0-9_-]{16,}", text)
found = [item for item in found if item.lower() not in ("successful", "tokenname", "tokenscope")]
print(found[0] if found else "")
'
}

if [ -f "$ENV_FILE" ] && [ "$KEEP_CONFIG" -eq 1 ]; then
    ok "Файл .env уже есть — сохраняю ваши токены"
    info "Изменить: nano $ENV_FILE && systemctl restart $SERVICE_NAME"
else
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
    case "$PANEL_URL_VALUE" in
        http://*|https://*) ;;
        *) PANEL_URL_VALUE="$SUGGESTED_URL"
           warn "Некорректный адрес, беру автоопределённый: $PANEL_URL_VALUE" ;;
    esac

    question PANEL_BASE "webBasePath панели, если он задан (пусто — если нет)" \
        "${PANEL_BASE_PATH:-${DETECTED_BASE}}"

    PROBE_BASE="${PANEL_URL_VALUE%/}"
    [ -n "$PANEL_BASE" ] && PROBE_BASE="$PROBE_BASE/${PANEL_BASE#/}"
    if curl -sS --max-time 5 -o /dev/null "$PROBE_BASE/" 2>/dev/null; then
        ok "Панель отвечает: $PROBE_BASE"
    else
        warn "Панель не ответила на $PROBE_BASE/ — если адрес верный, это заметит самопроверка"
    fi

    question PANEL_TOKEN "API-токен панели" "${PANEL_API_TOKEN:-$(env_get PANEL_API_TOKEN)}"
    if [ -z "$PANEL_TOKEN" ]; then
        if [ "$XUI_PRESENT" -eq 1 ] && { [ "$ASSUME_YES" -eq 0 ] || [ -n "${ALLOW_TOKEN_GEN:-}" ]; }; then
            warn "Выпускаю токен monitor-bot (scope node-sync) в панели."
            warn "Внимание: если такой токен уже был, старый перестанет работать."
            PANEL_TOKEN="$(detect_panel_token || true)"
        fi
        [ -n "$PANEL_TOKEN" ] || fail "Нужен API-токен панели. Создайте его в панели: Settings → API Tokens, либо выполните:
      sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
и вставьте значение в PANEL_API_TOKEN."
    fi

    DETECTED_SERVICES="x-ui"
    if systemctl list-unit-files 2>/dev/null | grep -q '^fail2ban\.service'; then
        DETECTED_SERVICES="x-ui,fail2ban"
    fi
    question SERVICES_LIST "Сервисы под наблюдением" \
        "${SERVICES:-${DETECTED_SERVICES}}"
    question START_CHAT "Чат для алертов (пусто = писать администраторам в личку)" ""

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
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

for key in TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_IDS PANEL_URL PANEL_API_TOKEN; do
    [ -n "$(env_get "$key")" ] || fail "В $ENV_FILE пусто $key — допишите и запустите установку снова"
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


# ------------------------------------------------------- sudo-обёртка

step "Настраиваем привилегированные операции (sudo-обёртка)"

install -o root -g root -m 0755 "$PREFIX/install/xui-ctl" "$WRAPPER_PATH" \
    || fail "Не удалось поставить $WRAPPER_PATH"
# Консольная панель для быстрых правок настроек (sudo xui-menu).
install -o root -g root -m 0755 "$PREFIX/install/xui-menu" "$MENU_PATH" \
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
sed -e "s/^User_Alias XUI_MONITOR_USERS = .*/User_Alias XUI_MONITOR_USERS = ${SERVICE_USER}/" \
    -e "s#/usr/local/bin/xui-ctl#${WRAPPER_PATH}#g" \
    "$PREFIX/install/xui-monitor-sudoers" > "$SUDOERS_FILE"
chown root:root "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

if [ -n "$VISUDO" ] && "$VISUDO" -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
    ok "Правила sudo валидны (проверено visudo)"
elif [ -z "$VISUDO" ]; then
    warn "visudo не найден — синтаксис sudoers не проверен (файл собран из шаблона)"
else
    rm -f "$SUDOERS_FILE"
    fail "Синтаксис sudoers невалиден — файл удалён, sudo не тронут"
fi
ok "Обёртка $WRAPPER_PATH установлена"

# --------------------------------------------------------------- сервис

step "Готовим systemd-юнит"

UNIT_FILE="$UNIT_PATH"
sed -e "s#^User=.*#User=${SERVICE_USER}#" \
    -e "s#^Group=.*#Group=${SERVICE_USER}#" \
    -e "s#/opt/xui-monitor-bot#${PREFIX}#g" \
    -e "s#/usr/local/bin/xui-ctl#${WRAPPER_PATH}#g" \
    "$PREFIX/install/${SERVICE_NAME}.service" > "$UNIT_FILE"
chmod 644 "$UNIT_FILE"
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
    check_note "Автозапуск не проверить: хост загружен не через systemd"
else
    warn "Автозапуск не подтверждён: systemctl is-enabled $SERVICE_NAME"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$UNIT_FILE" >/dev/null 2>&1 \
        || warn "systemd-analyze нашёл замечания к юниту (посмотрите: systemd-analyze verify $UNIT_FILE)"
fi

if [ "$START_SERVICE" -eq 1 ]; then
    step "Запускаем бота"

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
else
    step "Запуск пропущен"
    info "Запустить позже: systemctl enable --now $SERVICE_NAME"
fi

# ------------------------------------------------------------ самопроверка

step "Проверяем, что всё работает"

PY="$PREFIX/.venv/bin/python"
CFG_OK=0
# .env НЕ подключаем через source: значения вроде COMMAND_ALLOWLIST содержат
# пробелы, и оболочка приняла бы хвост значения за команду. Бот читает файл сам.
if (cd "$PREFIX" && "$PY" -c 'from xui_monitor_bot.settings import load_settings; s = load_settings(); print(s.api_base, s.telegram_token[:6])' >/dev/null 2>&1); then
    check_ok "Конфигурация читается ботом без ошибок"
    CFG_OK=1
else
    check_fail "Конфигурация не проходит проверку — смотрите: grep -v '^#' $ENV_FILE"
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
        "$PY" -c 'from xui_monitor_bot.settings import load_settings; print(load_settings().api_base)' 2>/dev/null || true
    )"
    PANEL_TOKEN_VALUE="$(env_get PANEL_API_TOKEN)"
    if [ -n "$PANEL_API" ] && [ -n "$PANEL_TOKEN_VALUE" ]; then
        PANEL_RESULT="$(curl -fsS --max-time 15 -H "Authorization: Bearer ${PANEL_TOKEN_VALUE}" \
            "${PANEL_API}/server/status" 2>/dev/null || true)"
        if printf '%s' "$PANEL_RESULT" | grep -q '"success":true'; then
            check_ok "API панели принимает токен ($PANEL_API/server/status)"
        elif printf '%s' "$PANEL_RESULT" | grep -q '"success":false'; then
            check_fail "Панель отклонила токен: $(printf '%s' "$PANEL_RESULT" | head -c 160) — если версия 3X-UI старая (без API-токенов), обновите панель"
        else
            check_fail "Панель не ответила на $PANEL_API/server/status (проверьте PANEL_URL/PANEL_BASE_PATH)"
        fi

        INBOUND_RESULT="$(curl -fsS --max-time 15 -H "Authorization: Bearer ${PANEL_TOKEN_VALUE}" \
            "${PANEL_API}/inbounds/list" 2>/dev/null || true)"
        if printf '%s' "$INBOUND_RESULT" | grep -q '"success":true'; then
            check_ok "Список inbound'ов и подписок доступен"
        else
            check_fail "inbounds/list недоступен — нужен scope node-sync: x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync"
        fi
    else
        check_fail "Не удалось вычислить адрес API панели"
    fi

    WRITE_OUT="$(
        cd "$PREFIX" || exit 1
        sudo -u "$SERVICE_USER" "$PY" -c '
from xui_monitor_bot.settings import load_settings
from xui_monitor_bot.storage import Storage
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

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    check_ok "Автозапуск: systemctl is-enabled $SERVICE_NAME = enabled"
elif [ "$SYSTEMD_ACTIVE" -eq 0 ]; then
    check_note "Автозапуск не проверен: хост загружен не через systemd"
else
    check_fail "Автозапуск выключен: systemctl enable $SERVICE_NAME"
fi

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
    sudo xui-menu            # правки настроек, рестарт, журнал, обновление

  Удаление бота:
    sudo bash install.sh --uninstall

EOF