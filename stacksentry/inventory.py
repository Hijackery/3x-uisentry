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
    StackItem(
        "3x-ui",
        "3X-UI",
        "panel",
        ("x-ui", "3x-ui", "xui"),
        ("/usr/local/x-ui", "/etc/x-ui", "/opt/x-ui"),
        (2053, 54321, 5580, 2096),
        "API поддерживается",
    ),
    StackItem(
        "s-ui",
        "S-UI",
        "panel",
        ("s-ui", "sui"),
        ("/usr/local/s-ui", "/etc/s-ui", "/opt/s-ui"),
        (2096,),
        "только служба и порт",
    ),
    StackItem(
        "marzban",
        "Marzban",
        "panel",
        ("marzban", "marzban-node"),
        ("/opt/marzban", "/etc/marzban"),
        (8000, 62050),
        "только служба и порт",
    ),
    StackItem(
        "hiddify",
        "Hiddify",
        "panel",
        ("hiddify", "hiddify-panel", "hiddify-manager"),
        ("/opt/hiddify-manager", "/opt/hiddify", "/etc/hiddify"),
        (8000, 443),
        "только служба и порт",
    ),
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
        "sing-box",
        "sing-box",
        "proxy",
        ("sing-box",),
        ("/etc/sing-box", "/usr/local/etc/sing-box"),
        (443,),
        "только служба и порт",
    ),
    StackItem(
        "wireguard",
        "WireGuard",
        "vpn",
        ("wg-quick@wg0", "wg-quick@*", "wireguard"),
        ("/etc/wireguard",),
        (51820,),
        "только служба",
    ),
    StackItem(
        "amneziawg",
        "AmneziaWG",
        "vpn",
        ("awg-quick@awg0", "awg-quick@*"),
        ("/etc/amnezia/amneziawg", "/etc/amneziawg"),
        (51820,),
        "только служба",
    ),
    StackItem(
        "openvpn",
        "OpenVPN",
        "vpn",
        ("openvpn@server", "openvpn@*", "openvpn"),
        ("/etc/openvpn",),
        (1194,),
        "только служба",
    ),
    StackItem(
        "shadowsocks",
        "Shadowsocks",
        "proxy",
        ("shadowsocks-libev", "shadowsocks", "ss-server"),
        ("/etc/shadowsocks-libev", "/etc/shadowsocks"),
        (8388,),
        "только служба",
    ),
    StackItem(
        "tailscale",
        "Tailscale",
        "mesh",
        ("tailscaled",),
        ("/var/lib/tailscale",),
        (41641,),
        "только служба",
    ),
)

#: Дополнительные службы, которые полезно наблюдать вместе со стеком.
EXTRA_UNITS = ("fail2ban", "docker", "nginx", "caddy")


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
