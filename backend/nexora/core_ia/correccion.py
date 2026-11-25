"""Interpreta lo que el usuario escribe sobre un archivo mal ubicado. Solo propone: mover requiere confirmación."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from nexora.core_ia.busqueda import BuscadorHibrido
from nexora.core_ia.carpetas import CarpetaInvalida
from nexora.core_ia.catalogo import Catalogo
from nexora.core_ia.clasificador import Clasificador, a_valor
from nexora.core_ia.errores import ArchivoNoEncontrado
from nexora.core_ia.llm import AdaptadorLLM
from nexora.db import Database
from nexora.db import repositorio as repo

UBICADOS = ("clasificado", "corregido", "pendiente")
MAX_CANDIDATOS = 8

SISTEMA = """\
El usuario de Nexora, un organizador de archivos, escribe que un archivo quedó en una carpeta equivocada.
Identifica de qué archivo habla y a qué carpeta quiere moverlo.

- archivo: el número del archivo en la lista. Usa 0 si no está claro cuál es.
- carpeta: si una de las carpetas de la lista corresponde a lo que pide, su nombre copiado exactamente. Si pide una
  carpeta que no existe, escríbela como ruta corta. Si no dice dónde lo quiere y no se deduce, déjala vacía.
- No elijas la carpeta en la que el archivo ya está."""

ESQUEMA = {
    "type": "object",
    "properties": {"archivo": {"type": "integer"}, "carpeta": {"type": "string"}},
    "required": ["archivo", "carpeta"],
    "additionalProperties": False,
}


@dataclass
class Propuesta:
    archivo: repo.Fila | None = None
    candidatos: list[repo.Fila] = field(default_factory=list)
    carpeta: str | None = None
    modelo: str = ""


class InterpreteCorrecciones:
    def __init__(self, db: Database, buscador: BuscadorHibrido, clasificador: Clasificador) -> None:
        self.db = db
        self.buscador = buscador
        self.clasificador = clasificador

    async def interpretar(self, texto: str, archivo_id: str | None) -> Propuesta:
        candidatos = await self._candidatos(texto, archivo_id)
        if not candidatos:
            return Propuesta()
        catalogo = await asyncio.to_thread(self.clasificador.buscador.catalogo)
        return await self.clasificador.llm.ejecutar(lambda llm: self._preguntar(llm, texto, candidatos, catalogo))

    async def _candidatos(self, texto: str, archivo_id: str | None) -> list[repo.Fila]:
        def leer() -> list[repo.Fila]:
            with self.db.conectar() as con:
                if archivo_id:
                    fila = repo.obtener_archivo(con, archivo_id)
                    if fila is None:
                        raise ArchivoNoEncontrado(archivo_id)
                    return [fila]
                return repo.actividad(con, 500)

        filas = await asyncio.to_thread(leer)
        if archivo_id:
            return filas
        # Primero los que el usuario nombra; después los que coinciden por contenido.
        minusculas = texto.casefold()
        nombrados = [
            f for f in filas
            if f["nombre"].casefold() in minusculas
            or re.search(rf"(?<!\w){re.escape(Path(f['nombre']).stem.casefold())}(?!\w)", minusculas)
        ]
        if not nombrados:
            nombrados = [r.fila for r in await self.buscador.buscar(texto, MAX_CANDIDATOS)]
        return [f for f in nombrados if f["estado"] in UBICADOS and Path(f["ruta_actual"]).is_file()][:MAX_CANDIDATOS]

    async def _preguntar(
        self, llm: AdaptadorLLM, texto: str, candidatos: list[repo.Fila], catalogo: Catalogo
    ) -> Propuesta:
        etiquetas = catalogo.etiquetas
        if llm.max_carpetas and len(etiquetas) > llm.max_carpetas:
            etiquetas = await asyncio.to_thread(
                self.clasificador.preseleccionar, llm.max_carpetas, "", texto, catalogo, []
            )
        lista = "\n".join(
            f"{i}. {f['nombre']} (está en {catalogo.etiqueta(f['categoria'] or 'sin carpeta')})"
            for i, f in enumerate(candidatos, 1)
        )
        datos = await llm.generar_json(
            SISTEMA,
            f"Archivos:\n{lista}\n\nCarpetas:\n" + "\n".join(f"- {e}" for e in etiquetas)
            + f"\n\nMensaje del usuario:\n<mensaje>\n{texto}\n</mensaje>",
            ESQUEMA,
        )
        propuesta = Propuesta(candidatos=candidatos, modelo=llm.descripcion)
        numero = datos.get("archivo")
        if len(candidatos) == 1:
            propuesta.archivo = candidatos[0]
        elif isinstance(numero, int) and 1 <= numero <= len(candidatos):
            propuesta.archivo = candidatos[numero - 1]
        try:
            propuesta.carpeta = a_valor(str(datos.get("carpeta") or ""), catalogo)
        except CarpetaInvalida:
            pass  # no dijo dónde: lo elige el usuario
        actual = propuesta.archivo["categoria"] if propuesta.archivo and propuesta.archivo["estado"] != "pendiente" else None
        if propuesta.carpeta and actual and propuesta.carpeta.casefold() == actual.casefold():
            propuesta.carpeta = None
        return propuesta
