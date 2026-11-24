"""Motor de clasificación: combina la decisión del LLM con las correcciones previas del usuario."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from nexora.core_ia import prompts
from nexora.core_ia.carpetas import CarpetaInvalida, normalizar_carpeta
from nexora.core_ia.extraccion import representacion_para_embedding
from nexora.core_ia.llm import AdaptadorLLM
from nexora.core_ia.vector_store import AlmacenVectorial, Vecino

log = logging.getLogger(__name__)

CARPETA_SIN_CLASIFICAR = "Sin clasificar"
SIMILITUD_RELEVANTE = 0.80   # a partir de aquí un ejemplo previo cuenta como "parecido"
SIMILITUD_DECISIVA = 0.92    # un ejemplo casi idéntico que contradice al LLM obliga a preguntar al usuario
CONFIANZA_EN_CONFLICTO = 0.60


class ResultadoClasificacion(BaseModel):
    carpeta_destino: str
    confianza: float = Field(ge=0, le=1)
    resumen_ejecutivo: str
    palabras_clave: list[str]
    alternativas: list[str] = []
    razonamiento: str = ""


class Clasificador:
    def __init__(self, llm: AdaptadorLLM, vectores: AlmacenVectorial, obtener_categorias: Callable[[], list[str]]) -> None:
        self.llm = llm
        self.vectores = vectores
        self.obtener_categorias = obtener_categorias

    async def clasificar(self, nombre: str, extension: str, texto: str) -> ResultadoClasificacion:
        consulta = representacion_para_embedding(nombre, texto)
        vecinos, categorias = await asyncio.gather(
            asyncio.to_thread(self.vectores.vecinos_aprendizaje, consulta, 5),
            asyncio.to_thread(self.obtener_categorias),
        )
        datos = await self.llm.generar_json(
            prompts.SISTEMA_CLASIFICACION,
            prompts.prompt_clasificacion(nombre, extension, texto, categorias, vecinos),
            prompts.ESQUEMA_CLASIFICACION,
        )
        datos["confianza"] = min(max(float(datos.get("confianza", 0)), 0.0), 1.0)
        try:
            resultado = ResultadoClasificacion.model_validate(datos)
        except ValidationError:
            log.warning("Respuesta del LLM con formato inesperado para %s: %s", nombre, datos)
            raise
        resultado = _normalizar(resultado)
        return ajustar_con_aprendizaje(resultado, vecinos)


def _normalizar(resultado: ResultadoClasificacion) -> ResultadoClasificacion:
    try:
        carpeta = normalizar_carpeta(resultado.carpeta_destino)
    except CarpetaInvalida:
        # Carpeta inutilizable: se manda a la bandeja de dudas en lugar de moverlo a ciegas.
        return resultado.model_copy(update={"carpeta_destino": CARPETA_SIN_CLASIFICAR, "confianza": 0.0})
    alternativas = []
    for alt in resultado.alternativas[:3]:
        try:
            alternativas.append(normalizar_carpeta(alt))
        except CarpetaInvalida:
            continue
    palabras = [p.strip() for p in resultado.palabras_clave if p.strip()][:8]
    return resultado.model_copy(update={"carpeta_destino": carpeta, "alternativas": alternativas, "palabras_clave": palabras})


def ajustar_con_aprendizaje(resultado: ResultadoClasificacion, vecinos: list[Vecino]) -> ResultadoClasificacion:
    """Sube la confianza si los ejemplos del usuario coinciden; la baja si un ejemplo casi idéntico la contradice."""
    relevantes = [v for v in vecinos if v.similitud >= SIMILITUD_RELEVANTE]
    if not relevantes:
        return resultado

    votos: dict[str, float] = defaultdict(float)
    for vecino in relevantes:
        votos[vecino.categoria.casefold()] += vecino.similitud
    propia = resultado.carpeta_destino.casefold()

    if propia in votos:
        apoyos = sum(1 for v in relevantes if v.categoria.casefold() == propia)
        nueva = min(1.0, resultado.confianza + min(0.15, 0.05 * apoyos))
        return resultado.model_copy(update={"confianza": nueva})

    contradicciones = [v for v in relevantes if v.similitud >= SIMILITUD_DECISIVA]
    if contradicciones:
        preferida = max(contradicciones, key=lambda v: v.similitud).categoria
        alternativas = [preferida, *[a for a in resultado.alternativas if a.casefold() != preferida.casefold()]][:3]
        return resultado.model_copy(update={
            "confianza": min(resultado.confianza, CONFIANZA_EN_CONFLICTO),
            "alternativas": alternativas,
            "razonamiento": f"{resultado.razonamiento} Antes guardaste un archivo muy parecido en '{preferida}'.".strip(),
        })
    return resultado
