"""Organizar archivos que ya estaban en una carpeta, por lotes y con el visto bueno del usuario.

La IA propone una carpeta para cada archivo del lote, pero no mueve nada: el usuario aprueba el lote (o parte de
él) y lo no aprobado se queda en la Bandeja de Dudas.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from nexora.core_ia.aprendizaje import ServicioAprendizaje
from nexora.core_ia.catalogo import _IGNORADAS, _es_proyecto
from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.file_watcher.indexador import Indexador
from nexora.file_watcher.watcher import debe_ignorarse

log = logging.getLogger(__name__)

TAMANO_LOTE = 10
MAX_ARCHIVOS = 5000


class CarpetaNoValida(ValueError):
    pass


@dataclass
class Tarea:
    carpeta: Path
    pendientes: list[Path]
    total: int
    lote: list[str] = field(default_factory=list)  # ids del lote en revisión
    estado: str = "lista"  # lista | analizando | revisar | terminada
    hechos: int = 0
    error: str = ""


class Organizador:
    def __init__(self, db: Database, indexador: Indexador, aprendizaje: ServicioAprendizaje, raiz: Path) -> None:
        self.db = db
        self.indexador = indexador
        self.aprendizaje = aprendizaje
        self.raiz = raiz.expanduser().resolve()
        self.tarea: Tarea | None = None
        self._trabajo: asyncio.Task | None = None

    async def preparar(self, carpeta: Path, recursiva: bool) -> Tarea:
        if self.tarea and self.tarea.estado == "analizando":
            raise CarpetaNoValida("Ya se está analizando otro lote. Espera a que termine.")
        carpeta = carpeta.expanduser()
        if not carpeta.is_absolute() or not carpeta.is_dir():
            raise CarpetaNoValida("Esa carpeta no existe.")
        carpeta = carpeta.resolve()
        if carpeta == Path(carpeta.anchor) or carpeta == self.raiz or carpeta.is_relative_to(self.raiz):
            raise CarpetaNoValida("No se puede organizar un disco entero ni la carpeta donde Nexora organiza.")
        archivos = await asyncio.to_thread(self._buscar, carpeta, recursiva)
        self.tarea = Tarea(carpeta, archivos, len(archivos))
        return self.tarea

    def _buscar(self, carpeta: Path, recursiva: bool) -> list[Path]:
        with self.db.conectar() as con:
            hechos = {
                f["ruta_actual"] for f in con.execute(
                    "SELECT ruta_actual FROM archivos_indexados WHERE estado NOT IN ('procesando', 'error')"
                )
            }
        encontrados: list[Path] = []
        for raiz, carpetas, archivos in os.walk(carpeta):
            actual = Path(raiz)
            carpetas[:] = [] if not recursiva else [
                c for c in carpetas
                if not c.startswith((".", "~")) and c.casefold() not in _IGNORADAS and not _es_proyecto(actual / c)
                and not (actual / c).resolve().is_relative_to(self.raiz)
            ]
            for nombre in sorted(archivos):
                ruta = actual / nombre
                if not debe_ignorarse(ruta, self.raiz) and str(ruta) not in hechos and ruta.is_file():
                    encontrados.append(ruta)
                    if len(encontrados) >= MAX_ARCHIVOS:
                        return encontrados
        return encontrados

    def analizar_siguiente(self) -> Tarea:
        tarea = self._tarea()
        if tarea.estado != "lista" or not tarea.pendientes:
            raise CarpetaNoValida("No hay ningún lote listo para analizar.")
        lote, tarea.pendientes = tarea.pendientes[:TAMANO_LOTE], tarea.pendientes[TAMANO_LOTE:]
        tarea.estado, tarea.lote, tarea.error = "analizando", [], ""
        self._trabajo = asyncio.create_task(self._analizar(tarea, lote))
        return tarea

    async def _analizar(self, tarea: Tarea, lote: list[Path]) -> None:
        for ruta in lote:
            try:
                archivo_id = await self.indexador.procesar(ruta, solo_proponer=True)
                if archivo_id:
                    tarea.lote.append(archivo_id)
            except Exception as exc:  # un archivo que falla no detiene el lote
                log.exception("No se pudo analizar %s", ruta)
                tarea.error = str(exc)[:200]
        tarea.hechos += len(lote)
        tarea.estado = "revisar" if tarea.lote else ("lista" if tarea.pendientes else "terminada")

    async def aprobar(self, aprobados: dict[str, str]) -> tuple[int, list[str]]:
        """Mueve los archivos aprobados (id -> carpeta) y deja el resto en la Bandeja de Dudas."""
        tarea = self._tarea()
        if tarea.estado != "revisar":
            raise CarpetaNoValida("No hay ningún lote esperando revisión.")
        movidos, errores = 0, []
        for archivo_id, carpeta in aprobados.items():
            if archivo_id not in tarea.lote:
                continue
            try:
                await self.aprendizaje.registrar(archivo_id, carpeta, None)
                movidos += 1
            except Exception as exc:
                errores.append(str(exc)[:150])
        tarea.lote = []
        tarea.estado = "lista" if tarea.pendientes else "terminada"
        return movidos, errores

    def cancelar(self) -> None:
        if self._trabajo and not self._trabajo.done():
            self._trabajo.cancel()
        self.tarea = None

    def _tarea(self) -> Tarea:
        if self.tarea is None:
            raise CarpetaNoValida("No hay ninguna carpeta en proceso de organización.")
        return self.tarea
