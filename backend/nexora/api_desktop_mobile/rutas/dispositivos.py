"""Teléfono vinculado: QR, vinculación 1 a 1, estado en línea y eventos en tiempo real."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import (
    Dispositivo, RespuestaCodigo, RespuestaEmparejar, SolicitudEmparejar, en_linea,
)
from nexora.db import repositorio as repo
from nexora.db.repositorio import Fila
from nexora.eventos import formato_sse
from nexora.security import emparejamiento, red
from nexora.security.tokens import DISPOSITIVOS, VER_DISPOSITIVOS
from nexora.servicios import Servicios

router = APIRouter(prefix="/api/devices", tags=["dispositivos"])
INTERVALO_EVENTOS = 15  # segundos entre comentarios de mantenimiento del flujo SSE


def _consultar(s: Servicios, consulta, *args):
    with s.db.conectar() as con:
        return consulta(con, *args)


def _borrar_subidas_a_medias(s: Servicios, dispositivo_id: str) -> None:
    for subida in _consultar(s, repo.subidas_sin_terminar, dispositivo_id):
        (s.settings.dir_datos / "subidas" / f"{subida['id']}.part").unlink(missing_ok=True)
        _consultar(s, repo.actualizar_subida, subida["id"], estado="cancelada")


class EstadoRed(BaseModel):
    lan: bool
    redes: list[dict]
    puerto: int
    huella: str | None


class Telefono(BaseModel):
    telefono: Dispositivo | None
    red: EstadoRed
    max_subida_mb: float
    app_movil: "VersionMovil | None" = None


class SolicitudRenombrar(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)


class VersionMovil(BaseModel):
    codigo: int
    fecha: str
    tamano: int
    sha256: str


def version_movil(s: Servicios) -> VersionMovil | None:
    """La última app del teléfono publicada con `pnpm movil:publicar` (si hay)."""
    ruta = s.settings.dir_datos / "app_movil" / "version.json"
    if not ruta.is_file() or not (ruta.parent / "nexora.apk").is_file():
        return None
    try:
        return VersionMovil.model_validate_json(ruta.read_text(encoding="utf-8"))
    except ValueError:
        return None


class InfoPC(BaseModel):
    pc_id: str
    nombre: str
    ips: list[str]
    puerto: int
    max_subida_mb: float
    app_movil: VersionMovil | None = None


def max_subida_mb(s: Servicios) -> float:
    return float(_consultar(s, repo.leer_ajuste, "max_subida_mb", "200"))


@router.get("", response_model=list[Dispositivo], dependencies=[Depends(requiere(DISPOSITIVOS))])
async def listar(s: Servicios = Depends(obtener_servicios)) -> list[Dispositivo]:
    return [Dispositivo.desde_fila(f) for f in await asyncio.to_thread(_consultar, s, repo.listar_dispositivos)]


@router.get("/phone", response_model=Telefono, dependencies=[Depends(requiere(DISPOSITIVOS))])
async def telefono(request: Request, s: Servicios = Depends(obtener_servicios)) -> Telefono:
    fila = await asyncio.to_thread(_consultar, s, repo.telefono_activo)
    return Telefono(
        telefono=Dispositivo.desde_fila(fila) if fila else None,
        red=EstadoRed(
            lan=s.settings.lan_habilitada, redes=await asyncio.to_thread(red.redes), puerto=s.settings.puerto_lan,
            huella=getattr(request.app.state, "huella", None),
        ),
        max_subida_mb=await asyncio.to_thread(max_subida_mb, s), app_movil=await asyncio.to_thread(version_movil, s),
    )


@router.post("/pairing-code", response_model=RespuestaCodigo)
async def crear_codigo(
    request: Request,
    reemplazar: bool = Query(False, description="Desvincula el teléfono actual y genera un QR nuevo."),
    dispositivo: Fila = Depends(requiere(DISPOSITIVOS)),
    s: Servicios = Depends(obtener_servicios),
) -> RespuestaCodigo:
    """Código de un solo uso (5 minutos) para mostrarlo como QR en el escritorio."""
    hosts = await asyncio.to_thread(red.ips_lan)
    try:
        codigo = await asyncio.to_thread(
            emparejamiento.crear_codigo, s.db, dispositivo["id"], hosts, s.settings.puerto_lan,
            getattr(request.app.state, "huella", None), reemplazar=reemplazar,
        )
    except emparejamiento.TelefonoYaVinculado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if codigo.revocado:
        await asyncio.to_thread(_borrar_subidas_a_medias, s, codigo.revocado)
        s.eventos.publicar("desvinculado", dispositivo_id=codigo.revocado)
    return RespuestaCodigo(expira_en=codigo.expira_en, contenido_qr=codigo.contenido_qr)


@router.post("/pair", response_model=RespuestaEmparejar, status_code=status.HTTP_201_CREATED)
async def emparejar(solicitud: SolicitudEmparejar, s: Servicios = Depends(obtener_servicios)) -> RespuestaEmparejar:
    """Ruta pública: el teléfono canjea el código del QR por su propio token."""
    try:
        resultado = await asyncio.to_thread(
            emparejamiento.canjear_codigo, s.db, solicitud.codigo, solicitud.nombre,
            solicitud.plataforma, solicitud.clave_publica,
        )
    except emparejamiento.TelefonoYaVinculado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if resultado is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Código no válido, caducado o ya usado.")
    pc_id, nombre = await asyncio.to_thread(emparejamiento.identidad_pc, s.db)
    s.eventos.publicar("vinculado", dispositivo_id=resultado.dispositivo_id, nombre=solicitud.nombre)
    return RespuestaEmparejar(
        dispositivo_id=resultado.dispositivo_id, token=resultado.token, permisos=resultado.permisos,
        pc_id=pc_id, pc_nombre=nombre,
    )


@router.post("/heartbeat", response_model=InfoPC)
async def latido(
    request: Request, dispositivo: Fila = Depends(requiere(VER_DISPOSITIVOS)), s: Servicios = Depends(obtener_servicios)
) -> InfoPC:
    """El teléfono avisa cada ~30 s de que sigue en la red. Sin latidos en 90 s, se considera desconectado."""
    if dispositivo["tipo"] != "movil":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Solo para el teléfono vinculado.")
    estaba = en_linea(dispositivo)
    ip = request.client.host if request.client else None
    await asyncio.to_thread(_consultar, s, repo.registrar_heartbeat, dispositivo["id"], ip)
    if not estaba:
        s.eventos.publicar("telefono", dispositivo_id=dispositivo["id"], en_linea=True)
    return await info_pc(s)


async def info_pc(s: Servicios) -> InfoPC:
    pc_id, nombre = await asyncio.to_thread(emparejamiento.identidad_pc, s.db)
    return InfoPC(
        pc_id=pc_id, nombre=nombre, ips=await asyncio.to_thread(red.ips_lan), puerto=s.settings.puerto_lan,
        max_subida_mb=await asyncio.to_thread(max_subida_mb, s), app_movil=await asyncio.to_thread(version_movil, s),
    )


@router.get("/pc", response_model=InfoPC, dependencies=[Depends(requiere(VER_DISPOSITIVOS))])
async def pc(s: Servicios = Depends(obtener_servicios)) -> InfoPC:
    return await info_pc(s)


@router.get("/app", response_model=VersionMovil | None, dependencies=[Depends(requiere(DISPOSITIVOS, VER_DISPOSITIVOS))])
async def app_disponible(s: Servicios = Depends(obtener_servicios)):
    return await asyncio.to_thread(version_movil, s)


@router.get("/app/apk", dependencies=[Depends(requiere(DISPOSITIVOS, VER_DISPOSITIVOS))])
async def descargar_app(s: Servicios = Depends(obtener_servicios)) -> FileResponse:
    """El APK publicado, para que el teléfono se actualice solo."""
    if await asyncio.to_thread(version_movil, s) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay ninguna versión de la app publicada.")
    return FileResponse(
        s.settings.dir_datos / "app_movil" / "nexora.apk", filename="nexora.apk",
        media_type="application/vnd.android.package-archive",
    )


class InformeFallo(BaseModel):
    nombre: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    texto: str = Field(max_length=300_000)
    version: str = Field(default="", max_length=40)


class ResumenFallo(BaseModel):
    archivo: str
    recibido: str
    tipo: str
    primera_linea: str
    tamano: int


def _carpeta_fallos(s: Servicios):
    return s.settings.dir_datos / "fallos_movil"


@router.post("/crash", status_code=status.HTTP_204_NO_CONTENT)
async def recibir_fallo(
    informe: InformeFallo, dispositivo: Fila = Depends(requiere(VER_DISPOSITIVOS)), s: Servicios = Depends(obtener_servicios)
) -> None:
    """El teléfono envía el informe de un cierre inesperado (se guarda en el PC, nunca sale de la red local)."""
    def guardar():
        carpeta = _carpeta_fallos(s)
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = carpeta / f"{repo.ahora()[:19].replace(':', '-')}_{informe.nombre}"
        destino.write_text(f"telefono: {dispositivo['nombre']}\n{informe.texto}", encoding="utf-8")
        for viejo in sorted(carpeta.glob("*.txt"))[:-200]:  # se conservan los 200 más recientes
            viejo.unlink(missing_ok=True)

    await asyncio.to_thread(guardar)
    s.eventos.publicar("fallo_telefono", nombre=informe.nombre)


@router.get("/crashes", response_model=list[ResumenFallo], dependencies=[Depends(requiere(DISPOSITIVOS))])
async def listar_fallos(s: Servicios = Depends(obtener_servicios)) -> list[ResumenFallo]:
    def leer():
        carpeta = _carpeta_fallos(s)
        resumen = []
        for f in sorted(carpeta.glob("*.txt"), reverse=True)[:50] if carpeta.is_dir() else []:
            lineas = f.read_text(encoding="utf-8", errors="replace").splitlines()
            tipo = next((l.split(":", 1)[1].strip() for l in lineas if l.startswith("tipo:")), "desconocido")
            detalle = next((l for l in lineas[6:] if l.strip() and not l.startswith(("descripcion", "hilo"))), "")
            resumen.append(ResumenFallo(archivo=f.name, recibido=f.name[:19], tipo=tipo, primera_linea=detalle[:200], tamano=f.stat().st_size))
        return resumen

    return await asyncio.to_thread(leer)


@router.get("/crashes/{archivo}", dependencies=[Depends(requiere(DISPOSITIVOS))])
async def ver_fallo(archivo: str, s: Servicios = Depends(obtener_servicios)) -> dict:
    ruta = (_carpeta_fallos(s) / archivo).resolve()
    if ruta.parent != _carpeta_fallos(s).resolve() or not ruta.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Informe no encontrado.")
    return {"archivo": archivo, "texto": await asyncio.to_thread(ruta.read_text, encoding="utf-8", errors="replace")}


@router.get("/events")
async def eventos(
    request: Request,
    max_eventos: int = Query(0, ge=0, description="Cierra el flujo tras N eventos (0 = sin límite)."),
    espera: float = Query(0, ge=0, le=3600, description="Cierra el flujo tras N segundos sin eventos (0 = nunca)."),
    dispositivo: Fila = Depends(requiere(DISPOSITIVOS, VER_DISPOSITIVOS)),
    s: Servicios = Depends(obtener_servicios),
) -> StreamingResponse:
    """Eventos en tiempo real (Server-Sent Events): telefono, vinculado, desvinculado, pendiente."""
    cola = s.eventos.suscribir()
    es_movil = dispositivo["tipo"] == "movil"

    async def flujo():
        enviados = 0
        try:
            fila = await asyncio.to_thread(_consultar, s, repo.telefono_activo)
            yield formato_sse({"tipo": "estado", "telefono": Dispositivo.desde_fila(fila).model_dump() if fila else None})
            while not max_eventos or enviados < max_eventos:
                try:
                    evento = await asyncio.wait_for(cola.get(), timeout=espera or INTERVALO_EVENTOS)
                except asyncio.TimeoutError:
                    if espera:
                        return
                    if await request.is_disconnected():
                        return
                    yield ": sigue\n\n"
                    evento = None
                if es_movil:  # al desvincular se corta la sesión del teléfono
                    actual = await asyncio.to_thread(_consultar, s, repo.obtener_dispositivo, dispositivo["id"])
                    if actual is None or actual["estado"] != "activo":
                        yield formato_sse({"tipo": "desvinculado", "dispositivo_id": dispositivo["id"]})
                        return
                if evento:
                    yield formato_sse(evento)
                    enviados += 1
        finally:
            s.eventos.cancelar(cola)

    return StreamingResponse(flujo(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.patch("/{dispositivo_id}", response_model=Dispositivo, dependencies=[Depends(requiere(DISPOSITIVOS))])
async def renombrar(dispositivo_id: str, solicitud: SolicitudRenombrar, s: Servicios = Depends(obtener_servicios)) -> Dispositivo:
    fila = await asyncio.to_thread(_consultar, s, repo.renombrar_dispositivo, dispositivo_id, solicitud.nombre.strip())
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado o ya desvinculado.")
    s.eventos.publicar("telefono", dispositivo_id=dispositivo_id, nombre=fila["nombre"])
    return Dispositivo.desde_fila(fila)


@router.delete("/{dispositivo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revocar(
    dispositivo_id: str, actual: Fila = Depends(requiere(DISPOSITIVOS)), s: Servicios = Depends(obtener_servicios)
) -> None:
    if dispositivo_id == actual["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes revocar el dispositivo con el que estás conectado.")
    if await asyncio.to_thread(_consultar, s, repo.revocar_dispositivos, "id = ?", (dispositivo_id,)) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado o ya revocado.")
    await asyncio.to_thread(_borrar_subidas_a_medias, s, dispositivo_id)
    s.eventos.publicar("desvinculado", dispositivo_id=dispositivo_id)


async def vigilar_conexion(s: Servicios, intervalo: float = 10) -> None:
    """Publica el paso a "desconectado" cuando el teléfono deja de enviar latidos."""
    anterior: tuple[str, bool] | None = None
    while True:
        with contextlib.suppress(Exception):
            fila = await asyncio.to_thread(_consultar, s, repo.telefono_activo)
            estado = (fila["id"], en_linea(fila)) if fila else None
            if estado and anterior and estado[0] == anterior[0] and estado[1] != anterior[1]:
                s.eventos.publicar("telefono", dispositivo_id=estado[0], en_linea=estado[1])
            anterior = estado
        await asyncio.sleep(intervalo)
