"""Middleware de autenticación.

- En 127.0.0.1 (app de escritorio): toda ruta /api/* exige un token válido, salvo las públicas.
- En la red local (servidor HTTPS para el teléfono): solo redes privadas y solo el teléfono vinculado activo.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from nexora.db import repositorio as repo
from nexora.security import red
from nexora.security.tokens import hash_token

RUTAS_PUBLICAS = {"/api/health", "/api/devices/pair"}
CLAVE_LAN = "nexora_lan"  # la pone el servidor de la red local en el scope ASGI


def extraer_token(request: Request) -> str | None:
    autorizacion = request.headers.get("authorization", "")
    if autorizacion.lower().startswith("bearer "):
        return autorizacion[7:].strip() or None
    return request.headers.get("x-api-key") or None


def _no_autorizado(mensaje: str) -> JSONResponse:
    return JSONResponse({"detail": mensaje}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def desde_red_local(request: Request) -> bool:
    return bool(request.scope.get(CLAVE_LAN))


class AutenticacionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        ruta = request.url.path
        servicios = request.app.state.servicios
        lan = desde_red_local(request)

        if lan:
            if not ruta.startswith("/api/"):
                return JSONResponse({"detail": "No encontrado."}, status_code=404)  # ni /docs ni nada más
            ip_local = (request.scope.get("server") or ("", 0))[0]
            if servicios.settings.exigir_red_privada and not await asyncio.to_thread(red.es_red_privada, ip_local):
                return JSONResponse(
                    {"detail": "La red Wi-Fi es pública o desconocida: el PC no acepta conexiones del teléfono."},
                    status_code=403,
                )
            if ruta == "/api/health":  # para comprobar que el PC está en la red; sin más detalles
                return JSONResponse({"estado": "ok", "app": "nexora", "pc": getattr(request.app.state, "pc_id", None)})

        if request.method == "OPTIONS" or not ruta.startswith("/api/") or ruta in RUTAS_PUBLICAS:
            return await call_next(request)

        token = extraer_token(request)
        if not token:
            return _no_autorizado("Falta el token de acceso.")

        ip = request.client.host if request.client else None

        def autenticar():
            with servicios.db.conectar() as con:
                dispositivo = repo.buscar_dispositivo_por_hash(con, hash_token(token))
                if dispositivo:
                    repo.registrar_acceso(con, dispositivo["id"], ip)
                return dispositivo

        dispositivo = await asyncio.to_thread(autenticar)
        if dispositivo is None:
            return _no_autorizado("Token no válido o revocado.")
        if lan and dispositivo["tipo"] != "movil":
            return _no_autorizado("Desde la red local solo puede conectarse el teléfono vinculado.")
        request.state.dispositivo = dispositivo
        return await call_next(request)
