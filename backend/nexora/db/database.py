"""Acceso a SQLite. Una conexión por operación: simple y seguro entre hilos (watcher, threadpool de FastAPI)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path


class Database:
    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta

    def inicializar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        esquema = resources.files("nexora.db").joinpath("schema.sql").read_text(encoding="utf-8")
        with self.conectar() as con:
            con.executescript(esquema)

    @contextmanager
    def conectar(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión, hace commit si todo va bien y rollback si hay una excepción."""
        con = sqlite3.connect(self.ruta, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
