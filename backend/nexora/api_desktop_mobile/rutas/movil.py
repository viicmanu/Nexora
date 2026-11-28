"""Rutas del teléfono: subidas por fragmentos y reanudables, lista de organizados, descargas y bandeja remota."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import Archivo
from nexora.core_ia.carpetas import CarpetaInvalida, normalizar_carpeta, ruta_libre
from nexora.db import repositorio as repo
from nexora.db.repositorio import Fila
from nexora.security.tokens import BANDEJA, DESCARGAR, LISTAR, ORGANIZAR, SUBIR
from nexora.servicios import Servicios

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["teléfono"])

MAX_FRAGMENTO = 8 * 1024 * 1024
# Ejecutables y scripts no se aceptan desde el teléfono.
EXTENSIONES_BLOQUEADAS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".lnk", ".dll", ".sys", ".reg", ".hta", ".cpl", ".jar", ".msc", ".pif", ".url",
}
_candados: dict[str, asyncio.Lock] = {}
_tareas: set[asyncio.Task] = set()


class SolicitudSubida(BaseModel):
    cliente_id: str = Field(min_length=1, max_length=100, description="Id del elemento en la cola del teléfono.")
    nombre: str = Field(min_length=1, max_length=200)
    tamano: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class Subida(BaseModel):
    id: str
    cliente_id: str
    nombre: str
    tamano: int
    bytes_recibidos: int
    estado: str
    error: str | None
    archivo: Archivo | None


class Organizado(Archivo):
    carpeta_etiqueta: str | None
    tipo: str
    clasificado_en: str | None


class RespuestaOrganizados(BaseModel):
    items: list[Organizado]
    ids: list[str] = Field(description="Todos los organizados: el teléfono borra de su caché los que ya no estén.")
    hasta: str | None = Field(description="Pásalo como `since` en la siguiente sincronización.")


def _consultar(s: Servicios, consulta, *args, **kwargs):
    with s.db.conectar() as con:
        return consulta(con, *args, **kwargs)


def _parcial(s: Servicios, subida_id: str) -> Path:
    return s.settings.dir_datos / "subidas" / f"{subida_id}.part"


def _vista(s: Servicios, fila: Fila) -> Subida:
    archivo = _consultar(s, repo.obtener_archivo, fila["archivo_id"]) if fila["archivo_id"] else None
    return Subida(
        id=fila["id"], cliente_id=fila["cliente_id"], nombre=fila["nombre"], tamano=fila["tamano"],
        bytes_recibidos=fila["bytes_recibidos"], estado=fila["estado"], error=fila["error"],
        archivo=Archivo.desde_fila(archivo) if archivo else None,
    )


def _subida(s: Servicios, dispositivo: Fila, subida_id: str) -> Fila:
    fila = _consultar(s, repo.buscar_subida, dispositivo["id"], subida_id=subida_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subida no encontrada.")
    return fila


def max_bytes(s: Servicios) -> int:
    return int(float(_consultar(s, repo.leer_ajuste, "max_subida_mb", "200")) * 1024 * 1024)


@router.post("/uploads", response_model=Subida)
async def crear_subida(
    solicitud: SolicitudSubida, dispositivo: Fila = Depends(requiere(SUBIR)), s: Servicios = Depends(obtener_servicios)
) -> Subida:
    """Abre una subida. Repetirla con el mismo cliente_id devuelve la existente: así se reanuda sin duplicar."""
    try:
        if re.search(r"[\\/]", solicitud.nombre):
            raise CarpetaInvalida("El nombre no puede contener rutas.")
        nombre = normalizar_carpeta(solicitud.nombre)
    except CarpetaInvalida as exc:
        raise HTTPException(422, str(exc))
    if Path(nombre).suffix.lower() in EXTENSIONES_BLOQUEADAS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Por seguridad no se aceptan programas ni scripts.")

    def abrir() -> Fila:
        existente = _consultar(s, repo.buscar_subida, dispositivo["id"], cliente_id=solicitud.cliente_id)
        if existente:
            return existente
        if solicitud.tamano > max_bytes(s):
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "El archivo supera el tamaño máximo configurado en el PC.")
        (s.settings.dir_datos / "subidas").mkdir(parents=True, exist_ok=True)
        fila = _consultar(
            s, repo.crear_subida, dispositivo["id"], solicitud.cliente_id, nombre, solicitud.tamano, solicitud.sha256.lower()
        )
        _parcial(s, fila["id"]).touch()
        return fila

    return await asyncio.to_thread(lambda: _vista(s, abrir()))


@router.put("/uploads/{subida_id}", response_model=Subida)
async def recibir_fragmento(
    subida_id: str, request: Request, offset: int = Query(ge=0),
    dispositivo: Fila = Depends(requiere(SUBIR)), s: Servicios = Depends(obtener_servicios),
):
    """Añade un fragmento (cuerpo binario, hasta 8 MB) a partir de `offset`, que debe ser lo ya recibido."""
    async with _candados.setdefault(subida_id, asyncio.Lock()):
        fila = await asyncio.to_thread(_subida, s, dispositivo, subida_id)
        if fila["estado"] != "recibiendo":
            raise HTTPException(status.HTTP_409_CONFLICT, f"La subida ya está {fila['estado']}.")
        if offset != fila["bytes_recibidos"]:
            # El teléfono reanuda desde lo que de verdad llegó.
            return JSONResponse(
                {"detail": "Desplazamiento incorrecto.", "bytes_recibidos": fila["bytes_recibidos"]}, status_code=409
            )
        parcial = _parcial(s, subida_id)
        recibidos = 0
        with parcial.open("r+b") as f:
            f.seek(offset)
            f.truncate()  # descarta restos de un fragmento que se cortó a medias
            async for bloque in request.stream():
                recibidos += len(bloque)
                if recibidos > MAX_FRAGMENTO or offset + recibidos > fila["tamano"]:
                    f.truncate(offset)
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Fragmento demasiado grande.")
                f.write(bloque)
        fila = await asyncio.to_thread(_consultar, s, repo.actualizar_subida, subida_id, bytes_recibidos=offset + recibidos)
        log.info("Subida %s: fragmento desde el byte %d (%d bytes)", subida_id[:8], offset, recibidos)
        return await asyncio.to_thread(_vista, s, fila)


@router.post("/uploads/{subida_id}/complete", response_model=Subida)
async def completar(
    subida_id: str, dispositivo: Fila = Depends(requiere(SUBIR)), s: Servicios = Depends(obtener_servicios)
) -> Subida:
    """Verifica el SHA-256, deja el archivo en la carpeta de recibidos y lo manda al pipeline de clasificación."""
    async with _candados.setdefault(subida_id, asyncio.Lock()):
        fila = await asyncio.to_thread(_subida, s, dispositivo, subida_id)
        if fila["estado"] != "recibiendo":
            return await asyncio.to_thread(_vista, s, fila)  # ya completada: repetir no duplica
        if fila["bytes_recibidos"] != fila["tamano"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "Faltan fragmentos por enviar.")
        parcial = _parcial(s, subida_id)

        def verificar_y_mover() -> Path | None:
            resumen = hashlib.sha256()
            with parcial.open("rb") as f:
                for bloque in iter(lambda: f.read(1024 * 1024), b""):
                    resumen.update(bloque)
            if resumen.hexdigest() != fila["hash_sha256"]:
                parcial.write_bytes(b"")  # llegó corrupto: se vuelve a empezar
                _consultar(s, repo.actualizar_subida, subida_id, bytes_recibidos=0)
                return None
            s.settings.carpeta_recibidos.mkdir(parents=True, exist_ok=True)
            destino = ruta_libre(s.settings.carpeta_recibidos, fila["nombre"])
            shutil.move(str(parcial), str(destino))
            return destino

        destino = await asyncio.to_thread(verificar_y_mover)
        if destino is None:
            raise HTTPException(422, "El archivo llegó dañado (SHA-256 distinto). Se enviará de nuevo.")
        fila = await asyncio.to_thread(_consultar, s, repo.actualizar_subida, subida_id, estado="recibida")

    async def organizar() -> None:
        try:
            archivo_id = await s.indexador.procesar(destino, origen=f"móvil: {dispositivo['nombre']}")
            await asyncio.to_thread(
                _consultar, s, repo.actualizar_subida, subida_id, estado="organizada", archivo_id=archivo_id
            )
        except Exception as exc:
            log.exception("No se pudo organizar %s", destino)
            await asyncio.to_thread(_consultar, s, repo.actualizar_subida, subida_id, estado="error", error=str(exc)[:300])

    tarea = asyncio.create_task(organizar())
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return await asyncio.to_thread(_vista, s, fila)


@router.get("/uploads/{subida_id}", response_model=Subida)
async def estado_subida(
    subida_id: str, dispositivo: Fila = Depends(requiere(SUBIR)), s: Servicios = Depends(obtener_servicios)
) -> Subida:
    return await asyncio.to_thread(lambda: _vista(s, _subida(s, dispositivo, subida_id)))


@router.delete("/uploads/{subida_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancelar_subida(
    subida_id: str, dispositivo: Fila = Depends(requiere(SUBIR)), s: Servicios = Depends(obtener_servicios)
) -> None:
    def cancelar():
        fila = _subida(s, dispositivo, subida_id)
        if fila["estado"] == "recibiendo":
            _parcial(s, subida_id).unlink(missing_ok=True)
            _consultar(s, repo.actualizar_subida, subida_id, estado="cancelada")

    await asyncio.to_thread(cancelar)


@router.get("/files/organized", response_model=RespuestaOrganizados, dependencies=[Depends(requiere(LISTAR))])
async def organizados(
    since: str | None = Query(None, description="Solo los cambiados después de esta marca (respuesta anterior)."),
    limite: int = Query(500, ge=1, le=2000),
    s: Servicios = Depends(obtener_servicios),
) -> RespuestaOrganizados:
    def leer():
        filas = _consultar(s, repo.organizados, since, limite)
        catalogo = s.carpetas.catalogo()
        items = [
            Organizado(
                **Archivo.desde_fila(f).model_dump(), carpeta_etiqueta=catalogo.etiqueta(f["categoria"]),
                tipo=(Path(f["nombre"]).suffix.lstrip(".") or "archivo").lower(), clasificado_en=f["clasificado_en"],
            )
            for f in filas
        ]
        return RespuestaOrganizados(
            items=items, ids=_consultar(s, repo.ids_organizados), hasta=filas[-1]["actualizado_en"] if filas else since
        )

    return await asyncio.to_thread(leer)


@router.get("/files/{archivo_id}/download", dependencies=[Depends(requiere(DESCARGAR))])
async def descargar(archivo_id: str, s: Servicios = Depends(obtener_servicios)) -> FileResponse:
    """Solo archivos del índice de Nexora: el teléfono envía un id, nunca una ruta."""
    fila = await asyncio.to_thread(_consultar, s, repo.obtener_archivo, archivo_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado en el índice.")
    ruta = Path(fila["ruta_actual"])
    if not ruta.is_file():
        raise HTTPException(status.HTTP_410_GONE, "El archivo ya no está en el PC.")
    return FileResponse(ruta, filename=fila["nombre"])  # admite Range: la descarga se puede reanudar


@router.post("/pending/{pendiente_id}/ignore", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(requiere(ORGANIZAR, BANDEJA))])
async def ignorar(pendiente_id: str, s: Servicios = Depends(obtener_servicios)) -> None:
    """El archivo se queda donde está y sale de la Bandeja de Dudas."""
    if await asyncio.to_thread(_consultar, s, repo.descartar_pendiente, pendiente_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Duda no encontrada o ya resuelta.")
