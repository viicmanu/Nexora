"""Búsqueda y explicación de archivos: pensadas para usarse también desde la app móvil."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import (
    Archivo, RespuestaBusqueda, RespuestaExplicacion, ResultadoBusqueda, SolicitudExplicacion,
)
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado, SinContenidoLegible
from nexora.core_ia.llm import ErrorLLM
from nexora.security.tokens import BUSCAR, EXPLICAR
from nexora.servicios import Servicios

router = APIRouter(prefix="/api/files", tags=["archivos"])


@router.get("/search", response_model=RespuestaBusqueda, dependencies=[Depends(requiere(BUSCAR))])
async def buscar_archivos(
    q: str = Query(min_length=1, max_length=300, description="Nombre, palabras clave o descripción en lenguaje natural."),
    limite: int = Query(10, ge=1, le=50),
    carpeta: str | None = Query(None, max_length=260, description="Restringe a una carpeta y sus subcarpetas."),
    servicios: Servicios = Depends(obtener_servicios),
) -> RespuestaBusqueda:
    """Devuelve dónde está guardado exactamente cada archivo que coincide con la búsqueda."""
    encontrados = await servicios.buscador.buscar(q, limite, carpeta)
    resultados = [
        ResultadoBusqueda(
            archivo=Archivo.desde_fila(r.fila), puntuacion=r.puntuacion, coincidencia=r.coincidencia,
            similitud=round(r.similitud, 4) if r.similitud is not None else None,
        )
        for r in encontrados
    ]
    return RespuestaBusqueda(consulta=q, total=len(resultados), resultados=resultados)


@router.post("/{archivo_id}/explain", response_model=RespuestaExplicacion, dependencies=[Depends(requiere(EXPLICAR))])
async def explicar_archivo(
    archivo_id: str, solicitud: SolicitudExplicacion, servicios: Servicios = Depends(obtener_servicios)
) -> RespuestaExplicacion:
    """Lee el archivo en el PC y pide al LLM una explicación o la respuesta a una pregunta concreta."""
    try:
        resultado = await servicios.explicador.explicar(
            archivo_id, solicitud.pregunta, detallado=solicitud.detalle == "detallado"
        )
    except ArchivoNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado en el índice.")
    except ArchivoNoDisponible as exc:
        raise HTTPException(status.HTTP_410_GONE, f"El archivo ya no está en {exc}.")
    except SinContenidoLegible:
        raise HTTPException(422, "No se pudo extraer texto de este archivo.")
    except ErrorLLM as exc:
        codigo = status.HTTP_503_SERVICE_UNAVAILABLE if exc.reintentable else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(codigo, str(exc))

    fila = resultado.fila
    return RespuestaExplicacion(
        archivo_id=fila["id"],
        nombre=fila["nombre"],
        ruta_actual=fila["ruta_actual"],
        pregunta=solicitud.pregunta,
        respuesta=resultado.respuesta,
        modelo=resultado.modelo,
        caracteres_analizados=resultado.caracteres_analizados,
        texto_truncado=resultado.texto_truncado,
    )
