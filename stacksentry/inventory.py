"""Инвентаризация панелей и VPN-стека на сервере.

Модуль намеренно использует только стандартную библиотеку: установщик вызывает
его системным Python ещё до создания venv. Бот использует тот же список, чтобы
показать, что найдено и что реально наблюдается.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StackItem:
    key: str
    title: str
    kind: str  # panel | proxy | vpn | mesh
    units: tuple[str, ...]
    paths: tuple[str, ...]
    ports: tuple[int, ...]
    note: str = ""


STACK: tuple[StackItem, ...] = (
    # --- панели ---
    StackItem(
        "3x-ui",
        "3X-UI",
        "panel",
        ("x-ui", "3x-ui", "xui"),
        ("/usr/local/x-ui", "/etc/x-ui", "/opt/x-ui"),
        (2053, 54321, 5580),
        "API поддерживается",
    ),
    StackItem(
        "s-ui",
        "S-UI",
        "panel",
        ("s-ui", "sui"),
        ("/usr/local/s-ui", "/etc/s-ui", "/opt/s-ui"),
        (2096,),
        "служба/порт",
    ),
    StackItem(
        "marzban",
        "Marzban",
        "panel",
        ("marzban", "marzban-node"),
        ("/opt/marzban", "/etc/marzban"),
        (8000, 62050),
        "служба/порт",
    ),
    StackItem(
        "marzneshin",
        "Marzneshin",
        "panel",
        ("marzneshin",),
        ("/opt/marzneshin", "/etc/marzneshin"),
        (8000,),
        "служба/порт",
    ),
    StackItem(
        "pasarguard",
        "PasarGuard",
        "panel",
        ("pasarguard",),
        ("/opt/pasarguard", "/etc/pasarguard"),
        (8000,),
        "служба/порт",
    ),
    StackItem(
        "hiddify",
        "Hiddify",
        "panel",
        ("hiddify", "hiddify-panel", "hiddify-manager"),
        ("/opt/hiddify-manager", "/opt/hiddify", "/etc/hiddify"),
        (8000, 443),
        "служба/порт",
    ),
    StackItem(
        "remnawave",
        "Remnawave",
        "panel",
        ("remnawave", "remnawave-node", "remnawave-backend"),
        ("/opt/remnawave", "/etc/remnawave"),
        (3000, 3010),
        "служба/порт",
    ),
    StackItem(
        "outline",
        "Outline",
        "panel",
        ("shadowbox", "outline-ss-server", "outline"),
        ("/opt/outline", "/etc/outline"),
        (443, 9000),
        "служба/порт",
    ),
    # --- прокси/ядра ---
    StackItem(
        "xray",
        "Xray",
        "proxy",
        ("xray", "xray-core"),
        ("/usr/local/bin/xray", "/usr/local/etc/xray", "/etc/xray"),
        (443,),
        "ядро; состояние через 3X-UI",
    ),
    StackItem(
        "v2ray",
        "V2Ray",
        "proxy",
        ("v2ray",),
        ("/usr/local/etc/v2ray", "/etc/v2ray"),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "sing-box",
        "sing-box",
        "proxy",
        ("sing-box",),
        ("/etc/sing-box", "/usr/local/etc/sing-box"),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "hysteria",
        "Hysteria",
        "proxy",
        ("hysteria", "hysteria-server", "hysteria2"),
        ("/etc/hysteria", "/etc/hysteria2"),
        (443, 36712),
        "служба/порт",
    ),
    StackItem(
        "tuic",
        "TUIC",
        "proxy",
        ("tuic", "tuic-server"),
        ("/etc/tuic",),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "trojan",
        "Trojan",
        "proxy",
        ("trojan", "trojan-go"),
        ("/etc/trojan", "/etc/trojan-go"),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "naiveproxy",
        "NaiveProxy",
        "proxy",
        ("naive", "naiveproxy"),
        ("/etc/naiveproxy",),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "mieru", "Mieru", "proxy", ("mieru",), ("/etc/mieru",), (443,), "служба/порт"
    ),
    StackItem(
        "shadowtls",
        "ShadowTLS",
        "proxy",
        ("shadow-tls", "shadowtls"),
        ("/etc/shadow-tls",),
        (443,),
        "служба/порт",
    ),
    StackItem(
        "brook", "Brook", "proxy", ("brook",), ("/etc/brook",), (9999,), "служба/порт"
    ),
    StackItem(
        "mtg",
        "MTProto (mtg)",
        "proxy",
        ("mtg", "mtproto-proxy"),
        ("/etc/mtg",),
        (443, 3128),
        "служба/порт",
    ),
    StackItem(
        "shadowsocks",
        "Shadowsocks",
        "proxy",
        (
            "shadowsocks-libev",
            "shadowsocks",
            "ss-server",
            "ssserver",
            "shadowsocks-rust",
        ),
        ("/etc/shadowsocks-libev", "/etc/shadowsocks", "/etc/shadowsocks-rust"),
        (8388,),
        "служба/порт",
    ),
    StackItem(
        "squid", "Squid", "proxy", ("squid",), ("/etc/squid",), (3128,), "служба/порт"
    ),
    StackItem(
        "dante",
        "Dante SOCKS",
        "proxy",
        ("danted", "sockd"),
        ("/etc/danted.conf", "/etc/sockd.conf"),
        (1080,),
        "служба/порт",
    ),
    StackItem(
        "3proxy",
        "3proxy",
        "proxy",
        ("3proxy",),
        ("/etc/3proxy",),
        (3128,),
        "служба/порт",
    ),
    StackItem(
        "gost", "GOST", "proxy", ("gost",), ("/etc/gost",), (8080,), "служба/порт"
    ),
    # --- VPN ---
    StackItem(
        "wireguard",
        "WireGuard",
        "vpn",
        ("wg-quick@wg0", "wg-quick@*", "wireguard", "wireguard-go"),
        ("/etc/wireguard",),
        (51820,),
        "служба/ядро",
    ),
    StackItem(
        "amneziawg",
        "AmneziaWG",
        "vpn",
        ("awg-quick@awg0", "awg-quick@*"),
        ("/etc/amnezia/amneziawg", "/etc/amneziawg"),
        (51820,),
        "служба",
    ),
    StackItem(
        "openvpn",
        "OpenVPN",
        "vpn",
        ("openvpn@server", "openvpn@*", "openvpn"),
        ("/etc/openvpn",),
        (1194,),
        "служба",
    ),
    StackItem(
        "strongswan",
        "IPsec/StrongSwan",
        "vpn",
        ("strongswan", "strongswan-starter", "ipsec"),
        ("/etc/ipsec.d", "/etc/strongswan.d"),
        (500, 4500),
        "служба",
    ),
    StackItem(
        "softether",
        "SoftEther",
        "vpn",
        ("vpnserver", "softether-vpnserver", "softether-vpnbridge"),
        ("/usr/local/vpnserver", "/opt/vpnserver"),
        (443, 992, 5555),
        "служба",
    ),
    StackItem(
        "zerotier",
        "ZeroTier",
        "mesh",
        ("zerotier-one",),
        ("/var/lib/zerotier-one",),
        (9993,),
        "служба",
    ),
    StackItem(
        "netbird",
        "NetBird",
        "mesh",
        ("netbird",),
        ("/etc/netbird", "/var/lib/netbird"),
        (443,),
        "служба",
    ),
    StackItem(
        "headscale",
        "Headscale",
        "mesh",
        ("headscale",),
        ("/etc/headscale",),
        (8080, 50443),
        "служба",
    ),
    StackItem(
        "tailscale",
        "Tailscale",
        "mesh",
        ("tailscaled",),
        ("/var/lib/tailscale",),
        (41641,),
        "служба",
    ),
    StackItem(
        "nebula", "Nebula", "mesh", ("nebula",), ("/etc/nebula",), (4242,), "служба"
    ),
)


#: Дополнительные службы, которые полезно наблюдать вместе со стеком.
EXTRA_UNITS = (
    "fail2ban",
    "docker",
    "containerd",
    "nginx",
    "caddy",
    "haproxy",
    "ssh",
    "sshd",
    "cron",
    "crond",
    "ufw",
    "firewalld",
    "nftables",
    "iptables",
    "chrony",
    "chronyd",
    "systemd-timesyncd",
    "unattended-upgrades",
)


@dataclass(frozen=True, slots=True)
class Detected:
    item: StackItem
    unit: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def watched_hint(self) -> str:
        if not self.unit:
            return "служба не найдена"
        if "unit" in self.evidence:
            return f"служба {self.unit}"
        return f"похоже на {self.unit} (не подтверждено службой)"


def _run(argv: list[str], timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def _units() -> set[str]:
    found: set[str] = set()
    systemctl = shutil.which("systemctl")
    if systemctl:
        for line in _run(
            [
                systemctl,
                "list-unit-files",
                "--type=service",
                "--no-legend",
                "--no-pager",
            ]
        ).splitlines():
            name = line.split()[0] if line.split() else ""
            if name.endswith(".service"):
                found.add(name[: -len(".service")])
    for directory in ("/etc/init.d", "/etc/rc.d", "/usr/local/etc/rc.d"):
        path = Path(directory)
        if path.is_dir():
            with suppress(OSError):
                found.update(item.name for item in path.iterdir() if item.is_file())
    return found


def _matches(unit: str, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate.endswith("*"):
            prefix = candidate[:-1]
            if any(name.startswith(prefix) for name in _units()):
                for name in sorted(_units()):
                    if name.startswith(prefix):
                        return name
        elif unit == candidate:
            return candidate
    return None


def _process_names() -> set[str]:
    text = _run(["ps", "-eo", "comm="])
    return {line.strip() for line in text.splitlines() if line.strip()}


def _listening_ports() -> set[int]:
    text = ""
    if shutil.which("ss"):
        text = _run(["ss", "-H", "-ltn"])
    elif shutil.which("netstat"):
        text = _run(["netstat", "-ltn"])
    ports: set[int] = set()
    for match in re.finditer(r"[:.](\d{1,5})\s", text):
        ports.add(int(match.group(1)))
    return ports


def detect_stack() -> list[Detected]:
    units = _units()
    processes = _process_names()
    detected: list[Detected] = []
    for item in STACK:
        evidence: list[str] = []
        unit = ""
        for candidate in item.units:
            if candidate.endswith("*"):
                prefix = candidate[:-1]
                match = next(
                    (name for name in sorted(units) if name.startswith(prefix)), ""
                )
            else:
                match = candidate if candidate in units else ""
            if match:
                unit = match
                evidence.append("unit")
                break
        if not unit:
            for candidate in item.units:
                name = candidate.rstrip("*").rstrip("@")
                if name and name in processes:
                    evidence.append("process")
                    break
        for path in item.paths:
            if Path(path).exists():
                if "path" not in evidence:
                    evidence.append("path")
                break
        if evidence:
            detected.append(Detected(item=item, unit=unit, evidence=tuple(evidence)))
    return detected


def watched_services() -> list[str]:
    """Службы, которые имеет смысл положить в SERVICES."""
    units = _units()
    services: list[str] = []
    for item in STACK:
        for candidate in item.units:
            if candidate.endswith("*"):
                prefix = candidate[:-1]
                services.extend(
                    sorted(name for name in units if name.startswith(prefix))
                )
            elif candidate in units:
                services.append(candidate)
    for extra in EXTRA_UNITS:
        if extra in units:
            services.append(extra)
    # Ничего не выдумываем: на минимальном хосте служб может не быть.
    return list(dict.fromkeys(services))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    items = detect_stack()
    if "--services" in args:
        print(",".join(watched_services()))
        return 0
    if "--panel" in args:
        confirmed = any(
            item.item.key == "3x-ui"
            and ("unit" in item.evidence or "path" in item.evidence)
            for item in items
        )
        print("1" if confirmed else "0")
        return 0
    if "--json" in args:
        print(
            json.dumps(
                [
                    {
                        "key": d.item.key,
                        "title": d.item.title,
                        "kind": d.item.kind,
                        "unit": d.unit,
                        "evidence": list(d.evidence),
                        "ports": list(d.item.ports),
                        "note": d.item.note,
                    }
                    for d in items
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not items:
        print("Ничего не найдено.")
        return 0
    for detected in items:
        print(
            f"{detected.item.title} ({detected.item.kind}): "
            f"{detected.watched_hint}; признаки: {', '.join(detected.evidence)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
