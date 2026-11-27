"""Bandeja de dudas (GET /api/pending) y feedback del usuario (POST /api/feedback)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import (
    Archivo, Pendiente, PropuestaCorreccion, RespuestaFeedback, SolicitudCorreccion, SolicitudFeedback,
)
from nexora.core_ia.carpetas import CarpetaInvalida, destino_existe
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado
from nexora.core_ia.llm import ErrorLLM
from nexora.db import repositorio as repo
from nexora.db.repositorio import Fila
from nexora.security.tokens import ORGANIZAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api", tags=["aprendizaje"])


def a_pendiente(f: repo.Fila, catalogo, servicios: Servicios) -> Pendiente:
    original = None
    if f["duplicado_de"]:
        with servicios.db.conectar() as con:
            original = repo.obtener_archivo(con, f["duplicado_de"])
    return Pendiente(
        pendiente_id=f["pendiente_id"],
        archivo=Archivo.desde_fila(f),
        carpeta_sugerida=f["carpeta_sugerida"],
        carpeta_etiqueta=catalogo.etiqueta(f["carpeta_sugerida"]) if f["carpeta_sugerida"] else "",
        duplicado_de=Archivo.desde_fila(original) if original else None,
        carpeta_existe=bool(f["carpeta_sugerida"]) and destino_existe(servicios.settings.raiz_organizada, f["carpeta_sugerida"]),
        confianza=f["confianza_sugerencia"],
        alternativas=json.loads(f["alternativas"] or "[]"),
        razonamiento=f["razonamiento"],
        creado_en=f["pendiente_creado_en"],
    )


@router.get("/pending", response_model=list[Pendiente], dependencies=[Depends(requiere(ORGANIZAR))])
async def listar_pendientes(
    limite: int = Query(50, ge=1, le=200), servicios: Servicios = Depends(obtener_servicios)
) -> list[Pendiente]:
    """Archivos con confianza por debajo del umbral que esperan la decisión del usuario."""

    def consultar():
        with servicios.db.conectar() as con:
            return repo.listar_pendientes(con, limite)

    filas, catalogo = await asyncio.gather(
        asyncio.to_thread(consultar), asyncio.to_thread(servicios.carpetas.catalogo)
    )
    return await asyncio.to_thread(lambda: [a_pendiente(f, catalogo, servicios) for f in filas])


@router.post("/corrections", response_model=PropuestaCorreccion, dependencies=[Depends(requiere(ORGANIZAR))])
async def proponer_correccion(
    solicitud: SolicitudCorreccion, servicios: Servicios = Depends(obtener_servicios)
) -> PropuestaCorreccion:
    """Entiende "X quedó mal, va en Y" y propone el cambio. No mueve nada: se confirma con POST /api/feedback."""
    try:
        p = await servicios.correcciones.interpretar(solicitud.texto, solicitud.archivo_id)
    except ArchivoNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado en el índice.")
    except ErrorLLM as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)[:300])
    catalogo = await asyncio.to_thread(servicios.carpetas.catalogo)
    return PropuestaCorreccion(
        archivo=Archivo.desde_fila(p.archivo) if p.archivo else None,
        candidatos=[Archivo.desde_fila(f) for f in p.candidatos],
        carpeta=p.carpeta,
        carpeta_etiqueta=catalogo.etiqueta(p.carpeta) if p.carpeta else None,
        carpeta_existe=bool(p.carpeta) and destino_existe(servicios.settings.raiz_organizada, p.carpeta),
        modelo=p.modelo,
    )


@router.post("/feedback", response_model=RespuestaFeedback)
async def registrar_feedback(
    solicitud: SolicitudFeedback,
    dispositivo: Fila = Depends(requiere(ORGANIZAR)),
    servicios: Servicios = Depends(obtener_servicios),
) -> RespuestaFeedback:
    """Mueve el archivo a la carpeta elegida y guarda la decisión como ejemplo para futuras clasificaciones.

    Sirve tanto para confirmar la sugerencia (misma carpeta) como para corregirla (otra carpeta).
    """
    try:
        resultado = await servicios.aprendizaje.registrar(
            solicitud.archivo_id, solicitud.carpeta_elegida, dispositivo["id"], solicitud.motivo.strip()
        )
    except CarpetaInvalida as exc:
        raise HTTPException(422, str(exc))
    except ArchivoNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado en el índice.")
    except ArchivoNoDisponible as exc:
        raise HTTPException(status.HTTP_410_GONE, f"El archivo ya no está en {exc}.")
    except PermissionError:
        raise HTTPException(status.HTTP_409_CONFLICT, "El archivo está abierto en otro programa; ciérralo y vuelve a intentarlo.")

    servicios.carpetas.invalidar()  # pudo crearse una carpeta nueva
    return RespuestaFeedback(
        archivo=Archivo.desde_fila(resultado.fila),
        carpeta_sugerida=resultado.carpeta_sugerida,
        carpeta_elegida=resultado.fila["categoria"],
        fue_correccion=resultado.fue_correccion,
        ruta_anterior=resultado.ruta_anterior,
        aprendizaje_registrado=resultado.aprendizaje_registrado,
    )
