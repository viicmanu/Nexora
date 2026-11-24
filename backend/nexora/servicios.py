"""Construye y conecta los servicios del backend (inyección de dependencias manual)."""

from __future__ import annotations

from dataclasses import dataclass

from nexora.config import Settings
from nexora.core_ia.aprendizaje import ServicioAprendizaje
from nexora.core_ia.busqueda import BuscadorHibrido
from nexora.core_ia.clasificador import Clasificador
from nexora.core_ia.explicador import Explicador
from nexora.core_ia.llm import AdaptadorLLM, crear_adaptador
from nexora.core_ia.vector_store import AlmacenChroma, AlmacenVectorial
from nexora.db import Database
from nexora.db import repositorio as repo
from nexora.file_watcher.indexador import Indexador
from nexora.file_watcher.watcher import VigilanteCarpetas


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

    vectores = vectores or AlmacenChroma(settings.ruta_chroma)
    llm = llm or crear_adaptador(settings)

    def obtener_categorias() -> list[str]:
        with db.conectar() as con:
            return repo.categorias_conocidas(con)

    clasificador = Clasificador(llm, vectores, obtener_categorias)
    indexador = Indexador(
        db, vectores, clasificador, settings.raiz_organizada, settings.umbral_confianza, settings.max_caracteres_clasificacion
    )
    return Servicios(
        settings=settings,
        db=db,
        vectores=vectores,
        llm=llm,
        clasificador=clasificador,
        indexador=indexador,
        buscador=BuscadorHibrido(db, vectores),
        explicador=Explicador(db, llm, settings.max_caracteres_explicacion),
        aprendizaje=ServicioAprendizaje(db, vectores, settings.raiz_organizada),
        vigilante=VigilanteCarpetas(db, indexador, settings.raiz_organizada),
    )
