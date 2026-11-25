"""Índice semántico en ChromaDB.

Dos colecciones:
- `archivos_ml`: un embedding por archivo indexado (búsqueda semántica desde escritorio o móvil).
- `aprendizaje_ml`: ejemplos de archivos bien ubicados. Los de fuente "usuario" son decisiones de la persona; los de
  fuente "ia" los deja el motor principal para que el de respaldo aprenda de él.

Las colecciones sin sufijo son del comparador anterior (pensado para inglés): se leen para migrar y no se modifican.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

log = logging.getLogger(__name__)
Fuente = Literal["usuario", "ia"]
Embebedor = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class Vecino:
    id: str
    categoria: str
    similitud: float  # 0..1 (1 - distancia coseno)
    nombre: str
    fuente: Fuente = "usuario"
    motivo: str = ""  # lo que explicó el usuario al corregir


class AlmacenVectorial(Protocol):
    def indexar_archivo(self, archivo_id: str, documento: str, metadatos: dict[str, Any]) -> None: ...
    def similitudes(self, consulta: str, textos: list[str]) -> list[float]: ...
    def buscar_archivos(self, consulta: str, n: int) -> list[tuple[str, float]]: ...
    def vecinos_aprendizaje(self, consulta: str, n: int) -> list[Vecino]: ...
    def registrar_ejemplo(
        self, ejemplo_id: str, archivo_id: str, categoria: str, fue_correccion: bool,
        fuente: Fuente = "usuario", motivo: str = "",
    ) -> bool: ...


class AlmacenChroma:
    def __init__(self, ruta: Path, embebedor: Embebedor | None = None) -> None:
        import chromadb

        ruta.mkdir(parents=True, exist_ok=True)
        self._cliente = chromadb.PersistentClient(path=str(ruta), settings=chromadb.Settings(anonymized_telemetry=False))
        self._recordados: dict[str, Any] = {}
        sufijo = "_ml" if embebedor else ""
        if embebedor is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            embebedor = DefaultEmbeddingFunction()
        self._embeber = embebedor
        self._archivos = self._coleccion("archivos" + sufijo)
        self._aprendizaje = self._coleccion("aprendizaje" + sufijo)
        if sufijo:
            self._migrar("archivos", self._archivos)
            self._migrar("aprendizaje", self._aprendizaje)

    def _coleccion(self, nombre: str):
        return self._cliente.get_or_create_collection(nombre, metadata={"hnsw:space": "cosine"})

    def _migrar(self, antigua: str, nueva) -> None:
        """Recalcula con el comparador multilingüe lo que solo está en la colección anterior."""
        if antigua not in {c if isinstance(c, str) else c.name for c in self._cliente.list_collections()}:
            return
        viejos = self._cliente.get_collection(antigua).get(include=["documents", "metadatas"])
        ya = set(nueva.get(include=[])["ids"])
        faltan = [i for i, x in enumerate(viejos["ids"]) if x not in ya]
        if not faltan:
            return
        documentos = [viejos["documents"][i] for i in faltan]
        nueva.upsert(
            ids=[viejos["ids"][i] for i in faltan], documents=documentos, embeddings=self._embeber(documentos),
            metadatas=[viejos["metadatas"][i] for i in faltan],
        )
        log.info("Índice '%s' recalculado: %d elementos", antigua, len(faltan))

    def similitudes(self, consulta: str, textos: list[str]) -> list[float]:
        """Similitud coseno de la consulta con cada texto (los embeddings de los textos se recuerdan)."""
        import numpy as np

        nuevos = [t for t in textos if t not in self._recordados]
        if nuevos:
            self._recordados.update(zip(nuevos, self._embeber(nuevos)))
        q = np.asarray(self._embeber([consulta])[0])
        matriz = np.asarray([self._recordados[t] for t in textos])
        normas = np.linalg.norm(matriz, axis=1) * np.linalg.norm(q)
        return (matriz @ q / np.maximum(normas, 1e-9)).tolist()

    def indexar_archivo(self, archivo_id: str, documento: str, metadatos: dict[str, Any]) -> None:
        limpios = {k: v for k, v in metadatos.items() if v is not None}
        self._archivos.upsert(
            ids=[archivo_id], documents=[documento], embeddings=self._embeber([documento]), metadatas=[limpios]
        )

    def buscar_archivos(self, consulta: str, n: int) -> list[tuple[str, float]]:
        total = self._archivos.count()
        if total == 0:
            return []
        res = self._archivos.query(
            query_embeddings=self._embeber([consulta]), n_results=min(n, total), include=["distances"]
        )
        return [(i, 1.0 - d) for i, d in zip(res["ids"][0], res["distances"][0])]

    def vecinos_aprendizaje(self, consulta: str, n: int) -> list[Vecino]:
        """Los n ejemplos más parecidos de cada fuente."""
        if self._aprendizaje.count() == 0:
            return []
        q = self._embeber([consulta])
        vecinos: list[Vecino] = []
        for fuente in ("usuario", "ia"):
            total = len(self._aprendizaje.get(where={"fuente": fuente}, include=[])["ids"])
            if not total:
                continue
            res = self._aprendizaje.query(
                query_embeddings=q, n_results=min(n, total), where={"fuente": fuente}, include=["distances", "metadatas"]
            )
            vecinos += [
                Vecino(
                    id=i, categoria=m["categoria"], similitud=1.0 - d, nombre=m.get("nombre", ""), fuente=fuente,
                    motivo=m.get("motivo", ""),
                )
                for i, d, m in zip(res["ids"][0], res["distances"][0], res["metadatas"][0])
            ]
        return vecinos

    def registrar_ejemplo(
        self, ejemplo_id: str, archivo_id: str, categoria: str, fue_correccion: bool,
        fuente: Fuente = "usuario", motivo: str = "",
    ) -> bool:
        """Guarda el archivo como ejemplo. Hay uno por archivo: lo último que se decide reemplaza lo anterior."""
        actual = self._archivos.get(ids=[archivo_id], include=["documents", "metadatas"])
        if not actual["ids"]:
            return False
        meta = actual["metadatas"][0] or {}
        # Se compara con archivos nuevos, de los que aún no hay resumen: se usa solo nombre y texto.
        documento = meta.get("base") or actual["documents"][0]
        anteriores = self._aprendizaje.get(where={"archivo_id": archivo_id}, include=["metadatas"])
        if fuente == "ia" and any(m.get("fuente") == "usuario" for m in anteriores["metadatas"]):
            return True  # la decisión del usuario manda
        self._aprendizaje.upsert(
            ids=[ejemplo_id], documents=[documento], embeddings=self._embeber([documento]),
            metadatas=[{
                "categoria": categoria, "archivo_id": archivo_id, "nombre": meta.get("nombre", ""),
                "fue_correccion": fue_correccion, "fuente": fuente, "motivo": motivo,
            }],
        )
        sobrantes = [i for i in anteriores["ids"] if i != ejemplo_id]
        if sobrantes:
            self._aprendizaje.delete(ids=sobrantes)
        self._archivos.update(ids=[archivo_id], metadatas=[{**meta, "categoria": categoria}])
        return True
