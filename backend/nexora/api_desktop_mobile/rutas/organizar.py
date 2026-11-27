"""Deshacer, duplicados y organización por lotes de carpetas existentes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import Archivo, Pendiente
from nexora.api_desktop_mobile.rutas.aprendizaje import a_pendiente
from nexora.core_ia.deshacer import NadaQueDeshacer, NoEsDuplicado
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado
from nexora.db import repositorio as repo
from nexora.file_watcher.organizador import CarpetaNoValida
from nexora.security.tokens import ORGANIZAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api", tags=["organizar"], dependencies=[Depends(requiere(ORGANIZAR))])


@router.post("/files/{archivo_id}/undo", response_model=Archivo)
async def deshacer(archivo_id: str, s: Servicios = Depends(obtener_servicios)):
    try:
        return Archivo.desde_fila(await s.deshacer.deshacer(archivo_id))
    except ArchivoNoEncontrado:
        raise HTTPException(404, "Archivo no encontrado en el índice.")
    except NadaQueDeshacer as exc:
        raise HTTPException(409, str(exc))
    except ArchivoNoDisponible as exc:
        raise HTTPException(410, f"El archivo ya no está en {exc}.")
    except PermissionError:
        raise HTTPException(409, "El archivo está abierto en otro programa; ciérralo y vuelve a intentarlo.")


@router.post("/files/{archivo_id}/trash", status_code=204)
async def a_papelera(archivo_id: str, s: Servicios = Depends(obtener_servicios)) -> None:
    try:
        await s.deshacer.a_papelera(archivo_id)
    except ArchivoNoEncontrado:
        raise HTTPException(404, "Archivo no encontrado en el índice.")
    except NoEsDuplicado as exc:
        raise HTTPException(409, str(exc))
    except OSError as exc:
        raise HTTPException(409, f"No se pudo enviar a la papelera: {exc}")


class SolicitudOrganizar(BaseModel):
    ruta: str = Field(min_length=1, max_length=1000)
    recursiva: bool = False


class SolicitudAprobar(BaseModel):
    aprobados: dict[str, str] = Field(description="id del archivo -> carpeta elegida")


class EstadoOrganizar(BaseModel):
    activa: bool
    carpeta: str | None = None
    total: int = 0
    hechos: int = 0
    restantes: int = 0
    estado: str = ""
    lote: list[Pendiente] = []
    resueltos: list[Archivo] = Field(default=[], description="Del lote, los que no necesitan decisión.")
    error: str = ""


async def _estado(s: Servicios) -> EstadoOrganizar:
    tarea = s.organizador.tarea
    if tarea is None:
        return EstadoOrganizar(activa=False)

    def leer():
        with s.db.conectar() as con:
            pendientes = [f for f in repo.listar_pendientes(con, 500) if f["id"] in tarea.lote]
            otros = [f for i in tarea.lote if (f := repo.obtener_archivo(con, i)) and f["estado"] != "pendiente"]
        return pendientes, otros, s.carpetas.catalogo()

    pendientes, otros, catalogo = await asyncio.to_thread(leer)
    return EstadoOrganizar(
        activa=True, carpeta=str(tarea.carpeta), total=tarea.total, hechos=tarea.hechos,
        restantes=len(tarea.pendientes), estado=tarea.estado,
        lote=[a_pendiente(f, catalogo, s) for f in pendientes], resueltos=[Archivo.desde_fila(f) for f in otros],
        error=tarea.error,
    )


def _fallo(exc: CarpetaNoValida):
    return HTTPException(409, str(exc))


@router.get("/organize", response_model=EstadoOrganizar)
async def estado(s: Servicios = Depends(obtener_servicios)):
    return await _estado(s)


@router.post("/organize", response_model=EstadoOrganizar)
async def preparar(solicitud: SolicitudOrganizar, s: Servicios = Depends(obtener_servicios)):
    """Cuenta los archivos sin organizar. No analiza nada hasta que el usuario lo pide."""
    try:
        await s.organizador.preparar(Path(solicitud.ruta.strip().strip('"')), solicitud.recursiva)
    except CarpetaNoValida as exc:
        raise HTTPException(422, str(exc))
    return await _estado(s)


@router.post("/organize/next", response_model=EstadoOrganizar)
async def siguiente(s: Servicios = Depends(obtener_servicios)):
    try:
        s.organizador.analizar_siguiente()
    except CarpetaNoValida as exc:
        raise _fallo(exc)
    return await _estado(s)


@router.post("/organize/approve", response_model=EstadoOrganizar)
async def aprobar(solicitud: SolicitudAprobar, s: Servicios = Depends(obtener_servicios)):
    try:
        movidos, errores = await s.organizador.aprobar(solicitud.aprobados)
    except CarpetaNoValida as exc:
        raise _fallo(exc)
    respuesta = await _estado(s)
    respuesta.error = "; ".join(errores)
    return respuesta


@router.delete("/organize", status_code=204)
async def cancelar(s: Servicios = Depends(obtener_servicios)) -> None:
    s.organizador.cancelar()
