"""Indicaciones permanentes del usuario ("las facturas de agua van en Hogar") y su interpretación."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nexora.core_ia.carpetas import CarpetaInvalida
from nexora.core_ia.catalogo import Catalogo
from nexora.core_ia.clasificador import Clasificador, a_valor
from nexora.core_ia.llm import AdaptadorLLM

ACCIONES = ("mover", "preguntar", "no_mover", "guia")

SISTEMA = """\
El usuario de Nexora, un organizador de archivos, escribe una indicación para archivos futuros. Interprétala.

- entendida: false si no es una indicación sobre cómo organizar archivos, o si no se sabe a qué archivos se refiere.
- resumen: una frase en español, en segunda persona, que diga a qué archivos se aplica y qué se hará con ellos. No añadas
  condiciones que el usuario no haya dicho. Si no la entiendes, di qué falta.
- accion:
  - "mover": los archivos van a una carpeta concreta.
  - "preguntar": hay que consultar siempre al usuario.
  - "no_mover": los archivos se dejan donde están.
  - "guia": cualquier otro criterio o preferencia (por ejemplo, separar por materia, por año o por cliente).
- carpeta: solo con "mover". Si es una de las carpetas de la lista, su nombre copiado exactamente; si pide una que no
  existe, una ruta corta. Vacía si no dice cuál."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "entendida": {"type": "boolean"},
        "resumen": {"type": "string"},
        "accion": {"type": "string", "enum": list(ACCIONES)},
        "carpeta": {"type": "string"},
    },
    "required": ["entendida", "resumen", "accion", "carpeta"],
    "additionalProperties": False,
}


@dataclass
class Interpretacion:
    entendida: bool
    resumen: str
    accion: str
    carpeta: str | None
    modelo: str


async def interpretar(clasificador: Clasificador, texto: str) -> Interpretacion:
    catalogo = await asyncio.to_thread(clasificador.buscador.catalogo)
    return await clasificador.llm.ejecutar(lambda llm: _con(llm, clasificador, texto, catalogo))


async def _con(llm: AdaptadorLLM, clasificador: Clasificador, texto: str, catalogo: Catalogo) -> Interpretacion:
    etiquetas = catalogo.etiquetas
    if llm.max_carpetas and len(etiquetas) > llm.max_carpetas:
        etiquetas = await asyncio.to_thread(clasificador.preseleccionar, llm.max_carpetas, "", texto, catalogo, [])
    datos = await llm.generar_json(
        SISTEMA,
        "Carpetas:\n" + "\n".join(f"- {e}" for e in etiquetas) + f"\n\nIndicación:\n<mensaje>\n{texto}\n</mensaje>",
        ESQUEMA,
    )
    accion = datos.get("accion") if datos.get("accion") in ACCIONES else "guia"
    carpeta = None
    if accion == "mover":
        try:
            carpeta = a_valor(str(datos.get("carpeta") or ""), catalogo)
        except CarpetaInvalida:
            pass  # no dijo cuál: la elige el usuario
    return Interpretacion(
        bool(datos.get("entendida")), str(datos.get("resumen") or "").strip()[:400], accion, carpeta, llm.descripcion
    )
