"""Aplicación FastAPI de Nexora."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nexora import __version__
from nexora.api_desktop_mobile.rutas import ROUTERS
from nexora.config import Settings
from nexora.security.middleware import AutenticacionMiddleware
from nexora.security.tokens import asegurar_token_escritorio
from nexora.servicios import Servicios, construir_servicios

log = logging.getLogger("nexora")


def create_app(settings: Settings | None = None, fabrica: Callable[[Settings], Servicios] | None = None) -> FastAPI:
    settings = settings or Settings()
    fabrica = fabrica or construir_servicios

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        servicios = fabrica(settings)
        app.state.servicios = servicios
        asegurar_token_escritorio(servicios.db, settings.ruta_token_escritorio)
        log.info("Token del escritorio en %s", settings.ruta_token_escritorio)
        if settings.habilitar_watcher:
            await servicios.vigilante.iniciar()
        try:
            yield
        finally:
            if settings.habilitar_watcher:
                await servicios.vigilante.detener()

    app = FastAPI(
        title="Nexora API",
        version=__version__,
        description="Clasificación de archivos con IA, índice semántico y consulta remota de documentos.",
        lifespan=ciclo_de_vida,
        docs_url="/docs" if settings.exponer_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.exponer_docs else None,
    )
    # Orden: el último middleware añadido es el más externo. CORS va fuera para que también
    # las respuestas 401 lleven sus cabeceras.
    app.add_middleware(AutenticacionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origenes,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )

    @app.get("/api/health", tags=["sistema"])
    async def salud() -> dict:
        return {"estado": "ok", "version": __version__, "llm": app.state.servicios.llm.descripcion}

    for router in ROUTERS:
        app.include_router(router)
    return app
