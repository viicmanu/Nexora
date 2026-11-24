"""Errores de dominio. La capa API los traduce a códigos HTTP."""


class ArchivoNoEncontrado(LookupError):
    """El id no corresponde a ningún archivo indexado."""


class ArchivoNoDisponible(FileNotFoundError):
    """El archivo está en el índice pero ya no existe en esa ruta del PC."""


class SinContenidoLegible(ValueError):
    """No se pudo extraer texto del archivo (formato no soportado, PDF escaneado, cifrado...)."""
