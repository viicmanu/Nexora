"""Vigilancia en tiempo real de carpetas con watchdog.

Watchdog avisa desde su propio hilo; los eventos se pasan al bucle de asyncio, se agrupan (debounce) y
un único consumidor los procesa en orden, esperando a que cada archivo termine de copiarse.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.file_watcher.indexador import Indexador

log = logging.getLogger(__name__)

EXTENSIONES_TEMPORALES = {".tmp", ".crdownload", ".part", ".partial", ".download", ".opdownload", ".!ut"}
NOMBRES_IGNORADOS = {"desktop.ini", "thumbs.db", ".ds_store"}
ESPERA_DEBOUNCE = 2.0  # segundos sin eventos antes de procesar


def debe_ignorarse(ruta: Path, raiz_organizada: Path) -> bool:
    nombre = ruta.name.casefold()
    if nombre in NOMBRES_IGNORADOS or nombre.startswith(("~$", ".")):
        return True
    if ruta.suffix.casefold() in EXTENSIONES_TEMPORALES:
        return True
    # Los archivos que Nexora ya organizó no se vuelven a procesar.
    return ruta.resolve().is_relative_to(raiz_organizada.expanduser().resolve())


class _Manejador(FileSystemEventHandler):
    def __init__(self, avisar) -> None:
        self._avisar = avisar

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._avisar(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Los navegadores descargan como .crdownload y renombran al terminar.
        if not event.is_directory:
            self._avisar(event.dest_path)


class VigilanteCarpetas:
    def __init__(self, db: Database, indexador: Indexador, raiz_organizada: Path) -> None:
        self.db = db
        self.indexador = indexador
        self.raiz = raiz_organizada
        self._cola: asyncio.Queue[Path] = asyncio.Queue()
        self._temporizadores: dict[Path, asyncio.TimerHandle] = {}
        self._observador: Observer | None = None
        self._consumidor: asyncio.Task | None = None
        self._bucle: asyncio.AbstractEventLoop | None = None

    async def iniciar(self) -> None:
        self._bucle = asyncio.get_running_loop()
        with self.db.conectar() as con:
            carpetas = repo.carpetas_activas(con)
        observador = Observer()
        manejador = _Manejador(self._desde_hilo)
        for carpeta in carpetas:
            ruta = Path(carpeta["ruta"])
            if ruta.is_dir():
                observador.schedule(manejador, str(ruta), recursive=bool(carpeta["recursiva"]))
                log.info("Vigilando %s", ruta)
            else:
                log.warning("La carpeta vigilada no existe: %s", ruta)
        observador.start()
        self._observador = observador
        self._consumidor = asyncio.create_task(self._consumir(), name="nexora-watcher")

    async def detener(self) -> None:
        if self._observador:
            self._observador.stop()
            await asyncio.to_thread(self._observador.join, 5)
        for temporizador in self._temporizadores.values():
            temporizador.cancel()
        if self._consumidor:
            self._consumidor.cancel()
            try:
                await self._consumidor
            except asyncio.CancelledError:
                pass

    def _desde_hilo(self, ruta: str) -> None:
        if self._bucle:
            self._bucle.call_soon_threadsafe(self._programar, Path(ruta))

    def _programar(self, ruta: Path) -> None:
        if debe_ignorarse(ruta, self.raiz):
            return
        anterior = self._temporizadores.pop(ruta, None)
        if anterior:
            anterior.cancel()
        self._temporizadores[ruta] = self._bucle.call_later(ESPERA_DEBOUNCE, self._encolar, ruta)

    def _encolar(self, ruta: Path) -> None:
        self._temporizadores.pop(ruta, None)
        self._cola.put_nowait(ruta)

    async def _consumir(self) -> None:
        while True:
            ruta = await self._cola.get()
            try:
                if await _esperar_estable(ruta):
                    await self.indexador.procesar(ruta)
            except Exception:
                log.exception("Error procesando %s", ruta)
            finally:
                self._cola.task_done()


async def _esperar_estable(ruta: Path, intentos: int = 30) -> bool:
    """Espera a que el tamaño deje de cambiar y el archivo se pueda abrir (Windows lo bloquea mientras se copia)."""
    tamano_previo = -1
    for _ in range(intentos):
        try:
            tamano = ruta.stat().st_size
            if tamano == tamano_previo:
                with ruta.open("rb"):
                    return True
            tamano_previo = tamano
        except FileNotFoundError:
            return False
        except PermissionError:
            pass
        await asyncio.sleep(1)
    log.warning("%s no terminó de escribirse; se omite", ruta)
    return False
