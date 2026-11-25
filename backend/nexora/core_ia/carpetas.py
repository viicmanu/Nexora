"""Validación de nombres de carpeta y movimiento seguro de archivos."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_CARACTERES_INVALIDOS = re.compile(r'[<>:"|?*\x00-\x1f]')
_NOMBRES_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NIVELES = 6


class CarpetaInvalida(ValueError):
    pass


def normalizar_carpeta(valor: str) -> str:
    """Convierte "finanzas\\hogar/" en "finanzas/hogar" y rechaza rutas absolutas, '..' o nombres no válidos en Windows."""
    partes = [p.strip() for p in re.split(r"[\\/]+", valor or "") if p.strip()]
    if not partes:
        raise CarpetaInvalida("La carpeta no puede estar vacía.")
    if len(partes) > MAX_NIVELES:
        raise CarpetaInvalida(f"Como máximo {MAX_NIVELES} niveles de carpetas.")
    for parte in partes:
        if parte in {".", ".."}:
            raise CarpetaInvalida("La carpeta no puede contener '.' ni '..'.")
        if _CARACTERES_INVALIDOS.search(parte):
            raise CarpetaInvalida(f"'{parte}' contiene caracteres no permitidos.")
        if parte.endswith((".", " ")):
            raise CarpetaInvalida(f"'{parte}' no puede terminar en punto ni espacio.")
        if parte.split(".")[0].upper() in _NOMBRES_RESERVADOS:
            raise CarpetaInvalida(f"'{parte}' es un nombre reservado del sistema.")
    return "/".join(partes)


def validar_destino(valor: str) -> str:
    """Forma canónica de un destino: relativo a la raíz de Nexora, o ruta absoluta."""
    if not Path(valor.strip()).is_absolute():
        return normalizar_carpeta(valor)
    ruta = Path(valor.strip()).resolve()
    existente = ruta
    while not existente.is_dir() and existente != existente.parent:
        existente = existente.parent
    if existente != ruta:  # subcarpeta nueva: solo dentro de una carpeta que ya existe
        if existente == Path(ruta.anchor):
            raise CarpetaInvalida("Esa carpeta no existe.")
        normalizar_carpeta(ruta.relative_to(existente).as_posix())
    sistema = [os.environ.get(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
    if ruta == Path(ruta.anchor) or any(s and ruta.is_relative_to(Path(s).resolve()) for s in sistema):
        raise CarpetaInvalida("No se pueden guardar archivos en la raíz del disco ni en carpetas del sistema.")
    return ruta.as_posix()


def canonico(raiz: Path, valor: str) -> str:
    """Como validar_destino, pero una ruta absoluta dentro de la raíz de Nexora se guarda como relativa."""
    valor = validar_destino(valor)
    raiz_resuelta = raiz.expanduser().resolve()
    ruta = Path(valor)
    if ruta.is_absolute() and ruta != raiz_resuelta and ruta.is_relative_to(raiz_resuelta):
        return ruta.relative_to(raiz_resuelta).as_posix()
    return valor


def ruta_destino(raiz: Path, valor: str) -> Path:
    valor = validar_destino(valor)
    if Path(valor).is_absolute():
        return Path(valor)
    raiz_resuelta = raiz.expanduser().resolve()
    destino = (raiz_resuelta / valor).resolve()
    if not destino.is_relative_to(raiz_resuelta):
        raise CarpetaInvalida("La carpeta debe estar dentro de la raíz organizada.")
    return destino


def destino_existe(raiz: Path, valor: str) -> bool:
    try:
        return ruta_destino(raiz, valor).is_dir()
    except CarpetaInvalida:
        return False


def ruta_libre(carpeta: Path, nombre: str) -> Path:
    base = Path(nombre)
    destino, contador = carpeta / nombre, 1
    while destino.exists():
        destino = carpeta / f"{base.stem} ({contador}){base.suffix}"
        contador += 1
    return destino


def mover_seguro(origen: Path, carpeta_destino: Path) -> Path:
    """Mueve el archivo sin sobrescribir: si ya existe uno igual, añade " (1)", " (2)"... al nombre."""
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    if (carpeta_destino / origen.name).resolve() == origen.resolve():
        return origen
    destino = ruta_libre(carpeta_destino, origen.name)
    return Path(shutil.move(str(origen), str(destino)))
