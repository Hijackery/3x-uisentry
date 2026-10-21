# Совместимость

StackSentry не привязан к одному дистрибутиву или одной панели. Установщик,
меню и бот определяют окружение и показывают только то, что реально применимо.

## Операционные системы и службы

| Компонент | Поддержка |
|---|---|
| Init | systemd, OpenRC, SysVinit; rc.d — по мере доступности |
| Дистрибутивы | Debian/Ubuntu, RHEL/Rocky/Alma, Fedora, openSUSE, Arch, Alpine, Void, Gentoo, Solus, FreeBSD (best effort) |
| Пакеты | `apt`, `dnf`, `yum`, `zypper`, `pacman`, `apk`, `xbps`, `emerge`, `eopkg`, `pkg` |
| Python | 3.10+ |
| Архитектуры | x86_64, ARM и другие, где есть Python и поддерживаемая init-система |
| sudo | классический sudo и `sudo-rs` (Ubuntu 26.04+) |

`stacksentry-ctl` определяет пакетный менеджер самостоятельно. Обновление
индексов и пакетов выполняется его штатными командами; phased- и
релиз-обновления (`do-release-upgrade`) доступны только на APT/Ubuntu.

## Панели

| `PANEL_TYPE` | Авторизация | Данные | Рестарт ядра |
|---|---|---|---|
| `3x-ui` | `PANEL_API_TOKEN`, Bearer | inbound'ы, клиенты, трафик, версии | `POST /panel/api/server/restartXrayService` |
| `marzban` | `PANEL_USERNAME` + `PANEL_PASSWORD`, OAuth2 | пользователи, трафик | `POST /api/core/restart` |
| `marzneshin` | `PANEL_USERNAME` + `PANEL_PASSWORD`, OAuth2 | пользователи, трафик | через `PUT /api/nodes/{id}/{backend}/config` с текущим конфигом |
| `pasarguard` | `PANEL_USERNAME` + `PANEL_PASSWORD`, OAuth2 | пользователи, трафик | `POST /api/core/{id}/restart` |
| `remnawave` | `PANEL_API_TOKEN`, Bearer | пользователи, трафик | нет в API |
| `s-ui` | `PANEL_API_TOKEN`, заголовок `Token` | клиенты, трафик | `POST /apiv2/restartSb` |
| `hiddify` | `PANEL_API_TOKEN` (admin UUID), `Hiddify-API-Key` | пользователи, трафик | нет в API |
| `outline` | секретный `SB_API_PREFIX` в `PANEL_URL` | access keys, трафик | нет в API |

Особенности адресов:

- 3X-UI: `PANEL_URL` — схема/хост/порт, `PANEL_BASE_PATH` — `webBasePath`.
- S-UI: `PANEL_URL` включает web path, например `http://host:2095/app`.
- Hiddify: `PANEL_URL` включает admin proxy path, например `https://host/AbCdEf`.
- Outline: `PANEL_URL` включает секретный `SB_API_PREFIX`, например
  `https://host:port/SecretPath`.
- Marzneshin/PasarGuard/Marzban/Remnawave: корневой адрес панели.

Marzneshin не имеет отдельного endpoint перезапуска. Бот читает список узлов,
берёт backend (приоритет — xray), получает его конфиг и отправляет тот же конфиг
обратно: панель вызывает `RestartBackend` на marznode. Нужен sudo-админ панели и
живой узел; настройки не меняются.

## Протоколы и ядра

Определяются по юнитам, путям конфигов и именам процессов:

- панели: 3X-UI, S-UI, Marzban, Marzneshin, PasarGuard, Hiddify, Remnawave, Outline;
- прокси/ядра: Xray, V2Ray, sing-box, Hysteria/Hysteria2, TUIC, Trojan, NaiveProxy, Mieru, ShadowTLS, Brook, mtg, Shadowsocks, Squid, Dante, 3proxy, GOST;
- VPN/mesh: WireGuard, AmneziaWG, OpenVPN, IPsec/StrongSwan, SoftEther, Tailscale, ZeroTier, NetBird, Headscale, Nebula.

У большинства протоколов нет стандартного management API. Для них StackSentry
честно показывает службу/порт/процесс и не выдумывает метрики. Docker-контейнеры
видны как служба `docker`, но Docker API не опрашивается: это дало бы боту
root-эквивалентные права.

## Ограничения

- Рестарт ядра доступен только там, где панель предоставляет операцию (см. таблицу).
- Outline не имеет рестарта в Management API; используйте рестарт службы/контейнера.
- Marzneshin-рестарт зависит от доступности узла и прав администратора панели.
- Релиз-обновления ОС и phased-обновления — только APT/Ubuntu.
- Сторонние панели и протоколы могут менять API между версиями: адаптеры стоит
  проверять после крупных обновлений панели.
