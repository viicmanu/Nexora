"""Bandeja de dudas (GET /api/pending) y feedback del usuario (POST /api/feedback)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import Archivo, Pendiente, RespuestaFeedback, SolicitudFeedback
from nexora.core_ia.carpetas import CarpetaInvalida
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado
from nexora.db import repositorio as repo
from nexora.db.repositorio import Fila
from nexora.security.tokens import ORGANIZAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api", tags=["aprendizaje"])


@router.get("/pending", response_model=list[Pendiente], dependencies=[Depends(requiere(ORGANIZAR))])
async def listar_pendientes(
    limite: int = Query(50, ge=1, le=200), servicios: Servicios = Depends(obtener_servicios)
) -> list[Pendiente]:
    """Archivos con confianza por debajo del umbral que esperan la decisión del usuario."""

    def consultar():
        with servicios.db.conectar() as con:
            return repo.listar_pendientes(con, limite)

    filas = await asyncio.to_thread(consultar)
    return [
        Pendiente(
            pendiente_id=f["pendiente_id"],
            archivo=Archivo.desde_fila(f),
            carpeta_sugerida=f["carpeta_sugerida"],
            confianza=f["confianza_sugerencia"],
            alternativas=json.loads(f["alternativas"] or "[]"),
            razonamiento=f["razonamiento"],
            creado_en=f["pendiente_creado_en"],
        )
        for f in filas
    ]


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
        resultado = await servicios.aprendizaje.registrar(solicitud.archivo_id, solicitud.carpeta_elegida, dispositivo["id"])
    except CarpetaInvalida as exc:
        raise HTTPException(422, str(exc))
    except ArchivoNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado en el índice.")
    except ArchivoNoDisponible as exc:
        raise HTTPException(status.HTTP_410_GONE, f"El archivo ya no está en {exc}.")
    except PermissionError:
        raise HTTPException(status.HTTP_409_CONFLICT, "El archivo está abierto en otro programa; ciérralo y vuelve a intentarlo.")

    return RespuestaFeedback(
        archivo=Archivo.desde_fila(resultado.fila),
        carpeta_sugerida=resultado.carpeta_sugerida,
        carpeta_elegida=resultado.fila["categoria"],
        fue_correccion=resultado.fue_correccion,
        ruta_anterior=resultado.ruta_anterior,
        aprendizaje_registrado=resultado.aprendizaje_registrado,
    )
