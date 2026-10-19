# xui-monitor-bot

[![CI](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-systemd%20%7C%20openrc%20%7C%20sysv-4EAA25)
![License](https://img.shields.io/badge/license-MIT-blue)

[Русский](README.md) · **English**

A Telegram bot for a server running the **3X-UI** panel: it watches the hardware,
services and the panel, sends clear alerts and shows subscriptions. Risky actions
— restart, system upgrade, reboot — happen only from a confirmed button and only
for an administrator.

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)
```

> One command: the wizard downloads the code, asks for tokens, installs
> dependencies, creates a dedicated user, enables autostart and verifies that
> everything works.

## Contents

1. [Why use it](#why-use-it)
2. [What it looks like](#what-it-looks-like)
3. [Compatibility](#compatibility)
4. [Quick start](#quick-start)
5. [Connecting to the panel](#connecting-to-the-panel)
6. [How it works](#how-it-works)
7. [Commands and screens](#commands-and-screens)
8. [`xui-menu` settings menu](#xui-menu-settings-menu)
9. [Settings](#settings)
10. [Security](#security)
11. [Update and removal](#update-and-removal)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)
14. [Project](#project)

---

## Why use it

The 3X-UI panel shows the state, but you have to open it. The bot brings what
matters to Telegram and stays quiet when everything is fine.

| | |
|---|---|
| **Monitoring** | CPU, RAM, swap, disk, network, load average; services; panel and xray; inbounds, subscriptions and client traffic |
| **Alerts** | hysteresis without flapping, cooldown, an “I know, until morning” mute; restart and log buttons right on the alert |
| **Actions** | service and xray restart, `apt` full/phased, release upgrade check, reboot, allow-listed diagnostic commands |
| **History** | hourly CPU/RAM/disk averages for the last day — see how the server behaved overnight |
| **Install** | one command, terminal prompts (works through `curl \| bash` too), an 8-point self-check |
| **Universality** | systemd, OpenRC, SysVinit; `apt`/`dnf`/`yum`/`zypper`/`pacman`; Python 3.10+ |
| **Terminal** | `xui-menu` — edit settings, restart, read the log, update; secrets are masked |
| **Security** | a dedicated unprivileged user, a narrow sudoers list of exact commands, every risky action confirmed and audited |

## What it looks like

The summary starts with a verdict — one glance is enough at night:

```text
🟢 Server state: all good · CPU 12% · RAM 41% · Disk 63% · x-ui 🟢 · panel 🟢

CPU  ▓▓░░░░░░░░  12.0% 🟢
RAM  ▓▓▓▓░░░░░░  41.0% 🟢
Disk ▓▓▓▓▓▓░░░░  63.0% 🟢
```

An alert with a reason and actions:

```text
🔴 CRITICAL · Service x-ui is down · 03:12
Status failed, SubState failed

[♻️ Restart x-ui] [📜 x-ui log]
[📊 Current state] [🏠 Menu]
[😴 I know, until morning]
```

An action result with the return code and a contextual button:

```text
✅ x-ui restarted in 1.2 s
Service x-ui restarted
[📜 x-ui log] [🏠 Menu]
```

## Compatibility

| Item | Support |
|---|---|
| **Services** | systemd (Debian/Ubuntu, RHEL/Rocky/Alma, Fedora, openSUSE, Arch), OpenRC (Alpine, Gentoo), SysVinit |
| **Packages** | `apt`, `dnf`, `yum`, `zypper`, `pacman` — detected automatically |
| **Python** | 3.10+; the installer picks a suitable interpreter |
| **Panel** | 3X-UI 2.x and 3.x (`/panel/api/...`, Bearer token) |
| **sudo** | classic sudo and `sudo-rs` (Ubuntu 26.04+): rules are generated without wildcards or aliases |
| **Architectures** | x86_64, ARM — anything with Python and a supported init system |

You can pick the service manager explicitly with `--init systemd|openrc|sysv`.
Without systemd the bot still works: the log is read from `bot.log`, and service
state is reported as unavailable (no false “crit” alerts).

## Quick start

### 1. What you need

- a Linux server with the 3X-UI panel;
- a Telegram bot token from [@BotFather](https://t.me/BotFather);
- your numeric ID (e.g. [@userinfobot](https://t.me/userinfobot));
- a panel API token with the `node-sync` or `admin` scope — see the
  [next section](#connecting-to-the-panel).

### 2. Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)
```

Or through a pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh | sudo bash
```

The wizard asks questions as **step 4, before installing dependencies**, so
answer them at the beginning. Questions are read from the terminal (`/dev/tty`),
so the dialogue works even when the script is piped.

What the wizard asks:

1. the bot token and admin IDs (the token is verified with Telegram immediately);
2. the panel address, `webBasePath` and API token (the address is verified);
3. the services to watch;
4. an optional alert chat.

### 3. After installation

```bash
sudo xui-menu          # menu: state, settings, restart, log, update
systemctl status xui-monitor-bot
```

Open the bot in Telegram, press `/start` → `/status`. If something is wrong, the
bot tells you what exactly.

### Unattended install

```bash
sudo env \
  TELEGRAM_BOT_TOKEN='123456789:AA...' \
  TELEGRAM_ADMIN_IDS='11111111' \
  PANEL_URL='https://panel.example.com:2053' \
  PANEL_API_TOKEN='...' \
  bash install.sh --yes
```

Useful options: `--repo owner/name`, `--ref branch`, `--prefix DIR`, `--user NAME`,
`--no-start`, `--force-config`, `--print-source` (show the version source),
`--sha256 HASH`, `--init TYPE`, `--menu`. Full list — `--help`.

If an incomplete `.env` is left from an aborted run, the wizard fills in the
missing values and offers the existing ones as defaults — no manual editing.

## Connecting to the panel

### 3X-UI token

The token is created **inside the panel**, not in Telegram and not in the bot.

**3X-UI v3.x**

1. “Panel settings → Account → API token” → create.
2. Scope: **`node-sync`** (or `admin`).
3. **Copy the value immediately** — the panel shows it once, later only a
   “configured” marker remains.

The same via API: `POST /panel/api/setting/apiTokens/create` with `name`, `scope`,
`expiresAt` (an existing admin token is required).

**3X-UI 2.x**

“Settings → API Tokens”, or from the terminal:

```bash
sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
```

On v3 this command no longer creates tokens — it only rotates the service
`cli-fallback` one.

Why not `monitor`: that scope only exposes panel status. Inbounds and
subscriptions need `node-sync`; actions inside the panel need `admin`.

### `webBasePath` — a common mistake

`webBasePath` is the separate **“Panel URL”** setting in 3X-UI. It is usually `/`,
and then the wizard field is left empty.

Do not enter the `/panel` path from the browser: the 3X-UI UI always lives at
`.../panel`, and the API at `.../panel/api`. Putting `/panel` into `webBasePath`
adds a duplicate segment (`.../panel/panel/api`).

The installer checks the path before requesting the token (a valid path answers
`401` without a token, a wrong one `404`) and silently fixes the mistake. A full
address pasted into that field is recognised too.

### Manual check

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Requested-With: XMLHttpRequest' \
  https://panel.example.com:2053/panel/api/server/status
```

`401` means the path is correct and a token is needed. With the token:

```bash
curl -fsS -H "Authorization: Bearer <token>" \
  https://panel.example.com:2053/panel/api/server/status
```

The response must contain `"success":true`.

### Address: IP, domain, localhost

- If the panel has **“Panel domain”** (`webDomain`) configured, 3X-UI checks the
  `Host` header and answers `403` to everything else. Then `PANEL_URL` must use
  exactly that name, not the IP.
- The name must resolve **from the server itself**. If the domain points to a
  public IP and there is no hairpin NAT, use `http://127.0.0.1:2053` with the same
  `PANEL_BASE_PATH`. Write IPv6 in brackets: `http://[::1]:2053`.
- `http` is fine when the panel listens on localhost: traffic never leaves the
  machine. If the panel is reachable publicly, use `https://` only — otherwise the
  token and API contents travel in plain text. For a self-signed certificate, do
  not disable verification; set `PANEL_CA_BUNDLE` instead.
- The bot does not follow redirects: if `http` redirects to `https`, just change
  the scheme.

## How it works

```text
Telegram  ⇄  bot (unprivileged user xui-monitor)
                 ├─ psutil / systemctl / journalctl        — metrics and services
                 ├─ panel API (Bearer, node-sync scope)     — inbounds, subscriptions, xray restart
                 └─ sudo xui-ctl (narrow sudoers)           — restart, apt, reboot, journal
```

- **Code** — `/opt/xui-monitor-bot`, owned by `root`; the bot cannot rewrite itself.
- **Secrets** — `.env` with `640 root:xui-monitor`.
- **State** — SQLite in `/var/lib/xui-monitor` (history, audit, mutes).
- **Privileges** — only through `sudo xui-ctl`; the allowed subcommands are listed
  exactly, without wildcards.
- **`apt`** — runs in dedicated systemd units (`xui-monitor-apt-*.service`)
  because the bot lives with `ProtectSystem=strict`.

## Commands and screens

| Command | What it shows |
|---|---|
| `/status` | a one-line verdict, issues, metrics, services and panel |
| `/traffic` | per-inbound totals and top clients by traffic |
| `/services` | service state; restart and log next to each |
| `/panel` | panel and xray availability, versions, updates |
| `/subs` | subscriptions: expiry, traffic, depleted and disabled |
| `/history` | hourly CPU/RAM/disk averages for the last day |
| `/logs` | selected service log with an “errors only” filter |
| `/audit` | bot action log with “denied / actions” filters |
| `/maint` | maintenance: `apt`, release upgrade, reboot, allowlisted command |
| `/run <command>` | run a diagnostic command from the allowlist |
| `/help` | plain-language help: what to press and what the numbers mean |

Buttons duplicate the commands; every screen has “🏠 Menu”, so you cannot get
stuck. Guests do not see the buttons. Moving between menus replaces the previous
screen, so no chain of messages piles up (Details and Collapse are the exception:
they expand the same screen).

**A server command can be sent from any section** — just send it as a message (or
`/run <command>`). The bot asks for confirmation and returns the output. Only safe
diagnostic commands from `COMMAND_ALLOWLIST` are allowed; `/run` without arguments
lists them. There is no arbitrary shell.

### Safe diagnostic commands

`/run <command>` (or just send it as a message) executes only commands from
`COMMAND_ALLOWLIST`. All of them are **read-only** and run as argv without a
shell: no pipes, redirects or writes. If a command is not installed, the bot says
so explicitly.

| Command | What it shows |
|---|---|
| `os_logs` / `os_errors` | OS journal: warnings / errors only (100 lines) |
| `kernel_logs` / `dmesg` | kernel messages |
| `xui_logs` | the `x-ui` service journal |
| `xui_service` / `x-ui status` | panel status |
| `failed_units` | failed systemd units |
| `sockets` / `net_summary` | listening ports / socket summary |
| `addresses` / `routes` | IP addresses / routes |
| `top_cpu` | processes by CPU and memory |
| `uname` / `cpu_info` / `block_devices` | system, CPU, disks |
| `log_size` / `inodes` / `df -h` / `free -h` / `uptime` | space and resources |
| `upgradable` / `upgrade_sim` | available updates / `apt` simulation |
| `vmstat` / `time_status` / `logged_in` / `boot_time` | load, time, sessions |
| `journalctl --disk-usage` / `systemctl list-timers --all` | journal and timers |

Examples: `/run os_logs`, `/run failed_units`, `/run sockets`.

**Commands and buttons work only in the administrator's private chat.** In a group
the bot refuses: addresses, subscriptions and traffic must not leak into a shared
chat. `ALERT_CHAT_ID` allows *outgoing* alerts to a group only — management there
is unavailable.

**Maintenance actions are separate and report to the chat.** `apt update` refreshes
indexes only; `full-upgrade` installs everything; `phased` allows Ubuntu phased
updates; “run release” runs `do-release-upgrade`, which first checks for a new OS
version and says so if there is none. These are different things: `apt update`
does not check for a new OS version. The bot watches the background systemd unit
and sends the exit code plus the log tail when it finishes — no `journalctl`
needed. One action is one message: “queued” → “running” → report, no pile-up. To
look manually: `sudo xui-menu apt-logs`.

`/status` starts with a verdict; details are hidden behind “📋 Details”. Metrics
are aligned in a monospace block, and time is labelled “server time”. Alerts carry
`♻️ Restart`, `📜 Log` and `😴 I know, until morning` — an 8-hour mute stored in
SQLite that survives restarts.

## `xui-menu` settings menu

A console panel is available after installation:

```bash
sudo xui-menu              # interactive menu
sudo xui-menu show         # settings (secrets masked)
sudo xui-menu keys         # which keys may be changed
xui-menu check PANEL_TIMEOUT 20   # validate without changes (no root)
sudo xui-menu set PANEL_TIMEOUT 20
sudo xui-menu restart       # apply
sudo xui-menu restart-xray  # restart xray via the panel API
sudo xui-menu apt-logs 100  # log of background apt upgrades
sudo xui-menu logs 100
```

The interactive menu is drawn in a frame and grouped into sections; the live
service state is shown on top:

```text
╔════════════════════════════════════════════════════════╗
│         3X-UI monitor bot — control panel              │
│  Service xui-monitor-bot: ● running                    │
│   Diagnostics                                          │
│    1. Service state and recent errors                  │
│    2. Show settings (secrets masked)                   │
│    3. Bot log (last 50 lines)                          │
│────────────────────────────────────────────────────────│
│   Telegram                                             │
│    4. Bot token (@BotFather)                           │
│    5. Admin IDs                                        │
│    6. Alert chat                                       │
│   …                                                    │
│   22. Remove the bot                                   │
│    0. Exit                                             │
╚════════════════════════════════════════════════════════╝
```

### What each item does and what to enter

| Item | What it does | What to enter | Example |
|---|---|---|---|
| 1 | service state and recent errors | — | — |
| 2 | shows settings, secrets masked | — | — |
| 3 | bot log, 50 lines | — | — |
| 4 | Telegram bot token | the value from @BotFather | `123456789:AAH...` |
| 5 | administrator IDs | numbers separated by commas | `11111111,22222222` |
| 6 | alert chat | chat ID; group IDs are negative; empty — private | `-1001234567890` |
| 7 | panel address (`PANEL_URL`) | scheme, host and port, **no path** | `https://panel.example.com:5580` |
| 8 | `webBasePath` (`PANEL_BASE_PATH`) | the “Panel URL” value from 3X-UI; usually empty. **Not** `/panel` | empty or `mypanel` |
| 9 | panel API token | the token value (`node-sync`/`admin` scope) | `AbCdEf...` |
| 10 | TLS and CA (submenu) | `PANEL_VERIFY_TLS`: `true`/`false`; `PANEL_CA_BUNDLE`: PEM path | `true`, `/etc/ssl/panel-ca.pem` |
| 11 | CPU thresholds (submenu) | `CPU_WARN` and `CPU_CRIT`, % | `80`, `95` |
| 12 | memory thresholds (submenu) | `MEM_WARN` and `MEM_CRIT`, % | `85`, `95` |
| 13 | disk thresholds (submenu) | `DISK_WARN` and `DISK_CRIT`, % | `85`, `93` |
| 14 | hysteresis and cooldown (submenu) | `ALERT_HYSTERESIS` in %, `ALERT_COOLDOWN` in s | `5`, `1800` |
| 15 | days before a subscription expiry warning | a whole number of days | `3` |
| 16 | monitored services | service names separated by commas | `x-ui,fail2ban` |
| 17 | log level | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| 18 | any allow-listed key | the key name first, then the value | `DISK_PATH` → `/` |
| 19 | restart the bot and verify | — | — |
| 20 | restart xray via the API | `y` to confirm | `y` |
| 21 | update from `main` | — | — |
| 22 | removal | `yes` to confirm | `yes` |

An empty value clears a setting: `sudo xui-menu set PANEL_BASE_PATH ""`.
For items 4–17 you can skip the menu and use
`sudo xui-menu set KEY VALUE` directly (same as item 18, one command).

```bash
# value examples
sudo xui-menu set PANEL_URL https://panel.example.com:5580
sudo xui-menu set PANEL_BASE_PATH ""          # “Panel URL” = /
sudo xui-menu set TELEGRAM_ADMIN_IDS 11111111,22222222
sudo xui-menu set ALERT_CHAT_ID -1001234567890
sudo xui-menu set SERVICES x-ui,fail2ban
sudo xui-menu set CPU_WARN 80
sudo xui-menu set LOG_LEVEL INFO

# validate a value without changing anything
xui-menu check CPU_WARN 80

# log of background updates (apt update / upgrade / phased / release)
sudo xui-menu apt-logs 100
```

Worth knowing:

- input is validated: a wrong number prints the valid range, `q` exits;
- only allow-listed keys can be changed (`xui-menu keys`); values are validated
  before writing;
- `.env` is rewritten atomically with `640 root:<service user>`;
- secrets are never printed in full;
- paths can be overridden: `sudo PREFIX=/opt/bot ENV_FILE=/opt/bot/.env xui-menu`;
- screen clearing can be disabled: `XUI_MENU_NO_CLEAR=1 sudo xui-menu`.

## Settings

All parameters live in `/opt/xui-monitor-bot/.env`; a commented example is in
[`.env.example`](.env.example). Process environment variables take precedence
over the file.

| Key | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot token from @BotFather |
| `TELEGRAM_ADMIN_IDS` | administrator IDs, comma-separated |
| `ALERT_CHAT_ID` | alert chat (empty — admins in private) |
| `TELEGRAM_API_URL` | custom Bot API or proxy (official by default) |
| `PANEL_URL` | panel address, e.g. `https://panel.example.com:2053` |
| `PANEL_BASE_PATH` | “Panel URL” (`webBasePath`), if set |
| `PANEL_API_TOKEN` | panel token (`node-sync` scope) |
| `PANEL_VERIFY_TLS` | verify the panel TLS certificate |
| `PANEL_CA_BUNDLE` | custom CA file (PEM); overrides `PANEL_VERIFY_TLS` |
| `PANEL_TIMEOUT` | panel request timeout, seconds |
| `SERVICES` | services to watch, comma-separated (`x-ui,fail2ban`) |
| `DISK_PATH` | path to check for free space |
| `CPU_WARN` | CPU warning threshold, % |
| `CPU_CRIT` | CPU critical threshold, % |
| `MEM_WARN` | memory warning threshold, % |
| `MEM_CRIT` | memory critical threshold, % |
| `DISK_WARN` | disk warning threshold, % |
| `DISK_CRIT` | disk critical threshold, % |
| `ALERT_HYSTERESIS` | how far a metric must recover before an alert clears, % |
| `ALERT_COOLDOWN` | how often an active alert repeats, seconds |
| `EXPIRING_SOON_DAYS` | days before expiry when a subscription is “soon” |
| `CHECK_INTERVAL` | background check period, seconds (minimum 15) |
| `ACTION_COOLDOWN` | pause between identical actions, seconds |
| `CONFIRM_TTL` | confirmation button lifetime, seconds |
| `COMMAND_TIMEOUT` | allowlisted command timeout, seconds |
| `COMMAND_ALLOWLIST` | safe diagnostic commands (aliases, see above) |
| `STATE_PATH` | SQLite database |
| `LOG_PATH` | log file |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SUDO_WRAPPER` | path to `xui-ctl` |

### Where alerts go

Leave `ALERT_CHAT_ID` empty and alerts go to every ID in `TELEGRAM_ADMIN_IDS` in
private. For a shared chat, add the bot there, send a message and find the chat ID
(`@userinfobot`, `@getidsbot`); group IDs are negative, e.g. `-1001234567890`.

Remember: alerts contain addresses, subscriptions and traffic — everyone in the
group will see them. Commands and buttons still work only in private chat.

## Security

- The bot runs as the unprivileged `xui-monitor` user; code and venv belong to
  `root`, and `.env` is read-only for the service.
- All privileged operations go through `sudo xui-ctl` and a narrow sudoers list of
  **exact commands** (no wildcards in arguments — required by both classic sudo
  and `sudo-rs`). There is no arbitrary shell.
- `apt` runs in dedicated `xui-monitor-apt-*.service` units that do not inherit the
  bot's sandbox; a fallback exists when a transient unit is denied.
- Risky actions require confirmation; the token is bound to the requesting
  administrator, is single-use, and everything is written to `audit_log`.
- The panel client does not follow redirects, panel values are escaped, and the
  bot token never reaches the logs even at `DEBUG`.
- Repeats are limited by `ACTION_COOLDOWN`; Telegram flood control is retried with
  a pause, and long messages are split by lines.
- Commands are accepted only in private chat; outsider attempts are audited
  (`denied`).
- The installer can verify the archive checksum (`--sha256`).
- Secrets are stripped from logs by a formatter (including exception text),
  Telegram IDs are masked (`…789`), and `xui-menu show/check` never print secrets.
- For a self-signed panel certificate use `PANEL_CA_BUNDLE` instead of disabling
  TLS verification.

Service resources are limited (`MemoryMax=256M`, `TasksMax=64`), command output is
truncated (16 KB) and has a timeout.

## Update and removal

```bash
# update (config and state are kept)
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)

# same from the menu
sudo xui-menu          # item 21

# full removal: service, sudoers, wrapper, menu, code, database and user
sudo bash install.sh --uninstall

# keep the database and user
sudo bash install.sh --uninstall --keep-state

# installer's interactive menu
sudo bash install.sh --menu
```

Removal prints a single “Bot removed.” line and closes `xui-menu`. With
`--keep-state` a warning remains that the database and user were preserved.

> The project publishes no releases or tags: the current version is always the
> `main` branch. GitHub may serve it from cache for a couple of minutes — simply
> re-run the installer.

## Troubleshooting

```bash
systemctl status xui-monitor-bot
journalctl -u xui-monitor-bot -n 50 --no-pager
```

| Symptom | What to do |
|---|---|
| “Invalid sudoers syntax” | the installer prints the `visudo` line — attach it to an issue |
| “System metrics unavailable: `/proc/stat`” | an old install still has `ProcSubset=pid`: `sudo sed -i '/^ProcSubset=/d' /etc/systemd/system/xui-monitor-bot.service && sudo systemctl daemon-reload && sudo systemctl restart xui-monitor-bot` |
| “Token rejected” | check `TELEGRAM_BOT_TOKEN` and `PANEL_API_TOKEN` via `sudo xui-menu` → “Show settings” |
| `inbounds/list unavailable` | the panel token lacks the `node-sync` scope |
| Panel returns `404` for the API | check `PANEL_URL`/`PANEL_BASE_PATH` (a common mistake is `/panel` in `webBasePath`) |
| Service logs are not visible | check the `journal` sudoers rule and that the call goes through `sudo` |
| Ubuntu 26.04+ | `sudo-rs` forbids wildcards in arguments — the installer already accounts for it |

More in [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
pip install -r requirements-dev.txt
ruff format --check .
ruff check .
mypy .
pytest -q
```

Tests need no panel, Telegram or systemd: external calls are mocked. CI runs
Python 3.10 and 3.13, `shellcheck`, `bash -n` and `visudo -cf`.

## Project

- [SECURITY.md](SECURITY.md) — how to report a vulnerability and what is protected.
- [CONTRIBUTING.md](CONTRIBUTING.md) — environment, required checks, style.
- [CHANGELOG.md](CHANGELOG.md) — change history.
- [`.env.example`](.env.example) — every setting with comments.
- [Русский README](README.md).

## License

MIT — see [LICENSE](LICENSE).
