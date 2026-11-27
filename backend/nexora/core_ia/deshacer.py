"""Deshacer el último movimiento de un archivo y enviar duplicados a la papelera."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from nexora.core_ia.carpetas import ruta_libre
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado
from nexora.core_ia.vector_store import AlmacenVectorial
from nexora.db import Database
from nexora.db import repositorio as repo

log = logging.getLogger(__name__)


class NadaQueDeshacer(LookupError):
    pass


class NoEsDuplicado(ValueError):
    pass


class ServicioDeshacer:
    def __init__(self, db: Database, vectores: AlmacenVectorial) -> None:
        self.db = db
        self.vectores = vectores
        self._lock = asyncio.Lock()

    async def deshacer(self, archivo_id: str) -> repo.Fila:
        """Devuelve el archivo a donde estaba y lo deja en la Bandeja de Dudas para decidir de nuevo."""
        async with self._lock:
            fila = await asyncio.to_thread(self._deshacer, archivo_id)
        try:
            await asyncio.to_thread(self.vectores.olvidar_ejemplos, archivo_id)  # esa decisión ya no vale
        except Exception:
            log.warning("No se pudieron borrar los ejemplos de %s", archivo_id, exc_info=True)
        return fila

    def _deshacer(self, archivo_id: str) -> repo.Fila:
        with self.db.conectar() as con:
            fila = repo.obtener_archivo(con, archivo_id)
            mov = repo.ultimo_movimiento(con, archivo_id)
        if fila is None:
            raise ArchivoNoEncontrado(archivo_id)
        if mov is None or mov["destino"] != fila["ruta_actual"]:
            raise NadaQueDeshacer("No hay ningún movimiento que deshacer.")
        actual = Path(mov["destino"])
        if not actual.is_file():
            raise ArchivoNoDisponible(str(actual))
        origen = Path(mov["origen"])
        origen.parent.mkdir(parents=True, exist_ok=True)
        vuelta = Path(shutil.move(str(actual), str(ruta_libre(origen.parent, origen.name))))
        try:
            with self.db.conectar() as con:
                repo.liberar_ruta(con, vuelta, archivo_id)
                repo.actualizar_archivo(
                    con, archivo_id, ruta_actual=str(vuelta), estado="pendiente", categoria=mov["categoria_anterior"],
                    clasificado_en=None,
                )
                anterior = repo.pendiente_abierta(con, archivo_id)
                if anterior:
                    repo.resolver_pendiente(con, anterior["id"])
                repo.crear_pendiente(
                    con, archivo_id, fila["categoria"] or "", 0.0, [],
                    f"Deshiciste el movimiento a esta carpeta. Elige dónde va.",
                )
                repo.marcar_deshecho(con, mov["id"])
                return repo.obtener_archivo(con, archivo_id)
        except Exception:
            shutil.move(str(vuelta), str(actual))
            raise

    async def a_papelera(self, archivo_id: str) -> None:
        """Solo para copias exactas: se envían a la papelera de Windows, de donde se pueden recuperar."""
        from send2trash import send2trash

        with self.db.conectar() as con:
            fila = repo.obtener_archivo(con, archivo_id)
        if fila is None:
            raise ArchivoNoEncontrado(archivo_id)
        if not fila["duplicado_de"]:
            raise NoEsDuplicado("Solo se envían a la papelera las copias exactas de otro archivo.")
        ruta = Path(fila["ruta_actual"])
        if ruta.is_file():
            await asyncio.to_thread(send2trash, str(ruta))
        with self.db.conectar() as con:
            con.execute("DELETE FROM archivos_indexados WHERE id = ?", (archivo_id,))
        try:
            await asyncio.to_thread(self.vectores.olvidar_ejemplos, archivo_id)
        except Exception:
            log.warning("No se pudieron borrar los datos de %s", archivo_id, exc_info=True)
