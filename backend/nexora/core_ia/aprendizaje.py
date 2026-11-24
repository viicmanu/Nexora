"""Feedback del usuario (Human-in-the-Loop): mueve el archivo, registra la decisión y la convierte en ejemplo."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from nexora.core_ia.carpetas import mover_seguro, normalizar_carpeta, ruta_destino
from nexora.core_ia.errores import ArchivoNoDisponible, ArchivoNoEncontrado
from nexora.core_ia.vector_store import AlmacenVectorial
from nexora.db import Database
from nexora.db import repositorio as repo

log = logging.getLogger(__name__)


@dataclass
class ResultadoFeedback:
    fila: repo.Fila
    feedback_id: str
    carpeta_sugerida: str | None
    fue_correccion: bool
    ruta_anterior: str
    aprendizaje_registrado: bool


class ServicioAprendizaje:
    def __init__(self, db: Database, vectores: AlmacenVectorial, raiz_organizada: Path) -> None:
        self.db = db
        self.vectores = vectores
        self.raiz = raiz_organizada
        self._lock = asyncio.Lock()  # evita dos movimientos simultáneos del mismo archivo

    async def registrar(self, archivo_id: str, carpeta_elegida: str, dispositivo_id: str | None) -> ResultadoFeedback:
        carpeta = normalizar_carpeta(carpeta_elegida)  # lanza CarpetaInvalida -> 422
        destino = ruta_destino(self.raiz, carpeta)
        async with self._lock:
            resultado = await asyncio.to_thread(self._aplicar, archivo_id, carpeta, destino, dispositivo_id)

        # El índice vectorial va después del commit: si falla, la decisión del usuario ya está guardada
        # y se informa con `aprendizaje_registrado = False`.
        try:
            resultado.aprendizaje_registrado = await asyncio.to_thread(
                self.vectores.registrar_ejemplo, resultado.feedback_id, archivo_id, carpeta, resultado.fue_correccion
            )
        except Exception:
            log.exception("No se pudo guardar el ejemplo de aprendizaje de %s", archivo_id)
        return resultado

    def _aplicar(self, archivo_id: str, carpeta: str, destino: Path, dispositivo_id: str | None) -> ResultadoFeedback:
        with self.db.conectar() as con:
            fila = repo.obtener_archivo(con, archivo_id)
        if fila is None:
            raise ArchivoNoEncontrado(archivo_id)
        origen = Path(fila["ruta_actual"])
        if not origen.is_file():
            raise ArchivoNoDisponible(str(origen))

        nueva_ruta = mover_seguro(origen, destino)
        try:
            with self.db.conectar() as con:
                pendiente = repo.pendiente_abierta(con, archivo_id)
                sugerida = pendiente["carpeta_sugerida"] if pendiente else fila["categoria"]
                confianza = pendiente["confianza"] if pendiente else fila["confianza"]
                fue_correccion = (sugerida or "").casefold() != carpeta.casefold()

                repo.actualizar_archivo(
                    con, archivo_id,
                    ruta_actual=str(nueva_ruta),
                    categoria=carpeta,
                    estado="corregido" if fue_correccion else "clasificado",
                    origen_clasificacion="usuario",
                    confianza=1.0,
                    clasificado_en=repo.ahora(),
                )
                if pendiente:
                    repo.resolver_pendiente(con, pendiente["id"])
                feedback_id = repo.insertar_feedback(
                    con, archivo_id, sugerida, carpeta, confianza, fue_correccion, dispositivo_id
                )
                fila_nueva = repo.obtener_archivo(con, archivo_id)
        except Exception:
            # Si la base de datos falla, el archivo vuelve a su sitio para que índice y disco coincidan.
            if nueva_ruta != origen:
                shutil.move(str(nueva_ruta), str(origen))
            raise

        return ResultadoFeedback(fila_nueva, feedback_id, sugerida, fue_correccion, str(origen), False)
