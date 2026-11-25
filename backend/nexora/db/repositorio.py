"""Consultas SQL de Nexora. Cada función recibe una conexión abierta con Database.conectar()."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

Fila = sqlite3.Row


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def nuevo_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# archivos_indexados
# ---------------------------------------------------------------------------

_COLUMNAS_EDITABLES = {
    "ruta_actual", "categoria", "resumen_ejecutivo", "palabras_clave", "confianza", "estado",
    "origen_clasificacion", "modelo_llm", "error", "clasificado_en", "hash_sha256", "tamano_bytes",
}


def insertar_archivo(con: sqlite3.Connection, **datos: Any) -> str:
    datos.setdefault("id", nuevo_id())
    if isinstance(datos.get("palabras_clave"), list):
        datos["palabras_clave"] = json.dumps(datos["palabras_clave"], ensure_ascii=False)
    columnas = ", ".join(datos)
    marcadores = ", ".join(f":{c}" for c in datos)
    con.execute(f"INSERT INTO archivos_indexados ({columnas}) VALUES ({marcadores})", datos)
    return datos["id"]


def actualizar_archivo(con: sqlite3.Connection, archivo_id: str, **campos: Any) -> None:
    desconocidas = set(campos) - _COLUMNAS_EDITABLES
    if desconocidas:
        raise ValueError(f"Columnas no editables: {sorted(desconocidas)}")
    if isinstance(campos.get("palabras_clave"), list):
        campos["palabras_clave"] = json.dumps(campos["palabras_clave"], ensure_ascii=False)
    campos["actualizado_en"] = ahora()
    asignaciones = ", ".join(f"{c} = :{c}" for c in campos)
    con.execute(f"UPDATE archivos_indexados SET {asignaciones} WHERE id = :_id", {**campos, "_id": archivo_id})


def obtener_archivo(con: sqlite3.Connection, archivo_id: str) -> Fila | None:
    return con.execute("SELECT * FROM archivos_indexados WHERE id = ?", (archivo_id,)).fetchone()


def obtener_archivos(con: sqlite3.Connection, ids: list[str]) -> dict[str, Fila]:
    if not ids:
        return {}
    marcadores = ", ".join("?" for _ in ids)
    filas = con.execute(f"SELECT * FROM archivos_indexados WHERE id IN ({marcadores})", ids).fetchall()
    return {f["id"]: f for f in filas}


def buscar_por_ruta(con: sqlite3.Connection, ruta: Path) -> Fila | None:
    return con.execute("SELECT * FROM archivos_indexados WHERE ruta_actual = ?", (str(ruta),)).fetchone()


def ensenables(con: sqlite3.Connection) -> list[Fila]:
    """Archivos que un motor en la nube ubicó por sí solo y el usuario no ha corregido."""
    return con.execute(
        """
        SELECT id, categoria FROM archivos_indexados
        WHERE estado = 'clasificado' AND origen_clasificacion = 'ia' AND categoria IS NOT NULL
          AND modelo_llm IS NOT NULL AND modelo_llm NOT LIKE 'ollama:%'
        """
    ).fetchall()


def liberar_ruta(con: sqlite3.Connection, ruta: Path, salvo_id: str) -> None:
    """Olvida el registro de un archivo borrado a mano cuya ruta va a ocupar otro archivo."""
    con.execute("DELETE FROM archivos_indexados WHERE ruta_actual = ? AND id <> ?", (str(ruta), salvo_id))


def resumen(con: sqlite3.Connection) -> dict[str, Any]:
    fila = con.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(date(clasificado_en, 'localtime') = date('now', 'localtime')), 0) AS clasificados_hoy,
               COALESCE(SUM(date(clasificado_en, 'localtime') = date('now', 'localtime', '-1 day')), 0) AS clasificados_ayer,
               COALESCE(SUM(estado = 'clasificado'), 0) AS aciertos,
               COALESCE(SUM(estado = 'corregido'), 0) AS correcciones,
               COALESCE(SUM(estado = 'pendiente'), 0) AS pendientes,
               COALESCE(SUM(estado = 'error'), 0) AS errores,
               COALESCE(SUM(estado = 'procesando'), 0) AS en_proceso
        FROM archivos_indexados
        """
    ).fetchone()
    datos = dict(fila)
    aciertos, correcciones = datos.pop("aciertos"), datos.pop("correcciones")
    decididos = aciertos + correcciones
    datos["precision"] = round(100 * aciertos / decididos, 1) if decididos else None
    return datos


def actividad(con: sqlite3.Connection, limite: int) -> list[Fila]:
    return con.execute("SELECT * FROM archivos_indexados ORDER BY actualizado_en DESC LIMIT ?", (limite,)).fetchall()


def rutas_sin_terminar(con: sqlite3.Connection) -> list[Path]:
    filas = con.execute("SELECT ruta_actual FROM archivos_indexados WHERE estado IN ('procesando', 'error')").fetchall()
    return [Path(f["ruta_actual"]) for f in filas]


def buscar_fts(con: sqlite3.Connection, consulta_fts: str, limite: int) -> list[Fila]:
    """Búsqueda de texto completo ordenada por relevancia BM25 (el nombre pesa más)."""
    if not consulta_fts:
        return []
    return con.execute(
        """
        SELECT a.*
        FROM archivos_fts
        JOIN archivos_indexados a ON a.num = archivos_fts.rowid
        WHERE archivos_fts MATCH ?
        ORDER BY bm25(archivos_fts, 10.0, 2.0, 4.0, 1.0)
        LIMIT ?
        """,
        (consulta_fts, limite),
    ).fetchall()


def categorias_conocidas(con: sqlite3.Connection) -> list[str]:
    filas = con.execute(
        """
        SELECT categoria, COUNT(*) AS usos FROM archivos_indexados
        WHERE categoria IS NOT NULL AND estado IN ('clasificado', 'corregido')
        GROUP BY categoria ORDER BY usos DESC LIMIT 200
        """
    ).fetchall()
    return [f["categoria"] for f in filas]


# ---------------------------------------------------------------------------
# clasificaciones_pendientes
# ---------------------------------------------------------------------------

def crear_pendiente(
    con: sqlite3.Connection, archivo_id: str, carpeta_sugerida: str, confianza: float,
    alternativas: list[str], razonamiento: str | None,
) -> str:
    pendiente_id = nuevo_id()
    con.execute(
        """
        INSERT INTO clasificaciones_pendientes (id, archivo_id, carpeta_sugerida, confianza, alternativas, razonamiento)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (pendiente_id, archivo_id, carpeta_sugerida, confianza, json.dumps(alternativas, ensure_ascii=False), razonamiento),
    )
    return pendiente_id


def pendiente_abierta(con: sqlite3.Connection, archivo_id: str) -> Fila | None:
    return con.execute(
        "SELECT * FROM clasificaciones_pendientes WHERE archivo_id = ? AND estado = 'abierta'", (archivo_id,)
    ).fetchone()


def listar_pendientes(con: sqlite3.Connection, limite: int = 50) -> list[Fila]:
    return con.execute(
        """
        SELECT p.id AS pendiente_id, p.carpeta_sugerida, p.confianza AS confianza_sugerencia, p.alternativas,
               p.razonamiento, p.creado_en AS pendiente_creado_en, a.*
        FROM clasificaciones_pendientes p
        JOIN archivos_indexados a ON a.id = p.archivo_id
        WHERE p.estado = 'abierta'
        ORDER BY p.creado_en DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def resolver_pendiente(con: sqlite3.Connection, pendiente_id: str) -> None:
    con.execute(
        "UPDATE clasificaciones_pendientes SET estado = 'resuelta', resuelto_en = ? WHERE id = ?",
        (ahora(), pendiente_id),
    )


# ---------------------------------------------------------------------------
# feedback_aprendizaje
# ---------------------------------------------------------------------------

def insertar_feedback(
    con: sqlite3.Connection, archivo_id: str, carpeta_sugerida: str | None, carpeta_elegida: str,
    confianza_original: float | None, fue_correccion: bool, dispositivo_id: str | None, motivo: str = "",
) -> str:
    feedback_id = nuevo_id()
    con.execute(
        """
        INSERT INTO feedback_aprendizaje
            (id, archivo_id, carpeta_sugerida, carpeta_elegida, confianza_original, fue_correccion, dispositivo_id, motivo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (feedback_id, archivo_id, carpeta_sugerida, carpeta_elegida, confianza_original, int(fue_correccion), dispositivo_id, motivo),
    )
    return feedback_id


def contar_feedback(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM feedback_aprendizaje").fetchone()[0]


# ---------------------------------------------------------------------------
# dispositivos_vinculados y códigos de emparejamiento
# ---------------------------------------------------------------------------

def crear_dispositivo(
    con: sqlite3.Connection, nombre: str, tipo: str, plataforma: str | None, token_hash: str,
    token_prefijo: str, permisos: list[str], clave_publica: str | None = None,
) -> str:
    dispositivo_id = nuevo_id()
    con.execute(
        """
        INSERT INTO dispositivos_vinculados (id, nombre, tipo, plataforma, token_hash, token_prefijo, permisos, clave_publica)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (dispositivo_id, nombre, tipo, plataforma, token_hash, token_prefijo, json.dumps(permisos), clave_publica),
    )
    return dispositivo_id


def buscar_dispositivo_por_hash(con: sqlite3.Connection, token_hash: str) -> Fila | None:
    return con.execute(
        "SELECT * FROM dispositivos_vinculados WHERE token_hash = ? AND estado = 'activo'", (token_hash,)
    ).fetchone()


def registrar_acceso(con: sqlite3.Connection, dispositivo_id: str, ip: str | None) -> None:
    con.execute(
        "UPDATE dispositivos_vinculados SET ultimo_acceso = ?, ultima_ip = ? WHERE id = ?", (ahora(), ip, dispositivo_id)
    )


def listar_dispositivos(con: sqlite3.Connection) -> list[Fila]:
    return con.execute("SELECT * FROM dispositivos_vinculados ORDER BY vinculado_en DESC").fetchall()


def revocar_dispositivos(con: sqlite3.Connection, condicion: str, parametros: tuple) -> int:
    cursor = con.execute(
        f"UPDATE dispositivos_vinculados SET estado = 'revocado', revocado_en = ? WHERE estado = 'activo' AND {condicion}",
        (ahora(), *parametros),
    )
    return cursor.rowcount


def crear_codigo_emparejamiento(con: sqlite3.Connection, codigo_hash: str, creado_por: str, vigencia: timedelta) -> str:
    expira = (datetime.now(timezone.utc) + vigencia).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    codigo_id = nuevo_id()
    con.execute(
        "INSERT INTO codigos_emparejamiento (id, codigo_hash, creado_por, expira_en) VALUES (?, ?, ?, ?)",
        (codigo_id, codigo_hash, creado_por, expira),
    )
    return expira


def consumir_codigo_emparejamiento(con: sqlite3.Connection, codigo_hash: str) -> Fila | None:
    """Marca el código como usado solo si existe, no se ha usado y no ha caducado (operación atómica)."""
    momento = ahora()
    return con.execute(
        """
        UPDATE codigos_emparejamiento SET usado_en = ?
        WHERE codigo_hash = ? AND usado_en IS NULL AND expira_en > ?
        RETURNING *
        """,
        (momento, codigo_hash, momento),
    ).fetchone()


def vincular_codigo(con: sqlite3.Connection, codigo_id: str, dispositivo_id: str) -> None:
    con.execute("UPDATE codigos_emparejamiento SET dispositivo_id = ? WHERE id = ?", (dispositivo_id, codigo_id))


# ---------------------------------------------------------------------------
# carpetas_vigiladas y ajustes
# ---------------------------------------------------------------------------

def sembrar_carpetas(con: sqlite3.Connection, rutas: list[Path]) -> None:
    for ruta in rutas:
        con.execute(
            "INSERT OR IGNORE INTO carpetas_vigiladas (id, ruta) VALUES (?, ?)", (nuevo_id(), str(ruta.expanduser()))
        )


def carpetas_activas(con: sqlite3.Connection) -> list[Fila]:
    return con.execute("SELECT * FROM carpetas_vigiladas WHERE activa = 1").fetchall()


def activar_carpeta(con: sqlite3.Connection, ruta: Path, recursiva: bool) -> str:
    return con.execute(
        """
        INSERT INTO carpetas_vigiladas (id, ruta, recursiva) VALUES (?, ?, ?)
        ON CONFLICT (ruta) DO UPDATE SET activa = 1, recursiva = excluded.recursiva RETURNING id
        """,
        (nuevo_id(), str(ruta), int(recursiva)),
    ).fetchone()["id"]


def desactivar_carpeta(con: sqlite3.Connection, carpeta_id: str) -> str | None:
    fila = con.execute(
        "UPDATE carpetas_vigiladas SET activa = 0 WHERE id = ? AND activa = 1 RETURNING ruta", (carpeta_id,)
    ).fetchone()
    return fila["ruta"] if fila else None


def leer_ajuste(con: sqlite3.Connection, clave: str, por_defecto: str) -> str:
    fila = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila else por_defecto


def guardar_ajuste(con: sqlite3.Connection, clave: str, valor: str) -> None:
    con.execute(
        """
        INSERT INTO ajustes (clave, valor, actualizado_en) VALUES (?, ?, ?)
        ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor, actualizado_en = excluded.actualizado_en
        """,
        (clave, valor, ahora()),
    )
