"""Base para emparejar la app móvil (Fase 2) mediante un código QR de un solo uso.

Flujo previsto:
1. El escritorio pide un código (POST /api/devices/pairing-code) y lo muestra como QR.
2. El móvil lee el QR y lo canjea (POST /api/devices/pair) a través del túnel o relay.
3. El backend crea un dispositivo `movil` con permisos de solo consulta y le devuelve su token una única vez.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.security.tokens import PERMISOS_MOVIL, generar_token, hash_token

VIGENCIA_CODIGO = timedelta(minutes=5)


@dataclass
class CodigoEmparejamiento:
    codigo: str
    expira_en: str
    contenido_qr: str


@dataclass
class DispositivoEmparejado:
    dispositivo_id: str
    token: str
    permisos: list[str]


def crear_codigo(db: Database, creado_por: str, host: str, puerto: int) -> CodigoEmparejamiento:
    codigo = secrets.token_urlsafe(24)
    with db.conectar() as con:
        expira = repo.crear_codigo_emparejamiento(con, hash_token(codigo), creado_por, VIGENCIA_CODIGO)
    # En la Fase 2 `host` será la dirección del túnel o relay en lugar de la del PC.
    contenido = json.dumps({"app": "nexora", "v": 1, "host": host, "puerto": puerto, "codigo": codigo}, separators=(",", ":"))
    return CodigoEmparejamiento(codigo, expira, contenido)


def canjear_codigo(
    db: Database, codigo: str, nombre: str, plataforma: str | None, clave_publica: str | None
) -> DispositivoEmparejado | None:
    token = generar_token()
    with db.conectar() as con:
        registro = repo.consumir_codigo_emparejamiento(con, hash_token(codigo))
        if registro is None:
            return None
        dispositivo_id = repo.crear_dispositivo(
            con, nombre=nombre, tipo="movil", plataforma=plataforma, token_hash=hash_token(token),
            token_prefijo=token[:10], permisos=PERMISOS_MOVIL, clave_publica=clave_publica,
        )
        repo.vincular_codigo(con, registro["id"], dispositivo_id)
    return DispositivoEmparejado(dispositivo_id, token, PERMISOS_MOVIL)
