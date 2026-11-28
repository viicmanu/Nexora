"""Anuncio del PC en la red local por mDNS/Zeroconf (_nexora._tcp), para que el teléfono lo encuentre aunque cambie de IP."""

from __future__ import annotations

import asyncio
import logging
import socket

from nexora.security.red import ips_lan

log = logging.getLogger(__name__)
TIPO = "_nexora._tcp.local."


class Anunciador:
    def __init__(self, pc_id: str, nombre: str, puerto: int, huella: str) -> None:
        self.pc_id, self.nombre, self.puerto, self.huella = pc_id, nombre, puerto, huella
        self._zc = None
        self._info = None
        self._ips: list[str] = []
        self._tarea: asyncio.Task | None = None

    def _servicio(self, ips: list[str]):
        from zeroconf import ServiceInfo

        return ServiceInfo(
            TIPO, f"Nexora-{self.pc_id[:8]}.{TIPO}",
            addresses=[socket.inet_aton(ip) for ip in ips], port=self.puerto,
            properties={"id": self.pc_id, "nombre": self.nombre, "huella": self.huella},
            server=f"nexora-{self.pc_id[:8]}.local.",
        )

    async def iniciar(self) -> None:
        from zeroconf.asyncio import AsyncZeroconf

        self._zc = AsyncZeroconf()
        self._tarea = asyncio.create_task(self._vigilar())

    async def _vigilar(self) -> None:
        """Vuelve a anunciarse si cambian las IP del PC (DHCP, cambio de Wi-Fi)."""
        while True:
            ips = await asyncio.to_thread(ips_lan)
            if ips != self._ips:
                try:
                    if self._info:
                        await self._zc.async_unregister_service(self._info)
                    self._info, self._ips = (self._servicio(ips), ips) if ips else (None, ips)
                    if self._info:
                        await self._zc.async_register_service(self._info)
                        log.info("Anunciado en la red local como %s en %s:%s", self._info.name, ips, self.puerto)
                except Exception:
                    log.warning("No se pudo anunciar el PC por mDNS", exc_info=True)
            await asyncio.sleep(60)

    async def detener(self) -> None:
        if self._tarea:
            self._tarea.cancel()
        if self._zc:
            if self._info:
                await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
