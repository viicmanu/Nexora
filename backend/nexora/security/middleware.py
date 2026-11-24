"""Middleware de autenticación: toda ruta /api/* exige un token de dispositivo válido salvo las públicas."""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from nexora.db import repositorio as repo
from nexora.security.tokens import hash_token

RUTAS_PUBLICAS = {"/api/health", "/api/devices/pair"}


def extraer_token(request: Request) -> str | None:
    autorizacion = request.headers.get("authorization", "")
    if autorizacion.lower().startswith("bearer "):
        return autorizacion[7:].strip() or None
    return request.headers.get("x-api-key") or None


def _no_autorizado(mensaje: str) -> JSONResponse:
    return JSONResponse({"detail": mensaje}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


class AutenticacionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        ruta = request.url.path
        if request.method == "OPTIONS" or not ruta.startswith("/api/") or ruta in RUTAS_PUBLICAS:
            return await call_next(request)

        token = extraer_token(request)
        if not token:
            return _no_autorizado("Falta el token de acceso.")

        db = request.app.state.servicios.db
        ip = request.client.host if request.client else None

        def autenticar():
            with db.conectar() as con:
                dispositivo = repo.buscar_dispositivo_por_hash(con, hash_token(token))
                if dispositivo:
                    repo.registrar_acceso(con, dispositivo["id"], ip)
                return dispositivo

        dispositivo = await asyncio.to_thread(autenticar)
        if dispositivo is None:
            return _no_autorizado("Token no válido o revocado.")
        request.state.dispositivo = dispositivo
        return await call_next(request)
