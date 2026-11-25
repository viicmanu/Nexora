"""Indicaciones permanentes del usuario para la IA."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.core_ia import indicaciones
from nexora.core_ia.carpetas import CarpetaInvalida, canonico, destino_existe, ruta_destino
from nexora.core_ia.llm import ErrorLLM
from nexora.db import repositorio as repo
from nexora.security.tokens import ORGANIZAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api/rules", tags=["indicaciones"], dependencies=[Depends(requiere(ORGANIZAR))])
Accion = Literal["mover", "preguntar", "no_mover", "guia"]


class SolicitudTexto(BaseModel):
    texto: str = Field(min_length=5, max_length=400)


class Interpretacion(BaseModel):
    entendida: bool
    resumen: str
    accion: Accion
    carpeta: str | None
    carpeta_etiqueta: str | None
    carpeta_existe: bool
    modelo: str


class SolicitudIndicacion(SolicitudTexto):
    resumen: str = Field(default="", max_length=400)
    accion: Accion = "guia"
    carpeta: str | None = Field(default=None, max_length=1000)
    crear_carpeta: bool = False


class SolicitudEstado(BaseModel):
    activa: bool


class Indicacion(BaseModel):
    id: str
    texto: str
    resumen: str
    accion: Accion
    carpeta: str | None
    carpeta_etiqueta: str | None
    carpeta_existe: bool
    activa: bool
    usos: int


def _consultar(s: Servicios, consulta, *args):
    with s.db.conectar() as con:
        return consulta(con, *args)


def _vista(s: Servicios, fila: repo.Fila) -> Indicacion:
    carpeta = fila["carpeta"]
    return Indicacion(
        id=fila["id"], texto=fila["texto"], resumen=fila["resumen"], accion=fila["accion"], carpeta=carpeta,
        carpeta_etiqueta=s.carpetas.catalogo().etiqueta(carpeta) if carpeta else None,
        carpeta_existe=bool(carpeta) and destino_existe(s.settings.raiz_organizada, carpeta),
        activa=bool(fila["activa"]), usos=fila["usos"],
    )


@router.post("/interpret", response_model=Interpretacion)
async def interpretar(solicitud: SolicitudTexto, s: Servicios = Depends(obtener_servicios)):
    """Explica cómo se entiende la indicación. No guarda nada."""
    try:
        r = await indicaciones.interpretar(s.clasificador, solicitud.texto.strip())
    except ErrorLLM as exc:
        raise HTTPException(502, str(exc)[:300])
    catalogo = await asyncio.to_thread(s.carpetas.catalogo)
    return Interpretacion(
        entendida=r.entendida, resumen=r.resumen, accion=r.accion, carpeta=r.carpeta,
        carpeta_etiqueta=catalogo.etiqueta(r.carpeta) if r.carpeta else None,
        carpeta_existe=bool(r.carpeta) and destino_existe(s.settings.raiz_organizada, r.carpeta),
        modelo=r.modelo,
    )


@router.get("", response_model=list[Indicacion])
async def listar(s: Servicios = Depends(obtener_servicios)):
    def leer():
        return [_vista(s, f) for f in _consultar(s, repo.listar_indicaciones)]

    return await asyncio.to_thread(leer)


@router.post("", response_model=Indicacion, status_code=201)
async def guardar(solicitud: SolicitudIndicacion, s: Servicios = Depends(obtener_servicios)):
    def escribir():
        carpeta = None
        if solicitud.accion == "mover":
            if not solicitud.carpeta:
                raise CarpetaInvalida("Falta elegir la carpeta.")
            raiz = s.settings.raiz_organizada
            carpeta = canonico(raiz, solicitud.carpeta)
            if not destino_existe(raiz, carpeta):
                if not solicitud.crear_carpeta:
                    raise CarpetaInvalida("Esa carpeta no existe.")
                ruta_destino(raiz, carpeta).mkdir(parents=True, exist_ok=True)
                s.carpetas.invalidar()
        texto = solicitud.texto.strip()
        if len(_consultar(s, repo.listar_indicaciones)) >= repo.MAX_INDICACIONES:
            raise CarpetaInvalida(f"Puedes tener hasta {repo.MAX_INDICACIONES} indicaciones. Borra alguna.")
        fila = _consultar(
            s, repo.crear_indicacion, texto, solicitud.resumen.strip() or texto, solicitud.accion, carpeta
        )
        return _vista(s, fila)

    try:
        return await asyncio.to_thread(escribir)
    except CarpetaInvalida as exc:
        raise HTTPException(422, str(exc))


@router.patch("/{indicacion_id}", response_model=Indicacion)
async def activar(indicacion_id: str, solicitud: SolicitudEstado, s: Servicios = Depends(obtener_servicios)):
    fila = await asyncio.to_thread(_consultar, s, repo.activar_indicacion, indicacion_id, solicitud.activa)
    if fila is None:
        raise HTTPException(404, "Indicación no encontrada.")
    return await asyncio.to_thread(_vista, s, fila)


@router.delete("/{indicacion_id}", status_code=204)
async def borrar(indicacion_id: str, s: Servicios = Depends(obtener_servicios)) -> None:
    if not await asyncio.to_thread(_consultar, s, repo.borrar_indicacion, indicacion_id):
        raise HTTPException(404, "Indicación no encontrada.")
