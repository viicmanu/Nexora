"""Rutas que alimentan la interfaz de escritorio: resumen, actividad, carpetas, subida de archivos y ajustes."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import Archivo
from nexora.core_ia.llm import AdaptadorConRespaldo, ErrorLLM
from nexora.db import repositorio as repo
from nexora.security.tokens import AJUSTES, ORGANIZAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api", tags=["panel"])
organizar = Depends(requiere(ORGANIZAR))


class Resumen(BaseModel):
    clasificados_hoy: int
    clasificados_ayer: int
    precision: float | None
    pendientes: int
    errores: int
    en_proceso: int
    total: int
    motor_ia: str
    usando_respaldo: bool
    motivo_respaldo: str = Field(default="", description="cuota o caida")
    vuelve_en: int = Field(description="Segundos hasta reintentar con el motor principal.")


class Evento(Archivo):
    error: str | None
    origen: str | None
    se_puede_deshacer: bool = False


class Destino(BaseModel):
    valor: str
    etiqueta: str


class SolicitudImportar(BaseModel):
    rutas: list[str] = Field(min_length=1, max_length=500)


class RespuestaImportar(BaseModel):
    aceptados: list[str]
    omitidos: list[str]


class Carpeta(BaseModel):
    id: str
    ruta: str
    recursiva: bool
    existe: bool


class SolicitudCarpeta(BaseModel):
    ruta: str = Field(min_length=1, max_length=1000)
    recursiva: bool = False


class InfoAjustes(BaseModel):
    llm: str
    respaldo: str | None
    umbral_confianza: float
    raiz_organizada: str
    zonas_busqueda: list[str]


class SolicitudUmbral(BaseModel):
    umbral_confianza: float = Field(ge=0.5, le=1)


class PruebaIA(BaseModel):
    modelo: str
    carpeta: str
    confianza: float
    resumen: str
    segundos: float


def _consultar(servicios: Servicios, consulta, *args):
    with servicios.db.conectar() as con:
        return consulta(con, *args)


@router.get("/stats", response_model=Resumen, dependencies=[organizar])
async def resumen(s: Servicios = Depends(obtener_servicios)):
    datos = await asyncio.to_thread(_consultar, s, repo.resumen)
    vuelve_en = s.llm.vuelve_en if isinstance(s.llm, AdaptadorConRespaldo) else 0
    motivo = s.llm.motivo if isinstance(s.llm, AdaptadorConRespaldo) and vuelve_en else ""
    return Resumen(
        **datos, motor_ia=s.llm.descripcion, usando_respaldo=vuelve_en > 0, vuelve_en=vuelve_en, motivo_respaldo=motivo
    )


@router.get("/activity", response_model=list[Evento], dependencies=[organizar])
async def actividad(limite: int = Query(50, ge=1, le=500), s: Servicios = Depends(obtener_servicios)):
    filas = await asyncio.to_thread(_consultar, s, repo.actividad, limite)
    return [
        Evento(**Archivo.desde_fila(f).model_dump(), error=f["error"], origen=f["origen_clasificacion"],
               se_puede_deshacer=bool(f["se_puede_deshacer"])) for f in filas
    ]


@router.get("/destinations", response_model=list[Destino], dependencies=[organizar])
async def destinos(s: Servicios = Depends(obtener_servicios)):
    """Carpetas que el usuario ya tiene y que pueden recibir archivos."""
    catalogo = await asyncio.to_thread(s.carpetas.catalogo)
    return [Destino(valor=catalogo.valor(e), etiqueta=e) for e in catalogo.etiquetas]


@router.get("/folders", response_model=list[Carpeta], dependencies=[organizar])
async def listar_carpetas(s: Servicios = Depends(obtener_servicios)):
    filas = await asyncio.to_thread(_consultar, s, repo.carpetas_activas)
    return [
        Carpeta(id=f["id"], ruta=f["ruta"], recursiva=bool(f["recursiva"]), existe=Path(f["ruta"]).is_dir()) for f in filas
    ]


@router.post("/folders", response_model=Carpeta, status_code=201, dependencies=[organizar])
async def agregar_carpeta(solicitud: SolicitudCarpeta, s: Servicios = Depends(obtener_servicios)):
    ruta = Path(solicitud.ruta.strip().strip('"')).expanduser()
    if not ruta.is_absolute() or not ruta.is_dir():
        raise HTTPException(422, "Esa carpeta no existe. Escribe la ruta completa.")
    ruta = ruta.resolve()
    raiz = s.settings.raiz_organizada.expanduser().resolve()
    if ruta == Path(ruta.anchor) or ruta.is_relative_to(raiz):
        raise HTTPException(422, "No se puede vigilar un disco entero ni la carpeta donde Nexora organiza.")
    carpeta_id = await asyncio.to_thread(_consultar, s, repo.activar_carpeta, ruta, solicitud.recursiva)
    s.vigilante.agregar(ruta, solicitud.recursiva)
    return Carpeta(id=carpeta_id, ruta=str(ruta), recursiva=solicitud.recursiva, existe=True)


@router.delete("/folders/{carpeta_id}", status_code=204, dependencies=[organizar])
async def quitar_carpeta(carpeta_id: str, s: Servicios = Depends(obtener_servicios)) -> None:
    ruta = await asyncio.to_thread(_consultar, s, repo.desactivar_carpeta, carpeta_id)
    if ruta is None:
        raise HTTPException(404, "Carpeta no encontrada.")
    s.vigilante.quitar(Path(ruta))


@router.post("/files/import", response_model=RespuestaImportar, status_code=202, dependencies=[organizar])
async def importar(solicitud: SolicitudImportar, s: Servicios = Depends(obtener_servicios)):
    """Pone en cola archivos del PC. Se trabaja con el original: al clasificarlo se mueve, no se copia."""
    raiz = s.settings.raiz_organizada.expanduser().resolve()

    def revisar():
        validas, omitidos = [], []
        for texto in solicitud.rutas:
            ruta = Path(texto)
            if not ruta.is_absolute() or not ruta.is_file():
                omitidos.append(f"{ruta.name or texto}: no es un archivo")
                continue
            ruta = ruta.resolve()
            fila = _consultar(s, repo.buscar_por_ruta, ruta)
            if ruta.is_relative_to(raiz) or (fila and fila["estado"] in ("clasificado", "corregido")):
                omitidos.append(f"{ruta.name}: ya está organizado")
            elif fila and fila["estado"] == "pendiente":
                omitidos.append(f"{ruta.name}: ya está en la Bandeja de Dudas")
            else:
                validas.append(ruta)
        return validas, omitidos

    validas, omitidos = await asyncio.to_thread(revisar)
    for ruta in validas:
        s.vigilante.encolar(ruta)
    return RespuestaImportar(aceptados=[r.name for r in validas], omitidos=omitidos)


@router.post("/files/{archivo_id}/retry", status_code=202, dependencies=[organizar])
async def reintentar(archivo_id: str, s: Servicios = Depends(obtener_servicios)):
    fila = await asyncio.to_thread(_consultar, s, repo.obtener_archivo, archivo_id)
    if fila is None:
        raise HTTPException(404, "Archivo no encontrado en el índice.")
    if fila["estado"] != "error":
        raise HTTPException(409, "Solo se reintentan archivos con error.")
    if not Path(fila["ruta_actual"]).is_file():
        raise HTTPException(410, "El archivo ya no está en su ubicación.")
    s.vigilante.encolar(Path(fila["ruta_actual"]))
    return {"estado": "en cola"}


def _info(s: Servicios) -> InfoAjustes:
    con_respaldo = isinstance(s.llm, AdaptadorConRespaldo)
    principal = s.llm.principal if con_respaldo else s.llm
    respaldo = s.llm.respaldo.descripcion if con_respaldo else None
    return InfoAjustes(
        llm=principal.descripcion, respaldo=respaldo, umbral_confianza=s.indexador.umbral, raiz_organizada=str(s.settings.raiz_organizada),
        zonas_busqueda=[str(ruta) for _, ruta, _ in s.carpetas.zonas],
    )


@router.get("/settings", response_model=InfoAjustes, dependencies=[organizar])
async def ajustes(s: Servicios = Depends(obtener_servicios)):
    return _info(s)


@router.put("/settings", response_model=InfoAjustes, dependencies=[Depends(requiere(AJUSTES))])
async def cambiar_ajustes(solicitud: SolicitudUmbral, s: Servicios = Depends(obtener_servicios)):
    await asyncio.to_thread(_consultar, s, repo.guardar_ajuste, "umbral", str(solicitud.umbral_confianza))
    s.indexador.umbral = solicitud.umbral_confianza
    return _info(s)


@router.post("/ai/test", response_model=PruebaIA, dependencies=[organizar])
async def probar_ia(s: Servicios = Depends(obtener_servicios)):
    """Clasifica un texto de ejemplo con el modelo configurado, sin tocar ningún archivo."""
    inicio = time.perf_counter()
    try:
        r = await s.clasificador.clasificar(
            "recibo_energia.txt", ".txt", "Recibo de energía del hogar. Total: 50.000 pesos."
        )
    except (ErrorLLM, ValueError) as exc:
        raise HTTPException(502, str(exc)[:300])
    return PruebaIA(
        modelo=r.modelo, carpeta=r.carpeta_destino, confianza=r.confianza, resumen=r.resumen_ejecutivo,
        segundos=round(time.perf_counter() - inicio, 1),
    )
