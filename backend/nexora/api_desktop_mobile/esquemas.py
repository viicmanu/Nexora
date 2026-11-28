"""Modelos de entrada y salida de la API (los mismos para escritorio y móvil)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from nexora.db.repositorio import Fila


class Archivo(BaseModel):
    id: str
    nombre: str
    ruta_actual: str = Field(description="Ruta completa del archivo en el PC.")
    carpeta: str | None = Field(description="Carpeta relativa a la raíz organizada.")
    resumen_ejecutivo: str | None
    palabras_clave: list[str]
    confianza: float | None
    estado: str
    existe_en_disco: bool
    actualizado_en: str
    origen_dispositivo: str | None = Field(default=None, description='"móvil: <nombre>" si llegó desde el teléfono.')

    @classmethod
    def desde_fila(cls, fila: Fila) -> "Archivo":
        return cls(
            id=fila["id"],
            nombre=fila["nombre"],
            ruta_actual=fila["ruta_actual"],
            carpeta=fila["categoria"],
            resumen_ejecutivo=fila["resumen_ejecutivo"],
            palabras_clave=json.loads(fila["palabras_clave"] or "[]"),
            confianza=fila["confianza"],
            estado=fila["estado"],
            existe_en_disco=Path(fila["ruta_actual"]).is_file(),
            actualizado_en=fila["actualizado_en"],
            origen_dispositivo=fila["origen_dispositivo"] if "origen_dispositivo" in fila.keys() else None,
        )


# --- búsqueda ---

class ResultadoBusqueda(BaseModel):
    archivo: Archivo
    puntuacion: float
    coincidencia: Literal["nombre", "semantica", "ambas"]
    similitud: float | None = None


class RespuestaBusqueda(BaseModel):
    consulta: str
    total: int
    resultados: list[ResultadoBusqueda]


# --- explicación ---

class SolicitudExplicacion(BaseModel):
    pregunta: str | None = Field(default=None, max_length=2000, description="Si se omite, se explica el documento.")
    detalle: Literal["breve", "detallado"] = "detallado"


class RespuestaExplicacion(BaseModel):
    archivo_id: str
    nombre: str
    ruta_actual: str
    pregunta: str | None
    respuesta: str
    modelo: str
    caracteres_analizados: int
    texto_truncado: bool


# --- bandeja de dudas y feedback ---

class Pendiente(BaseModel):
    pendiente_id: str
    archivo: Archivo
    carpeta_sugerida: str
    carpeta_etiqueta: str
    duplicado_de: Archivo | None = Field(default=None, description="Original del que es copia exacta.")
    carpeta_existe: bool = Field(description="False: la IA propone crearla y hay que preguntar al usuario.")
    confianza: float
    alternativas: list[str]
    razonamiento: str | None
    creado_en: str


class SolicitudFeedback(BaseModel):
    archivo_id: str
    carpeta_elegida: str = Field(min_length=1, max_length=1000, examples=["Finanzas/Hogar"])
    motivo: str = Field(default="", max_length=500, description="Explicación del usuario; la IA la tendrá en cuenta.")


class SolicitudCorreccion(BaseModel):
    texto: str = Field(min_length=3, max_length=500, examples=["La factura de agua debería estar en Hogar"])
    archivo_id: str | None = None


class PropuestaCorreccion(BaseModel):
    archivo: Archivo | None = Field(description="None si no está claro de qué archivo habla.")
    candidatos: list[Archivo]
    carpeta: str | None = Field(description="None si no está claro dónde lo quiere.")
    carpeta_etiqueta: str | None
    carpeta_existe: bool
    modelo: str


class RespuestaFeedback(BaseModel):
    archivo: Archivo
    carpeta_sugerida: str | None
    carpeta_elegida: str
    fue_correccion: bool
    ruta_anterior: str
    aprendizaje_registrado: bool = Field(description="False si no se pudo guardar el ejemplo en ChromaDB.")


# --- tema ---

ModoTema = Literal["claro", "oscuro", "auto"]


class TokensColor(BaseModel):
    fondo: str
    superficie: str
    acento_primario: str
    acento_secundario: str


class RespuestaTema(BaseModel):
    modo: ModoTema
    modo_efectivo: Literal["claro", "oscuro"]
    detectado_del_so: bool
    tokens: TokensColor


class SolicitudTema(BaseModel):
    modo: ModoTema


# --- dispositivos ---

SEGUNDOS_EN_LINEA = 90


def en_linea(fila: Fila) -> bool:
    """Un teléfono está en línea si envió un latido en los últimos 90 segundos."""
    latido = fila["ultimo_heartbeat"] if "ultimo_heartbeat" in fila.keys() else None
    if not latido or fila["estado"] != "activo":
        return False
    momento = datetime.strptime(latido, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - momento).total_seconds() < SEGUNDOS_EN_LINEA


class Dispositivo(BaseModel):
    id: str
    nombre: str
    tipo: str
    plataforma: str | None
    token_prefijo: str
    permisos: list[str]
    estado: str
    vinculado_en: str
    ultimo_acceso: str | None
    ultima_ip: str | None = None
    ultimo_heartbeat: str | None = None
    en_linea: bool = False

    @classmethod
    def desde_fila(cls, fila: Fila) -> "Dispositivo":
        datos = {k: fila[k] for k in fila.keys() if k in cls.model_fields}
        datos["permisos"] = json.loads(fila["permisos"] or "[]")
        datos["en_linea"] = en_linea(fila)
        return cls(**datos)


class RespuestaCodigo(BaseModel):
    expira_en: str
    contenido_qr: str = Field(description="Texto a codificar en el QR que leerá la app móvil.")


class SolicitudEmparejar(BaseModel):
    codigo: str = Field(min_length=16, max_length=128)
    nombre: str = Field(min_length=1, max_length=80)
    plataforma: Literal["android", "ios"] | None = None
    clave_publica: str | None = Field(default=None, max_length=4096)


class RespuestaEmparejar(BaseModel):
    dispositivo_id: str
    token: str = Field(description="Se muestra una sola vez; el móvil debe guardarlo de forma segura.")
    permisos: list[str]
    pc_id: str = ""
    pc_nombre: str = ""
