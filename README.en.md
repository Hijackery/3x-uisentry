# xui-monitor-bot

[![CI](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Hijackery/xui-monitor-bot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-systemd%20%7C%20openrc%20%7C%20sysv-4EAA25)
![License](https://img.shields.io/badge/license-MIT-blue)

[Русский](README.md) · **English**

A Telegram bot for a server with a 3X-UI panel: it watches hardware, systemd
services and the panel, sends alerts, shows subscriptions and traffic. Dangerous
actions (restart a service, restart xray, upgrade the system, reboot) run only
after a button confirmation and only for the administrator.

## Features

- **Monitoring** — system, systemd services, 3X-UI panel, xray, inbounds,
  subscriptions and traffic, plus hourly averages for the last day.
- **Alerts** — hysteresis (no flapping), cooldown and an “I know, until morning”
  mute; every alert carries “Restart” and “Log” buttons.
- **Safe actions** — service/xray restart, `apt` (full and phased), release-upgrade
  check, reboot: confirmation required, everything audited.
- **One-command install** — the wizard asks for tokens immediately, verifies the
  @BotFather token and panel reachability, and finishes with an 8-point self-test.
- **Any popular Linux** — systemd, OpenRC (Alpine, Gentoo), SysVinit;
  `apt`/`dnf`/`yum`/`zypper`/`pacman`; Python 3.10+.
- **`xui-menu` terminal panel** — edit settings, restart, read the log and update,
  with secrets always masked.

## What it looks like

```text
🟢 Server state: all good · CPU 12% · RAM 41% · Disk 63% · x-ui 🟢 · panel 🟢

CPU  ▓▓░░░░░░░░  12.0% 🟢
RAM  ▓▓▓▓░░░░░░  41.0% 🟢
Disk ▓▓▓▓▓▓░░░░  63.0% 🟢
```

```text
🔴 CRITICAL · Service x-ui is not running · 03:12
State failed, SubState failed

[♻️ Restart x-ui] [📜 Log x-ui]
[📊 Current state] [🏠 Back to menu]
[😴 I know, until morning]
```

```text
✅ x-ui restarted in 1.2 s
Service x-ui restarted
[📜 Log x-ui] [🏠 Back to menu]
```

## How it works

```text
Telegram  ⇄  bot (unprivileged user xui-monitor)
                 ├─ psutil / systemctl / journalctl    — metrics and services
                 ├─ panel API (Bearer, scope node-sync) — inbounds, subscriptions, xray restart
                 └─ sudo xui-ctl (narrow sudoers)       — restart, apt, reboot, log
```

Secrets live in `.env` (`640 root:xui-monitor`), state in SQLite
(`/var/lib/xui-monitor`), the code is owned by `root`. See
[Security](#security) and [SECURITY.md](SECURITY.md).

## Requirements

- Linux; the service manager is detected automatically: **systemd**
  (Debian/Ubuntu, RHEL/CentOS/Rocky/Alma, Fedora, openSUSE, Arch/Manjaro) or
  **no systemd** — OpenRC (Alpine, Gentoo) and SysVinit. The package manager is
  detected as well (`apt`/`dnf`/`yum`/`zypper`/`pacman`). Force it manually:
  `--init systemd|openrc|sysv`.
- Python 3.10+ — the installer picks a suitable interpreter itself.
- A 3X-UI panel and its API token.
- A Telegram bot (token from [@BotFather](https://t.me/BotFather)) and your
  numeric ID (get it from [@userinfobot](https://t.me/userinfobot)).

## Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh)
```

or through a pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/Hijackery/xui-monitor-bot/main/install.sh | sudo bash
```

The script downloads the code, installs dependencies, asks for the tokens,
creates the `xui-monitor` system user, enables autostart and runs a self-check at
the end. **The wizard asks its questions early (step 4 of 11) — before installing
dependencies — so answer right away.** If a `.env` is left over from an
interrupted run, the wizard fills in the missing values (existing ones become the
defaults), so there is no need to edit the file by hand. The questions are read from the terminal,
so the dialogue works both with a pipe and with process substitution.

Trailing slashes and spaces in the address (`https://host:5580/`) are stripped by
the installer itself; `webBasePath` is a path (`/panel`, no slashes), and if you
paste an address into that field the installer notices and fixes it.

The token is created **inside the 3X-UI panel** (not in Telegram and not in the
bot):

1. Open the panel → **Settings** → **API Tokens** (some builds call it “API
   Settings”).
2. Create a token with any name (e.g. `monitor-bot`) and the **`node-sync`**
   scope: it exposes inbounds and subscriptions. `monitor` gives status only,
   `admin` is not needed.
3. Copy the value — that is `PANEL_API_TOKEN`.

The quickest way is from the server terminal (the command prints the value):

```bash
sudo x-ui setting -getApiToken true -tokenName monitor-bot -tokenScope node-sync
```

Verify the token works (the response must be `{"success":true,…}`):

```bash
curl -fsS -H "Authorization: Bearer <token>" \
  https://panel.example.com:2053/panel/api/server/status
```

The installer performs the same check itself, and if you leave the field empty
while the panel is running it issues a token automatically.

Unattended mode (values from the environment):

```bash
sudo env \
  TELEGRAM_BOT_TOKEN='123456789:AA...' \
  TELEGRAM_ADMIN_IDS='11111111' \
  PANEL_URL='https://panel.example.com:2053' \
  PANEL_API_TOKEN='...' \
  bash install.sh --yes
```

If the sources are not on GitHub, but on your own server or in your own Git:

```bash
sudo bash install.sh --tarball https://host/xui-monitor-bot.tar.gz
sudo bash install.sh --repo my-login/xui-monitor-bot --ref main
sudo env GIT_URL=https://git.example.com/me/xui-monitor-bot.git bash install.sh --yes
```

Check where the version will come from (without touching the system):

```bash
bash install.sh --print-source --repo my-login/xui-monitor-bot
```

Useful options: `--repo owner/name`, `--ref BRANCH`, `--prefix DIR`, `--user NAME`,
`--no-start`, `--force-config`, `--print-source`, `--sha256 HASH`, `--init TYPE`,
`--menu`. Full list — `--help`.


GitHub may serve the branch from its cache for a couple of minutes after a
repository update — if you see an old revision, just run the command again later.
The code archive is fetched with a cache-busting timestamp and is always current.

### If the system has no systemd

The installer drops `/etc/init.d/xui-monitor-bot` (`start`/`stop`/`status`/
`restart`) and registers autostart via `rc-update` (OpenRC) or
`update-rc.d`/`chkconfig` (SysVinit). Management, `.env`, the sudoers wrapper and
`xui-menu` work the same; the log is taken from `bot.log` when `journalctl` is
unavailable. Differences: no systemd unit isolation, and `/services` reports that
service state is unavailable (the bot says so under “Needs attention”).

## Settings menu (`xui-menu`)

After installation a console panel appears — faster than editing `.env` by hand:

```bash
sudo xui-menu          # interactive menu
sudo xui-menu show     # current settings (secrets are masked)
sudo xui-menu keys     # which keys may be changed
xui-menu check PANEL_TIMEOUT 20   # validate a value without changes (no root)
sudo xui-menu set PANEL_TIMEOUT 20
sudo xui-menu restart  # apply
sudo xui-menu logs 100
```

Structure of the interactive menu:

```text
1) Service state and errors         7) Alert thresholds (CPU)
2) Show settings                    8) Change any key by name
3) Change bot token                 9) Restart the bot
4) Change admin IDs                10) Bot log (50 lines)
5) Panel: address                  11) Update the bot
6) Panel: API token                12) Remove the bot (asks for yes)
0) Exit
```

What each item does:

| Item | Action |
|---|---|
| 1 | `systemctl status` plus the latest warnings from the bot log |
| 2 | show settings; tokens are masked (`123456 ···a1b2`, short ones — `*** (N chars)`) |
| 3–6 | guided change of the bot token, admin IDs, panel address, panel API token |
| 7 | the `CPU_WARN` threshold (other thresholds via item 8) |
| 8 | edit any key from the allowlist by name (`xui-menu keys`) |
| 9 | restart the bot and verify that the service is active |
| 10 | bot log (50 lines) |
| 11 | update: downloads a fresh installer from the `main` branch and runs it with the same `PREFIX`; without network it falls back to the local copy at `/opt/xui-monitor-bot/install.sh` |
| 12 | removal; the menu asks you to type `yes` |

Worth knowing:

- only keys from the allowlist can be changed (`xui-menu keys`); values are
  validated before writing (`check`) — for example `LOG_LEVEL` accepts only
  `DEBUG/INFO/WARNING/ERROR`, and `PANEL_URL` must start with `http`;
- `.env` is rewritten atomically with `640 root:<service user>` permissions, as
  during installation; changes are applied after a restart (the menu offers it);
- secrets are never printed in full, and `.env` should never be shared with
  anyone — it holds both tokens;
- paths can be overridden: `sudo PREFIX=/opt/bot ENV_FILE=/opt/bot/.env xui-menu`.

## Update and removal

```bash
# update (config and state are kept)
bash <(curl -Ls .../install.sh)

# remove the bot (service, sudoers, wrapper, code)
sudo bash install.sh --uninstall

# the same, but also the state and the system user
sudo bash install.sh --purge

# interactive menu
sudo bash install.sh --menu
```

## Commands

| Command | What it shows |
|---|---|
| `/status` | summary: server, services, panel, traffic |
| `/services` | systemd unit state, restart, log |
| `/panel` | panel and xray availability, versions, updates |
| `/subs` | subscriptions: expiry, traffic, disabled |
| `/history` | hourly CPU/RAM/disk averages for a day |
| `/logs` | log of the selected service |
| `/audit` | bot action log |
| `/maint` | maintenance: apt (full and phased), release upgrade, arbitrary command, reboot |
| `/help` | manual: how to use the bot, what each metric means (“❓ Help” button) |

The buttons under messages duplicate the commands: every screen has “🏠 В меню”
(“Back to menu”), so you can never get stuck. Guests are not shown any buttons.

Commands and buttons work **only in the administrator's private chat**: in a group
the bot refuses, so addresses, subscriptions and traffic never leak into a shared
chat.

`/status` starts with a one-line verdict (`всё в норме` / `сервисы не работают: …`),
warnings come right after it, and the details are hidden behind `📋 Подробнее`
(“More”). Metrics are aligned in a monospaced block, the time is labelled
“server time”.

Buttons that collect data show an intermediate “⏳”, and after an action
contextual buttons remain (for example the log of the restarted service). Under an
alert there is `♻️ Restart <service>`, `📜 Log` and `😴 “I know, until morning”`
to mute a specific problem for 8 hours (the mute is stored in SQLite and survives
a restart). The service log offers `🔎 Errors only` and `📜 200 more lines`, and
`/audit` offers “denials / actions” filters. Confirmations explain the
consequences (“the panel and VPN will be down for 1–2 minutes”). Rebooting the
server lives in `/maint` → `🔻 Перезагрузить сервер` (“Reboot the server”), behind
a confirmation.

## Configuration

All settings are in `/opt/xui-monitor-bot/.env`, an annotated example is
[`.env.example`](.env.example).

| Key | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot token from @BotFather |
| `TELEGRAM_ADMIN_IDS` | administrator IDs, comma-separated |
| `ALERT_CHAT_ID` | chat for alerts (empty — admins in private) |
| `TELEGRAM_API_URL` | custom Bot API or proxy (official by default) |
| `PANEL_URL` | panel address: IP or domain name, e.g. `https://panel.example.com:2053` (if the panel has “Panel domain” set, use exactly that) |
| `PANEL_BASE_PATH` | panel `webBasePath`, if set |
| `PANEL_API_TOKEN` | panel token (scope `node-sync`) |
| `PANEL_VERIFY_TLS` | verify the panel TLS certificate (default `true`) |
| `PANEL_CA_BUNDLE` | custom CA file (PEM) for the panel; overrides `PANEL_VERIFY_TLS` |
| `PANEL_TIMEOUT` | panel request timeout, seconds |
| `SERVICES` | monitored units, comma-separated (`x-ui,fail2ban`) |
| `DISK_PATH` | path to check for free space |
| `CPU_WARN` | CPU warning threshold, % |
| `CPU_CRIT` | CPU critical threshold, % |
| `MEM_WARN` | RAM warning threshold, % |
| `MEM_CRIT` | RAM critical threshold, % |
| `DISK_WARN` | disk warning threshold, % |
| `DISK_CRIT` | disk critical threshold, % |
| `ALERT_HYSTERESIS` | how far a metric must fall back to clear the alert |
| `ALERT_COOLDOWN` | how often an active alert is repeated, seconds |
| `EXPIRING_SOON_DAYS` | when a subscription counts as expiring soon |
| `CHECK_INTERVAL` | background check period, seconds (minimum 15) |
| `ACTION_COOLDOWN` | pause between identical actions, seconds |
| `CONFIRM_TTL` | how long the confirmation button lives, seconds |
| `COMMAND_TIMEOUT` | timeout for allowed commands, seconds |
| `COMMAND_ALLOWLIST` | diagnostic command allowlist (must match `install/xui-ctl`) |
| `STATE_PATH` | SQLite database |
| `LOG_PATH` | log file |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SUDO_WRAPPER` | path to `xui-ctl` |

`PANEL_URL` accepts both an IP and a domain name. Two practical rules:

- if the panel has **“Panel domain”** (`webDomain`) configured, 3X-UI validates
  the `Host` header and answers `403` to anything else — then `PANEL_URL` must use
  exactly that name, not the IP;
- the name must resolve **from the server itself** (the bot runs there). If the
  domain points to the public IP and NAT loopback is unavailable, use
  `http://127.0.0.1:2053` with the same `PANEL_BASE_PATH`. Bracket IPv6:
  `http://[::1]:2053`.

Quick check: `curl -fsS -o /dev/null -w '%{http_code}\n' https://your-domain:5580/panel/`
(a `403` means the domain validation is the reason).

Process environment variables take precedence over the `.env` file.

## Security

- The bot runs as the unprivileged `xui-monitor` user; code and venv belong to
  root, and `.env` is read-only for the service.
- All privileged operations go through `/usr/local/bin/xui-ctl` and a narrow
  sudoers allowlist; there is no arbitrary shell. `apt` runs as a separate
  systemd transient unit, because the service lives with `ProtectSystem=strict`.
- Dangerous actions require confirmation, the token is bound to the requesting
  administrator, and everything is written to `audit_log`.
- The panel client does not follow redirects (otherwise the token could leak to a
  foreign host), values from the panel are escaped before sending, and the bot
  token never ends up in logs, even at `DEBUG`.
- Repeats are protected: a confirmation fires once, identical actions are limited
  by `ACTION_COOLDOWN` (including the xray restart), and messages rejected by
  Telegram flood control are retried.
- Commands are accepted only in a private chat; foreign access attempts are
  logged (`denied`), and reading the service log is rate-limited.
- A non-standard `TELEGRAM_API_URL` is reported in the log, and the installer can
  verify the archive checksum (`--sha256`).
- Secrets never reach the logs: third-party loggers (`telegram`, `httpx`) are
  throttled, and a formatter on top strips the bot token from URLs and the exact
  token values from the configuration — including exception tracebacks. Telegram
  IDs in messages are masked (`…789`), and `xui-menu show/check` never print
  secrets.
- For a self-signed panel certificate there is `PANEL_CA_BUNDLE`: instead of
  disabling TLS verification you can supply your own CA and keep pinning.

### Spam and accidental-damage protection

- Every server-changing action requires a button confirmation; the confirmation
  token is single-use and bound to the administrator who requested it.
- Cooldowns: `ACTION_COOLDOWN` (30 s by default) for actions, 5 s for reading the
  log, 2 s for “Refresh” — the buttons cannot be used to load the panel.
- `apt` runs as a separate transient unit, and a second run is impossible while
  the first is going; the command itself is fixed.
- The bot **cannot** run arbitrary commands (`run_command` is six literals from
  the allowlist), delete files, edit system configs, or write to `/usr`, `/boot`,
  `/etc`. Privileges are limited by a narrow sudoers rule for
  `/usr/local/bin/xui-ctl`.
- Service resources are capped (`MemoryMax=256M`, `TasksMax=64`), command output
  is truncated (16 KB) and timed out, and the database is pruned on a schedule.
- Telegram load is capped: on flood control at most 3 attempts and no longer than
  30 s, long messages are split by lines.

## Troubleshooting

```bash
systemctl status xui-monitor-bot
journalctl -u xui-monitor-bot -n 50 --no-pager
```

- “Token rejected” — check `TELEGRAM_BOT_TOKEN` and `PANEL_API_TOKEN`
  (easier through `sudo xui-menu` → “Show settings” / “Change bot token”).
- `inbounds/list unavailable` — the panel token lacks the `node-sync` scope.
- Service logs are not visible — make sure the sudoers file has the `journal` rule.

## Development

Quality checks are needed only when changing the code:

```bash
pip install ruff mypy
ruff format --check .
ruff check .
mypy .
```

## Project

- [SECURITY.md](SECURITY.md) — how to report a vulnerability and what is already
  protected.
- [CONTRIBUTING.md](CONTRIBUTING.md) — environment, required checks, style.
- [CHANGELOG.md](CHANGELOG.md) — what the project already does.
- [`.env.example`](.env.example) — every setting with comments.

## License

MIT — see [LICENSE](LICENSE).
