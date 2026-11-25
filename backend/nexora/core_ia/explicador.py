"""Lee un archivo local del PC y pide al LLM que lo explique o responda una pregunta sobre él."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from nexora.core_ia import prompts
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado, SinContenidoLegible
from nexora.core_ia.extraccion import extraer_texto
from nexora.core_ia.llm import AdaptadorLLM
from nexora.db import Database
from nexora.db import repositorio as repo


@dataclass
class Explicacion:
    fila: repo.Fila
    respuesta: str
    modelo: str
    caracteres_analizados: int
    texto_truncado: bool


class Explicador:
    def __init__(self, db: Database, llm: AdaptadorLLM, max_caracteres: int) -> None:
        self.db = db
        self.llm = llm
        self.max_caracteres = max_caracteres

    async def explicar(self, archivo_id: str, pregunta: str | None, detallado: bool) -> Explicacion:
        fila = await asyncio.to_thread(self._obtener, archivo_id)
        if fila is None:
            raise ArchivoNoEncontrado(archivo_id)

        # Solo se leen rutas que están en el índice: el cliente nunca envía una ruta del disco.
        ruta = Path(fila["ruta_actual"])
        if not ruta.is_file():
            raise ArchivoNoDisponible(str(ruta))

        # Se lee un carácter más del límite para saber si el documento se recortó.
        texto = await asyncio.to_thread(extraer_texto, ruta, self.max_caracteres + 1)
        if not texto.strip():
            raise SinContenidoLegible(fila["nombre"])

        async def con(llm: AdaptadorLLM) -> Explicacion:
            limite = min(self.max_caracteres, llm.max_caracteres_explicacion or self.max_caracteres)
            parte, truncado = texto[:limite], len(texto) > limite
            respuesta = await llm.generar_texto(
                prompts.SISTEMA_EXPLICACION,
                prompts.prompt_explicacion(fila["nombre"], str(ruta), parte, pregunta, detallado, truncado),
                max_tokens=16000 if detallado or pregunta else 4000,
                esfuerzo="medio",
            )
            return Explicacion(fila, respuesta, llm.descripcion, len(parte), truncado)

        return await self.llm.ejecutar(con)

    def _obtener(self, archivo_id: str) -> repo.Fila | None:
        with self.db.conectar() as con:
            return repo.obtener_archivo(con, archivo_id)
