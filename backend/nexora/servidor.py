"""Arranque de los dos servidores: 127.0.0.1 (HTTP, app de escritorio) y red local (HTTPS, teléfono)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI

from nexora.config import Settings
from nexora.security.middleware import CLAVE_LAN

log = logging.getLogger("nexora")


def app_red_local(app: FastAPI):
    """La misma API, marcando las peticiones como llegadas por la red local. El ciclo de vida lo lleva el servidor local."""

    async def asgi(scope: dict[str, Any], receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                mensaje = await receive()
                if mensaje["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif mensaje["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        scope[CLAVE_LAN] = True
        await app(scope, receive, send)

    return asgi


async def servir(app: FastAPI, settings: Settings) -> None:
    local = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port))
    tarea_local = asyncio.create_task(local.serve())
    while not local.started:  # la red local empieza cuando los servicios ya están listos
        if tarea_local.done():
            return await tarea_local
        await asyncio.sleep(0.1)

    anunciador = None
    tareas = [tarea_local]
    certificado = getattr(app.state, "certificado", None)
    if settings.lan_habilitada and certificado:
        red_local = uvicorn.Server(uvicorn.Config(
            app_red_local(app), host=settings.host_lan, port=settings.puerto_lan, lifespan="on",
            ssl_certfile=str(certificado.certificado), ssl_keyfile=str(certificado.clave), log_level="warning",
        ))
        tareas.append(asyncio.create_task(red_local.serve()))
        try:
            from nexora.security.descubrimiento import Anunciador

            anunciador = Anunciador(app.state.pc_id, app.state.pc_nombre, settings.puerto_lan, certificado.huella)
            await anunciador.iniciar()
        except Exception:
            log.warning("Sin anuncio mDNS: el teléfono usará la IP del QR", exc_info=True)
        log.info("Red local para el teléfono en https://%s:%s", settings.host_lan, settings.puerto_lan)
    try:
        await asyncio.gather(*tareas)
    finally:
        if anunciador:
            await anunciador.detener()
