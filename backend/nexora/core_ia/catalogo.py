"""Catálogo de las carpetas que el usuario ya tiene en su PC.

Cada carpeta tiene dos formas:
- `valor`: lo que se guarda y se usa para mover. Relativo si está en la raíz de Nexora ("Finanzas/Hogar"),
  ruta absoluta si está en otro lugar ("C:/Users/yo/Documents/Universidad").
- `etiqueta`: nombre corto que ven el usuario y la IA ("Documentos/Universidad"). Así no se envían rutas
  absolutas al proveedor de IA.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

PROFUNDIDAD = 3
MAX_CARPETAS = 400
MAX_VISITADAS = 4000
VIGENCIA = 15.0  # segundos

_IGNORADAS = {
    "node_modules", "__pycache__", "venv", "env", "dist", "build", "target", "bin", "obj", "$recycle.bin",
    "site-packages", "appdata",
}
# Una carpeta con alguno de estos archivos es un proyecto de programación: no se meten documentos dentro.
_MARCAS_PROYECTO = {".git", "package.json", "pyproject.toml", "cargo.toml", "pom.xml"}


@dataclass
class Catalogo:
    valor_de: dict[str, str] = field(default_factory=dict)      # etiqueta en minúsculas -> valor
    etiqueta_de: dict[str, str] = field(default_factory=dict)   # valor en minúsculas -> etiqueta

    bases: dict[str, str] = field(default_factory=dict)         # zonas que no son destino: etiqueta -> ruta

    @property
    def etiquetas(self) -> list[str]:
        return list(self.etiqueta_de.values())

    def agregar(self, etiqueta: str, valor: str) -> None:
        if etiqueta.casefold() not in self.valor_de and valor.casefold() not in self.etiqueta_de:
            self.valor_de[etiqueta.casefold()] = valor
            self.etiqueta_de[valor.casefold()] = etiqueta

    def etiqueta(self, valor: str) -> str:
        if valor.casefold() in self.etiqueta_de or not os.path.isabs(valor):
            return self.etiqueta_de.get(valor.casefold(), valor)
        for madre in Path(valor).parents:  # subcarpeta nueva dentro de una conocida
            ruta = madre.as_posix()
            nombre = self.etiqueta_de.get(ruta.casefold()) or next(
                (n for n, base in self.bases.items() if base.casefold() == ruta.casefold()), None
            )
            if nombre:
                return f"{nombre}/{Path(valor).relative_to(madre).as_posix()}"
        return Path(valor).name

    def valor(self, etiqueta: str) -> str | None:
        limpia = "/".join(p.strip() for p in etiqueta.replace("\\", "/").split("/") if p.strip())
        return self.valor_de.get(limpia.casefold())

    def valor_nuevo(self, carpeta: str) -> str:
        """Destino de una carpeta que aún no existe: si cuelga de una del usuario, se crea allí y no en Nexora."""
        partes = carpeta.split("/")
        for corte in range(len(partes) - 1, 0, -1):
            etiqueta = "/".join(partes[:corte])
            madre = self.valor(etiqueta) or next(
                (base for n, base in self.bases.items() if n.casefold() == etiqueta.casefold()), None
            )
            if madre:
                return "/".join([madre, *partes[corte:]])
        return carpeta


def zonas_por_defecto() -> list[tuple[str, Path, bool]]:
    """(nombre, ruta, se_puede_usar_como_destino). En Windows respeta las carpetas redirigidas a OneDrive."""
    definiciones = [
        ("Documentos", "Personal", "Documents", False),
        ("Escritorio", "Desktop", "Desktop", False),
        ("Descargas", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads", False),
        ("Imágenes", "My Pictures", "Pictures", True),
        ("Música", "My Music", "Music", True),
        ("Videos", "My Video", "Videos", True),
    ]
    rutas: dict[str, Path] = {}
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as clave:
                for _, registro, _, _ in definiciones:
                    try:
                        rutas[registro] = Path(os.path.expandvars(winreg.QueryValueEx(clave, registro)[0]))
                    except OSError:
                        continue
        except OSError:
            pass
    return [(n, rutas.get(reg, Path.home() / carpeta), destino) for n, reg, carpeta, destino in definiciones]


class BuscadorCarpetas:
    def __init__(self, raiz: Path, zonas: list[Path] | None = None) -> None:
        self.raiz = raiz.expanduser().resolve()
        self.zonas = [(z.name, z.expanduser(), False) for z in zonas] if zonas is not None else zonas_por_defecto()
        self._cache: tuple[float, Catalogo] | None = None

    def invalidar(self) -> None:
        self._cache = None

    def catalogo(self) -> Catalogo:
        if self._cache and time.monotonic() - self._cache[0] < VIGENCIA:
            return self._cache[1]
        catalogo = Catalogo()
        visitadas = 0
        # Recorrido por niveles en todas las zonas a la vez: si se llega al límite, quedan las carpetas principales.
        cola: deque[tuple[Path, str, int, bool]] = deque([(self.raiz, "", 0, True)])
        for nombre, ruta, es_destino in self.zonas:
            if ruta.is_dir():
                catalogo.bases[nombre] = ruta.resolve().as_posix()
            if es_destino and ruta.is_dir():
                catalogo.agregar(nombre, ruta.resolve().as_posix())
            cola.append((ruta, nombre, 0, False))
        while cola and visitadas < MAX_VISITADAS and len(catalogo.valor_de) < MAX_CARPETAS:
            carpeta, prefijo, nivel, en_raiz = cola.popleft()
            visitadas += 1
            for hija in _subcarpetas(carpeta):
                etiqueta = f"{prefijo}/{hija.name}" if prefijo else hija.name
                ruta = hija.resolve()
                if not en_raiz and (ruta == self.raiz or ruta.is_relative_to(self.raiz)):
                    continue  # la raíz de Nexora ya se recorre por su cuenta
                valor = ruta.relative_to(self.raiz).as_posix() if en_raiz else ruta.as_posix()
                catalogo.agregar(etiqueta, valor)
                if nivel + 1 < PROFUNDIDAD:
                    cola.append((hija, etiqueta, nivel + 1, en_raiz))
        self._cache = (time.monotonic(), catalogo)
        return catalogo


def _subcarpetas(carpeta: Path) -> list[Path]:
    try:
        with os.scandir(carpeta) as entradas:
            hijas = [
                Path(e.path) for e in entradas
                if e.is_dir(follow_symlinks=False) and not e.is_junction() and not e.name.startswith((".", "~"))
                and e.name.casefold() not in _IGNORADAS
            ]
    except OSError:
        return []
    return sorted((h for h in hijas if not _es_proyecto(h)), key=lambda p: p.name.casefold())


def _es_proyecto(carpeta: Path) -> bool:
    return any((carpeta / marca).exists() for marca in _MARCAS_PROYECTO)
