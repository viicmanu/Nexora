"""Gestión de dispositivos y base del emparejamiento por QR (Fase 2)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import (
    Dispositivo, RespuestaCodigo, RespuestaEmparejar, SolicitudEmparejar,
)
from nexora.db import repositorio as repo
from nexora.db.repositorio import Fila
from nexora.security import emparejamiento
from nexora.security.tokens import DISPOSITIVOS
from nexora.servicios import Servicios

router = APIRouter(prefix="/api/devices", tags=["dispositivos"])


@router.get("", response_model=list[Dispositivo], dependencies=[Depends(requiere(DISPOSITIVOS))])
async def listar(servicios: Servicios = Depends(obtener_servicios)) -> list[Dispositivo]:
    def consultar():
        with servicios.db.conectar() as con:
            return repo.listar_dispositivos(con)

    return [Dispositivo.desde_fila(f) for f in await asyncio.to_thread(consultar)]


@router.post("/pairing-code", response_model=RespuestaCodigo)
async def crear_codigo(
    dispositivo: Fila = Depends(requiere(DISPOSITIVOS)), servicios: Servicios = Depends(obtener_servicios)
) -> RespuestaCodigo:
    """Genera un código de un solo uso (5 minutos) para mostrarlo como QR en el escritorio."""
    ajustes = servicios.settings
    codigo = await asyncio.to_thread(emparejamiento.crear_codigo, servicios.db, dispositivo["id"], ajustes.host, ajustes.port)
    return RespuestaCodigo(expira_en=codigo.expira_en, contenido_qr=codigo.contenido_qr)


@router.post("/pair", response_model=RespuestaEmparejar, status_code=status.HTTP_201_CREATED)
async def emparejar(solicitud: SolicitudEmparejar, servicios: Servicios = Depends(obtener_servicios)) -> RespuestaEmparejar:
    """Ruta pública: el móvil canjea el código del QR por su propio token de solo consulta."""
    resultado = await asyncio.to_thread(
        emparejamiento.canjear_codigo, servicios.db, solicitud.codigo, solicitud.nombre,
        solicitud.plataforma, solicitud.clave_publica,
    )
    if resultado is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Código no válido, caducado o ya usado.")
    return RespuestaEmparejar(dispositivo_id=resultado.dispositivo_id, token=resultado.token, permisos=resultado.permisos)


@router.delete("/{dispositivo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revocar(
    dispositivo_id: str, actual: Fila = Depends(requiere(DISPOSITIVOS)), servicios: Servicios = Depends(obtener_servicios)
) -> None:
    if dispositivo_id == actual["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes revocar el dispositivo con el que estás conectado.")

    def ejecutar():
        with servicios.db.conectar() as con:
            return repo.revocar_dispositivos(con, "id = ?", (dispositivo_id,))

    if await asyncio.to_thread(ejecutar) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado o ya revocado.")
