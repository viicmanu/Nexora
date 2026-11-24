"""Validación de nombres de carpeta y movimiento seguro de archivos."""

from __future__ import annotations

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


def ruta_destino(raiz: Path, carpeta: str) -> Path:
    """Resuelve la carpeta dentro de la raíz organizada y garantiza que no se sale de ella."""
    raiz_resuelta = raiz.expanduser().resolve()
    destino = (raiz_resuelta / normalizar_carpeta(carpeta)).resolve()
    if not destino.is_relative_to(raiz_resuelta):
        raise CarpetaInvalida("La carpeta debe estar dentro de la raíz organizada.")
    return destino


def mover_seguro(origen: Path, carpeta_destino: Path) -> Path:
    """Mueve el archivo sin sobrescribir: si ya existe uno igual, añade " (1)", " (2)"... al nombre."""
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    destino = carpeta_destino / origen.name
    if destino.resolve() == origen.resolve():
        return origen
    contador = 1
    while destino.exists():
        destino = carpeta_destino / f"{origen.stem} ({contador}){origen.suffix}"
        contador += 1
    return Path(shutil.move(str(origen), str(destino)))
