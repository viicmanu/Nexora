"""Vinculación del teléfono por QR, en la red local y 1 a 1.

1. El escritorio pide un código (POST /api/devices/pairing-code) y lo muestra como QR, con las IP del PC, el puerto
   y la huella del certificado TLS.
2. El teléfono lo canjea (POST /api/devices/pair) y recibe su token una única vez.
3. Como mucho hay un teléfono activo: lo garantiza un índice único parcial, así que dos canjes simultáneos no pueden
   dejar dos teléfonos vinculados.
"""

from __future__ import annotations

import json
import secrets
import socket
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.security.tokens import PERMISOS_MOVIL, generar_token, hash_token

VIGENCIA_CODIGO = timedelta(minutes=5)
MENSAJE_YA_VINCULADO = "Este PC ya tiene un teléfono vinculado. Desvincúlalo primero"


class TelefonoYaVinculado(Exception):
    pass


@dataclass
class CodigoEmparejamiento:
    codigo: str
    expira_en: str
    contenido_qr: str
    revocado: str | None  # id del teléfono reemplazado


@dataclass
class DispositivoEmparejado:
    dispositivo_id: str
    token: str
    permisos: list[str]


def identidad_pc(db: Database) -> tuple[str, str]:
    """(id estable del PC, nombre del equipo)."""
    with db.conectar() as con:
        pc_id = repo.leer_ajuste(con, "pc_id", "")
        if not pc_id:
            pc_id = repo.nuevo_id()
            repo.guardar_ajuste(con, "pc_id", pc_id)
    return pc_id, socket.gethostname()


def crear_codigo(
    db: Database, creado_por: str, hosts: list[str], puerto: int, huella: str | None, *, reemplazar: bool = False
) -> CodigoEmparejamiento:
    pc_id, nombre = identidad_pc(db)
    codigo = secrets.token_urlsafe(24)
    revocado = None
    with db.conectar() as con:
        telefono = repo.telefono_activo(con)
        if telefono:
            if not reemplazar:
                raise TelefonoYaVinculado(MENSAJE_YA_VINCULADO)
            repo.revocar_dispositivos(con, "id = ?", (telefono["id"],))
            revocado = telefono["id"]
        expira = repo.crear_codigo_emparejamiento(con, hash_token(codigo), creado_por, VIGENCIA_CODIGO)
    contenido = json.dumps(
        {"app": "nexora", "v": 2, "pc": pc_id, "nombre": nombre, "hosts": hosts, "puerto": puerto, "huella": huella,
         "codigo": codigo},
        separators=(",", ":"), ensure_ascii=False,
    )
    return CodigoEmparejamiento(codigo, expira, contenido, revocado)


def canjear_codigo(
    db: Database, codigo: str, nombre: str, plataforma: str | None, clave_publica: str | None
) -> DispositivoEmparejado | None:
    """None si el código no vale. Si ya hay un teléfono, TelefonoYaVinculado y el código sigue sin usarse."""
    token = generar_token()
    try:
        with db.conectar() as con:
            con.execute("BEGIN IMMEDIATE")  # canjes simultáneos se ejecutan uno detrás de otro
            registro = repo.consumir_codigo_emparejamiento(con, hash_token(codigo))
            if registro is None:
                return None
            if repo.telefono_activo(con):
                raise TelefonoYaVinculado(MENSAJE_YA_VINCULADO)  # el rollback devuelve el código
            dispositivo_id = repo.crear_dispositivo(
                con, nombre=nombre, tipo="movil", plataforma=plataforma, token_hash=hash_token(token),
                token_prefijo=token[:10], permisos=PERMISOS_MOVIL, clave_publica=clave_publica,
            )
            repo.vincular_codigo(con, registro["id"], dispositivo_id)
    except sqlite3.IntegrityError as exc:  # el índice único ganó la carrera
        raise TelefonoYaVinculado(MENSAJE_YA_VINCULADO) from exc
    return DispositivoEmparejado(dispositivo_id, token, PERMISOS_MOVIL)
