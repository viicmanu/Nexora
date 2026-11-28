"""Red local: direcciones del PC y si la red actual es privada (en Windows, su perfil de red)."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import subprocess
import sys
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

_RANGOS_LAN = [ipaddress.ip_network(r) for r in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
_CACHE_SEGUNDOS = 30.0
_cache: tuple[float, dict[str, str]] | None = None

# Las pruebas pueden sustituirla para no depender de la red del equipo.
comprobar_privada: Callable[[str], bool] | None = None


def es_ip_lan(ip: str) -> bool:
    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return direccion.version == 4 and any(direccion in r for r in _RANGOS_LAN)


def ips_lan() -> list[str]:
    """IPv4 privadas del PC (sin las de enlace local ni las de VPN como Tailscale)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # la IP con la que se sale a la red, por si el nombre del equipo no resuelve todas
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no envía nada: solo elige interfaz
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if es_ip_lan(ip))


def _categorias_windows() -> dict[str, str]:
    """IP local -> categoría de la red de su interfaz (Private, Public, DomainAuthenticated)."""
    script = (
        "$p = @{}; Get-NetConnectionProfile | ForEach-Object { $p[$_.InterfaceIndex] = [string]$_.NetworkCategory };"
        "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $p.ContainsKey($_.InterfaceIndex) } |"
        " ForEach-Object { [pscustomobject]@{ ip = $_.IPAddress; cat = $p[$_.InterfaceIndex] } } | ConvertTo-Json -Compress"
    )
    salida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    datos = json.loads(salida) if salida else []
    if isinstance(datos, dict):
        datos = [datos]
    return {d["ip"]: d["cat"] for d in datos}


def es_red_privada(ip_local: str) -> bool:
    """True si la interfaz por la que llegó la conexión pertenece a una red marcada como privada o de dominio."""
    if comprobar_privada is not None:
        return comprobar_privada(ip_local)
    if sys.platform != "win32":
        return es_ip_lan(ip_local)
    global _cache
    if _cache is None or time.monotonic() - _cache[0] > _CACHE_SEGUNDOS:
        try:
            _cache = (time.monotonic(), _categorias_windows())
        except Exception:
            log.warning("No se pudo consultar el perfil de red de Windows", exc_info=True)
            _cache = (time.monotonic(), {})
    return _cache[1].get(ip_local, "Public") in ("Private", "DomainAuthenticated")


def redes() -> list[dict[str, str | bool]]:
    """Para la interfaz: cada IP local y si su red es privada."""
    return [{"ip": ip, "privada": es_red_privada(ip)} for ip in ips_lan()]
