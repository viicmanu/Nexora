"""Tokens de acceso por dispositivo y permisos (scopes)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

from nexora.db import Database
from nexora.db import repositorio as repo

log = logging.getLogger(__name__)

PREFIJO_TOKEN = "nx_"

# Permisos disponibles.
BUSCAR = "files:search"            # GET /api/files/search
EXPLICAR = "files:explain"         # POST /api/files/{id}/explain (lee el contenido del archivo)
ORGANIZAR = "files:organize"       # bandeja de dudas y feedback (mueve archivos)
AJUSTES = "settings:write"
DISPOSITIVOS = "devices:manage"    # generar códigos QR, revocar móviles
TODOS = "*"

PERMISOS_ESCRITORIO = [TODOS]
# Fase 2: un móvil emparejado solo puede consultar; no mueve archivos ni gestiona dispositivos.
PERMISOS_MOVIL = [BUSCAR, EXPLICAR]


def generar_token() -> str:
    return PREFIJO_TOKEN + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tiene_permiso(permisos_json: str, permiso: str) -> bool:
    permisos = json.loads(permisos_json or "[]")
    return TODOS in permisos or permiso in permisos


def asegurar_token_escritorio(db: Database, ruta_token: Path) -> str:
    """Devuelve el token de la app de escritorio; lo crea la primera vez.

    El token se guarda en un archivo del perfil del usuario que la app Tauri lee al arrancar y envía en
    la cabecera Authorization. En la base de datos solo queda su hash.
    """
    if ruta_token.is_file():
        token = ruta_token.read_text(encoding="utf-8").strip()
        with db.conectar() as con:
            if repo.buscar_dispositivo_por_hash(con, hash_token(token)):
                return token
        log.warning("El token del escritorio no es válido o fue revocado; se genera uno nuevo.")

    token = generar_token()
    with db.conectar() as con:
        repo.revocar_dispositivos(con, "tipo = ?", ("escritorio",))
        repo.crear_dispositivo(
            con, nombre="Este PC", tipo="escritorio", plataforma=os.name, token_hash=hash_token(token),
            token_prefijo=token[:10], permisos=PERMISOS_ESCRITORIO,
        )
    ruta_token.parent.mkdir(parents=True, exist_ok=True)
    ruta_token.write_text(token, encoding="utf-8")
    try:
        ruta_token.chmod(0o600)  # en Windows solo afecta al atributo de solo lectura; el perfil ya es privado
    except OSError:
        pass
    return token
