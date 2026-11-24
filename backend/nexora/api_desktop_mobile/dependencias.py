"""Dependencias de FastAPI compartidas por las rutas."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from nexora.db.repositorio import Fila
from nexora.security.tokens import tiene_permiso
from nexora.servicios import Servicios


def obtener_servicios(request: Request) -> Servicios:
    return request.app.state.servicios


def dispositivo_actual(request: Request) -> Fila:
    """El middleware ya validó el token y dejó el dispositivo en request.state."""
    dispositivo = getattr(request.state, "dispositivo", None)
    if dispositivo is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado.")
    return dispositivo


def requiere(permiso: str) -> Callable[..., Fila]:
    def comprobar(dispositivo: Fila = Depends(dispositivo_actual)) -> Fila:
        if not tiene_permiso(dispositivo["permisos"], permiso):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Este dispositivo no tiene el permiso '{permiso}'.")
        return dispositivo

    return comprobar
