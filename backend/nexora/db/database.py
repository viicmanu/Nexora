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
            # Bases creadas antes de que existiera la columna.
            if "motivo" not in {c["name"] for c in con.execute("PRAGMA table_info(feedback_aprendizaje)")}:
                con.execute("ALTER TABLE feedback_aprendizaje ADD COLUMN motivo TEXT")
            if "duplicado_de" not in {c["name"] for c in con.execute("PRAGMA table_info(archivos_indexados)")}:
                con.execute("ALTER TABLE archivos_indexados ADD COLUMN duplicado_de TEXT")  # id del archivo original
            if "origen_dispositivo" not in {c["name"] for c in con.execute("PRAGMA table_info(archivos_indexados)")}:
                con.execute("ALTER TABLE archivos_indexados ADD COLUMN origen_dispositivo TEXT")  # "móvil: <nombre>"
            if "ultimo_heartbeat" not in {c["name"] for c in con.execute("PRAGMA table_info(dispositivos_vinculados)")}:
                con.execute("ALTER TABLE dispositivos_vinculados ADD COLUMN ultimo_heartbeat TEXT")
            # Vinculación 1 a 1: como mucho un teléfono activo. Si una base antigua tiene varios, se conserva el último.
            con.execute(
                """
                UPDATE dispositivos_vinculados SET estado = 'revocado', revocado_en = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tipo = 'movil' AND estado = 'activo' AND id <> (
                    SELECT id FROM dispositivos_vinculados WHERE tipo = 'movil' AND estado = 'activo'
                    ORDER BY vinculado_en DESC LIMIT 1)
                """
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_un_telefono_activo ON dispositivos_vinculados (tipo) "
                "WHERE tipo = 'movil' AND estado = 'activo'"
            )

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
