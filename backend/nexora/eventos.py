"""Eventos en tiempo real (Server-Sent Events): estado del teléfono, vinculaciones y nuevas dudas."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class BusEventos:
    def __init__(self) -> None:
        self._suscriptores: set[asyncio.Queue] = set()

    def suscribir(self) -> asyncio.Queue:
        cola: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._suscriptores.add(cola)
        return cola

    def cancelar(self, cola: asyncio.Queue) -> None:
        self._suscriptores.discard(cola)

    def publicar(self, tipo: str, **datos: Any) -> None:
        mensaje = {"tipo": tipo, **datos}
        for cola in list(self._suscriptores):
            try:
                cola.put_nowait(mensaje)
            except asyncio.QueueFull:  # un cliente que no lee no debe frenar a los demás
                pass


def formato_sse(evento: dict[str, Any]) -> str:
    return f"event: {evento['tipo']}\ndata: {json.dumps(evento, ensure_ascii=False)}\n\n"
