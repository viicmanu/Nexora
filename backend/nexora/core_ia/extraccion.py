"""Extracción de texto de PDF, DOCX y archivos de texto plano."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

EXTENSIONES_TEXTO = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml", ".ini",
    ".py", ".js", ".ts", ".java", ".sql", ".rtf",
}
EXTENSIONES_SOPORTADAS = EXTENSIONES_TEXTO | {".pdf", ".docx"}


def extraer_texto(ruta: Path, max_caracteres: int | None = None) -> str:
    """Devuelve el texto del archivo (hasta `max_caracteres` si se indica). Cadena vacía si no se puede leer."""
    extension = ruta.suffix.lower()
    try:
        if extension == ".pdf":
            texto = _extraer_pdf(ruta, max_caracteres)
        elif extension == ".docx":
            texto = _extraer_docx(ruta)
        elif extension in EXTENSIONES_TEXTO:
            texto = _extraer_plano(ruta, max_caracteres)
        else:
            return ""
    except Exception:
        log.warning("No se pudo extraer texto de %s", ruta, exc_info=True)
        return ""
    return texto[:max_caracteres] if max_caracteres else texto


def _extraer_pdf(ruta: Path, max_caracteres: int | None) -> str:
    from pypdf import PdfReader

    lector = PdfReader(str(ruta))
    partes: list[str] = []
    total = 0
    for pagina in lector.pages:
        contenido = pagina.extract_text() or ""
        partes.append(contenido)
        total += len(contenido)
        if max_caracteres and total >= max_caracteres:
            break
    return "\n".join(partes)


def _extraer_docx(ruta: Path) -> str:
    import docx

    documento = docx.Document(str(ruta))
    lineas = [p.text for p in documento.paragraphs if p.text.strip()]
    for tabla in documento.tables:
        for fila in tabla.rows:
            lineas.append(" | ".join(celda.text.strip() for celda in fila.cells))
    return "\n".join(lineas)


def _extraer_plano(ruta: Path, max_caracteres: int | None) -> str:
    limite = max_caracteres * 4 if max_caracteres else -1  # UTF-8 usa hasta 4 bytes por carácter
    with ruta.open("rb") as f:
        datos = f.read(limite)
    for codificacion in ("utf-8", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


def representacion_para_embedding(nombre: str, texto: str, resumen: str | None = None, palabras: list[str] | None = None) -> str:
    """Texto que se convierte en embedding. Igual para el índice y para los ejemplos de aprendizaje."""
    partes = [f"Archivo: {nombre}"]
    if palabras:
        partes.append("Palabras clave: " + ", ".join(palabras))
    if resumen:
        partes.append("Resumen: " + resumen)
    if texto:
        partes.append(texto[:3000])
    return "\n".join(partes)
