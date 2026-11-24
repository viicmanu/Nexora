"""Índice semántico en ChromaDB.

Dos colecciones:
- `archivos`: un embedding por archivo indexado (búsqueda semántica desde escritorio o móvil).
- `aprendizaje`: ejemplos confirmados o corregidos por el usuario. Al clasificar un archivo nuevo se buscan
  los ejemplos más parecidos y su carpeta influye en la decisión (aprendizaje continuo).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Vecino:
    id: str
    categoria: str
    similitud: float  # 0..1 (1 - distancia coseno)
    nombre: str


class AlmacenVectorial(Protocol):
    def indexar_archivo(self, archivo_id: str, documento: str, metadatos: dict[str, Any]) -> None: ...
    def buscar_archivos(self, consulta: str, n: int) -> list[tuple[str, float]]: ...
    def vecinos_aprendizaje(self, consulta: str, n: int) -> list[Vecino]: ...
    def registrar_ejemplo(self, ejemplo_id: str, archivo_id: str, categoria: str, fue_correccion: bool) -> bool: ...


class AlmacenChroma:
    def __init__(self, ruta: Path) -> None:
        import chromadb

        ruta.mkdir(parents=True, exist_ok=True)
        self._cliente = chromadb.PersistentClient(path=str(ruta), settings=chromadb.Settings(anonymized_telemetry=False))
        # Función de embedding por defecto de Chroma (all-MiniLM-L6-v2, se ejecuta localmente con ONNX).
        self._archivos = self._cliente.get_or_create_collection("archivos", metadata={"hnsw:space": "cosine"})
        self._aprendizaje = self._cliente.get_or_create_collection("aprendizaje", metadata={"hnsw:space": "cosine"})

    def indexar_archivo(self, archivo_id: str, documento: str, metadatos: dict[str, Any]) -> None:
        limpios = {k: v for k, v in metadatos.items() if v is not None}
        self._archivos.upsert(ids=[archivo_id], documents=[documento], metadatas=[limpios])

    def buscar_archivos(self, consulta: str, n: int) -> list[tuple[str, float]]:
        total = self._archivos.count()
        if total == 0:
            return []
        res = self._archivos.query(query_texts=[consulta], n_results=min(n, total), include=["distances"])
        return [(i, 1.0 - d) for i, d in zip(res["ids"][0], res["distances"][0])]

    def vecinos_aprendizaje(self, consulta: str, n: int) -> list[Vecino]:
        total = self._aprendizaje.count()
        if total == 0:
            return []
        res = self._aprendizaje.query(query_texts=[consulta], n_results=min(n, total), include=["distances", "metadatas"])
        return [
            Vecino(id=i, categoria=m["categoria"], similitud=1.0 - d, nombre=m.get("nombre", ""))
            for i, d, m in zip(res["ids"][0], res["distances"][0], res["metadatas"][0])
        ]

    def registrar_ejemplo(self, ejemplo_id: str, archivo_id: str, categoria: str, fue_correccion: bool) -> bool:
        """Copia el embedding del archivo a la colección de aprendizaje con la carpeta que eligió el usuario."""
        actual = self._archivos.get(ids=[archivo_id], include=["embeddings", "documents", "metadatas"])
        if not actual["ids"]:
            return False
        metadatos_archivo = actual["metadatas"][0] or {}
        self._aprendizaje.upsert(
            ids=[ejemplo_id],
            embeddings=[actual["embeddings"][0]],
            documents=[actual["documents"][0]],
            metadatas=[{
                "categoria": categoria,
                "archivo_id": archivo_id,
                "nombre": metadatos_archivo.get("nombre", ""),
                "fue_correccion": fue_correccion,
                "fuente": "usuario",
            }],
        )
        self._archivos.update(ids=[archivo_id], metadatas=[{**metadatos_archivo, "categoria": categoria}])
        return True
