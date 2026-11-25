"""Procesa un archivo nuevo: extrae, clasifica, indexa y lo mueve o lo deja en la bandeja de dudas."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from pathlib import Path

from nexora.core_ia.carpetas import destino_existe, mover_seguro, ruta_destino
from nexora.core_ia.clasificador import Clasificador, ResultadoClasificacion
from nexora.core_ia.extraccion import extraer_texto, representacion_para_embedding
from nexora.core_ia.vector_store import AlmacenVectorial
from nexora.db import Database
from nexora.db import repositorio as repo

log = logging.getLogger(__name__)


def calcular_hash(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


class Indexador:
    def __init__(
        self, db: Database, vectores: AlmacenVectorial, clasificador: Clasificador,
        raiz_organizada: Path, umbral_confianza: float, max_caracteres: int,
    ) -> None:
        self.db = db
        self.vectores = vectores
        self.clasificador = clasificador
        self.raiz = raiz_organizada
        self.umbral = umbral_confianza
        self.max_caracteres = max_caracteres

    async def procesar(self, ruta: Path) -> str | None:
        """Devuelve el id del archivo indexado, o None si se ignoró."""
        if not ruta.is_file():
            return None
        hash_archivo = await asyncio.to_thread(calcular_hash, ruta)
        existente = await asyncio.to_thread(self._buscar_por_ruta, ruta)
        if (
            existente is not None
            and existente["hash_sha256"] == hash_archivo
            and existente["estado"] not in ("procesando", "error")  # interrumpido o fallido: se reintenta
        ):
            return existente["id"]  # ya indexado y sin cambios

        texto = await asyncio.to_thread(extraer_texto, ruta, self.max_caracteres)
        archivo_id = await asyncio.to_thread(self._registrar, ruta, hash_archivo, existente)
        try:
            resultado = await self.clasificador.clasificar(ruta.name, ruta.suffix.lower(), texto)
        except Exception as exc:
            log.exception("Fallo al clasificar %s", ruta)
            await asyncio.to_thread(self._marcar_error, archivo_id, str(exc))
            return archivo_id

        try:
            ruta_final = await asyncio.to_thread(self._aplicar, archivo_id, ruta, resultado)
        except Exception as exc:
            log.exception("Fallo al mover o registrar %s", ruta)
            await asyncio.to_thread(self._marcar_error, archivo_id, str(exc))
            return archivo_id
        documento = representacion_para_embedding(ruta.name, texto, resultado.resumen_ejecutivo, resultado.palabras_clave)
        await asyncio.to_thread(
            self.vectores.indexar_archivo, archivo_id, documento,
            {
                "nombre": ruta.name, "categoria": resultado.carpeta_destino, "ruta_actual": str(ruta_final),
                "base": representacion_para_embedding(ruta.name, texto),
            },
        )
        if resultado.ensena and ruta_final != ruta:
            await asyncio.to_thread(
                self.vectores.registrar_ejemplo, f"ia:{archivo_id}", archivo_id, resultado.carpeta_destino, False, "ia"
            )
        return archivo_id

    # --- operaciones síncronas (se ejecutan en el threadpool) ---

    def _buscar_por_ruta(self, ruta: Path) -> repo.Fila | None:
        with self.db.conectar() as con:
            return repo.buscar_por_ruta(con, ruta)

    def _registrar(self, ruta: Path, hash_archivo: str, existente: repo.Fila | None) -> str:
        with self.db.conectar() as con:
            if existente is not None:  # el contenido cambió: se vuelve a clasificar
                repo.actualizar_archivo(
                    con, existente["id"], hash_sha256=hash_archivo, tamano_bytes=ruta.stat().st_size,
                    estado="procesando", error=None,
                )
                return existente["id"]
            return repo.insertar_archivo(
                con,
                nombre=ruta.name,
                extension=ruta.suffix.lower(),
                mime_type=mimetypes.guess_type(ruta.name)[0],
                tamano_bytes=ruta.stat().st_size,
                hash_sha256=hash_archivo,
                ruta_original=str(ruta),
                ruta_actual=str(ruta),
            )

    def _aplicar(self, archivo_id: str, ruta: Path, resultado: ResultadoClasificacion) -> Path:
        comunes = dict(
            categoria=resultado.carpeta_destino,
            resumen_ejecutivo=resultado.resumen_ejecutivo,
            palabras_clave=resultado.palabras_clave,
            confianza=resultado.confianza,
            modelo_llm=resultado.modelo,
        )
        # Sin intervención del usuario solo se usan carpetas que ya existen; una nueva siempre se consulta.
        if resultado.confianza >= self.umbral and destino_existe(self.raiz, resultado.carpeta_destino):
            nueva_ruta = mover_seguro(ruta, ruta_destino(self.raiz, resultado.carpeta_destino))
            with self.db.conectar() as con:
                repo.liberar_ruta(con, nueva_ruta, archivo_id)
                repo.actualizar_archivo(
                    con, archivo_id, **comunes, ruta_actual=str(nueva_ruta), estado="clasificado",
                    origen_clasificacion="ia", clasificado_en=repo.ahora(),
                )
            log.info("%s -> %s (%.0f%%)", ruta.name, resultado.carpeta_destino, resultado.confianza * 100)
            return nueva_ruta

        with self.db.conectar() as con:
            repo.actualizar_archivo(con, archivo_id, **comunes, estado="pendiente")
            anterior = repo.pendiente_abierta(con, archivo_id)
            if anterior:
                repo.resolver_pendiente(con, anterior["id"])
            repo.crear_pendiente(
                con, archivo_id, resultado.carpeta_destino, resultado.confianza,
                resultado.alternativas, resultado.razonamiento,
            )
        log.info("%s queda pendiente de revisión (%.0f%%)", ruta.name, resultado.confianza * 100)
        return ruta

    def _marcar_error(self, archivo_id: str, mensaje: str) -> None:
        with self.db.conectar() as con:
            repo.actualizar_archivo(con, archivo_id, estado="error", error=mensaje[:500])
