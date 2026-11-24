"""Sincronización del tema (claro / oscuro / automático) con sus tokens de color."""

from __future__ import annotations

import asyncio
import subprocess
import sys

from fastapi import APIRouter, Depends

from nexora.api_desktop_mobile.dependencias import obtener_servicios, requiere
from nexora.api_desktop_mobile.esquemas import RespuestaTema, SolicitudTema, TokensColor
from nexora.db import repositorio as repo
from nexora.security.tokens import AJUSTES
from nexora.servicios import Servicios

router = APIRouter(prefix="/api/settings", tags=["ajustes"])

TOKENS = {
    "oscuro": TokensColor(fondo="#120324", superficie="#1C0B35", acento_primario="#00F0FF", acento_secundario="#FF007F"),
    # Variante clara derivada de la paleta oscura (acentos oscurecidos para mantener el contraste sobre blanco).
    "claro": TokensColor(fondo="#F4F0FC", superficie="#FFFFFF", acento_primario="#00A3B4", acento_secundario="#D1006A"),
}


def detectar_tema_so() -> str:
    """Devuelve 'claro' u 'oscuro' según la preferencia del sistema operativo; 'oscuro' si no se puede saber."""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            ) as clave:
                valor, _ = winreg.QueryValueEx(clave, "AppsUseLightTheme")
            return "claro" if valor == 1 else "oscuro"
        if sys.platform == "darwin":
            salida = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2
            )
            return "oscuro" if "Dark" in salida.stdout else "claro"
        salida = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], capture_output=True, text=True, timeout=2
        )
        return "oscuro" if "dark" in salida.stdout else "claro"
    except (OSError, subprocess.SubprocessError):
        return "oscuro"


async def _respuesta(servicios: Servicios) -> RespuestaTema:
    def leer():
        with servicios.db.conectar() as con:
            return repo.leer_ajuste(con, "tema", "auto")

    modo = await asyncio.to_thread(leer)
    efectivo = await asyncio.to_thread(detectar_tema_so) if modo == "auto" else modo
    return RespuestaTema(modo=modo, modo_efectivo=efectivo, detectado_del_so=modo == "auto", tokens=TOKENS[efectivo])


@router.get("/theme", response_model=RespuestaTema)
async def obtener_tema(servicios: Servicios = Depends(obtener_servicios)) -> RespuestaTema:
    return await _respuesta(servicios)


@router.put("/theme", response_model=RespuestaTema, dependencies=[Depends(requiere(AJUSTES))])
async def cambiar_tema(solicitud: SolicitudTema, servicios: Servicios = Depends(obtener_servicios)) -> RespuestaTema:
    def guardar():
        with servicios.db.conectar() as con:
            repo.guardar_ajuste(con, "tema", solicitud.modo)

    await asyncio.to_thread(guardar)
    return await _respuesta(servicios)
