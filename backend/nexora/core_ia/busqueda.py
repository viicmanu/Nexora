"""Búsqueda híbrida: texto completo (nombre, resumen, palabras clave, ruta) + similitud semántica.

Las dos listas se combinan con Reciprocal Rank Fusion, que no necesita que las puntuaciones de
BM25 y de los embeddings estén en la misma escala.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.core_ia.vector_store import AlmacenVectorial

K_RRF = 60
SIMILITUD_MINIMA = 0.30  # por debajo, un resultado semántico es ruido
MAX_TERMINOS = 12

Coincidencia = Literal["nombre", "semantica", "ambas"]


@dataclass
class ResultadoBusqueda:
    fila: repo.Fila
    puntuacion: float
    coincidencia: Coincidencia
    similitud: float | None


def consulta_fts(texto: str) -> str:
    """Convierte texto libre en una consulta FTS5 segura: cada término entre comillas y como prefijo."""
    terminos = re.findall(r"\w+", texto, flags=re.UNICODE)[:MAX_TERMINOS]
    return " OR ".join(f'"{t}"*' for t in terminos)


class BuscadorHibrido:
    def __init__(self, db: Database, vectores: AlmacenVectorial) -> None:
        self.db = db
        self.vectores = vectores

    async def buscar(self, texto: str, limite: int = 10, carpeta: str | None = None) -> list[ResultadoBusqueda]:
        candidatos = limite * 3
        filas_fts, semanticos = await asyncio.gather(
            asyncio.to_thread(self._buscar_fts, texto, candidatos),
            asyncio.to_thread(self.vectores.buscar_archivos, texto, candidatos),
        )
        semanticos = [(i, s) for i, s in semanticos if s >= SIMILITUD_MINIMA]

        puntuaciones: dict[str, float] = {}
        por_nombre: set[str] = set()
        similitudes: dict[str, float] = {}
        for posicion, fila in enumerate(filas_fts):
            puntuaciones[fila["id"]] = puntuaciones.get(fila["id"], 0) + 1 / (K_RRF + posicion + 1)
            por_nombre.add(fila["id"])
        for posicion, (archivo_id, similitud) in enumerate(semanticos):
            puntuaciones[archivo_id] = puntuaciones.get(archivo_id, 0) + 1 / (K_RRF + posicion + 1)
            similitudes[archivo_id] = similitud

        filas = {f["id"]: f for f in filas_fts}
        faltan = [i for i in puntuaciones if i not in filas]
        if faltan:
            filas.update(await asyncio.to_thread(self._obtener, faltan))

        resultados: list[ResultadoBusqueda] = []
        for archivo_id, puntuacion in sorted(puntuaciones.items(), key=lambda par: par[1], reverse=True):
            fila = filas.get(archivo_id)
            if fila is None:  # está en Chroma pero se borró de SQLite
                continue
            if carpeta and not (fila["categoria"] or "").casefold().startswith(carpeta.casefold()):
                continue
            en_nombre, en_semantica = archivo_id in por_nombre, archivo_id in similitudes
            coincidencia: Coincidencia = "ambas" if en_nombre and en_semantica else ("nombre" if en_nombre else "semantica")
            resultados.append(ResultadoBusqueda(fila, round(puntuacion, 5), coincidencia, similitudes.get(archivo_id)))
            if len(resultados) >= limite:
                break
        return resultados

    def _buscar_fts(self, texto: str, limite: int) -> list[repo.Fila]:
        with self.db.conectar() as con:
            return repo.buscar_fts(con, consulta_fts(texto), limite)

    def _obtener(self, ids: list[str]) -> dict[str, repo.Fila]:
        with self.db.conectar() as con:
            return repo.obtener_archivos(con, ids)
