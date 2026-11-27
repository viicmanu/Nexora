"""Construye y conecta los servicios del backend (inyección de dependencias manual)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from nexora.config import Settings
from nexora.core_ia.aprendizaje import ServicioAprendizaje
from nexora.core_ia.busqueda import BuscadorHibrido
from nexora.core_ia.catalogo import BuscadorCarpetas
from nexora.core_ia.clasificador import Clasificador
from nexora.core_ia.correccion import InterpreteCorrecciones
from nexora.core_ia.deshacer import ServicioDeshacer
from nexora.core_ia.embeddings import EmbeddingsMultilingues
from nexora.core_ia.explicador import Explicador
from nexora.core_ia.llm import AdaptadorLLM, crear_adaptador
from nexora.core_ia.vector_store import AlmacenChroma, AlmacenVectorial
from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.file_watcher.indexador import Indexador
from nexora.file_watcher.organizador import Organizador
from nexora.file_watcher.watcher import VigilanteCarpetas

log = logging.getLogger(__name__)


def _crear_almacen(settings: Settings) -> AlmacenChroma:
    try:
        embebedor = EmbeddingsMultilingues(settings.dir_datos / "modelos")
    except Exception:
        log.warning("Sin comparador multilingüe (¿falta internet?): se usa el básico hasta el próximo arranque", exc_info=True)
        embebedor = None
    return AlmacenChroma(settings.ruta_chroma, embebedor)


def _ensenar_anteriores(db: Database, vectores: AlmacenVectorial) -> None:
    """Convierte en ejemplos lo que el motor principal ya había clasificado (no gasta cuota)."""
    with db.conectar() as con:
        filas = repo.ensenables(con)
    for fila in filas:
        try:
            vectores.registrar_ejemplo(f"ia:{fila['id']}", fila["id"], fila["categoria"], False, "ia")
        except Exception:
            log.warning("No se pudo crear el ejemplo de %s", fila["id"], exc_info=True)


@dataclass
class Servicios:
    settings: Settings
    db: Database
    vectores: AlmacenVectorial
    llm: AdaptadorLLM
    clasificador: Clasificador
    indexador: Indexador
    buscador: BuscadorHibrido
    explicador: Explicador
    carpetas: BuscadorCarpetas
    correcciones: InterpreteCorrecciones
    deshacer: ServicioDeshacer
    organizador: Organizador
    aprendizaje: ServicioAprendizaje
    vigilante: VigilanteCarpetas


def construir_servicios(
    settings: Settings, *, vectores: AlmacenVectorial | None = None, llm: AdaptadorLLM | None = None
) -> Servicios:
    """`vectores` y `llm` se pueden sustituir (p. ej. en las pruebas)."""
    db = Database(settings.ruta_db)
    db.inicializar()
    with db.conectar() as con:
        repo.sembrar_carpetas(con, settings.carpetas_vigiladas)
        umbral = float(repo.leer_ajuste(con, "umbral", str(settings.umbral_confianza)))

    vectores = vectores or _crear_almacen(settings)
    llm = llm or crear_adaptador(settings)

    buscador_carpetas = BuscadorCarpetas(settings.raiz_organizada, settings.zonas_busqueda)

    def obtener_indicaciones() -> list[repo.Fila]:
        with db.conectar() as con:
            return repo.indicaciones_activas(con)

    clasificador = Clasificador(llm, vectores, buscador_carpetas, obtener_indicaciones)
    indexador = Indexador(
        db, vectores, clasificador, settings.raiz_organizada, umbral, settings.max_caracteres_clasificacion
    )
    buscador = BuscadorHibrido(db, vectores)
    aprendizaje = ServicioAprendizaje(db, vectores, settings.raiz_organizada)
    _ensenar_anteriores(db, vectores)
    return Servicios(
        settings=settings,
        db=db,
        vectores=vectores,
        llm=llm,
        clasificador=clasificador,
        indexador=indexador,
        buscador=buscador,
        correcciones=InterpreteCorrecciones(db, buscador, clasificador),
        deshacer=ServicioDeshacer(db, vectores),
        organizador=Organizador(db, indexador, aprendizaje, settings.raiz_organizada),
        carpetas=buscador_carpetas,
        explicador=Explicador(db, llm, settings.max_caracteres_explicacion),
        aprendizaje=aprendizaje,
        vigilante=VigilanteCarpetas(db, indexador, settings.raiz_organizada),
    )
