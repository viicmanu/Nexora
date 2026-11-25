"""Motor de clasificación: combina la decisión del LLM con las correcciones previas del usuario."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from nexora.core_ia import prompts
from nexora.core_ia.carpetas import CarpetaInvalida, normalizar_carpeta
from nexora.core_ia.catalogo import BuscadorCarpetas, Catalogo
from nexora.core_ia.extraccion import representacion_para_embedding
from nexora.core_ia.llm import AdaptadorLLM
from nexora.core_ia.vector_store import AlmacenVectorial, Vecino

log = logging.getLogger(__name__)

CARPETA_SIN_CLASIFICAR = "Sin clasificar"
SIMILITUD_RELEVANTE = 0.65   # a partir de aquí un ejemplo previo cuenta como "parecido"
SIMILITUD_DECISIVA = 0.80    # un ejemplo casi idéntico que contradice al LLM obliga a preguntar al usuario
CONFIANZA_EN_CONFLICTO = 0.60


class ResultadoClasificacion(BaseModel):
    carpeta_destino: str
    confianza: float = Field(ge=0, le=1)
    resumen_ejecutivo: str
    palabras_clave: list[str]
    alternativas: list[str] = []
    razonamiento: str = ""
    modelo: str = ""  # motor que respondió
    ensena: bool = False
    indicacion_id: str | None = None
    accion: str = ""  # la de la indicación aplicada


class Clasificador:
    def __init__(
        self, llm: AdaptadorLLM, vectores: AlmacenVectorial, buscador: BuscadorCarpetas,
        obtener_indicaciones: Callable[[], Sequence[Mapping]] = list,
    ) -> None:
        self.llm = llm
        self.vectores = vectores
        self.buscador = buscador
        self.obtener_indicaciones = obtener_indicaciones

    async def clasificar(self, nombre: str, extension: str, texto: str) -> ResultadoClasificacion:
        consulta = representacion_para_embedding(nombre, texto)
        vecinos, catalogo, indicaciones = await asyncio.gather(
            asyncio.to_thread(self.vectores.vecinos_aprendizaje, consulta, 5),
            asyncio.to_thread(self.buscador.catalogo),
            asyncio.to_thread(self.obtener_indicaciones),
        )
        return await self.llm.ejecutar(
            lambda llm: self._clasificar_con(llm, nombre, extension, texto, catalogo, vecinos, indicaciones)
        )

    async def _clasificar_con(
        self, llm: AdaptadorLLM, nombre: str, extension: str, texto: str, catalogo: Catalogo, vecinos: list[Vecino],
        indicaciones: Sequence[Mapping],
    ) -> ResultadoClasificacion:
        # El motor principal no necesita sus propios ejemplos; el de respaldo aprende de ellos.
        propios = [v for v in vecinos if v.fuente == "usuario"]
        vecinos = propios if llm.ensena else propios + [v for v in vecinos if v.fuente == "ia"]
        limite = llm.max_caracteres_clasificacion
        texto = texto[:limite] if limite else texto
        aplicable: Mapping | None = None
        if indicaciones and not llm.ensena:
            # Un modelo pequeño tiende a aplicar indicaciones a todo si las lee junto al resto: se pregunta aparte.
            aplicable = await _elegir_indicacion(llm, nombre, texto, indicaciones)
            indicaciones = [aplicable] if aplicable else []
        etiquetas = catalogo.etiquetas
        if llm.max_carpetas and len(etiquetas) > llm.max_carpetas:
            etiquetas = await asyncio.to_thread(self.preseleccionar, llm.max_carpetas, nombre, texto, catalogo, vecinos)
        datos = await llm.generar_json(
            prompts.SISTEMA_CLASIFICACION,
            prompts.prompt_clasificacion(nombre, extension, texto, catalogo, etiquetas, vecinos, indicaciones),
            prompts.ESQUEMA_CLASIFICACION,
        )
        numero = datos.pop("indicacion", 0)
        datos["confianza"] = min(max(float(datos.get("confianza", 0)), 0.0), llm.confianza_maxima)
        try:
            resultado = ResultadoClasificacion.model_validate(datos)
        except ValidationError:
            log.warning("Respuesta del LLM con formato inesperado para %s: %s", nombre, datos)
            raise
        resultado = _normalizar(resultado, catalogo).model_copy(update={"modelo": llm.descripcion, "ensena": llm.ensena})
        if llm.ensena and isinstance(numero, int) and 1 <= numero <= len(indicaciones):
            aplicable = indicaciones[numero - 1]
        if aplicable:
            # Una indicación expresa manda sobre los ejemplos previos.
            return aplicar_indicacion(resultado, aplicable, llm.ensena)
        return ajustar_con_aprendizaje(resultado, vecinos)

    def preseleccionar(self, n: int, nombre: str, texto: str, catalogo: Catalogo, vecinos: list[Vecino]) -> list[str]:
        """Las carpetas más prometedoras: las que eligió antes el usuario y las más parecidas al archivo."""
        etiquetas = catalogo.etiquetas
        raices = _raices(f"{nombre} {texto}")
        coincidencias = [len(_raices(e.split("/")[-1]) & raices) + 0.5 * len(_raices(e) & raices) for e in etiquetas]
        # Se compara la carpeta final: la ruta completa diluye su significado.
        parecido = self.vectores.similitudes(f"{Path(nombre).stem} {texto[:500]}", [e.split("/")[-1] for e in etiquetas])
        orden = sorted(range(len(etiquetas)), key=lambda i: coincidencias[i] * 0.3 + parecido[i], reverse=True)
        previas = [catalogo.etiqueta(v.categoria) for v in vecinos if v.categoria.casefold() in catalogo.etiqueta_de]
        return list(dict.fromkeys([*previas, *(etiquetas[i] for i in orden)]))[:n]


def _raices(texto: str) -> set[str]:
    """Comienzos de palabra sin tildes ("integrales" e "integral" coinciden)."""
    plano = unicodedata.normalize("NFD", texto.casefold())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return {p[:5] for p in re.findall(r"[a-z0-9]{4,}", plano)}


def a_valor(etiqueta: str, catalogo: Catalogo) -> str:
    """Carpeta existente si la etiqueta está en el catálogo; si no, una carpeta nueva."""
    return catalogo.valor(etiqueta) or catalogo.valor_nuevo(normalizar_carpeta(etiqueta))


def _normalizar(resultado: ResultadoClasificacion, catalogo: Catalogo) -> ResultadoClasificacion:
    alternativas = []
    for alt in resultado.alternativas[:3]:
        try:
            alternativas.append(a_valor(alt, catalogo))
        except CarpetaInvalida:
            continue
    palabras = [p.strip() for p in resultado.palabras_clave if p.strip()][:8]
    cambios = {"alternativas": alternativas, "palabras_clave": palabras}
    try:
        cambios["carpeta_destino"] = a_valor(resultado.carpeta_destino, catalogo)
    except CarpetaInvalida:
        # Carpeta inutilizable: se manda a la bandeja de dudas en lugar de moverlo a ciegas.
        cambios |= {"carpeta_destino": CARPETA_SIN_CLASIFICAR, "confianza": 0.0}
    return resultado.model_copy(update=cambios)


async def _elegir_indicacion(
    llm: AdaptadorLLM, nombre: str, texto: str, indicaciones: Sequence[Mapping]
) -> Mapping | None:
    datos = await llm.generar_json(
        prompts.SISTEMA_INDICACION, prompts.prompt_indicacion(nombre, texto[:800], indicaciones), prompts.ESQUEMA_INDICACION
    )
    numero = datos.get("indicacion")
    return indicaciones[numero - 1] if isinstance(numero, int) and 1 <= numero <= len(indicaciones) else None


def aplicar_indicacion(resultado: ResultadoClasificacion, indicacion: Mapping, fiable: bool) -> ResultadoClasificacion:
    accion = indicacion["accion"]
    if accion == "no_mover" and not fiable:
        accion = "preguntar"  # un modelo pequeño puede equivocarse de indicación: que lo confirme el usuario
    cambios = {
        "indicacion_id": indicacion["id"], "accion": accion,
        "razonamiento": f"Según tu indicación: «{indicacion['texto']}»",
    }
    if indicacion["carpeta"]:
        otras = [a for a in (resultado.carpeta_destino, *resultado.alternativas) if a.casefold() != indicacion["carpeta"].casefold()]
        cambios |= {"carpeta_destino": indicacion["carpeta"], "alternativas": list(dict.fromkeys(otras))[:3]}
    return resultado.model_copy(update=cambios)


def ajustar_con_aprendizaje(resultado: ResultadoClasificacion, vecinos: list[Vecino]) -> ResultadoClasificacion:
    """Sube la confianza si los ejemplos del usuario coinciden; la baja si un ejemplo casi idéntico la contradice."""
    # Un ejemplo del motor principal solo se ofrece como alternativa: no autoriza a mover.
    for v in sorted((v for v in vecinos if v.fuente == "ia"), key=lambda v: v.similitud):
        if v.similitud >= SIMILITUD_RELEVANTE and v.categoria.casefold() != resultado.carpeta_destino.casefold():
            otras = [a for a in resultado.alternativas if a.casefold() != v.categoria.casefold()]
            resultado = resultado.model_copy(update={"alternativas": [v.categoria, *otras][:3]})
    relevantes = [v for v in vecinos if v.fuente == "usuario" and v.similitud >= SIMILITUD_RELEVANTE]
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
